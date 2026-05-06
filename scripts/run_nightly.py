from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from agents.life_guard_agent import run_life_guard
    from agents.summary_agent import summarize_week
    from core.time_utils import ist_today

    repo_root = Path(__file__).resolve().parents[1]
    life_log_root = repo_root / "life_log"
    yesterday = ist_today() - timedelta(days=1)

    summary = summarize_week(repo_root, life_log_root, target_day=yesterday)
    hygiene = run_life_guard(repo_root, life_log_root, lookback_days=14, auto_fix=True)

    print("Nightly run completed")
    print(f"Weekly summary: {summary.summary_path} | commit={summary.commit}")
    print(f"Life-Guard report: {hygiene.report_path} | commit={hygiene.commit}")


if __name__ == "__main__":
    main()
