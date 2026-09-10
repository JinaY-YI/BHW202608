"""EMS 主站启动入口。

用法示例：
    python run_ems.py                        # 打开图形界面（默认内置仿真桩自启）
    python run_ems.py --headless --mock      # 无界面 + 内置仿真桩（控制台实时表格）
    python run_ems.py --host 192.168.1.10    # 图形界面，界面内切换为联调模式
    python run_ems.py --headless --host 192.168.1.10   # 无界面联调学生 A
"""
import sys

from ems.app import run

if __name__ == "__main__":
    sys.exit(run())
