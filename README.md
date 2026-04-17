# canvas-cli

A Python CLI for Canvas LMS. Fast, task-oriented commands for students and AI agents to work with courses, assignments, submissions, and grades.

Eight commands that each replace a handful of Canvas API calls. All commands emit clean JSON envelopes with `--json` for scripting. Cookie auth with automatic refresh. Async fan-out for multi-course queries. A safety-gated `submit` command for assignment submissions.

## Why

Canvas's REST API is verbose — fetching a course overview takes a dozen GETs. Students and AI-agent workflows need concise, composable primitives:

- **One command** returns your grades + upcoming work + todos + per-course announcements.
- **One command** syncs an entire course (modules, assignments, discussions, announcements, optional deep page+rubric fetch) in parallel.
- **One command** submits work with safety gates so you can't accidentally send the wrong file to the wrong assignment.

## Features

- 8 task-oriented commands (vs. ~18 raw endpoints)
- Human-readable output by default, `--json` envelopes for automation
- Async parallelism for multi-course queries (`sync-all --deep` syncs 5 courses in ~5s)
- Cookie-based auth with optional automated refresh
- Safety-gated `submit` command: typed confirmation, hash re-verification, audit receipts
- Canvas CSRF handling (`X-CSRF-Token`) built in
- File upload via Canvas's 3-step inst-fs flow

## Install

```bash
pip install git+https://github.com/blakemister/canvas-cli.git
```

Or for local development:

```bash
git clone https://github.com/blakemister/canvas-cli.git
cd canvas-cli
pip install -e ".[dev]"
pytest tests/
```

Requires Python 3.10+.

## Setup

### 1. Configure your Canvas URL

```bash
# macOS / Linux
export CANVAS_BASE_URL=https://canvas.instructure.com

# Windows PowerShell
$env:CANVAS_BASE_URL = 'https://canvas.instructure.com'
```

Use your school's Canvas URL (e.g. `https://your-school.instructure.com`).

### 2. Extract your session cookies

This CLI authenticates via browser session cookies. To get them:

**Option A: Browser DevTools**

1. Log into your Canvas in a browser.
2. Open DevTools → Application (Chrome) or Storage (Firefox) → Cookies → your Canvas URL.
3. Copy the values of these cookies into a JSON file at `~/.canvas-cli/cookies.json`:

```json
[
  {"name": "canvas_session", "value": "<paste>"},
  {"name": "_csrf_token", "value": "<paste>"},
  {"name": "log_session_id", "value": "<paste>"}
]
```

**Option B: Browser extension**

Use a "Get cookies.txt" or "EditThisCookie" extension to export all cookies for your Canvas domain, then convert to the JSON format above.

**Option C: Automated refresh script**

If your Canvas uses SSO, you can write a Playwright/Selenium script that logs in and writes the cookie file, then set `CANVAS_TOKEN_REFRESH_SCRIPT=/absolute/path/to/script.py`. The CLI will invoke it on 401 errors to refresh.

### 3. Verify

```bash
canvas auth
# Auth OK
```

## Commands

| Command | What it does |
|---------|--------------|
| `canvas auth` | Verify auth, trigger refresh on 401 |
| `canvas briefing [--course X] [--deep]` | Grades + upcoming + todo + submissions + announcements (one call) |
| `canvas sync COURSE [--deep]` | Full course snapshot: syllabus, modules, assignments, discussions, announcements |
| `canvas sync-all [--deep]` | Sync every active course in parallel |
| `canvas page COURSE PAGE_URL` | Fetch a wiki page by URL slug |
| `canvas download COURSE FILE_ID --dir PATH` | Download a Canvas file |
| `canvas thread COURSE TOPIC_ID` | Full discussion thread with all replies |
| `canvas rubric COURSE ASSIGNMENT_ID` | Rubric criteria and rating scale |
| `canvas submit COURSE ASSIGN_ID --type TYPE ...` | Submit to an assignment (with safety gates) |

`COURSE` accepts either the course code (`CS101`, `MATH202`) or the numeric Canvas ID (`12345`).

Add `--json` for structured output:

```bash
canvas --json briefing --course CS101 --deep
canvas --json sync CS101 --deep > snapshot.json
```

All JSON responses use the envelope form:

