from __future__ import annotations

from pathlib import Path

FOLDERS = [
    "life_log/calendar",
    "life_log/journal/daily",
    "life_log/journal/weekly",
    "life_log/journal/monthly",
    "life_log/meta/indices",
    "life_log/meta/health_reports",
    "life_log/templates",
]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for folder in FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
    print("Bootstrap complete.")


if __name__ == "__main__":
    main()
