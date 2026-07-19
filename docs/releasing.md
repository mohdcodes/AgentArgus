# Releasing AgentArgus to PyPI

The `agentargus` name is already reserved on PyPI (a v0.0.0 stub). A release is a
**version bump of the existing project**, TestPyPI-smoke-tested first, then real
PyPI — per spec §10.

## One-time setup (Trusted Publishing, no stored token)
1. On PyPI → project `agentargus` → *Publishing* → add a **trusted publisher**:
   - Owner: `mohdcodes`, repo: `AgentArgus`, workflow: `publish.yml`,
     environment: `pypi`.
2. On TestPyPI, do the same with environment `testpypi`.
3. In the GitHub repo → *Settings → Environments*, create `pypi` and `testpypi`.

This lets `publish.yml` upload via OIDC — no `PYPI_API_TOKEN` secret to leak.

## Release flow
1. **Bump the version** in `pyproject.toml` (`[project].version`) and
   `agentargus/__init__.py` (`__version__`) — keep them identical.
   For the first real release: `0.1.0.dev0` → `0.1.0`.
2. Ensure the gate is green locally:
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy agentargus && uv run pytest
   ```
3. **TestPyPI dry-run:** trigger the `Publish` workflow manually
   (Actions → Publish → *Run workflow*). It builds and uploads to TestPyPI.
4. **Smoke test from TestPyPI** in a clean venv:
   ```bash
   uv venv /tmp/smoke && /tmp/smoke/bin/python -m pip install \
     --index-url https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ agentargus
   /tmp/smoke/bin/python -c "import agentargus; print(agentargus.__version__)"
   ```
5. **Real release:** tag and push:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
   The tag triggers the real PyPI publish job.
6. Create a GitHub Release from the tag, notes from `CHANGELOG.md`.

## Manual fallback (if not using Trusted Publishing)
```bash
python -m build
python -m twine check dist/*
python -m twine upload --repository testpypi dist/*   # then verify
python -m twine upload dist/*                          # real PyPI
```
`twine upload` needs a PyPI token in `~/.pypirc` or `TWINE_PASSWORD` — never
commit it.

## What ships in the wheel
Only the `agentargus/` package (+ the Jinja template asset). The `examples/`,
`tests/`, and docs are NOT packaged — verified via a zipfile check in CI/build.
