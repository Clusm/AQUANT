"""Launch the TradingAgents web UI via `tradingagents-web` command.

Pinned Streamlit flags:
  --server.headless true          不自动开浏览器,避免后台启动弹窗
  --server.runOnSave false        关闭文件改动自动重启(否则跑分析时落盘
                                  .json/.md 会触发 Streamlit 反复重启)
  --server.fileWatcherType none   彻底关闭源文件监视器。runOnSave 只关"自动
                                  重跑",监视器仍会在源码落盘时把已加载模块从
                                  sys.modules 逐出,导致下次重跑重新导入
                                  web.app_main 时与并发 import 撞车,抛
                                  KeyError: 'web.app_main'(改 web/*.py 或
                                  分析落盘均可能触发)。开发改代码后需手动重启。
  --browser.gatherUsageStats false 不上报遥测
  --server.port 8501              固定端口便于书签
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    app_path = Path(__file__).parent / "app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.headless", "true",
        "--server.runOnSave", "false",
        "--server.fileWatcherType", "none",
        "--browser.gatherUsageStats", "false",
        "--server.port", "8501",
    ]
    try:
        completed = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print(
            "未找到 Streamlit。请先安装主依赖:pip install -e .",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
