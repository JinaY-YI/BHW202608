/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "stm32g4xx_hal.h"
#include <string.h>
#include <stdio.h>
#include <stdbool.h>
#include "wind_shared.h"       /* 共享寄存器（串口/TCP/控制共用） */
#include "wind_ctrl_app.h"     /* 风机控制器应用层（WiFi TCP 协议） */
#include "wind_compute.h"      /* 风机控制计算（启停/桨距/可用功率） */
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* ----------------------------- 全局变量 ----------------------------- */
/* 类型定义与 extern 声明见 wind_shared.h；以下为唯一定义点 */
/* 参数默认值需与电网模拟器(学生A compute_engine.py _load_params 默认
   {cut_in:3, cut_out:25, rated_wind:12, rated_power:50})保持一致；
   上线联调时会经串口 CMD 0x01 / TCP param_push 由两端 HMI 同步，但
   代码默认必须对齐，否则纯前馈反解会按错误基准算桨距角 */
WindParam_t g_wind_params = {3.0f, 25.0f, 12.0f, 50.0f};
uint8_t g_ctrl_mode = 0;      // 0=开环, 1=闭环
RealtimeData_t g_realtime = {0};

/* ---------- 串口接收环形缓冲区 ---------- */
#define RX_RING_SIZE   256
static uint8_t rx_ring[RX_RING_SIZE];
static volatile uint16_t rx_head = 0;
static volatile uint16_t rx_tail = 0;

/* HAL 库接收缓冲区（用于 DMA） */
#define RX_BUF_SIZE     128
static uint8_t rx_buffer[RX_BUF_SIZE];

/* 定时发送标志 */
static uint32_t last_send_tick = 0;

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* ----------------------------- 硬件定义 ----------------------------- */
#define USART2_TX_PIN      GPIO_PIN_2
#define USART2_RX_PIN      GPIO_PIN_3
#define USART2_AF          GPIO_AF7_USART2

/* ----------------------------- 协议常量 ----------------------------- */
#define FRAME_HEAD1        0xAA
#define FRAME_HEAD2        0x55
#define FRAME_TAIL         0x0D
#define MAX_FRAME_LEN      64

#define CMD_SET_PARAM      0x01
#define CMD_REPORT_REALTIME 0x02
#define CMD_SET_MODE       0x03
#define CMD_ACK_RESULT     0x04
#define CMD_QUERY_PARAM    0x05
#define CMD_RESP_PARAM     0x06

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
UART_HandleTypeDef huart2;
DMA_HandleTypeDef hdma_usart2_rx;   // 仅保留接收 DMA

/* USER CODE BEGIN PV */

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_DMA_Init(void);
static void MX_USART2_UART_Init(void);
/* USER CODE BEGIN PFP */

static void ring_push(uint8_t byte);
static uint16_t ring_pop(uint8_t *buf, uint16_t max_len);

static void float_to_bytes(float val, uint8_t *bytes);
static float bytes_to_float(uint8_t *bytes);
static uint16_t CRC16_Modbus(uint8_t *buf, uint16_t len);
static void PackFrame(uint8_t cmd, uint8_t *data, uint16_t data_len, uint8_t *out_buf, uint16_t *out_len);
static bool ParseFrame(uint8_t *buf, uint16_t len, uint8_t *cmd, uint8_t *data, uint16_t *data_len);

