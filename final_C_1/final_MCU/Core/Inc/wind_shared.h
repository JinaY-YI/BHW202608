/**
  ******************************************************************************
  * @file    wind_shared.h
  * @brief   风机控制器全局寄存器：类型定义 + extern 声明（唯一定义点在 main.c）
  *          变量含义与单位见《南极考察站微电网-变量字典与通信协议-修订版V3》
  ******************************************************************************
  */
#ifndef __WIND_SHARED_H
#define __WIND_SHARED_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 风机参数（寄存器）：默认风电上位机为权威编辑点，经串口 CMD 0x01 修改 */
typedef struct {
    float cut_in_wind;    /* 切入风速 m/s   */
    float cut_out_wind;   /* 切出风速 m/s   */
    float rated_wind;     /* 额定风速 m/s   */
    float rated_power;    /* 额定功率 kW    */
} WindParam_t;

/* 实时数据（串口 CMD 0x02 上送内容 / TCP wind_output 内容） */
typedef struct {
    float wind_speed;          /* 来自电网模拟器 wind_input            */
    float wtg_power_set;       /* EMS 调度目标，电网模拟器转发          */
    float wtg_power;           /* 实际并网功率。闭环：电网模拟器 wind_result 回传；
                                  开环：模拟器不执行指令也不回传，由 wind_compute.c
                                  本地预估填入 est = available × max(0,1-pitch/90) */
    float pitch_angle_set;     /* 控制器计算（目标桨距角）              */
    float available_power;     /* 控制器计算（当前可用功率）            */
    uint8_t wtg_status;        /* 0=停机 1=运行（控制器决策，开环同样计算） */
    uint8_t ctrl_mode_wind;    /* 0=开环 1=闭环                        */
    uint8_t link_grid;         /* 1=控制器与电网模拟器 TCP 在线         */
    uint8_t reserved;          /* 串口 CMD 0x02 保留字节，本工程用作下发标志：
                                  bit0 = 1 本周期已下发闭环控制指令（闭环且在线）
                                         0 未下发（开环策略预览 / 链路离线） */
} RealtimeData_t;

/* 全局实例：在 main.c 中定义（非 static），供串口处理与应用层共享 */
extern WindParam_t   g_wind_params;
extern uint8_t       g_ctrl_mode;   /* 0=开环, 1=闭环 */
extern RealtimeData_t g_realtime;

#ifdef __cplusplus
}
#endif

#endif /* __WIND_SHARED_H */
