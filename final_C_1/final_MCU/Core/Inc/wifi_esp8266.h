/**
  ******************************************************************************
  * @file    wifi_esp8266.h
  * @brief   ESP8266(AT固件) TCP Client 驱动（USART3, PB10-TX/PB11-RX）
  *
  *  角色：风机控制器 = TCP Client，主动连接电网模拟器(TCP Server:8888)
  *  - Wifi_Init()     初始化 USART3 并启动 AT 状态机
  *  - Wifi_Periodic() 必须在主循环中频繁调用（状态机/断线重连/接收解析）
  *  - Wifi_TcpSend()  阻塞发送一帧 TCP 数据（数据需以 '\n' 结尾，符合协议）
  *  - Wifi_RxLineGet() 取出完整的一行 TCP 数据（不含 '\n'），供上层解析
  ******************************************************************************
  */
#ifndef __WIFI_ESP8266_H
#define __WIFI_ESP8266_H

#include <stdint.h>
#include <stdbool.h>

/* ================= 配置区：按实际环境修改 ================= */

/* 与电网模拟器(学生A 计算机)同一个局域网的 AP */
#ifndef WIFI_SSID
#define WIFI_SSID        "12345678 Mate 70 Pro"
#endif
#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD    "5x368eff"
#endif

/* 电网模拟器 TCP Server：计算机A 的局域网 IP，端口 8888 */
#ifndef GRID_SERVER_IP
#define GRID_SERVER_IP   "192.168.43.72"
#endif
#ifndef GRID_SERVER_PORT
#define GRID_SERVER_PORT 8088u
#endif

/* ESP8266 AT 固件串口波特率（绝大多数出厂为 115200） */
#ifndef WIFI_AT_BAUD
#define WIFI_AT_BAUD     115200
#endif

/* ================= 接口 ================= */

void     Wifi_Init(void);                 /* 初始化 UART + 状态机，必须在主循环前调用一次 */
void     Wifi_Periodic(void);             /* 周期轮询：主循环每圈调用 */
bool     Wifi_IsUp(void);                 /* 1 = TCP 已连接（链路在线） */
bool     Wifi_TcpSend(const char *payload, uint16_t len);  /* 发送，阻塞至完成/失败 */
uint16_t Wifi_RxLineGet(char *buf, uint16_t max_len);      /* 取一行接收数据，返回长度(不含NUL) */

#endif /* __WIFI_ESP8266_H */
