/**
  ******************************************************************************
  * @file    wind_compute.c
  * @brief   风机控制器 —— 控制计算层（V3 协议权责实现 + 闭环反馈校正）
  *
  *  设计原则
  *  ────────
 *  1. 严格按 V3 §1"计算权责"分配：
 *       - wtg_power  闭环：唯一来源是电网模拟器（wind_result 回传）→ 本模块
 *         不计算，但作为反馈量用于积分校正
 *         开环：模拟器不执行本模块指令、也不回传该值（其 wind_result 只在收到
 *         wind_output 后才应答），故由本模块按同一出力模型本地预估后填入
 *         （见 §6(c) 与 fill_est_power），供上位机显示
 *       - available_power 由本模块计算（理论最大可用功率）
 *       - pitch_angle_set 由本模块计算（控制量）
 *       - wtg_status      由本模块决策（启停）
  *
 *  2. 闭环限功率控制律：前馈反解 + 积分校正
 *     ─────────
 *     前馈：按电网模拟器真实模型(compute_engine._calc_wind_power)反解
 *         base        = rated_power*(v-cut_in)/(rated_wind-cut_in) (v<=rated_wind)
 *         base        = rated_power                                (v> rated_wind)
 *         pitch_factor= max(0, 1 - pitch/90)      ← 线性因子(已核对学生A源码)
 *         actual      = base * pitch_factor
 *     要让 actual == p_set，精确反解：
 *         pitch_ff = 90° * (1 - p_set / available_power)
 *       本控制器 available_power 用与 base 相同的线性公式，故前馈一步到顶。
 *
 *     反馈（防模型失配/参数不同步/时滞的兜底）：模拟器回传 wtg_power 与
 *     EMS 目标求偏差做积分校正：
 *         s_I += KI * (wtg_power - p_set)     （每 1s 一拍）
 *         pitch = clamp(pitch_ff + s_I, 0, 90)
 *     方向说明：实际偏低 -> s_I 变负 -> 桨距角减小 -> 出力增大
 *     （桨距角增大是"减出力"方向，积分符号勿反，反了会发散到顺桨）
 *
 *     典型失配场景（本模块加入积分的目的）：
 *       - 控制器与模拟器额定功率/风速不同步（曾出现 MCU 默认 rated_power=100
 *         而模拟器=50，纯前馈下 set=50 -> pitch=45° -> 模拟器只出 25kW，
 *         永久差 25kW 无法自愈）——积分项会把实际功率拉回设定值
 *       - wtg_power 回传 1~3 拍滞后
 *
 *     注意：available_power 依赖控制器本地 g_wind_params。若该参数与模拟器
 *     不一致，前馈基准就偏，显示值 available_power 也会有偏差——故上线前
 *     必须保证两端额定参数一致（见 main.c g_wind_params 默认值注释）。
 *
 *  3. 稳健性措施
 *     ─────────
 *     - 抗积分饱和：输出限幅后 back-calculation 退饱和，|s_I| ≤ 45°
  *     - 积分门控1：仅闭环、TCP 在线(link_grid=1)、运行态、且 p_set < available
  *       的调节区内积分；启停/切出/开环时积分清零
  *     - 积分门控2：启停/满发-限功区域切换后 2 拍内不积分（wtg_power 回传
  *       尚未反映切换后的执行结果，防止用陈旧反馈预饱和）
  *     - 调节态桨距限速 30°/s（切出/停机顺桨立即执行，不受限）
  *     - wtg_power 回传天然滞后 1~3 拍，KI=0.1 时纯积分对该延迟单调收敛
  *
  *  4. 启停决策（切入/切出 + 滞回）
  *     ──────────
  *     [停机] -- v >= v_in 且 EMS 不让停 --> [运行]
  *     [运行] -- v <  v_in*0.97          --> [停机]   （3% 滞回）
  *     [运行] -- v >= v_out              --> [强制停机] 立即顺桨
  *     [运行] -- EMS 让停 (P_set<0.5kW)  --> [停机]   （仅闭环）
  *
 *  5. 切出保护优先级最高：任何情况下 v ≥ v_out 强制 status=0 / pitch=90° /
 *     available=0
 *
 *  6. 开环 / 闭环的差别（本模块只负责"算"，"发不发"由应用层决定）
 *     ────────────────────────────────────────────────
 *     - 开环(ctrl_mode=0) 与闭环(ctrl_mode=1) 使用**同一套**启停策略与桨距角
 *       前馈策略，因此开环时上位机同样能看到完整的控制策略结果
 *       （wtg_status / pitch_angle_set / available_power，经串口 CMD 0x02 上报）。
 *     - 差异共三点：
 *         (a) 开环不做积分反馈校正：模拟器不执行开环指令，wtg_power 不随
 *             桨距角变化，反馈无意义且会导致积分饱和/发散；
 *         (b) 开环不下发 TCP wind_output（见 wind_crtl_app.c），
 *             即"形成策略但不输出可执行的闭环控制命令"；
 *         (c) 开环下 wtg_power 字段由本模块填入**本地预估值**
 *             est = available_power × max(0, 1 - pitch/90)，即"若执行本策略"
 *             的出力；闭环下该字段保持电网模拟器实测值，本模块不覆盖。
 *     - 开环且 p_set≈0（EMS 未下发/归零）时，按最大风能捕获（pitch=0）形成
 *       策略；闭环下 p_set≈0 视为"EMS 让停"，走停机顺桨分支。
 *     - 启停：EMS 让停仅在闭环下生效；开环的启停只由风速 + 切入/切出滞回决定。
  ******************************************************************************
 */
