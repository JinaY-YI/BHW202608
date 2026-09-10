/**
  ******************************************************************************
  * @file    wind_ctrl_app.c
  * @brief   风机控制器应用层（协议 V3）
  *
  *  链路：上位机<--串口USART2(已在main.c)----控制器----WiFi(USART3)-->电网模拟器
  *
  *  本文件职责（与《协议V3》一一对应）：
  *   - TCP Client 应用报文：hello / heartbeat / wind_output / param_sync
  *   - 接收解析：hello_ack / heartbeat / wind_input / wind_result /
  *               param_push / param_ack
 *   - 1s 周期：控制计算（开环/闭环都执行）→ 闭环时回发 wind_output。
 *     闭环下 wtg_power 取电网模拟器 wind_result 回传值转发给上位机（数据源唯一）；
 *     开环下模拟器不回传该值，改由 wind_compute.c 填入本地预估出力（见 §6(c)）。
 *   - 开环(ctrl_mode=0)：同样形成启停与桨距角策略，但**不下发** wind_output，
 *     即不输出可执行的闭环控制命令；策略结果（含预估出力）仅经串口 CMD 0x02
 *     上报上位机，并在 reserved 字节 bit0 标记"本周期未下发执行指令"。
  *   - 控制计算实现在 wind_compute.c（WindCompute_Step）。
  ******************************************************************************
  */
#include "wind_ctrl_app.h"
#include "wind_shared.h"
#include "wind_compute.h"
#include "wifi_esp8266.h"
#include "main.h"
#include <string.h>
#include <stdio.h>

/* ---------------- 本地变量 ---------------- */
static uint32_t s_t1s = 0;        /* 1s 控制/上报周期 */
static uint32_t s_t5s = 0;        /* 5s 心跳周期     */
static bool     s_prev_up = false;/* 上一次链路状态  */
static bool     s_param_pending = false; /* 上位机改参数后待同步 TCP */

static char     s_json[384];      /* 发送报文缓冲区 */

/* ---------------- 内部函数 ---------------- */
static void ftoa2(float v, char *out);
static bool json_get_type(const char *s, char *out, uint16_t maxlen);
static bool json_get_float(const char *s, const char *key, float *out);
static bool tcp_send_msg(const char *json);

static void SendHello(void);
static void SendHeartbeat(void);
static void SendWindOutput(void);
static void SendParamSync(const char *editor);
static void HandleTcpLine(char *line);
static void WindCtrl_Compute1s(void);

/* ================= 初始化 ================= */
void WindCtrl_Init(void)
{
    Wifi_Init();
    WindCompute_Init();

    g_realtime.ctrl_mode_wind = g_ctrl_mode;
    g_realtime.link_grid      = 0;

    s_t1s = HAL_GetTick();
    s_t5s = HAL_GetTick();
    s_prev_up = false;
    s_param_pending = false;
}

/* ================= 周期任务 ================= */
void WindCtrl_Periodic(void)
{
    char  line[512];
    uint16_t l;

    /* 1) WiFi/AT 状态机（含断线重连、接收解析） */
    Wifi_Periodic();

    /* 2) 取完所有收到的 TCP 行并处理 */
    while ((l = Wifi_RxLineGet(line, sizeof(line))) > 0) {
        line[l] = 0;
        HandleTcpLine(line);
    }

    /* 3) 链路状态反映到寄存器（供串口 CMD 0x02 上报） */
    bool up = Wifi_IsUp();
    g_realtime.link_grid      = up ? 1u : 0u;
    g_realtime.ctrl_mode_wind = g_ctrl_mode;

    /* 4) 刚连上：发 hello；若上位机参数还未同步成功则补发 */
    if (up && !s_prev_up) {
        s_prev_up = true;
        SendHello();
    } else if (!up) {
        s_prev_up = false;
    }

    uint32_t now = HAL_GetTick();

    /* 5) 心跳：5s 周期；对方心跳也即时回（在 HandleTcpLine 内） */
    if (up && (now - s_t5s) >= 5000u) {
        s_t5s = now;
        SendHeartbeat();
    }

    /* 6) 1s 周期：控制计算（开环/闭环都算）+ 闭环回发 + 参数补同步 */
    if ((now - s_t1s) >= 1000u) {
        s_t1s = now;

        /* 控制计算：开环同样形成启停策略与桨距角策略，结果写入 g_realtime，
           由串口 CMD 0x02 每秒上报上位机（含 wtg_status / pitch_angle_set /
           available_power），实现"开环只上报状态与结果、不下发执行指令" */
        WindCtrl_Compute1s();

        /* 闭环指令下发：仅闭环且 TCP 在线时发送 wind_output。
           开环时只形成策略、不输出可执行的闭环控制命令。 */
        bool cmd_issued = false;
        if (up) {
            if (g_ctrl_mode == 1u) {            /* 闭环：必须发送 wind_output */
                SendWindOutput();
                cmd_issued = true;
            }
            if (s_param_pending) {              /* 参数未同步成功则重试 */
                SendParamSync("upper_pc");
            }
        }

        /* 串口 CMD 0x02 的 reserved 字节用作下发标志：
           bit0 = 1 本周期已下发闭环控制指令（闭环且链路在线）
                  0 未下发（开环策略预览 / 链路离线），供上位机区分显示 */
        g_realtime.reserved = cmd_issued ? 0x01u : 0x00u;
    }
}

