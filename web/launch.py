"""Launch the TradingAgents web UI via `tradingagents-web` command.

Pinned Streamlit flags:
  --server.headless true          不自动开浏览器,避免后台启动弹窗
  --server.runOnSave false        关闭文件改动自动重启(否则跑分析时落盘
                                  .json/.md 会触发 Streamlit 反复重启)
  --browser.gatherUsageStats false 不上报遥测
  --server.port 8501              固定端口便于书签
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    app_path = Path(__file__).parent / "app.py"
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.headless", "true",
        "--server.runOnSave", "false",
        "--browser.gatherUsageStats", "false",
        "--server.port", "8501",
    ])


if __name__ == "__main__":
    main()
