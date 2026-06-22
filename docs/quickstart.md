# VivoType Quickstart

## The fast path — use the app (no Terminal needed)

1. **Build:** run `./clients/mac/build_app.sh` once from the repo root. It produces
   `clients/mac/build/VivoType.app` and prints a build summary.

2. **Install:** drag `VivoType.app` into `/Applications`.

3. **Launch:** open VivoType from `/Applications`. A **"Welcome to VivoType"** setup
   window appears on first launch. It builds the Python engine in the background
   (takes ~1 minute; requires **Python 3.11+**). If Python is missing, VivoType
   shows an install hint and a **Retry** button.

4. **Permissions:** a guided **Permissions** checklist appears on first launch
   and walks you through granting **Microphone** and **Accessibility** — approve
   both for **VivoType** (not Terminal); each card shows a green check once granted.

5. **Dictate:** hold **Right-Option**, speak, release — your words appear in
   whatever app has focus.

> **Uninstall:** delete `VivoType.app` and `~/Library/Application Support/VivoType/`.

---

## CLI workflow (for developers)

Set up the backend in the repo root:

```bash
./scripts/setup_core.sh "$(pwd)"      # creates ./.venv, installs deps
source .venv/bin/activate
python core/cli.py path/to/audio.wav  # prints the transcript
```

`setup_core.sh` requires **Python 3.11+** and exits with a clear message if
it isn't found.

---

## Record your voice (personalization samples)

```bash
source .venv/bin/activate
python core/record.py --label "the quick brown fox jumps over the lazy dog"
```

Press **Return** to start, speak, press **Return** to stop. Clips go to
`core/data/raw/` (kept out of git).

---

## Measure accuracy on your voice

1. Record the standard paragraph:

```bash
source .venv/bin/activate
python core/record.py --label "$(cat core/data/prompts/training-paragraph.txt)"
```

2. Compare models:

```bash
python core/benchmark.py core/data/raw --reference-file core/data/prompts/training-paragraph.txt
```

Prints an accuracy % for `tiny.en` vs `small.en` on your own recordings.

---

## Run the tests

```bash
./scripts/run_tests.sh
```

Should end with **`OK`** (86 tests).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Holding Right-Option does nothing | Grant **Accessibility** (and **Input Monitoring**) to **VivoType**, then quit & relaunch. |
| No text appears after speaking | Same as above; also check **Microphone** is on for **VivoType**. |
| Setup window says Python is missing | Install Python 3.11+ (`brew install python@3.11` or from python.org), then click **Retry**. |
| `command not found` / `no such file` (CLI) | Check you're in the repo root and ran `setup_core.sh` first. |
