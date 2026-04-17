# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-04-17

Initial public release.

### Added

- 8 task-oriented commands: `auth`, `briefing`, `sync`, `sync-all`, `page`, `download`, `thread`, `rubric`, `submit`
- Cookie-based authentication with optional auto-refresh (`CANVAS_TOKEN_REFRESH_SCRIPT`)
- Automatic `X-CSRF-Token` header on state-changing requests
- Async client with concurrency limiter for multi-course queries
- `briefing` fetches grades + upcoming + todo + per-course submissions + announcements in parallel
- `sync COURSE` fetches full course snapshot; `--deep` fetches page content, rubrics, and discussion threads
- `sync-all` fans out over every active course
- `submit` command with multi-layered safety gates:
  - Assignment type cross-check (`--type` vs Canvas `submission_types`)
  - File extension, non-empty, size limits
  - Resubmit detection from both Canvas and local receipts
  - Typed confirmation (last 4 digits of assignment ID, breaks muscle memory)
  - Content SHA-256 captured at preview, re-verified before POST
  - Dry-run mode that runs every gate but skips the Canvas write
  - TTY check + env var requirement for non-interactive use
  - JSON audit receipts in `~/.canvas-cli/submissions/`
- Configurable via env vars: `CANVAS_BASE_URL`, `CANVAS_COOKIE_FILE`, `CANVAS_TOKEN_REFRESH_SCRIPT`, `CANVAS_UPLOAD_SIZE_LIMIT_MB`, `CANVAS_I_UNDERSTAND_RESUBMIT`
- 66-test pytest suite with respx for HTTP mocking — zero network calls in CI
- GitHub Actions CI for Python 3.10, 3.11, 3.12 on ubuntu-latest and windows-latest

### Security

- `.gitignore` blocks `canvas_cookies.json`, `*.cookies.json`, and `.pytest_cache/`
- Test fixtures use fake Canvas IDs only (12345, 22222, 33333, etc.)
- No hardcoded credentials, URLs, or institution identifiers