#include "wind_compute.h"
#include <stddef.h>

/* ============== 内部参数 ============== */

/* 切入/切出滞回比例（3% 滞回带，避免临界点反复启停） */
#define WIND_HYST_RATIO     0.03f

/* 停机判定阈值：EMS 设定 < 此值视为"让停"（仅闭环下生效） */
#define WIND_EMS_STOP_THR   0.5f

/* 积分校正增益 °/(kW·拍)：KI*误差 每拍累加。符号约定：实际偏低(p_fb<p_set)
 * 时积分变负 -> 减小桨距角 -> 出力增大（桨距增大是减出力方向！）。
 * 0.1 对 1~3 拍回传延迟单调收敛无振荡（仿真验证 0.2 在 3 拍延迟下会振
 * 荡；现场可调 0.05~0.2） */
#define WIND_KI             0.10f

/* 积分校正限幅 °（限定前馈失配的最大补偿量，防失控） */
#define WIND_INTEG_LIM      45.0f

/* 调节态桨距变化限速 °/s（1s 一拍；切出/停机顺桨不受此限） */
#define WIND_PITCH_SLEW     30.0f

/* 区域切换后的稳定闸门（拍数）：启停/满发-限功切换后的前几拍，
 * wtg_power 回传的还是切换前的陈旧值，此期间不积分，防止积分预饱和 */
#define WIND_SETTLE_TICKS   2u

/* ============== 模块状态 ============== */

static uint8_t s_run_state = 0;   /* 0=停机 1=运行（带滞回的状态机） */
static float   s_integ      = 0.0f;   /* 积分校正量 ° */
static float   s_pitch_prev = 90.0f;  /* 上一拍输出桨距角 °（限速用） */
static uint8_t s_settle     = 0;      /* 稳定闸门计数（>0 期间不积分） */

/* ============== 内部辅助 ============== */

