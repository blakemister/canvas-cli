## Summary

What does this PR do? One or two sentences.

## Motivation

Why is this change needed?

## Changes

- File-by-file or logical change list

## Testing

- [ ] `pytest tests/` passes
- [ ] New tests added for new behavior
- [ ] Regression tests for any safety-critical change (submit gates, auth, etc.)

## Checklist

- [ ] No hardcoded URLs, course IDs, or institution identifiers
- [ ] No committed secrets (cookies, tokens, .env files)
- [ ] README / CHANGELOG updated if user-facing
- [ ] Safety gates on `submit` preserved (or opt-out is behind an explicit flag + env var)
