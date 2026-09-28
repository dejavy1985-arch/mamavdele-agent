#!/usr/bin/env python3
"""Запуск всех офлайн-тестов центра управления без сторонних пакетов.

Использование:
    python3 tests/run_tests.py
или из папки tests:
    python3 run_tests.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(HERE)  # agent_control_center/
sys.path.insert(0, PKG_ROOT)
sys.path.insert(0, HERE)  # чтобы был доступен _fixtures


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=HERE, pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
