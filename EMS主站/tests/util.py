"""测试工具：在工作区内部创建临时数据库。

注意：本沙箱对 `tempfile.mkdtemp` 创建的目录不做写授权，故改用 `os.makedirs`
+ uuid 生成唯一目录，确保 sqlite3 能正常建库。
"""
import os
import shutil
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP_ROOT = os.path.join(PROJECT_ROOT, ".test_tmp")


def make_temp_db() -> str:
    """在工作区 .test_tmp 下创建唯一目录，返回 ems.db 完整路径。"""
    os.makedirs(_TMP_ROOT, exist_ok=True)
    d = os.path.join(_TMP_ROOT, "t_" + uuid.uuid4().hex)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "ems.db")


def cleanup_temp_dbs() -> None:
    """清空测试临时目录。"""
    if os.path.isdir(_TMP_ROOT):
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
