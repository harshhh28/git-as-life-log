# Contributing

Thanks for your interest in improving Git-as-Life-Log.

## Development setup

1. Create a virtual environment.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Add environment secrets locally (never commit secrets):
   - `GROQ_API_KEY`
4. Run the app:
   - `streamlit run streamlit_app/main.py`

## Pull request guidelines

- Keep PRs focused and small.
- Add/update docs when behavior changes.
- Do not commit personal data into `life_log/` for public PRs.
- Use sample or redacted fixtures under `life_log_sample/`.
- Keep `life_log/` runtime output out of public PRs (daily/weekly/monthly/calendar entries, meta JSON indices, processed event cache).
- Run Ruff before opening a PR:
  - `python -m ruff check .`
  - `python -m ruff format --check .`

## Commit message style

Prefer clear prefixes:
- `record: ...`
- `summary: ...`
- `life-guard: ...`
- `docs: ...`
- `fix: ...`

