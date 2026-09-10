/**
  ******************************************************************************
  * @file    wifi_esp8266.c
  * @brief   ESP8266(AT) TCP Client 驱动：USART3(PB10/PB11) 115200 8N1
  *
  *  说明：
  *   1) 本文件自行定义并初始化 USART3 句柄 huart3（借助工程已有的
  *      stm32g4xx_hal_msp.c 中 USART3 的 MSP 配置：时钟/GPIO/NVIC）。
  *   2) USART3_IRQHandler 在本文件定义；请保持 stm32g4xx_it.c 中该函数为
  *      注释状态（当前已是），避免重复定义。
  *   3) 接收：单字节中断 -> 环形缓冲 -> 字节状态机（识别 AT 应答与 +IPD 帧）。
  *   4) 发送：阻塞轮询 HAL_UART_Transmit，仅在主循环上下文调用。
  *   5) 链路管理：连接成功送 hello 由应用层(WindCtrl)完成；本层负责
  *      AT 拨号状态机、指数退避重连(1,2,4,...上限10s)、15s 静默看门狗。
  ******************************************************************************
  */
#include "wifi_esp8266.h"
#include "main.h"          /* Error_Handler 等 */
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

/* ================= UART / 接收缓冲 ================= */

UART_HandleTypeDef huart3;   /* USART3 全局句柄（MSP 配置见 stm32g4xx_hal_msp.c） */

#define WIFI_RX_RING_SIZE   1024u
#define LINE_MAX            160u
#define TCP_STREAM_MAX      1024u
#define RXQ_SLOTS           4u
#define RXQ_LINE_MAX        512u
#define TX_PAYLOAD_MAX      512u

static uint8_t  s_rx_ring[WIFI_RX_RING_SIZE];
static volatile uint16_t s_rx_head = 0;   /* ISR 写 */
static volatile uint16_t s_rx_tail = 0;   /* 主循环读 */

static uint8_t  s_rx1;                    /* 单字节接收缓存(中断方式) */

/* +IPD 行缓冲 */
static char     s_line[LINE_MAX];
static uint16_t s_line_len = 0;

/* 解析状态 */
typedef enum {
    FS_LINE = 0,   /* 普通 AT 应答行 */
    FS_IPD_LEN,    /* 解析 +IPD,<n>: 的长度 */
    FS_IPD_DATA    /* 接收 <n> 字节 TCP 载荷 */
} FsmState_t;
static FsmState_t s_fsm = FS_LINE;
static uint32_t   s_ipd_remain = 0;
static char       s_ipd_num[12];
static uint8_t    s_ipd_num_len = 0;

/* TCP 流缓冲：把 IPD 载荷拼接后按 '\n' 切行 */
static char     s_stream[TCP_STREAM_MAX];
static uint16_t s_stream_in = 0, s_stream_out = 0;

/* 行队列（供应用层读取） */
static char     s_rxq[RXQ_SLOTS][RXQ_LINE_MAX];
static uint16_t s_rxq_len[RXQ_SLOTS];
static uint8_t  s_rxq_head = 0, s_rxq_tail = 0;

/* AT 应答令牌 */
#define B_OK       0x0001u
#define B_ERR      0x0002u
#define B_CONN     0x0004u
#define B_IP       0x0008u
#define B_SENDOK   0x0010u
#define B_CLOSED   0x0020u
#define B_DISCONN  0x0040u
#define B_PROMPT   0x0080u
#define B_ALREADY  0x0100u

static volatile uint16_t s_tok = 0;

/* 链路状态机 */
typedef enum {
    WF_ATE0 = 0,
    WF_CWMODE,
    WF_CWJAP,
    WF_CIPSTART,
    WF_STEADY,
    WF_CWQAP,
    WF_BACKOFF
} WifiState_t;