/* 钳位到 [lo, hi] */
static inline float clampf(float v, float lo, float hi)
{
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/* ---------- 开环本地预估出力 ----------
   开环下电网模拟器不执行本控制器的指令，其 wind_result 也只在收到 wind_output
   后才应答，因此 wtg_power 在开环下没有实测来源。这里按与电网模拟器
   compute_engine._calc_wind_power 完全一致的模型，本地推算"若执行本策略"
   的出力，并填入 wtg_power 字段供上位机显示：

       est = available_power × max(0, 1 - pitch/90)

   停机 / 顺桨 / 无可用功率时为 0，上限截额定功率。
   闭环下**不写入**——该字段留给 wind_result 回传的实测值（数据源唯一）。 */
static inline void fill_est_power(RealtimeData_t *out, uint8_t ctrl_mode,
                                  float p_avail, float pitch, uint8_t run_state)
{
    if (ctrl_mode != 0u) return;          /* 闭环：不覆盖模拟器实测值 */

    float p = 0.0f;
    if (run_state != 0u && p_avail > 0.0f) {
        float f = 1.0f - pitch / 90.0f;   /* 与模拟器一致的线性桨距因子 */
        if (f < 0.0f) f = 0.0f;
        p = p_avail * f;
        if (p > g_wind_params.rated_power) p = g_wind_params.rated_power;
    }
    out->wtg_power = p;
}

/* ============== 公共接口 ============== */

void WindCompute_Init(void)
{
    s_run_state  = 0;
    s_integ      = 0.0f;
    s_pitch_prev = 90.0f;
    s_settle     = WIND_SETTLE_TICKS;
}

/* ---------- 纯函数：可用功率 ---------- */
float WindCompute_AvailablePower(float v, const WindParam_t *p)
{
    if (p == NULL) return 0.0f;

    const float v_in  = p->cut_in_wind;
    const float v_out = p->cut_out_wind;
    const float v_rat = p->rated_wind;
    const float p_rat = p->rated_power;

    /* 异常参数兜底（防止分母 ≤ 0 / 颠倒） */
    if (v_in < 0.0f || v_out <= v_in || v_rat < 0.0f || p_rat < 0.0f) {
        return 0.0f;
    }

    if (v < v_in)               return 0.0f;
    if (v >= v_out)             return 0.0f;
    if (v >= v_rat)             return p_rat;

    /* v_in ≤ v < v_rat：线性爬坡（与电网模拟器 compute_engine base 对齐）
       P(v) = P_rat * (v - v_in) / (v_rat - v_in)
       若后续与学生A确认模拟器 base 为立方律，此处需同步修改；
       即便不一致，闭环积分校正也能消除该基准差，只是 available_power
       作为"理论可用功率"的显示值会有偏差。 */
    if (v_rat > v_in) {
        float k = (v - v_in) / (v_rat - v_in);
        return p_rat * k;
    }

    /* v_in == v_rat 这种异常参数时，按 0/额定二元处理（风速达到 v_in 即出额定） */
    return p_rat;
}

/* ---------- 主控步进 ---------- */
void WindCompute_Step(const RealtimeData_t *in,
                      uint8_t ctrl_mode,
                      RealtimeData_t *out)
{
    if (in == NULL || out == NULL) return;

    /* ---- 先读全部输入（in 与 out 是同一寄存器，避免读回已改写值）---- */
    const float   v     = in->wind_speed;
    const float   p_set = in->wtg_power_set;   /* EMS 目标 */
    const float   p_fb  = in->wtg_power;       /* 模拟器回传实际功率（反馈） */
    const uint8_t link  = in->link_grid;       /* TCP 在线状态 */
    const float   v_in  = g_wind_params.cut_in_wind;
    const float   v_out = g_wind_params.cut_out_wind;

    /* ==== 1) 切出保护（最高优先级，无滞回，立即顺桨不限速）==== */
    if (v >= v_out) {
        s_run_state  = 0;
        s_integ      = 0.0f;
        s_pitch_prev = 90.0f;
        out->wtg_status      = 0;
        out->available_power = 0.0f;
        out->pitch_angle_set = 90.0f;
        fill_est_power(out, ctrl_mode, 0.0f, 90.0f, 0u);  /* 开环预估出力 = 0 */
        return;
    }

    /* ==== 2) 切入/切出滞回启停决策 ==== */
    const bool cmd_stop = (ctrl_mode == 1u) && (p_set < WIND_EMS_STOP_THR);

    if (s_run_state == 0) {
        /* 停机 -> 运行：风速 ≥ v_in 且 EMS 不让停 */
        if (v >= v_in && !cmd_stop) {
            s_run_state = 1;
        }
    } else {
        /* 运行 -> 停机：风速 < v_in*(1-滞回) 或 EMS 让停 */
        const float v_in_hyst = v_in * (1.0f - WIND_HYST_RATIO);
        if (v < v_in_hyst || cmd_stop) {
            s_run_state = 0;
        }
    }

    out->wtg_status = s_run_state;

    /* ==== 3) 可用功率 ==== */
    const float p_avail = WindCompute_AvailablePower(v, &g_wind_params);
    out->available_power = p_avail;

    /* ==== 4) 目标桨距角 ==== */
    /* 【开环/闭环统一策略】
       开环与闭环采用同一套"前馈反解 + 调节态限速"的桨距角策略，
       区别共三点：
         (a) 开环无反馈通路——电网模拟器不执行本控制器的指令，wtg_power 不会
             随桨距角变化，若继续做积分校正必然饱和/发散，故开环关闭积分项；
         (b) 开环不下发 TCP wind_output（见 wind_ctrl_app.c），
             策略结果仅经串口 CMD 0x02 上报上位机；
         (c) 开环下 wtg_power 由 fill_est_power 填入本地预估出力。
       这样开环时上位机同样能看到完整的启停策略、桨距角策略与预估出力。 */
    const bool closed_loop = (ctrl_mode == 1u);
    if (!closed_loop) {
        s_integ = 0.0f;      /* 开环：无反馈通路，积分清零 */
    }

    if (s_run_state == 0u) {
        /* 停机：顺桨（立即，不限速） */
        s_integ      = 0.0f;
        s_pitch_prev = 90.0f;
        s_settle     = WIND_SETTLE_TICKS;
        out->pitch_angle_set = 90.0f;
        fill_est_power(out, ctrl_mode, p_avail, 90.0f, 0u);  /* 停机 -> 预估 0 */
        return;
    }

    if (p_avail <= 0.0f) {
        /* 风在切入/切出边缘，available 已为 0；保持顺桨 */
        s_integ      = 0.0f;
        s_pitch_prev = 90.0f;
        s_settle     = WIND_SETTLE_TICKS;
        out->pitch_angle_set = 90.0f;
        fill_est_power(out, ctrl_mode, 0.0f, 90.0f, 1u);  /* 无可用功率 -> 预估 0 */
        return;
    }

    /* ---- 调节区（开环/闭环共用同一前馈策略）---- */

    /* 开环且无有效设定值（p_set≈0，即 EMS 尚未下发或已归零）：
       开环不跟踪调度指令，此时按"最大风能捕获"形成策略（pitch=0），
       而不是误按"限功率到 0"顺桨；闭环下 p_set≈0 属"EMS 让停"，已在
       上面启停逻辑中处理为停机顺桨。 */
    const bool open_no_set = (!closed_loop && p_set <= WIND_EMS_STOP_THR);

    /* (a) 前馈反解：模拟器 pitch_factor 为线性 (1-pitch/90)，精确反解
     *        pitch_ff = 90°*(1 - p_set/available)   （见文件头 §2） */
    float pitch_ff;
    if (open_no_set || p_set >= p_avail) {
        /* 目标 ≥ 当前可用能力（或开环无设定）：保持最优 Cp（pitch=0），积分清零 */
        pitch_ff = 0.0f;
        s_integ  = 0.0f;
        s_settle = WIND_SETTLE_TICKS;
    } else {
        const float k = p_set / p_avail;   /* 0 < k < 1 */
        pitch_ff = 90.0f * (1.0f - k);     /* 线性律精确反解 */
        /* (b) 积分校正：消除前馈模型失配 + 1~2 拍回传延迟的稳态误差
         *   方向：实际偏低(p_fb<p_set) -> 积分变负 -> 减小桨距 -> 出力增大
         *   门控0：仅闭环积分——开环下模拟器不执行本控制器指令，
         *          p_fb 不随桨距变化，积分无意义且会饱和/发散
         *   门控1：仅 TCP 在线时积分（离线时 wtg_power 是陈旧值）
         *   门控2：区域切换后 WIND_SETTLE_TICKS 拍内不积分（回传尚未反映
         *          本区域执行结果，防止用陈旧反馈预饱和）
         *   注：p_fb 对应上一拍桨距的执行结果，纯积分对该延迟天然稳定 */
        if (closed_loop && link == 1u && s_settle == 0u) {
            s_integ += WIND_KI * (p_fb - p_set);
            if (s_integ >  WIND_INTEG_LIM) s_integ =  WIND_INTEG_LIM;
            if (s_integ < -WIND_INTEG_LIM) s_integ = -WIND_INTEG_LIM;
        }
    }

    /* (c) 合成 + 限幅 + 抗积分饱和（back-calculation 退饱和） */
    const float u_raw = pitch_ff + s_integ;
    float pitch = clampf(u_raw, 0.0f, 90.0f);
    if (pitch != u_raw) {
        s_integ = pitch - pitch_ff;
    }

    /* (d) 调节态桨距限速：平滑指令，防功率冲击（停机/切出顺桨分支不受限，
           开环与闭环同样限速，保证"策略信号"与闭环执行指令形态一致） */
    if (pitch > s_pitch_prev + WIND_PITCH_SLEW) pitch = s_pitch_prev + WIND_PITCH_SLEW;
    if (pitch < s_pitch_prev - WIND_PITCH_SLEW) pitch = s_pitch_prev - WIND_PITCH_SLEW;
    s_pitch_prev = pitch;

    out->pitch_angle_set = pitch;

    /* 开环：上报"若执行本策略"的预估出力（闭环由 wind_result 实测值占据该字段） */
    fill_est_power(out, ctrl_mode, p_avail, pitch, s_run_state);
}
