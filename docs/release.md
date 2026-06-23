# Release Checklist  
## Versioning  
- The pyproject minor version tracks the current implementation phase and patch versions track bugfix passes inside the phase  
- `PLAN.md` has its own roadmap version and should be updated after each meaningful implementation pass  
## Pre-Release Checks  
- Confirm Alembic head and note the migration ID included in the release  
- Run `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check`, `uv run pytest`, `uv run alembic upgrade head`, `uv run pzp-bot --help` and `uv run pzp-bot doctor`  
- Review config changes in `docs/setup.md`, `docs/deployment.md`, `docs/secrets.md` and `docs/operations.md`  
- Run a manual Telegram smoke test for command registration, `/help`, `/learn_status`, `/runtime_settings`, `/audit_events`, `/ask`, `/memory_export` and `/forget` confirmation  
- Docker runtime verification and CI workflow gates are deferred until Docker is fully installed on this workstation  
## Release Notes  
- Include migration state, new environment variables, runtime setting keys, admin command changes, privacy/audit changes and any manual Telegram smoke-test result  