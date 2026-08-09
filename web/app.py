"""TradingAgents A股分析 - Streamlit Web UI entry point.

Minimal entry point. The actual streamlit code is in web/app_main.py.

This file has a `if __name__ == "__main__"` guard to prevent multiprocessing
spawn workers from re-executing the streamlit code.

Background:
- web/runner.py runs quant pick() in a daemon thread.
- pick() uses multiprocessing.Pool with spawn context (Windows default).
- Spawn workers re-import __main__ (this file) via runpy.run_path with
  run_name="__mp_main__" (Python 3.8+).
- Without the guard, workers would re-execute all streamlit calls
  (st.set_page_config, st.markdown, st.tabs, ...), causing cascade crashes
  and process proliferation when user clicks 开始选股.
- With the guard, workers see __name__="__mp_main__" != "__main__" and skip
  calling main(), so the streamlit code in app_main.py is never executed
  in worker processes.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

# Load .env once at process startup. Spawn workers inherit env vars from
# parent, so this also covers workers (although workers don't run main()).
load_dotenv(_PROJECT_ROOT / ".env", override=True)


if __name__ == "__main__":
    # 后台自动拉取主板全量增量数据(daemon 线程,进程内只启动一次)
    from web.background_fetcher import start_background_fetcher
    start_background_fetcher()

    from web.app_main import main

    main()
