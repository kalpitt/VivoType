# core/data — Personalization Data

Voice samples you record for future personalization live here.

```
data/
  raw/                  # your recorded .wav clips (mono, 16 kHz, 16-bit)
  labels.csv            # manifest: maps each .wav -> the label/prompt you spoke
  corrections.jsonl     # captured corrections awaiting review (from learn.py)
  user_dictionary.json  # your promoted term fixes, merged over the default dict
  lexicon/contacts.json # your names lexicon for fuzzy matching
```

All of the above except this README are **git-ignored** — they're personal.

Record a sample with:

```bash
source .venv/bin/activate
python core/record.py --label "the quick brown fox"
# or: python -m core.record --label "…"
```

WAVs land in the writable data dir (`core/data/raw/` in a repo checkout, or
Application Support once installed). `labels.csv` stores a path relative to that
data dir when possible, so a custom `--outdir` does not orphan the manifest row.

> **Privacy:** your recordings (`raw/*.wav`) and `labels.csv` are git-ignored on
> purpose — personal voice data should not be committed. Only this README and
> the folder layout are tracked. There is **no training** in this phase; the
> recorder just collects labeled clips.