static void SendAck(uint8_t result);
static void SendParamResponse(void);
static void SendRealtimeData(void);
static void ProcessReceivedFrame(uint8_t cmd, uint8_t *data, uint16_t data_len);
void Error_Handler(void);

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */

	HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_USART2_UART_Init();
  /* USER CODE BEGIN 2 */

  /* 初始化风机控制器应用：Wi-Fi(USART3/ESP8266) TCP 拨号 + 协议状态机 */
  WindCtrl_Init();

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */

      /* ----- 0. 风机控制器周期任务：TCP 收发/心跳/断线重连 + 1s 占位控制计算 ----- */
      WindCtrl_Periodic();

      /* ----- 1. 从环形缓冲区提取并解析一帧（循环处理所有完整帧） ----- */
      while (1) {
          uint8_t frame_buf[MAX_FRAME_LEN];
          uint16_t frame_len = 0;
          bool found = false;

          // 查找帧头 0xAA 0x55
          while (rx_tail != rx_head) {
              uint8_t byte = rx_ring[rx_tail];
              if (byte == FRAME_HEAD1) {
                  // 尝试复制最多 MAX_FRAME_LEN 字节
                  uint16_t temp_tail = rx_tail;
                  uint16_t cnt = 0;
                  while (temp_tail != rx_head && cnt < MAX_FRAME_LEN) {
                      frame_buf[cnt++] = rx_ring[temp_tail];
                      temp_tail = (temp_tail + 1) % RX_RING_SIZE;
                  }
                  if (cnt >= 7) {
                      uint8_t cmd, data[MAX_FRAME_LEN];
                      uint16_t data_len;
                      if (ParseFrame(frame_buf, cnt, &cmd, data, &data_len)) {
                          // 解析成功，从环形缓冲区移除这些字节
                          for (uint16_t i = 0; i < cnt; i++) {
                              rx_tail = (rx_tail + 1) % RX_RING_SIZE;
                          }
                          ProcessReceivedFrame(cmd, data, data_len);
                          found = true;
                          break;   // 成功处理一帧，重新查找下一帧
                      } else {
                          // 帧无效，丢弃第一个字节继续查找
                          rx_tail = (rx_tail + 1) % RX_RING_SIZE;
                          continue;
                      }
                  } else {
                      // 数据不足，等待更多数据
                      break;
                  }
              } else {
                  // 不是帧头，丢弃
                  rx_tail = (rx_tail + 1) % RX_RING_SIZE;
              }
          }
          if (!found) break; // 没有更多完整帧，退出内循环
      }

      /* ----- 2. 定时任务：每秒发送实时数据（使用阻塞发送） ----- */
      if (HAL_GetTick() - last_send_tick >= 1000) {
          last_send_tick = HAL_GetTick();
          /* g_realtime 由 WindCtrl_Periodic() 内的控制计算更新。
             开环/闭环都会计算启停与桨距角策略，因此本帧上报的是：
               wtg_status / pitch_angle_set / available_power —— 控制策略结果
               wtg_power      —— 闭环：电网模拟器 wind_result 回传的实测出力
                                 开环：wind_compute.c 本地预估出力
                                       （模拟器不执行指令、不回传该值）
               ctrl_mode_wind —— 当前开环/闭环
               reserved.bit0  —— 本周期是否已下发闭环控制指令
                                （0 = 开环策略预览或链路离线，未输出执行命令） */
          SendRealtimeData();
      }

      /* ----- 3. 控制计算在 WindCtrl_Periodic() 的 1s 周期任务中执行
                （wind_compute.c: WindCompute_Step），此处无需重复调用 ----- */

  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1_BOOST);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = RCC_PLLM_DIV4;
  RCC_OscInitStruct.PLL.PLLN = 85;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
  RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief USART2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART2_UART_Init(void)
{

  /* USER CODE BEGIN USART2_Init 0 */

  /* USER CODE END USART2_Init 0 */

  /* USER CODE BEGIN USART2_Init 1 */

  /* USER CODE END USART2_Init 1 */
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 115200;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  huart2.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart2.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  huart2.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetTxFifoThreshold(&huart2, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetRxFifoThreshold(&huart2, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_DisableFifoMode(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART2_Init 2 */

  /* 使能 USART2 中断（用于空闲中断） */
  HAL_NVIC_SetPriority(USART2_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(USART2_IRQn);

  /* 启动 DMA 接收，等待空闲中断触发回调 */
  if (HAL_UARTEx_ReceiveToIdle_DMA(&huart2, rx_buffer, RX_BUF_SIZE) != HAL_OK)
  {
    Error_Handler();
  }

  /* USER CODE END USART2_Init 2 */

}

/**
  * Enable DMA controller clock and configure DMA
  */
static void MX_DMA_Init(void)
{

  /* DMA controller clock enable */
  __HAL_RCC_DMAMUX1_CLK_ENABLE();
  __HAL_RCC_DMA1_CLK_ENABLE();

  /* ----- 接收 DMA 配置（DMA1 Channel1）----- */
  hdma_usart2_rx.Instance = DMA1_Channel1;
  hdma_usart2_rx.Init.Request = DMA_REQUEST_USART2_RX;
  hdma_usart2_rx.Init.Direction = DMA_PERIPH_TO_MEMORY;
  hdma_usart2_rx.Init.PeriphInc = DMA_PINC_DISABLE;
  hdma_usart2_rx.Init.MemInc = DMA_MINC_ENABLE;
  hdma_usart2_rx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
  hdma_usart2_rx.Init.MemDataAlignment = DMA_MDATAALIGN_BYTE;
  hdma_usart2_rx.Init.Mode = DMA_NORMAL;
  hdma_usart2_rx.Init.Priority = DMA_PRIORITY_HIGH;
  if (HAL_DMA_Init(&hdma_usart2_rx) != HAL_OK)
  {
    Error_Handler();
  }
  __HAL_LINKDMA(&huart2, hdmarx, hdma_usart2_rx);
  HAL_NVIC_SetPriority(DMA1_Channel1_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(DMA1_Channel1_IRQn);

  /* 注意：发送不再使用 DMA，因此无需配置 DMA1 Channel2 */
}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOA_CLK_ENABLE();

  /* PA2 - USART2_TX, PA3 - USART2_RX 复用为 AF7 */
  GPIO_InitStruct.Pin = GPIO_PIN_2 | GPIO_PIN_3;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_PULLUP;          // RX 空闲高电平
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  GPIO_InitStruct.Alternate = GPIO_AF7_USART2;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
}

/* USER CODE BEGIN 4 */

/* ----------------------------- 环形缓冲区操作 ----------------------------- */

static void ring_push(uint8_t byte)
{
    uint16_t next = (rx_head + 1) % RX_RING_SIZE;
    if (next != rx_tail) {
        rx_ring[rx_head] = byte;
        rx_head = next;
    }
}

static uint16_t ring_pop(uint8_t *buf, uint16_t max_len)
{
    uint16_t cnt = 0;
    while (rx_tail != rx_head && cnt < max_len) {
        buf[cnt++] = rx_ring[rx_tail];
        rx_tail = (rx_tail + 1) % RX_RING_SIZE;
    }
    return cnt;
}

/* ----------------------------- 协议工具函数 ----------------------------- */

static void float_to_bytes(float val, uint8_t *bytes)
{
    memcpy(bytes, &val, 4);
}
static float bytes_to_float(uint8_t *bytes)
{
    float val;
    memcpy(&val, bytes, 4);
    return val;
}

static uint16_t CRC16_Modbus(uint8_t *buf, uint16_t len)
{
    uint16_t crc = 0xFFFF;
    for (uint16_t i = 0; i < len; i++) {
        crc ^= buf[i];
        for (uint8_t j = 0; j < 8; j++) {
            if (crc & 0x0001) {
                crc = (crc >> 1) ^ 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    return crc;
}

static void PackFrame(uint8_t cmd, uint8_t *data, uint16_t data_len, uint8_t *out_buf, uint16_t *out_len)
{
    uint16_t total_len = 7 + data_len;   // 帧头2 + 长度1 + 命令1 + 数据N + CRC2 + 帧尾1
    out_buf[0] = FRAME_HEAD1;
    out_buf[1] = FRAME_HEAD2;
    out_buf[2] = (uint8_t)total_len;
    out_buf[3] = cmd;
    if (data && data_len) {
        memcpy(&out_buf[4], data, data_len);
    }
    uint16_t crc = CRC16_Modbus(&out_buf[2], 2 + data_len); // 长度+命令+数据
    out_buf[4 + data_len] = crc & 0xFF;
    out_buf[5 + data_len] = (crc >> 8) & 0xFF;
    out_buf[6 + data_len] = FRAME_TAIL;
    *out_len = total_len;
}

static bool ParseFrame(uint8_t *buf, uint16_t len, uint8_t *cmd, uint8_t *data, uint16_t *data_len)
{
    if (len < 7) return false;
    if (buf[0] != FRAME_HEAD1 || buf[1] != FRAME_HEAD2 || buf[len-1] != FRAME_TAIL)
        return false;
    uint8_t frame_len = buf[2];
    if (frame_len != len) return false;
    if (frame_len > MAX_FRAME_LEN) return false;

    *cmd = buf[3];
    uint16_t dlen = frame_len - 7;
    *data_len = dlen;
    if (dlen > 0 && data) {
        memcpy(data, &buf[4], dlen);
    }
    // CRC校验
    uint16_t crc_calc = CRC16_Modbus(&buf[2], 2 + dlen);
    uint16_t crc_recv = buf[4 + dlen] | (buf[5 + dlen] << 8);
    return (crc_calc == crc_recv);
}

/* ----------------------------- 发送函数（阻塞轮询） ----------------------------- */

static void SendAck(uint8_t result)
{
    uint8_t tx_buf[MAX_FRAME_LEN];
    uint16_t len;
    PackFrame(CMD_ACK_RESULT, &result, 1, tx_buf, &len);
    HAL_UART_Transmit(&huart2, tx_buf, len, 100);
}

static void SendParamResponse(void)
{
    uint8_t tx_buf[MAX_FRAME_LEN];
    uint8_t data[16];
    float_to_bytes(g_wind_params.cut_in_wind,  &data[0]);
    float_to_bytes(g_wind_params.cut_out_wind, &data[4]);
    float_to_bytes(g_wind_params.rated_wind,   &data[8]);
    float_to_bytes(g_wind_params.rated_power,  &data[12]);
    uint16_t len;
    PackFrame(CMD_RESP_PARAM, data, 16, tx_buf, &len);
    HAL_UART_Transmit(&huart2, tx_buf, len, 100);
}

static void SendRealtimeData(void)
{
    uint8_t tx_buf[MAX_FRAME_LEN];
    uint8_t data[24];
    float_to_bytes(g_realtime.wind_speed,      &data[0]);
    float_to_bytes(g_realtime.wtg_power_set,   &data[4]);
    float_to_bytes(g_realtime.wtg_power,       &data[8]);
    float_to_bytes(g_realtime.pitch_angle_set, &data[12]);
    float_to_bytes(g_realtime.available_power, &data[16]);
    data[20] = g_realtime.wtg_status;
    data[21] = g_realtime.ctrl_mode_wind;
    data[22] = g_realtime.link_grid;
    data[23] = g_realtime.reserved;
    uint16_t len;
    PackFrame(CMD_REPORT_REALTIME, data, 24, tx_buf, &len);
    HAL_UART_Transmit(&huart2, tx_buf, len, 100);
}

/* ----------------------------- 命令处理 ----------------------------- */

static void ProcessReceivedFrame(uint8_t cmd, uint8_t *data, uint16_t data_len)
{
    switch (cmd) {
        case CMD_SET_PARAM: {
            if (data_len == 16) {
                g_wind_params.cut_in_wind   = bytes_to_float(&data[0]);
                g_wind_params.cut_out_wind  = bytes_to_float(&data[4]);
                g_wind_params.rated_wind    = bytes_to_float(&data[8]);
                g_wind_params.rated_power   = bytes_to_float(&data[12]);
                SendAck(0);  // 成功
                WindCtrl_OnSerialParamChanged();  // 触发 TCP param_sync 同步电网模拟器
            } else {
                SendAck(1);  // 失败
            }
            break;
        }
        case CMD_SET_MODE: {
            if (data_len == 1) {
                uint8_t mode = data[0];
                if (mode == 0 || mode == 1) {
                    g_ctrl_mode = mode;
                    g_realtime.ctrl_mode_wind = mode;
                    SendAck(0);
                    WindCtrl_OnSerialModeChanged();
                } else {
                    SendAck(1);
                }
            } else {
                SendAck(1);
            }
            break;
        }
        case CMD_QUERY_PARAM: {
            SendParamResponse();
            break;
        }
        default:
            // 未知命令，忽略
            break;
    }
}

/* ----------------------------- DMA 回调函数（接收） ----------------------------- */

/**
  * @brief  Rx Event callback (空闲中断触发，接收完成)
  * @param  huart UART handle
  * @param  Size 本次接收到的字节数
  * @retval None
  */
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance == USART2)
    {
        /* 将接收到的数据逐字节压入环形缓冲区 */
        for (uint16_t i = 0; i < Size; i++)
        {
            ring_push(rx_buffer[i]);
        }

        /* 重新启动 DMA 接收，等待下一帧 */
        if (HAL_UARTEx_ReceiveToIdle_DMA(&huart2, rx_buffer, RX_BUF_SIZE) != HAL_OK)
        {
            Error_Handler();
        }
    }
}

/* ----------------------------- USART2 中断服务函数 ----------------------------- */
/* 注意：请确保在 stm32g4xx_it.c 中注释或删除原有的 USART2_IRQHandler，避免重复定义 */

void USART2_IRQHandler(void)
{
    /* 调用 HAL 库通用中断处理（会处理空闲中断并触发回调） */
    HAL_UART_IRQHandler(&huart2);
}

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
