"""
电网模拟器 - 南极考察站微电网智能调控系统
学生A：主入口程序（拆分版）
"""

import sys
import os
import threading
import tkinter as tk
from tkinter import messagebox

from db_utils import GridDB
from compute_engine import ComputeEngine
from core.tcp_server import TCPServer
from gui.main_window import MainWindow


class GridSimulatorApp:
    """电网模拟器应用程序（主控类）"""

    def __init__(self, root):
        self.root = root
        self.root.title("❄️ 南极考察站微电网 - 电网模拟器 V3.0")
        self.root.geometry("1400x900")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.running = True

        # 初始化数据库
        self.db = GridDB()

        # 初始化计算引擎
        self.engine = ComputeEngine()

        # 日志队列（线程安全）
        self._log_queue = []
        self._log_lock = threading.Lock()
        self._log_interval = 100  # 毫秒

        # 初始化TCP服务器
        self.tcp_server = TCPServer(self.db, self.engine, log_callback=self._log)

        # 构建GUI
        self.gui = MainWindow(self.root, self.db, self.engine, self.tcp_server, log_callback=self._log)

        # 启动后台服务
        self._start_background()

        # 启动日志队列处理
        self.root.after(self._log_interval, self._process_log_queue)

    # ==================== 日志系统 ====================

    def _log(self, msg, level="INFO"):
        """日志记录（线程安全）"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌"}.get(level, "📌")
        line = f"[{timestamp}] {prefix} {msg}\n"
        with self._log_lock:
            self._log_queue.append((line, level, msg))
        self.root.after(0, self._process_log_queue)
        self.db.write_log(level, "GUI", msg)

    def _process_log_queue(self):
        """主线程消费日志队列"""
        with self._log_lock:
            if not self._log_queue:
                return
            lines = []
            for item in self._log_queue:
                lines.append(item[0])
            self._log_queue.clear()
        if hasattr(self.gui, 'log_view') and hasattr(self.gui.log_view, 'text'):
            for line in lines:
                self.gui.log_view.text.insert(tk.END, line)
                self.gui.log_view.text.see(tk.END)
        self.root.after(self._log_interval, self._process_log_queue)

    # ==================== 后台服务 ====================

    def _start_background(self):
        """启动所有后台服务"""
        # 强制初始状态为 STOPPED
        self.db.update_sim_status("STOPPED")

        # ===== 启动时清一次 scada_yc.sim_time = 0，避免遗留上次的值 =====
        yc = self.db.get_scada_yc()
        if yc:
            self.db.update_scada_yc(0, yc.get('wind_speed', 0), yc.get('load_power', 0),
                                    yc.get('wtg_power', 0), yc.get('diesel_power', 0))
        # ================================================================

        # 启动计算引擎（状态为 STOPPED，不会推进时间）
        self.engine.start()
        self._log("计算引擎已启动（待命，点击启动开始仿真）")

        # 启动TCP服务器
        threading.Thread(target=self.tcp_server.start, daemon=True).start()
        self._log("TCP Server 已启动")

        # 启动GUI定时刷新
        self.root.after(500, self._update_display)

    def _update_display(self):
        """定时刷新显示"""
        if not self.root.running:
            return

        try:
            # 更新GUI各组件
            if hasattr(self.gui, 'realtime_data'):
                self.gui.realtime_data.update()

            if hasattr(self.gui, 'comm_status'):
                ems_online = 'ems' in self.tcp_server.clients and self.tcp_server.clients['ems'].connected
                wind_online = 'wind_ctrl' in self.tcp_server.clients and self.tcp_server.clients['wind_ctrl'].connected
                self.gui.comm_status.set_status(ems_online, wind_online)

            if hasattr(self.gui, 'realtime_curve'):
                self.gui.realtime_curve.update()

            # SCADA每5秒刷新一次
            import time
            if int(time.time()) % 5 == 0 and hasattr(self.gui, 'scada_table'):
                self.gui.scada_table.refresh()

            # ===== 更新标题栏时间：直接读数据库，不再根据状态判断 =====
            # 停止 → scada_yc.sim_time 保留上次的值，自动显示
            # 重置 → control_panel._reset() 里已经 update_scada_yc(0, ...)，自动归零
            yc = self.db.get_scada_yc()
            if yc and hasattr(self.gui, 'time_label'):
                self.gui.time_label.config(text=f"⏱ t={yc.get('sim_time', 0)}s")

            # 更新状态标签
            sim = self.db.get_sim_param()
            if sim and hasattr(self.gui, 'status_label'):
                status = sim.get("sim_status", "STOPPED")
                if status == "RUNNING":
                    self.gui.status_label.config(text="● 仿真运行中", fg=self.gui.colors['success'])
                elif status == "PAUSED":
                    self.gui.status_label.config(text="● 已暂停", fg=self.gui.colors['warning'])
                else:
                    self.gui.status_label.config(text="● 系统就绪", fg=self.gui.colors['success'])

        except Exception:
            pass

        if self.root.running:
            self.root.after(500, self._update_display)

    # ==================== 关闭处理 ====================

    def on_close(self):
        """窗口关闭处理"""
        self.root.running = False
        self._log("正在关闭系统...")
        self.tcp_server.stop()
        self.engine.stop()
        self.db.close()
        self.root.destroy()


# ==================== 主入口 ====================

def check_database():
    """检查数据库是否存在"""
    if not os.path.exists("grid.db"):
        print("=" * 60)
        print("❌ 数据库不存在！")
        print("请先运行 create_db.py 创建数据库")
        print("=" * 60)
        return False
    return True


def main():
    """主函数"""
    if not check_database():
        input("\n按回车键退出...")
        sys.exit(1)

    root = tk.Tk()
    try:
        root.iconbitmap(default='icon.ico')
    except:
        pass

    app = GridSimulatorApp(root)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("\n用户中断，正在退出...")
        root.quit()


if __name__ == "__main__":
    main()