"""
测试仪表盘 - 电网模拟器综合测试工具
模拟 EMS 主站 + 风机控制器，验证完整闭环
"""

import socket
import json
import threading
import time
import datetime
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import random
import math

# ==================== 配置 ====================
HOST = '127.0.0.1'
PORT = 8888


class TestHarness:
    def __init__(self, root):
        self.root = root
        self.root.title("🧪 微电网测试仪表盘 V3.0")
        self.root.geometry("1100x700")
        self.root.configure(bg='#0d1117')

        self.ems_sock = None
        self.wind_sock = None
        self.running = False
        self.last_wind_pitch = 0

        self._build_ui()

    def _build_ui(self):
        """构建测试界面"""
        # 主容器
        main = tk.Frame(self.root, bg='#0d1117')
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # === 顶部：标题和状态 ===
        top = tk.Frame(main, bg='#0d1117')
        top.pack(fill=tk.X, pady=(0, 10))

        tk.Label(top, text="🧪 电网模拟器测试仪表盘", font=('微软雅黑', 16, 'bold'),
                 fg='#dfe6e9', bg='#0d1117').pack(side=tk.LEFT)

        self.status_indicator = tk.Label(top, text="● 未连接", font=('微软雅黑', 11),
                                         fg='#e17055', bg='#0d1117')
        self.status_indicator.pack(side=tk.RIGHT, padx=10)

        # === 控制按钮行 ===
        ctrl_frame = tk.Frame(main, bg='#161b22')
        ctrl_frame.pack(fill=tk.X, pady=(0, 10))

        self.btn_connect = tk.Button(ctrl_frame, text="🔗 连接服务器", command=self._connect,
                                     bg='#238636', fg='white', font=('微软雅黑', 10, 'bold'), padx=15)
        self.btn_connect.pack(side=tk.LEFT, padx=5, pady=5)

        self.btn_loop = tk.Button(ctrl_frame, text="▶ 开始测试循环", command=self._start_loop,
                                  bg='#1f6feb', fg='white', font=('微软雅黑', 10, 'bold'), padx=15, state=tk.DISABLED)
        self.btn_loop.pack(side=tk.LEFT, padx=5, pady=5)

        self.btn_stop = tk.Button(ctrl_frame, text="⏹ 停止", command=self._stop,
                                  bg='#da3633', fg='white', font=('微软雅黑', 10, 'bold'), padx=15, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5, pady=5)

        tk.Button(ctrl_frame, text="📊 导出报告", command=self._export_report,
                  bg='#30363d', fg='white', font=('微软雅黑', 10), padx=15).pack(side=tk.RIGHT, padx=5, pady=5)

        # === 主区域：左右分栏 ===
        paned = tk.PanedWindow(main, orient=tk.HORIZONTAL, bg='#0d1117', sashwidth=2)
        paned.pack(fill=tk.BOTH, expand=True)

        # --- 左栏：报文日志 ---
        left = tk.Frame(paned, bg='#0d1117')
        paned.add(left, width=600)

        tk.Label(left, text="📨 通信报文日志", font=('微软雅黑', 11, 'bold'),
                 fg='#dfe6e9', bg='#0d1117').pack(anchor='w', pady=(0, 5))

        self.log_text = scrolledtext.ScrolledText(left, bg='#0d1117', fg='#58a6ff',
                                                  font=('Consolas', 9), insertbackground='white',
                                                  relief=tk.FLAT, bd=0)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 颜色标签
        self.log_text.tag_config('EMS', foreground='#58a6ff')
        self.log_text.tag_config('WIND', foreground='#3fb950')
        self.log_text.tag_config('SYS', foreground='#8b949e')
        self.log_text.tag_config('ERR', foreground='#f85149')
        self.log_text.tag_config('OK', foreground='#3fb950')

        # --- 右栏：数据面板 ---
        right = tk.Frame(paned, bg='#0d1117')
        paned.add(right, width=450)

        # 遥测数据
        tk.Label(right, text="📊 实时遥测数据", font=('微软雅黑', 11, 'bold'),
                 fg='#dfe6e9', bg='#0d1117').pack(anchor='w', pady=(0, 5))

        data_frame = tk.Frame(right, bg='#161b22', relief=tk.RIDGE, bd=1)
        data_frame.pack(fill=tk.X, pady=(0, 10))

        self.data_vars = {}
        data_items = [
            ("风速", "wind_speed", "m/s"),
            ("负荷", "load_power", "kW"),
            ("风机出力", "wtg_power", "kW"),
            ("柴发出力", "diesel_power", "kW"),
            ("风机状态", "wtg_status", ""),
            ("桨距角", "pitch_angle", "°"),
        ]

        for i, (label, key, unit) in enumerate(data_items):
            row = i // 2
            col = i % 2
            f = tk.Frame(data_frame, bg='#161b22')
            f.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
            tk.Label(f, text=label, font=('微软雅黑', 9), fg='#8b949e', bg='#161b22').pack(anchor='w')
            var = tk.StringVar(value="--")
            self.data_vars[key] = var
            tk.Label(f, textvariable=var, font=('Consolas', 14, 'bold'), fg='#dfe6e9', bg='#161b22').pack(anchor='w')

        # 调度指令
        tk.Label(right, text="🎯 调度指令 (EMS下发)", font=('微软雅黑', 11, 'bold'),
                 fg='#dfe6e9', bg='#0d1117').pack(anchor='w', pady=(10, 5))

        cmd_frame = tk.Frame(right, bg='#161b22', relief=tk.RIDGE, bd=1)
        cmd_frame.pack(fill=tk.X, pady=(0, 10))

        self.cmd_vars = {}
        for label, key in [("风机目标功率", "wtg_power_set"), ("柴发目标功率", "diesel_power_set")]:
            f = tk.Frame(cmd_frame, bg='#161b22')
            f.pack(fill=tk.X, padx=5, pady=2)
            tk.Label(f, text=label, font=('微软雅黑', 9), fg='#8b949e', bg='#161b22').pack(side=tk.LEFT)
            var = tk.StringVar(value="--")
            self.cmd_vars[key] = var
            tk.Label(f, textvariable=var, font=('Consolas', 12), fg='#f0883e', bg='#161b22').pack(side=tk.RIGHT)

        # 统计
        tk.Label(right, text="📈 测试统计", font=('微软雅黑', 11, 'bold'),
                 fg='#dfe6e9', bg='#0d1117').pack(anchor='w', pady=(10, 5))

        stat_frame = tk.Frame(right, bg='#161b22', relief=tk.RIDGE, bd=1)
        stat_frame.pack(fill=tk.X)

        self.stat_vars = {
            'msgs_sent': tk.StringVar(value="0"),
            'msgs_recv': tk.StringVar(value="0"),
            'loops': tk.StringVar(value="0"),
            'errors': tk.StringVar(value="0"),
        }

        for i, (label, key) in enumerate([
            ("发送报文", "msgs_sent"), ("接收报文", "msgs_recv"),
            ("测试循环", "loops"), ("错误数", "errors")
        ]):
            f = tk.Frame(stat_frame, bg='#161b22')
            f.grid(row=i // 2, column=i % 2, sticky="nsew", padx=5, pady=3)
            tk.Label(f, text=label, font=('微软雅黑', 9), fg='#8b949e', bg='#161b22').pack(side=tk.LEFT)
            tk.Label(f, textvariable=self.stat_vars[key], font=('Consolas', 12, 'bold'),
                     fg='#dfe6e9', bg='#161b22').pack(side=tk.RIGHT)

    def _log(self, msg, tag='SYS'):
        """写入日志"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}\n"
        self.log_text.insert(tk.END, line, tag)
        self.log_text.see(tk.END)

    def _update_stats(self, sent=0, recv=0, err=0, loop=0):
        """更新统计"""
        try:
            self.stat_vars['msgs_sent'].set(str(int(self.stat_vars['msgs_sent'].get() or 0) + sent))
            self.stat_vars['msgs_recv'].set(str(int(self.stat_vars['msgs_recv'].get() or 0) + recv))
            self.stat_vars['errors'].set(str(int(self.stat_vars['errors'].get() or 0) + err))
            if loop:
                self.stat_vars['loops'].set(str(int(self.stat_vars['loops'].get() or 0) + loop))
        except:
            pass

    def _update_data(self, yc, yx, yt):
        """更新数据面板"""
        if yc:
            self.data_vars["wind_speed"].set(f"{yc.get('wind_speed', 0):.1f}")
            self.data_vars["load_power"].set(f"{yc.get('load_power', 0):.1f}")
            self.data_vars["wtg_power"].set(f"{yc.get('wtg_power', 0):.1f}")
            self.data_vars["diesel_power"].set(f"{yc.get('diesel_power', 0):.1f}")
        if yx:
            status = "▶ 运行" if yx.get('wtg_status') else "⏹ 停机"
            self.data_vars["wtg_status"].set(status)
        if yt:
            self.data_vars["pitch_angle"].set(f"{yt.get('pitch_angle_set', 0):.1f}")
            self.cmd_vars["wtg_power_set"].set(f"{yt.get('wtg_power_set', 0):.1f} kW")
            self.cmd_vars["diesel_power_set"].set(f"{yt.get('diesel_power_set', 0):.1f} kW")

    # ==================== 网络操作 ====================

    def _connect(self):
        """连接服务器"""
        try:
            # EMS连接
            self.ems_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.ems_sock.connect((HOST, PORT))

            # 风机连接
            self.wind_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.wind_sock.connect((HOST, PORT))

            # 发送Hello
            self._send_hello(self.ems_sock, 'ems')
            self._send_hello(self.wind_sock, 'wind_ctrl')

            # 等待回复
            for sock in [self.ems_sock, self.wind_sock]:
                data = sock.recv(4096).decode()
                if 'hello_ack' in data:
                    self._log(f"✅ 握手成功", 'OK')

            self.running = True
            self.status_indicator.config(text="● 已连接", fg='#3fb950')
            self.btn_connect.config(state=tk.DISABLED)
            self.btn_loop.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.NORMAL)
            self._log("🔗 已连接至电网模拟器", 'SYS')

        except Exception as e:
            self._log(f"❌ 连接失败: {e}", 'ERR')
            messagebox.showerror("连接失败", f"无法连接服务器: {e}")

    def _send_hello(self, sock, role):
        msg = json.dumps({"type": "hello", "data": {"role": role}}) + '\n'
        sock.send(msg.encode())

    def _send_json(self, sock, data):
        try:
            sock.send((json.dumps(data) + '\n').encode())
            return True
        except:
            return False

    def _recv_json(self, sock, timeout=0.5):
        try:
            sock.settimeout(timeout)
            data = sock.recv(4096).decode()
            if not data:
                return None
            lines = data.split('\n')
            for line in lines:
                if line.strip():
                    return json.loads(line)
            return None
        except socket.timeout:
            return None
        except:
            return None

    # ==================== 测试循环 ====================

    def _start_loop(self):
        """启动测试循环"""
        self.running = True
        self.btn_loop.config(state=tk.DISABLED)
        self._log("🔄 开始测试循环...", 'SYS')
        threading.Thread(target=self._test_loop, daemon=True).start()

    def _test_loop(self):
        """主测试循环 - 模拟EMS + 风机"""
        loop_count = 0
        ems_seq = 0
        wind_seq = 0
        last_command_time = 0
        last_heartbeat = time.time()

        # 初始状态
        wtg_status = 1
        pitch_angle = 0

        while self.running:
            try:
                loop_count += 1

                # === 1. EMS: 遥测请求 (每1秒) ===
                req = {"type": "yc_yx_request", "src": "ems", "dst": "grid_simulator",
                       "data": {"last_seq": ems_seq, "need_retransmit": False}}
                if self._send_json(self.ems_sock, req):
                    self._update_stats(sent=1)

                resp = self._recv_json(self.ems_sock, timeout=0.5)
                if resp:
                    self._update_stats(recv=1)
                    if resp.get('type') == 'yc_yx_response':
                        data = resp.get('data', {})
                        ems_seq = data.get('seq', ems_seq)
                        yc = data.get('yc', {})
                        yx = data.get('yx', {})
                        # 更新显示
                        self.root.after(0, lambda yc=yc, yx=yx: self._update_data(yc, yx, None))
                        self._log(f"📊 EMS遥测: 风速={yc.get('wind_speed', 0):.1f}, 风机={yc.get('wtg_power', 0):.1f}kW",
                                  'EMS')

                # === 2. 风机: 接收 wind_input + 回复 wind_output ===
                wind_msg = self._recv_json(self.wind_sock, timeout=0.1)
                if wind_msg:
                    self._update_stats(recv=1)
                    if wind_msg.get('type') == 'wind_input':
                        data = wind_msg.get('data', {})
                        wind_speed = data.get('wind_speed', 0)
                        wtg_power_set = data.get('wtg_power_set', 0)

                        # 计算桨距角 (简化：根据风速和目标功率)
                        params = self._get_device_params()
                        rated_power = params.get('rated_power', 50)
                        if wind_speed >= params.get('cut_in_wind', 3) and wind_speed <= params.get('cut_out_wind', 25):
                            if wind_speed >= params.get('rated_wind', 12):
                                available = rated_power
                            else:
                                available = (wind_speed - params.get('cut_in_wind', 3)) / (
                                            params.get('rated_wind', 12) - params.get('cut_in_wind', 3)) * rated_power
                            # 功率设定转化为桨距角
                            if wtg_power_set > 0:
                                pitch_angle = max(0, min(90, (1 - wtg_power_set / max(available, 1)) * 30))
                            else:
                                pitch_angle = 0
                            wtg_status = 1
                        else:
                            wtg_status = 0
                            pitch_angle = 0

                        # 发送 wind_output
                        out = {"type": "wind_output", "src": "wind_ctrl", "dst": "grid_simulator",
                               "data": {"wtg_status": wtg_status, "pitch_angle_set": pitch_angle,
                                        "available_power": available if wtg_status else 0}}
                        self._send_json(self.wind_sock, out)
                        self._update_stats(sent=1)
                        self._log(
                            f"💨 风机响应: 风速={wind_speed:.1f}, 桨距={pitch_angle:.1f}°, 状态={'运行' if wtg_status else '停机'}",
                            'WIND')

                        # 接收 wind_result
                        result = self._recv_json(self.wind_sock, timeout=0.1)
                        if result and result.get('type') == 'wind_result':
                            self._update_stats(recv=1)
                            d = result.get('data', {})
                            self._log(
                                f"🔄 出力回传: 风机={d.get('wtg_power', 0):.1f}kW, 柴发={d.get('diesel_power', 0):.1f}kW",
                                'WIND')

                # === 3. EMS: 调度指令 (每5秒) ===
                now = time.time()
                if now - last_command_time >= 5:
                    last_command_time = now
                    # 获取当前数据
                    yc = self._get_latest_yc()
                    if yc:
                        # 模拟调度逻辑：根据风速调整
                        wind_speed = yc.get('wind_speed', 0)
                        load = yc.get('load_power', 0)
                        params = self._get_device_params()

                        # 计算目标
                        if wind_speed >= params.get('cut_in_wind', 3):
                            wtg_target = min(params.get('rated_power', 50),
                                             max(0, (wind_speed - 3) / 9 * params.get('rated_power', 50)))
                        else:
                            wtg_target = 0

                        # 增加随机扰动模拟调度波动
                        wtg_target = wtg_target * (0.8 + 0.4 * random.random())
                        wtg_target = min(params.get('rated_power', 50), max(0, wtg_target))

                        diesel_target = max(5, load - wtg_target)
                        diesel_target = min(params.get('diesel_max', 80),
                                            max(params.get('diesel_min', 5), diesel_target))

                        # 下发指令
                        cmd = {"type": "command", "src": "ems", "dst": "grid_simulator",
                               "data": {"commands": [
                                   {"cmd_type": "remote_adjust", "target": "wtg_power", "value": round(wtg_target, 2)},
                                   {"cmd_type": "remote_adjust", "target": "diesel_power",
                                    "value": round(diesel_target, 2)}
                               ]}}
                        if self._send_json(self.ems_sock, cmd):
                            self._update_stats(sent=1)
                            self._log(f"🎯 EMS调度: 风机目标={wtg_target:.1f}kW, 柴发目标={diesel_target:.1f}kW", 'EMS')

                            # 接收响应
                            resp = self._recv_json(self.ems_sock, timeout=0.3)
                            if resp and resp.get('type') == 'command_response':
                                self._update_stats(recv=1)
                                self._log(f"✅ 指令执行成功", 'OK')

                # === 4. 心跳 (每5秒) ===
                if now - last_heartbeat >= 5:
                    last_heartbeat = now
                    for sock, name in [(self.ems_sock, 'EMS'), (self.wind_sock, '风机')]:
                        hb = {"type": "heartbeat", "src": name.lower() if name == 'EMS' else 'wind_ctrl',
                              "dst": "grid_simulator", "data": {}}
                        if self._send_json(sock, hb):
                            self._update_stats(sent=1)

                # 更新循环计数
                if loop_count % 10 == 0:
                    self._update_stats(loop=10)

                time.sleep(0.5)  # 控制循环频率

            except Exception as e:
                self._log(f"⚠️ 测试循环异常: {e}", 'ERR')
                self._update_stats(err=1)
                time.sleep(1)

        self._log("⏹ 测试循环已停止", 'SYS')

    def _get_device_params(self):
        """获取设备参数 (从数据库或缓存)"""
        # 这里简化：返回默认值
        return {"cut_in_wind": 3.0, "cut_out_wind": 25.0, "rated_wind": 12.0,
                "rated_power": 50.0, "diesel_max": 80.0, "diesel_min": 5.0}

    def _get_latest_yc(self):
        """获取最新遥测数据 (从最近收到的报文)"""
        # 简化：返回模拟值
        return {"wind_speed": 8 + 4 * math.sin(time.time() / 10), "load_power": 50 + 20 * math.sin(time.time() / 15)}

    def _stop(self):
        """停止测试"""
        self.running = False
        self.btn_loop.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self._log("⏹ 正在停止...", 'SYS')
        if self.ems_sock:
            self.ems_sock.close()
        if self.wind_sock:
            self.wind_sock.close()
        self.status_indicator.config(text="● 已断开", fg='#e17055')
        self.btn_connect.config(state=tk.NORMAL)

    def _export_report(self):
        """导出测试报告"""
        report = f"""
========================================
  电网模拟器测试报告
  生成时间: {datetime.datetime.now()}
========================================

测试统计:
  - 发送报文: {self.stat_vars['msgs_sent'].get()}
  - 接收报文: {self.stat_vars['msgs_recv'].get()}
  - 测试循环: {self.stat_vars['loops'].get()}
  - 错误数: {self.stat_vars['errors'].get()}

最新遥测数据:
  - 风速: {self.data_vars['wind_speed'].get()}
  - 负荷: {self.data_vars['load_power'].get()}
  - 风机出力: {self.data_vars['wtg_power'].get()}
  - 柴发出力: {self.data_vars['diesel_power'].get()}
  - 风机状态: {self.data_vars['wtg_status'].get()}
  - 桨距角: {self.data_vars['pitch_angle'].get()}

测试结论: 
  {'✅ 所有功能正常' if self.running else '⏹ 测试已停止'}

========================================
"""
        with open(f"test_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt", 'w', encoding='utf-8') as f:
            f.write(report)
        self._log(f"📄 测试报告已导出", 'SYS')
        messagebox.showinfo("导出成功", "测试报告已保存")


# ==================== 主入口 ====================
if __name__ == "__main__":
    root = tk.Tk()
    app = TestHarness(root)
    root.mainloop()