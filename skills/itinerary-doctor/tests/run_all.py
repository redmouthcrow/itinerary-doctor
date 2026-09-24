#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次跑完所有测试：python tests/run_all.py

（不用 unittest discover —— tests/ 不是包，discover 会报不可导入。）
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["test_normalize.py", "test_schema.py", "test_render.py"]
rc = 0
for f in FILES:
    print("=" * 60)
    print("▶ %s" % f)
    r = subprocess.run([sys.executable, os.path.join(HERE, f)], cwd=HERE)
    rc |= r.returncode
print("=" * 60)
print("全部通过 ✓" if rc == 0 else "有失败项，见上")
sys.exit(rc)
