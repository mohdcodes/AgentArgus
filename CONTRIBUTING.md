# Contributing to AgentArgus

Thanks for your interest! Bug reports, feature requests, and PRs are welcome.

## Reporting issues
Open a [GitHub issue](https://github.com/mohdcodes/AgentArgus/issues) using the
appropriate template (bug report / feature request). For bugs, please include:
- AgentArgus version (`python -c "import agentargus; print(agentargus.__version__)"`)
- Python version and OS
- A minimal reproducible snippet and the full traceback

## Dev setup (uv)
This project uses [uv](https://docs.astral.sh/uv/). `uv.lock` is committed and
Python is pinned via `.python-version` (3.12 for dev; CI tests 3.10–3.12).

```bash
uv venv
uv pip install -e ".[dev]"
```

## The green gate (must pass before a PR merges)
```bash
uv run ruff check .            # lint (bans print() in library code)
uv run ruff format --check .   # formatting
uv run mypy agentargus         # strict type-checking
uv run pytest --cov=agentargus # tests; target >=80% coverage
```
CI runs the same across Python 3.10 / 3.11 / 3.12.

## Conventions
- **No `print()` in library code** — use `get_logger(...)`. Examples/CLI may print.
- **No secrets in code** — keys via `.env` (gitignored) / environment.
- **One behaviour, one home** — extract shared logic rather than duplicating.
- **Keep the public API small** — most things are internal (`_internal/`).
- Every change ships with tests in the same PR.

## Running the examples
```bash
uv pip install -e ".[dev,examples]"
cp .env.example .env    # add ANTHROPIC_API_KEY for real runs (optional)
uv run python examples/deep_research_agent/run.py
```

## Releasing
See [docs/releasing.md](docs/releasing.md) (maintainers only).
