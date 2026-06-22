# Contributing to VivoType

Thanks for your interest in VivoType — a fully local, open-source voice dictation tool for macOS. This guide summarizes how the project is organized and the workflow we follow. It mirrors the working agreement in `CLAUDE.md` (which is written for AI coding agents) in plain terms for human contributors.

## Project layout

```
core/         # Python ASR backend + Indic post-processing (CLI only)
clients/mac/  # Native macOS client: hotkey, mic capture, text injection, UI
docs/         # Specs, the phased execution plan, and ADRs
scripts/      # Setup + test helpers
branding/     # Logo, icon, and wordmark assets
```

The architectural rule of thumb: put logic in `core/` unless it genuinely requires macOS APIs, in which case it belongs in `clients/mac/`.

## Getting set up

See the **Getting Started** section of the [README](README.md). In short, for backend/CLI work:

```bash
./scripts/setup_core.sh "$(pwd)"   # creates ./.venv (needs Python 3.11+)
source .venv/bin/activate
./scripts/run_tests.sh             # run the test suite before you start
```

For the macOS client:

```bash
./clients/mac/build_app.sh         # needs Xcode Command Line Tools
```

All Python dependencies must live in a `.venv` — never install globally.

## Architectural Decision Records (ADRs)

Before proposing a new dependency, a packaging change, or a major refactor, **read [`docs/adr/README.md`](docs/adr/README.md)**. Several non-obvious choices are deliberate and recorded there — for example: a persistent NDJSON daemon (ADR-0002), building the `.venv` on first launch instead of bundling binaries (ADR-0003), native Swift audio resampling (ADR-0004), and the MLX transcription engine (ADR-0007).

Do not submit changes that contradict an `Accepted` ADR. If you believe a decision should change, **write a new ADR** (copy `docs/adr/template.md`, give it the next number, and explain context → decision → consequences) rather than silently reversing it.

## Hard constraints

These are non-negotiable for the project's privacy promise and packaging model:

- **No cloud.** No OpenAI/Google/AWS or any network ML API. Audio and text stay 100% on-device.
- **No heavy audio C-deps.** No `librosa`, `soundfile`, `ffmpeg`, or `pydub` in `requirements.txt` — resampling happens in Swift (ADR-0004).
- **Engine is MLX.** Don't reintroduce `faster-whisper`/`ctranslate2` or other CPU backends (ADR-0007).
- **Respect the bundle boundary.** Immutable code in `Contents/Resources/`; mutable state (`.venv`, models, logs, your dictionary) in `~/Library/Application Support/VivoType/` (ADR-0003).

## Git workflow

- **Never commit directly to `main`.** Branch first: `git checkout -b feature/your-change` (or `fix/…`, `docs/…`, `chore/…`).
- Keep each branch focused on one logical change so it can be reviewed and merged independently.
- Verify before you open a PR: run `./scripts/run_tests.sh`, and for client changes rebuild and launch `VivoType.app`.
- Only merge to `main` after the change is verified.

## Development phases

The project is tracked as a sequence of phases in [`docs/phases.md`](docs/phases.md), each with an explicit Definition of Done. If you're picking up substantial work, check there for the current state and conventions before writing code.
