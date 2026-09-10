"""运行全部 EMS 单元测试与集成测试。

用法：
    python run_tests.py            # 运行全部测试
    python run_tests.py -v         # 详细输出
"""
import sys
import unittest


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")
    verbosity = 2 if "-v" in sys.argv or "--verbose" in sys.argv else 1
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=verbosity)
    result = runner.run(suite)
    # 清理测试临时库
    try:
        from tests.util import cleanup_temp_dbs
        cleanup_temp_dbs()
    except Exception:
        pass
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