```json
{"success": true, "data": {...}}
{"success": false, "error": "APIError", "message": "..."}
```

### Exit codes

- `0` success
- `1` Canvas API error
- `2` usage error (missing args, gate rejected)
- `3` auth failure after refresh
- `4` network/timeout error

## The `submit` command

Submitting to Canvas is the highest-stakes operation — once sent, work goes to your professor. This command runs a 10+ gate pipeline to make it hard to submit the wrong thing.

### Usage

```bash
# Text entry
canvas submit CS101 12345 --type text --text-file ./work.md

# File upload
canvas submit CS101 12345 --type file --file ./lab.ipynb

# URL submission
canvas submit CS101 12345 --type url --url https://example.com/my-work

# Dry run (runs every gate, does NOT submit)
canvas submit CS101 12345 --type text --text-file ./work.md --dry-run

# Resubmit (requires explicit flag + env var for non-interactive)
CANVAS_I_UNDERSTAND_RESUBMIT=1 canvas submit CS101 12345 \
  --type file --file ./lab.ipynb --confirm 2345 --resubmit
```

### Safety gates (fail closed)

1. Assignment is fetched up-front. Course name, title, URL, due date, points, and allowed submission types are printed for cross-check.
2. `--type` must be in the assignment's `submission_types` array.
3. Assignment must not be `locked_for_user`.
4. For files: extension must be in `allowed_extensions` (if non-empty), non-empty, under 100MB.
5. For text: non-empty after whitespace strip.
6. Resubmit is blocked unless `--resubmit` is passed. Prior-submission detected from **both** Canvas (`submission.submitted_at`) AND local receipts — either triggers.
7. Past-due warning (doesn't block; Canvas accepts late submissions).
8. Preview printed with SHA-256 of the content and a confirm token (last 4 digits of assignment ID).
9. User must type the confirm token (interactive) OR pass `--confirm <token>` (non-interactive). 3 attempts then abort.
10. Content is re-hashed immediately before POST — any modification between preview and confirm aborts the submission.
11. On success, a JSON receipt is written to `~/.canvas-cli/submissions/{course}_{aid}_{timestamp}.json` with the Canvas response, content hash, and assignment metadata.

Dry-run (`--dry-run`) runs all gates, prints the preview, writes a dry-run receipt, and exits 0 — without calling the Canvas submission endpoint.

### Receipts

Every successful submission writes an audit record:

```
~/.canvas-cli/submissions/CS101_12345_20260417-173023-875597Z-3455700.json
~/.canvas-cli/submissions/dry-run/CS101_12345_20260417-172726-178498Z.json  ← dry-runs
```

Receipts contain the full Canvas response, content hash, assignment metadata, and submission context. Use them for grade disputes, audit trails, or just peace of mind.

## AI agent usage

Use `--json` for structured output. Envelopes include `success`, `data`, `error`, `message` fields.

For submissions in automated workflows:

- Pass `--confirm <last-4-of-assignment-id>` to skip the interactive prompt.
- For resubmissions, additionally set `CANVAS_I_UNDERSTAND_RESUBMIT=1` as an env var.
- Use `--dry-run` first to validate the submission will pass the gates before actually sending.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CANVAS_BASE_URL` | *(required)* | Your Canvas instance URL, no trailing slash |
| `CANVAS_COOKIE_FILE` | `~/.canvas-cli/cookies.json` | Path to cookies JSON |
| `CANVAS_TOKEN_REFRESH_SCRIPT` | *(unset)* | Script to refresh cookies on 401 |
| `CANVAS_UPLOAD_SIZE_LIMIT_MB` | `100` | File upload size cap |
| `CANVAS_I_UNDERSTAND_RESUBMIT` | *(unset)* | Must be `1` for non-interactive resubmit |

## Development

```bash
git clone https://github.com/blakemister/canvas-cli.git
cd canvas-cli
pip install -e ".[dev]"
pytest tests/
```

66 tests, ~1s runtime, no real network calls. See `tests/` for the full matrix.

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines, and [the issue tracker](https://github.com/blakemister/canvas-cli/issues) for things to work on.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This tool is not affiliated with Instructure or Canvas LMS. Use at your own risk. Check your institution's academic integrity policy before using submission automation — the `submit` command is a convenience for running your own workflow, not a vehicle for plagiarism.