/* ================= 串口事件 ================= */
/* 上位机经串口 CMD 0x01 修改风机参数 -> 下一个周期内经 TCP 同步给电网模拟器 */
void WindCtrl_OnSerialParamChanged(void)
{
    s_param_pending = true;
    /* 若已在线，立即补一次发送（仍在“下一个1s周期内”的窗口） */
    if (Wifi_IsUp()) {
        SendParamSync("upper_pc");
    }
}

void WindCtrl_OnSerialModeChanged(void)
{
    /* 模式只影响 wind_output 是否发送与计算分支，无需额外动作 */
}

/* ================= TCP 上行报文 ================= */

static void SendHello(void)
{
    uint32_t now = HAL_GetTick();
    snprintf(s_json, sizeof(s_json),
             "{\"msg_id\":%lu,\"timestamp\":%lu,\"src\":\"wind_ctrl\","
             "\"dst\":\"grid_simulator\",\"type\":\"hello\","
             "\"data\":{\"role\":\"wind_ctrl\",\"reset_seq\":true}}",
             (unsigned long)now, (unsigned long)(now / 1000));
    tcp_send_msg(s_json);
}

static void SendHeartbeat(void)
{
    uint32_t now = HAL_GetTick();
    snprintf(s_json, sizeof(s_json),
             "{\"msg_id\":%lu,\"timestamp\":%lu,\"src\":\"wind_ctrl\","
             "\"dst\":\"grid_simulator\",\"type\":\"heartbeat\",\"data\":{}}",
             (unsigned long)now, (unsigned long)(now / 1000));
    tcp_send_msg(s_json);
}

/* 闭环模式下每 1s：启停状态(wtg_status) + 目标桨距角 -> 电网模拟器执行 */
static void SendWindOutput(void)
{
    char p1[16], p2[16];
    ftoa2(g_realtime.pitch_angle_set, p1);
    ftoa2(g_realtime.available_power, p2);

    uint32_t now = HAL_GetTick();
    snprintf(s_json, sizeof(s_json),
             "{\"msg_id\":%lu,\"timestamp\":%lu,\"src\":\"wind_ctrl\","
             "\"dst\":\"grid_simulator\",\"type\":\"wind_output\","
             "\"data\":{\"wtg_status\":%u,\"pitch_angle_set\":%s,"
             "\"available_power\":%s}}",
             (unsigned long)now, (unsigned long)(now / 1000),
             (unsigned)g_realtime.wtg_status, p1, p2);
    tcp_send_msg(s_json);
}

/* 上位机改参数 -> 同步电网模拟器；电网模拟器 HMI 下发后原样回传确认 */
static void SendParamSync(const char *editor)
{
    char c1[16], c2[16], c3[16], c4[16];
    ftoa2(g_wind_params.cut_in_wind,  c1);
    ftoa2(g_wind_params.cut_out_wind, c2);
    ftoa2(g_wind_params.rated_wind,   c3);
    ftoa2(g_wind_params.rated_power,  c4);

    uint32_t now = HAL_GetTick();
    snprintf(s_json, sizeof(s_json),
             "{\"msg_id\":%lu,\"timestamp\":%lu,\"src\":\"wind_ctrl\","
             "\"dst\":\"grid_simulator\",\"type\":\"param_sync\","
             "\"data\":{\"cut_in_wind\":%s,\"cut_out_wind\":%s,"
             "\"rated_wind\":%s,\"rated_power\":%s,\"editor\":\"%s\"}}",
             (unsigned long)now, (unsigned long)(now / 1000),
             c1, c2, c3, c4, editor);
    if (tcp_send_msg(s_json) == true) {
        s_param_pending = false;   /* 发送成功即视为已同步（不依赖 ack 亦可） */
    }
}

/* ================= TCP 下行报文处理 ================= */