static WifiState_t s_st      = WF_ATE0;
static uint32_t    s_dline   = 0;         /* 当前 AT 命令超时点 */
static bool        s_cmdwait = false;     /* 是否有 AT 命令等待应答 */
static bool        s_link_up = false;     /* TCP 是否在线 */
static bool        s_need_ap = true;      /* 重连是否需要重新关联 AP */
static uint32_t    s_backoff = 0;         /* 当前退避延时 ms */
static uint32_t    s_backoff_t0 = 0;
static uint32_t    s_last_rx = 0;         /* 最近一次收到服务器数据时刻 */
static uint8_t     s_tcpfail = 0;         /* 连续 CIPSTART 失败次数 */

/* ================= 内部函数声明 ================= */
static void  at_poll_rx(void);
static void  ring_push(uint8_t b);
static void  fsm_byte(uint8_t b);
static void  line_classify(void);
static void  stream_extract(void);
static void  rxq_put(const char *line, uint16_t len);
static void  uart3_write(const uint8_t *d, uint16_t n);
static void  at_send_cmd(const char *cmd);
static int   at_wait_ms(uint32_t ms, uint16_t okmask, uint16_t badmask);
static void  wf_enter_backoff(bool need_ap);
static void  wf_start_cmd(const char *cmd, uint32_t ms);
static int   wf_poll_cmd(uint16_t okmask, uint16_t badmask);

/* ================= 串口中断回调（USART3 接收） ================= */

void USART3_IRQHandler(void)
{
    HAL_UART_IRQHandler(&huart3);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART3) {
        ring_push(s_rx1);
        HAL_UART_Receive_IT(&huart3, &s_rx1, 1);   /* 重新使能单字节接收 */
    }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART3) {
        /* 清错误标志 */
        __HAL_UART_CLEAR_OREFLAG(&huart3);
        __HAL_UART_CLEAR_FEFLAG(&huart3);
        __HAL_UART_CLEAR_NEFLAG(&huart3);

        /* 关键：清空接收 ring 与解析 FSM，防止 ORE/帧错误后状态错位
           （ring 内残余数据会让后续 +IPD 解析错位/坏行） */
        s_rx_head = s_rx_tail = 0;
        s_fsm = FS_LINE;
        s_line_len = 0;
        s_ipd_remain = 0;
        s_ipd_num_len = 0;
        s_stream_in = s_stream_out = 0;
        s_rxq_head = s_rxq_tail = 0;
        s_tok = 0;

        /* 重新使能单字节接收 */
        HAL_UART_Receive_IT(&huart3, &s_rx1, 1);
    }
}

/* ================= 初始化 ================= */

