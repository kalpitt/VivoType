# core/data — Personalization Data

Voice samples you record for future personalization live here.

```
data/
  raw/                  # your recorded .wav clips (mono, 16 kHz, 16-bit)
  labels.csv            # manifest: maps each .wav -> the label/prompt you spoke
  corrections.jsonl     # captured corrections awaiting review (from learn.py)
  user_dictionary.json  # your promoted term fixes, merged over the default dict
  lexicon/contacts.json # your names lexicon for fuzzy matching
  prompts/              # your reference paragraph for record.py / benchmark.py
```

These files are personal. How each one is kept private:

- **Recordings** (`raw/*.wav`) and `corrections.jsonl` are git-ignored and
  never committed.
- `labels.csv`, `user_dictionary.json`, `lexicon/contacts.json` and `prompts/`
  are **tracked on purpose** in the maintainer's *private* development repo
  (a `.gitignore` entry does not untrack a file that is already committed).
  They are kept out of the public open-source repo by the exclude list in
  `scripts/publish_to_public.sh`. If you add a new personal file here, add it
  to that exclude list, and to the bundle excludes in
  `clients/mac/build_app.sh` so it never ships inside the app.

Record a sample with:

```bash
source .venv/bin/activate
python core/record.py --label "the quick brown fox"
# or: python -m core.record --label "…"
```

WAVs land in the writable data dir (`core/data/raw/` in a repo checkout, or
Application Support once installed). `labels.csv` stores a path relative to that
data dir when possible, so a custom `--outdir` does not orphan the manifest row.

> **Privacy:** your recordings (`raw/*.wav`) are git-ignored on purpose —
> personal voice data should not be committed. `labels.csv` is tracked only in
> the private development repo (see above). There is **no training** in this phase; the
> recorder just collects labeled clips.
