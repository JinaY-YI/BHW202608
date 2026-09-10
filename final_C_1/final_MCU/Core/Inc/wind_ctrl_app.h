/**
  ******************************************************************************
  * @file    wind_ctrl_app.h
  * @brief   风机控制器应用层接口（协议 V3 行为 + 占位控制计算）
  ******************************************************************************
  */
#ifndef __WIND_CTRL_APP_H
#define __WIND_CTRL_APP_H

void WindCtrl_Init(void);            /* 主循环前调用一次（内部启动 WiFi） */
void WindCtrl_Periodic(void);        /* 主循环每圈调用：WiFi周期任务+收发+1s控制计算 */

/* 串口 CMD 0x01 改参数成功后调用：触发 param_sync 同步给电网模拟器 */
void WindCtrl_OnSerialParamChanged(void);
/* 串口 CMD 0x03 改控制模式成功后调用 */
void WindCtrl_OnSerialModeChanged(void);

#endif /* __WIND_CTRL_APP_H */