static void HandleTcpLine(char *line)
{
    char type[24];
    if (!json_get_type(line, type, sizeof(type))) return;

    if (strcmp(type, "heartbeat") == 0) {
        /* 对端心跳：立即回一个心跳 */
        SendHeartbeat();
    }
    else if (strcmp(type, "hello_ack") == 0) {
        /* 服务器确认 hello；链路已在线，无需动作 */
    }
    else if (strcmp(type, "wind_input") == 0) {
        /* 电网模拟器下发本周期输入：风速 + EMS 有功设定 */
        float f;
        if (json_get_float(line, "wind_speed", &f))    g_realtime.wind_speed = f;
        if (json_get_float(line, "wtg_power_set", &f)) g_realtime.wtg_power_set = f;
        /* 容错：若服务器将 wind_result 字段合并在 wind_input 中 */
        if (json_get_float(line, "wtg_power", &f))     g_realtime.wtg_power = f;
    }
    else if (strcmp(type, "wind_result") == 0) {
        /* 闭环下 wtg_power 的唯一来源；开环下模拟器不发送该报文，
           该字段由 wind_compute.c 填入本地预估出力，转发给上位机显示 */
        float f;
        if (json_get_float(line, "wtg_power", &f))     g_realtime.wtg_power = f;
    }
    else if (strcmp(type, "param_push") == 0) {
        /* 电网模拟器 HMI 修改风机参数：写入寄存器并按协议原样回传 param_sync */
        float f;
        if (json_get_float(line, "cut_in_wind",  &f)) g_wind_params.cut_in_wind  = f;
        if (json_get_float(line, "cut_out_wind", &f)) g_wind_params.cut_out_wind = f;
        if (json_get_float(line, "rated_wind",   &f)) g_wind_params.rated_wind   = f;
        if (json_get_float(line, "rated_power",  &f)) g_wind_params.rated_power  = f;
        SendParamSync("hmi");
    }
    else if (strcmp(type, "param_ack") == 0) {
        /* 电网模拟器确认参数已写入 grid.db */
        s_param_pending = false;
    }
    else {
        /* 其它类型（command_response 等属 EMS 通道）忽略 */
    }
}

/* ================= 控制计算（1s，开环/闭环均执行） =================
   实际控制律/启停/变桨逻辑在 wind_compute.c 的 WindCompute_Step 中实现。
   开环与闭环使用同一套策略，差别仅为：开环不做积分反馈校正、不下发执行指令。
   这里保留薄包装，使调用方（WindCtrl_Periodic）的代码无需改动。 */
static void WindCtrl_Compute1s(void)
{
    WindCompute_Step(&g_realtime, g_ctrl_mode, &g_realtime);
}

/* ================= 工具函数 ================= */

/* 发送报文并自动追加 '\n'（协议要求每条报文以换行结尾） */
static bool tcp_send_msg(const char *json)
{
    uint16_t l = (uint16_t)strlen(json);
    if (l > 510) return false;
    char b[520];
    memcpy(b, json, l);
    b[l] = '\n';
    b[l + 1] = 0;
    return Wifi_TcpSend(b, (uint16_t)(l + 1));
}

/* 浮点转字符串（两位小数，不依赖 printf 浮点支持） */
static void ftoa2(float v, char *out)
{
    char tmp[24];
    bool neg = false;
    if (v < 0.0f) { neg = true; v = -v; }
    if (v > 999999.0f) v = 999999.0f;

    long scaled = (long)(v * 100.0f + 0.5f);
    long ip = scaled / 100;
    long fp = scaled % 100;
    sprintf(tmp, "%ld.%02ld", ip, fp);
    if (neg) {
        out[0] = '-';
        strcpy(&out[1], tmp);
    } else {
        strcpy(out, tmp);
    }
}

/* 从 JSON 中取 "type":"xxx" */
static bool json_get_type(const char *s, char *out, uint16_t maxlen)
{
    const char *p = strstr(s, "\"type\"");
    if (!p) return false;
    p = strchr(p + 6, ':');
    if (!p) return false;
    p = strchr(p, '"');
    if (!p) return false;
    p++;
    uint16_t i = 0;
    while (*p && *p != '"' && i < maxlen - 1) out[i++] = *p++;
    out[i] = 0;
    return (i > 0);
}

/* 从 JSON 中取数值键（键名精确匹配，如 "wtg_power" 不会误匹配 "wtg_power_set"） */
static bool json_get_float(const char *s, const char *key, float *out)
{
    char k[40];
    snprintf(k, sizeof(k), "\"%s\"", key);
    const char *p = strstr(s, k);
    if (!p) return false;
    p = strchr(p, ':');
    if (!p) return false;
    p++;
    while (*p == ' ' || *p == '\t') p++;

    float sign = 1.0f;
    if (*p == '-') { sign = -1.0f; p++; }

    float val = 0.0f;
    bool any = false;
    while (*p >= '0' && *p <= '9') { val = val * 10.0f + (float)(*p - '0'); p++; any = true; }
    if (*p == '.') {
        p++;
        float dec = 0.1f;
        while (*p >= '0' && *p <= '9') { val += (float)(*p - '0') * dec; dec *= 0.1f; p++; any = true; }
    }
    /* 极小概率出现科学计数法：简单跳过尾数处理 */
    if (!any) return false;
    *out = val * sign;
    return true;
}
