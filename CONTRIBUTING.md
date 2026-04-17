# Contributing to canvas-cli

Thanks for your interest. This project is small and opinionated — read this before filing issues or PRs.

## Ground rules

1. **No telemetry, no tracking, no phone-home.** This is a local CLI. It talks to Canvas and to your disk, nothing else.
2. **No institution-specific code.** The repo must work for any Canvas user. Hardcoded course IDs, school URLs, SSO flows, or professor names belong in your own fork, not upstream.
3. **Safety first for `submit`.** Any PR that weakens a safety gate (removes typed confirmation, skips hash re-verification, etc.) needs a compelling reason and an explicit flag opt-out.
4. **Be nice.** This is run as a side project.

## Development setup

```bash
git clone https://github.com/blakemister/canvas-cli.git
cd canvas-cli
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest tests/
```

All tests must pass before you open a PR. 66 tests, ~1s runtime, no network calls.

## Running the CLI locally

```bash
export CANVAS_BASE_URL=https://your-canvas.example.edu
# Put cookies at ~/.canvas-cli/cookies.json
canvas --help
```

Never commit `cookies.json`, `canvas_cookies.json`, or any file containing real session tokens.

## PR checklist

- [ ] `pytest tests/` passes
- [ ] New code has tests
- [ ] Existing tests cover regression (especially for `submit` safety gates)
- [ ] No hardcoded URLs, paths, or identifiers
- [ ] No emojis in commits, comments, or error messages unless there's a strong reason
- [ ] README / CHANGELOG updated if user-facing behavior changed

## Filing issues

Include:
- `canvas --version` output
- Your Canvas instance type (self-hosted, instructure.com, etc.)
- Exact command you ran
- Expected vs actual behavior
- Stack trace or error output (redact session cookies / assignment IDs if sensitive)

## Architecture

```
canvas_cli/
  main.py              # Click CLI entry, command registration
  client.py            # httpx sync+async clients, retry, auth refresh, CSRF
  auth.py              # cookie loading + optional refresh
  config.py            # env var reading, paths, constants
  resolve.py           # course code → ID resolution + cache
  output.py            # JSON envelope helpers
  submit.py            # submit command + safety gate pipeline
  submissions_api.py   # Canvas submissions endpoints
  receipts.py          # hash + receipt writer

tests/
  test_*.py            # pytest + respx for HTTP mocking
  fixtures/            # fake Canvas assignment JSONs
```

## Commit style

- Imperative present tense: "add sync-all command", not "added" or "adds"
- Short subject (≤72 chars), blank line, then body explaining "why"
- Reference issue numbers in the body where relevant
- Co-author attribution for AI tooling is fine and encouraged

## Testing patterns

- Unit tests for gates (each gate has at least one pass + one fail case)
- respx for HTTP mocking — never hit real Canvas in tests
- CliRunner for end-to-end tests with Click
- Regression guards for safety behavior (see `test_submit_regression` style)

## Security

If you find a security issue (auth bypass, unsafe file handling, etc.), please email the maintainer directly instead of opening a public issue. See GitHub's Security tab.
