# Git-as-Life-Log (CrewAI + Groq + Streamlit)

A personal history tracker where Git-tracked Markdown files are the only data store.  
Agents read and write `life_log/` and commit meaningful changes to the repository.

## Open-source compatibility

This repository is structured to be open-source friendly:

- Source code is safe to publish.
- Real personal data should stay private.
- Public examples should live under `life_log_sample/`.

Recommended setup:

1. Keep this repo public for code and templates.
2. Keep real life entries in a separate private repo (or private worktree).
3. Use redacted sample files for demos, issues, and pull requests.

## Project layout

- `life_log/` - life-log database (daily, weekly, monthly, calendar, metadata index/reports)
- `agents/` - CrewAI agents: Recorder, Summary, Search/Fact, Life-Guard
- `core/` - markdown/front-matter helpers and Git wrapper utilities
- `streamlit_app/main.py` - Streamlit UI
- `scripts/run_nightly.py` - nightly summary + hygiene entrypoint
- `config/defaults.yaml` - defaults for model and behavior

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Set secrets/environment:
   - `GROQ_API_KEY=...`
   - optional for Hugging Face model access/download limits: `HF_TOKEN=...`
   - optional for pushing from automation: `GIT_PAT`, `GIT_USER_EMAIL`, `GIT_USER_NAME`
4. Copy `.env.example` to `.env` and fill values for local usage.

## Run locally

- Start app:
  - `streamlit run streamlit_app/main.py`
- Run nightly tasks manually:
  - `python scripts/run_nightly.py`
- Run Telegram bot (polling):
  - `python bot/telegram_bot.py`

## Linting (Ruff)

- Run lint checks:
  - `python -m ruff check .`
- Run formatting:
  - `python -m ruff format .`
- Verify formatting without changes:
  - `python -m ruff format --check .`

CI runs Ruff automatically on pushes to `main` and on pull requests via `.github/workflows/lint.yml`.

## Streamlit Community Cloud deployment

1. Push this repo to GitHub.
2. In Streamlit Community Cloud, create a new app from this repo.
3. Set **Main file path** to:
   - `streamlit_app/main.py`
4. In app **Settings -> Secrets**, add:
   - `GROQ_API_KEY = "your_key"`
   - optional git push credentials if your deployment pushes commits.
5. Deploy. Streamlit will install `requirements.txt` automatically.

## Telegram bot setup (single-user mode)

This release supports one authorized Telegram chat only.

Required environment variables:

- `TELEGRAM_BOT_TOKEN` - Bot token from BotFather
- `ALLOWED_CHAT_ID` - single chat id allowed to use the bot
- `GROQ_API_KEY` - required for LLM-backed actions
- `HF_TOKEN` - optional, for authenticated Hugging Face model downloads

Bot behavior:

- Unauthorized chat ids are rejected.
- Duplicate record imports are ignored (same message in same minute bucket) using:
  - `life_log/meta/processed_events.json`
- Menu actions:
  - Record note
  - Ask your life
  - Summarize today
  - Summarize week
  - Summarize month
  - Flush all data (requires typed confirmation: `FLUSH`)

Run:

- `python bot/telegram_bot.py`

Notes:

- Polling mode only in this release.
- Streamlit and Telegram follow the same core action logic (record dedupe, ask, summarize).
- Multi-user architecture is intentionally deferred.

## Security and privacy checklist before publishing

- Confirm no secrets are committed (`GROQ_API_KEY`, PAT, etc.).
- Confirm no personal entries are present in public branches.
- Keep `.streamlit/secrets.toml` local only.
- Review `SECURITY.md` for reporting and data safety policy.

### Notes for free tier scheduling

Streamlit Cloud is request-driven, so use UI buttons for manual triggers.  
For nightly automation, use GitHub Actions cron to run `python scripts/run_nightly.py` and push commits.

## Agent operations

- **Recorder Agent**
  - Parses raw event text into structured metadata.
  - Writes/updates `life_log/calendar/...` and daily journal files.
  - Commits with messages like: `record: add meeting for 2026-05-05 (webshop)`.

- **Summary Agent**
  - Aggregates daily notes and writes weekly/monthly summaries.
  - Extracts highlights + metrics via Groq.
  - Commits summary files.

- **Search/Fact Agent**
  - Interprets natural-language question.
  - Uses lightweight semantic RAG over markdown chunks + git history and returns answer with sources.
  - Maintains indices in `life_log/meta/indices/`:
    - `people_project_index.json`
    - `semantic_chunks_index.json`

- **Life-Guard Agent**
  - Detects missing or malformed daily files.
  - Auto-fixes safe defaults.
  - Writes hygiene report under `life_log/meta/health_reports/`.

## End-to-end scenario

1. Open Streamlit app.
2. In **Run Agents**, paste a raw event and click **Record import**.
3. Open **Daily Journal** and confirm meeting section was added.
4. Click **Record import** again with the same text and confirm duplicate is ignored.
5. Click **Summarize today** and verify answer text is generated.
6. Click **Summarize yesterday (weekly rollup)** and open **Summaries**.
7. In **Ask Your Life**, ask: `When did I last meet @alice?`
8. Click **Check life hygiene** and verify generated report path.

## Telegram manual validation checklist

- Authorized user can run `/start` and receive menu.
- Unauthorized user is blocked with a simple message.
- Record import creates expected files and commit hash.
- Sending the same record message again in the same minute returns `already recorded`.
- Ask your life returns concise answer and limited sources.
- `/today` returns today's entries, missing sections, and quick stats.
- Long replies stay within Telegram size constraints (truncate/split behavior).

## Flow docs

Mermaid flow diagrams live under `docs/flows/`:

- `01_system_end_to_end.mmd` - Streamlit + Telegram end-to-end paths.
- `02_recorder_flow.mmd` - recorder + duplicate guard path.
- `03_summary_flow.mmd` - weekly/monthly summary generation.
- `04_search_rag_topk_flow.mmd` - search and retrieval flow.
- `05_indexing_pipeline_flow.mmd` - semantic index generation.
- `06_lifeguard_flow.mmd` - life hygiene flow.
- `07_client_parity_flow.mmd` - client parity map for Streamlit and Telegram.

## Community files

- `LICENSE` (MIT)
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`