void Wifi_Init(void)
{
    /* 与 main.c 中 USART2 完全一致的参数配置方式（G4 需处理 FIFO） */
    huart3.Instance = USART3;
    huart3.Init.BaudRate = WIFI_AT_BAUD;
    huart3.Init.WordLength = UART_WORDLENGTH_8B;
    huart3.Init.StopBits = UART_STOPBITS_1;
    huart3.Init.Parity = UART_PARITY_NONE;
    huart3.Init.Mode = UART_MODE_TX_RX;
    huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart3.Init.OverSampling = UART_OVERSAMPLING_16;
    huart3.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart3.Init.ClockPrescaler = UART_PRESCALER_DIV1;
    huart3.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
    if (HAL_UART_Init(&huart3) != HAL_OK) {
        Error_Handler();
    }
    if (HAL_UARTEx_SetTxFifoThreshold(&huart3, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK) Error_Handler();
    if (HAL_UARTEx_SetRxFifoThreshold(&huart3, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK) Error_Handler();
    if (HAL_UARTEx_DisableFifoMode(&huart3) != HAL_OK) Error_Handler();

    /* 使能单字节接收中断（NVIC 已在 MSP 中使能） */
    HAL_UART_Receive_IT(&huart3, &s_rx1, 1);

    /* 等 ESP8266 上电自检完成 */
    HAL_Delay(1200);

    s_st      = WF_ATE0;
    s_need_ap = true;
    s_backoff = 0;
    s_last_rx = HAL_GetTick();
}

/* ================= 对外接口 ================= */

bool Wifi_IsUp(void)
{
    return s_link_up;
}

/* 周期轮询：必须频繁调用 */
void Wifi_Periodic(void)
{
    at_poll_rx();   /* 先消化接收缓冲 */

    switch (s_st) {
    case WF_ATE0: {
        if (!s_cmdwait) {
            wf_start_cmd("ATE0\r\n", 1500u);
        } else {
            int r = wf_poll_cmd(B_OK, B_ERR);
            if (r == 1)      { s_st = WF_CWMODE; }
            else if (r < 0)  { wf_enter_backoff(true); }
        }
        break;
    }
    case WF_CWMODE: {
        if (!s_cmdwait) {
            wf_start_cmd("AT+CWMODE=1\r\n", 2000u);
        } else {
            int r = wf_poll_cmd(B_OK, B_ERR);
            if (r == 1)      { s_st = WF_CWJAP; }
            else if (r < 0)  { wf_enter_backoff(true); }
        }
        break;
    }
    case WF_CWJAP: {
        if (!s_cmdwait) {
            char cmd[200];
            snprintf(cmd, sizeof(cmd), "AT+CWJAP=\"%s\",\"%s\"\r\n",
                     WIFI_SSID, WIFI_PASSWORD);
            wf_start_cmd(cmd, 12000u);
        } else {
            int r = wf_poll_cmd(B_OK, B_ERR);
            if (r == 1)      { s_st = WF_CIPSTART; }
            else if (r < 0)  { wf_enter_backoff(true); }
        }
        break;
    }
    case WF_CIPSTART: {
        if (!s_cmdwait) {
            char cmd[200];
            snprintf(cmd, sizeof(cmd), "AT+CIPSTART=\"TCP\",\"%s\",%u\r\n",
                     GRID_SERVER_IP, (unsigned)GRID_SERVER_PORT);
            wf_start_cmd(cmd, 10000u);
        } else {
            int r = wf_poll_cmd(B_CONN | B_ALREADY | B_OK, B_ERR | B_CLOSED | B_DISCONN);
            if (r == 1) {
                s_st      = WF_STEADY;
                s_link_up = true;
                s_backoff = 0;
                s_tcpfail = 0;
                s_need_ap = false;
                s_last_rx = HAL_GetTick();
            } else if (r < 0) {
                /* 关键修改：将"触发 CWJAP 重连 AP"门槛从 3 提至 6。
                   原因：server 端的临时关闭（如 socket 池回收、心跳超时重置）
                   会导致 CIPSTART 短时反复失败 1-2 次，不应立刻回退到重新关联 AP。
                   累积够多次才视为 WiFi/AP 有问题。 */
                if (++s_tcpfail >= 6) s_need_ap = true;
                wf_enter_backoff(s_need_ap);
            }
        }
        break;
    }
    case WF_CWQAP: {
        if (!s_cmdwait) {
            wf_start_cmd("AT+CWQAP\r\n", 2000u);
        } else {
            /* CWQAP 失败(未连 AP 时返回 ERROR)也继续 CWJAP，故 ERR 视为完成 */
            int r = wf_poll_cmd(B_OK, 0u);
            if (r == 1 || r < 0) { s_st = WF_CWJAP; }
        }
        break;
    }
    case WF_STEADY: {
        if (s_tok & (B_CLOSED | B_DISCONN)) {
            bool disconn = (s_tok & B_DISCONN) != 0;
            s_tok = 0;
            s_link_up = false;
            /* 关键修改：WIFI DISCONNECT 视为 WiFi 层抖动，不强制重连 AP。
               ESP8266 默认会自动重连已保存的 AP（除非断电）；跑完整 CWJAP 一遍
               至少耗 12s，将导致"每次 server 关 socket → STM32 走 24s + backoff"
               这种恶性链路。改为 false：backoff 之后仅做 CIPSTART，让 ESP8266 自己
               负责 WiFi 维持。仅当 CIPSTART 连续失败达到阈值（见下）才回退 CWJAP。 */
            (void)disconn;          /* 保留判别以便日志/扩展 */
            wf_enter_backoff(false);
            break;
        }
        s_tok = 0;   /* 其余令牌为瞬时事件，清空防误判 */

        /* 15s 未收到服务器任何数据 => 判定断线，重连（不重连 AP） */
        if ((HAL_GetTick() - s_last_rx) > 15000u) {
            s_link_up = false;
            wf_enter_backoff(false);
        }
        break;
    }
    case WF_BACKOFF: {
        if ((HAL_GetTick() - s_backoff_t0) >= s_backoff) {
            s_st = s_need_ap ? WF_CWQAP : WF_CIPSTART;
        }
        break;
    }
    default:
        break;
    }
}

/* 发送一帧 TCP 数据。payload 需以 '\n' 结尾（协议要求），长度 ≤ TX_PAYLOAD_MAX */
bool Wifi_TcpSend(const char *payload, uint16_t len)
{
    if (!s_link_up || len == 0 || len > TX_PAYLOAD_MAX) return false;

    /* AT+CIPSEND=<len> -> 等待 '>' -> 发数据 -> 等待 SEND OK */
    char cmd[48];
    snprintf(cmd, sizeof(cmd), "AT+CIPSEND=%u\r\n", (unsigned)len);

    at_send_cmd(cmd);
    int r = at_wait_ms(1000u, B_PROMPT, B_ERR | B_CLOSED | B_DISCONN);
    if (r == 1) {
        uart3_write((const uint8_t *)payload, len);
        r = at_wait_ms(2000u, B_SENDOK, B_ERR | B_CLOSED | B_DISCONN);
    }
    if (r != 1) {
        s_link_up = false;
        wf_enter_backoff(false);
        return false;
    }
    return true;
}

/* 从行队列取一条完整 TCP 数据行（不含 '\n'）。返回长度；0 表示无数据 */
uint16_t Wifi_RxLineGet(char *buf, uint16_t max_len)
{
    if (s_rxq_head == s_rxq_tail) return 0;

    uint16_t l = s_rxq_len[s_rxq_tail];
    if (l > max_len - 1) l = max_len - 1;
    memcpy(buf, s_rxq[s_rxq_tail], l);
    buf[l] = 0;
    s_rxq_tail = (s_rxq_tail + 1) % RXQ_SLOTS;
    return l;
}

/* ================= 内部实现 ================= */

/* 接收字节入环形缓冲（ISR 上下文调用） */
static void ring_push(uint8_t b)
{
    uint16_t next = (s_rx_head + 1) % WIFI_RX_RING_SIZE;
    if (next != s_rx_tail) {
        s_rx_ring[s_rx_head] = b;
        s_rx_head = next;
    }
}

/* 把串口数据逐个送入解析状态机 */
static void at_poll_rx(void)
{
    while (s_rx_tail != s_rx_head) {
        uint8_t b = s_rx_ring[s_rx_tail];
        s_rx_tail = (s_rx_tail + 1) % WIFI_RX_RING_SIZE;
        fsm_byte(b);
    }
}

/* 字节状态机：识别 '+IPD,<n>:' 载荷 / AT 应答 / '>' 提示符 */
static void fsm_byte(uint8_t b)
{
    switch (s_fsm) {
    case FS_LINE:
        /* '>' 单独出现（CIPSEND 提示符），独立成行时也算 */
        if (b == '>' && s_line_len == 0) {
            s_tok |= B_PROMPT;
            break;
        }
        if (b == '\r') break;              /* 忽略回车 */
        if (b == '\n') {
            line_classify();
            s_line_len = 0;
            break;
        }
        if (s_line_len < LINE_MAX - 1) {
            s_line[s_line_len++] = (char)b;
            s_line[s_line_len] = 0;
            /* 出现 +IPD, 头 -> 转去解析长度 */
            if (s_line_len >= 5 && memcmp(s_line, "+IPD,", 5) == 0) {
                s_fsm = FS_IPD_LEN;
                s_ipd_num_len = 0;
            }
        }
        break;

    case FS_IPD_LEN:
        if (b >= '0' && b <= '9') {
            if (s_ipd_num_len < sizeof(s_ipd_num) - 1)
                s_ipd_num[s_ipd_num_len++] = (char)b;
        } else if (b == ':') {
            s_ipd_num[s_ipd_num_len] = 0;
            s_ipd_remain = (uint32_t)strtoul(s_ipd_num, NULL, 10);
            s_ipd_num_len = 0;
            if (s_ipd_remain > 0) {
                s_fsm = FS_IPD_DATA;
                s_line_len = 0;          /* 清除 +IPD,.. 头部残留 */
            } else {
                s_fsm = FS_LINE;         /* 长度为 0，回到行模式 */
                s_line_len = 0;
            }
        } else if (b == '\r' || b == '\n') {
            /* 容忍换行 */
        } else {
            s_fsm = FS_LINE;             /* 格式异常，放弃该行 */
            s_line_len = 0;
        }
        break;

    case FS_IPD_DATA:
        /* TCP 载荷字节：写入流缓冲（视为服务器数据，刷新静默看门狗） */
        if (s_stream_in < TCP_STREAM_MAX) {
            s_stream[s_stream_in++] = (char)b;
        }
        if (s_ipd_remain > 0) s_ipd_remain--;
        if (s_ipd_remain == 0) {
            s_last_rx = HAL_GetTick();
            stream_extract();            /* 按 '\n' 切出完整行入队 */
            s_fsm = FS_LINE;
            s_line_len = 0;
        }
        break;
    }
}

/* AT 应答行分类 -> 置令牌 */
static void line_classify(void)
{
    const char *s = s_line;
    if (s_line_len == 0) return;

    if (strcmp(s, "OK") == 0) {
        s_tok |= B_OK;
    } else if (strstr(s, "SEND FAIL") || strstr(s, "CONNECT FAIL") ||
               strstr(s, "DNS FAIL") || strstr(s, "ERROR") || strstr(s, "FAIL")) {
        s_tok |= B_ERR;
    } else if (strstr(s, "ALREADY CONNECTED")) {
        s_tok |= B_ALREADY;
    } else if (strcmp(s, "CONNECT") == 0) {
        s_tok |= B_CONN;
    } else if (strstr(s, "SEND OK")) {
        s_tok |= B_SENDOK;
    } else if (strstr(s, "WIFI DISCONNECT")) {
        s_tok |= B_DISCONN;
    } else if (strstr(s, "CLOSED") || strstr(s, "Unlink")) {
        s_tok |= B_CLOSED;
    } else if (strstr(s, "WIFI GOT IP")) {
        s_tok |= B_IP;
    }
    /* 其它行忽略 */
}

/* 从 TCP 流缓冲按 '\n' 切行入队 */
static void stream_extract(void)
{
    while (s_stream_out < s_stream_in) {
        /* 找行尾 '\n' */
        uint16_t i = s_stream_out;
        while (i < s_stream_in && s_stream[i] != '\n') i++;
        if (i >= s_stream_in) break;          /* 还不完整 */

        uint16_t len = i - s_stream_out;
        /* 去掉可能存在的 '\r' */
        if (len > 0 && s_stream[s_stream_out + len - 1] == '\r') len--;

        if (len > 0) {
            /* 栈缓冲：每帧就地分配，避免 static 在快速切行时长度截断/覆盖。
               单条 IPD 行不会超过 RXQ_LINE_MAX（rxq_put 还会有最终兜底截断）。 */
            char tmp[RXQ_LINE_MAX];
            if (len > RXQ_LINE_MAX - 1) len = RXQ_LINE_MAX - 1;
            memcpy(tmp, &s_stream[s_stream_out], len);
            rxq_put(tmp, len);
        }
        s_stream_out = i + 1;                 /* 跳过 '\n' */
    }
    /* 压缩 */
    if (s_stream_out >= s_stream_in) {
        s_stream_in = s_stream_out = 0;
    } else if (s_stream_out > 0) {
        memmove(s_stream, &s_stream[s_stream_out], s_stream_in - s_stream_out);
        s_stream_in -= s_stream_out;
        s_stream_out = 0;
    }
}

static void rxq_put(const char *line, uint16_t len)
{
    uint8_t next = (s_rxq_head + 1) % RXQ_SLOTS;
    if (next == s_rxq_tail) return;           /* 队列满，丢弃新行 */
    memcpy(s_rxq[s_rxq_head], line, len);
    s_rxq_len[s_rxq_head] = len;
    s_rxq_head = next;
}

static void uart3_write(const uint8_t *d, uint16_t n)
{
    HAL_UART_Transmit(&huart3, (uint8_t *)d, n, 200);
}

/* AT 命令发送：发送前必须复位接收解析 FSM
   重要：若当前 s_fsm 正处于 FS_IPD_DATA（还在收 TCP 载荷），此时发 AT 命令
   后续字节会被错当成 TCP 载荷，污染流缓冲并破坏 +IPD 帧解析。 */
static void at_send_cmd(const char *cmd)
{
    if (s_fsm != FS_LINE) {
        s_fsm = FS_LINE;
        s_line_len = 0;
        s_ipd_remain = 0;
        s_ipd_num_len = 0;
        /* 不清空 s_stream，交由下次 +IPD 自己覆盖；如需立即干净化可一并清 */
    }
    s_tok = 0;
    uart3_write((const uint8_t *)cmd, (uint16_t)strlen(cmd));
}

/* 阻塞等待令牌（内部轮询接收解析，不阻塞接收） */
static int at_wait_ms(uint32_t ms, uint16_t okmask, uint16_t badmask)
{
    uint32_t t0 = HAL_GetTick();
    while ((HAL_GetTick() - t0) < ms) {
        at_poll_rx();
        if (s_tok & badmask) return -1;
        if (s_tok & okmask)  return 1;
    }
    return 0;
}

/* 状态机工具：开始一条 AT 命令 */
static void wf_start_cmd(const char *cmd, uint32_t ms)
{
    at_send_cmd(cmd);
    s_dline = HAL_GetTick() + ms;
    s_cmdwait = true;
}

/* 状态机工具：轮询当前命令结果；1=成功 -1=失败/超时 0=等待中 */
static int wf_poll_cmd(uint16_t okmask, uint16_t badmask)
{
    if (s_tok & okmask) {
        s_tok = 0; s_cmdwait = false;
        return 1;
    }
    if ((s_tok & badmask) || (HAL_GetTick() > s_dline)) {
        s_tok = 0; s_cmdwait = false;
        return -1;
    }
    return 0;
}

/* 进入退避重连：指数退避 1,2,4,...上限 10s */
static void wf_enter_backoff(bool need_ap)
{
    if (s_st == WF_BACKOFF) return;          /* 已在退避中 */

    s_link_up = false;
    if (need_ap) s_need_ap = true;

    if (s_backoff == 0) {
        s_backoff = 1000u;
    } else {
        s_backoff *= 2;
        if (s_backoff > 10000u) s_backoff = 10000u;
    }
    s_backoff_t0 = HAL_GetTick();
    s_cmdwait = false;
    s_tok = 0;
    s_st = WF_BACKOFF;
}
