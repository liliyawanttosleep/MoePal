"""双击启动（无控制台窗口）。 Double-click launcher (no console)."""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
if sys.stderr is None:      # pythonw 下没有控制台，把报错写进日志
    sys.stderr = sys.stdout = open(os.path.join("data", "moepal.log"), "a", encoding="utf-8", buffering=1)

from moepal.main import main

sys.exit(main())
