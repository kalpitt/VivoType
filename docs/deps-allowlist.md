# Dependency Allowlist

Pre-approved packages that Claude Code may install **without** asking first, per
Hard Constraint #4 in `CLAUDE.md`. Anything **not** listed here still requires
explicit confirmation before `brew install` or `pip install`.

This list is **empty by default** — that is intentional. Every entry widens the
autonomy envelope, so add packages deliberately and keep the justification.

## Rules

- A package counts as allowlisted only if its exact name appears in a table below.
- `pip` installs must target the project `.venv` (dev `./.venv` or the installed
  app's `~/Library/Application Support/VivoType/.venv`) — never global pip.
- Local-only still applies: never add a package whose purpose is to reach a cloud
  ASR / LLM / text API (Hard Constraint #1).
- Do **not** add audio libraries banned by the Architecture Contract
  (`soundfile`, `librosa`, etc.) — WAV handling stays on stdlib `wave` + `numpy`.

## Python (pip, into `.venv` only)

| Package | Why it's allowed | Added |
|---------|------------------|-------|
| _(none yet)_ | | |

## System (Homebrew)

| Formula / Cask | Why it's allowed | Added |
|----------------|------------------|-------|
| _(none yet)_ | | |
