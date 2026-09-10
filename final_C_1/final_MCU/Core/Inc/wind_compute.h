/**
  ******************************************************************************
  * @file    wind_compute.h
  * @brief   风机控制器 —— 控制计算层
  *          依据 V3 协议权责：available_power / pitch_angle_set / wtg_status
  *          三个量在本模块计算；wtg_power 由电网模拟器回传，本模块不计算。
  *
 *  输入（只读） ：当前风速、cut_in/cut_out/rated_wind/rated_power、EMS 目标、
 *                  wind_result 回传的实际功率 wtg_power（闭环反馈校正用）
 *  输出（只写） ：wtg_status / available_power / pitch_angle_set
 *                  + wtg_power（**仅开环**：本地预估出力，见下方说明）
 *  接口风格    ：纯函数 + 一步式 step 调用，外部可独立单测。
 *
 *  控制结构    ：前馈反解（线性模型精确基准）+ wind_result 实际功率的
 *                  积分反馈校正（消除模型失配 / 参数不同步 / 时滞造成的
 *                  稳态误差）+ 调节态 pitch 限速 + 抗积分饱和（稳健性）。
 *  详见 wind_compute.c 文件头说明。
  ******************************************************************************
  */
#ifndef __WIND_COMPUTE_H
#define __WIND_COMPUTE_H

#include "wind_shared.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 初始化内部状态（滞回计数器等）。可在 WindCtrl_Init 中调用一次。 */
void WindCompute_Init(void);

/* 主控步进：1s 周期调用
 *   in          输入寄存器（in 字段被视为只读）
 *   ctrl_mode   0=开环 1=闭环
 *   out         输出寄存器（写 wtg_status / available_power / pitch_angle_set；
 *               开环时另写 wtg_power = 本地预估出力，闭环时不写）
 *
 *  说明：
 *   - in->wtg_power、in->wind_speed_set、in->ctrl_mode_wind 仅作读引用，不改写
 *   - 切出保护：风速 >= v_out -> 强制顺桨 + 停机
 *   - 切入/切出滞回 3% 避免临界点抖动；闭环 EMS 让停阈值 0.5kW
 *   - 闭环 EMS 目标 < 物理上限 -> 前馈精确反解 pitch = 90°*(1-r/available)
 *     （模拟器 pitch_factor 为线性，见 wind_compute.c），并叠加 wind_result
 *     实际功率的积分反馈校正（消除两端参数不同步 / 回传延迟的稳态误差）
 *   - 闭环 EMS 目标 >= 物理上限 -> pitch=0 (最佳捕能，模拟器自然限额定)
 *   - 正常调节桨距限速 30°/s（切出/停机顺桨立即执行、不受限）
 *   - 开环：使用与闭环**相同**的启停策略与前馈桨距策略（结果经串口
 *     CMD 0x02 上报上位机），但不做积分反馈校正、不下发闭环控制指令；
 *     开环且 p_set≈0 时按最大风能捕获（pitch=0）形成策略。
 *   - 开环下 wtg_power 无实测来源（电网模拟器不回传），由本模块填入本地
 *     预估出力 est = available_power × max(0, 1 - pitch/90)，上位机据此显示；
 *     闭环下本模块不写该字段，保持 wind_result 实测值。
 */
void WindCompute_Step(const RealtimeData_t *in,
                      uint8_t ctrl_mode,
                      RealtimeData_t *out);

/* 纯函数：仅算可用功率（不写其他状态）。便于 GUI / 上位机查询用。
 *   风速 < cut_in              : 0
 *   cut_in <= v < cut_out      : 线性爬坡至额定（与电网模拟器对齐）
 *   v_rated <= v < cut_out     : 额定功率
 *   v >= cut_out               : 0  (切出保护)
 *   参数异常（v_rated<=v_in）  : 保守兜底 0 或额定
 *
 *   注：采用线性公式 P_rat*(v-v_in)/(v_rat-v_in)，与电网模拟器
 *       compute_engine._calc_wind_power 的 base 完全一致，保证闭环
 *       限功率时 available_power 可作为精确反解的基准。 */
float WindCompute_AvailablePower(float v, const WindParam_t *p);

#ifdef __cplusplus
}
#endif

#endif /* __WIND_COMPUTE_H */
