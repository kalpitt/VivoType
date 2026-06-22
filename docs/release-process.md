# VivoType — Release Process

## Cutting a release (one command)

```bash
git tag v0.2.0 && git push origin v0.2.0
```

That's it. GitHub Actions does the rest automatically (~30 seconds):

1. Builds `VivoType.app` on a macOS-15 Apple Silicon runner
2. Zips it with `ditto` (preserves macOS extended attributes)
3. Publishes a GitHub Release with the zip attached and install instructions in the body
4. Computes the SHA-256 of the zip and updates the Homebrew cask automatically

## What the release workflow produces

| Thing | Where |
|---|---|
| `VivoType-v0.x.x.zip` | Attached to the GitHub Release |
| SHA-256 in release body | For manual verification |
| Updated Homebrew cask | `kalpitt/homebrew-vivotype` — auto-committed by CI |

## Three install methods users can choose from

| Method | Command |
|---|---|
| curl (recommended) | `curl -fsSL https://raw.githubusercontent.com/kalpitt/VivoType/main/install.sh \| bash` |
| Homebrew | `brew tap kalpitt/vivotype && brew install --cask vivotype` |
| Manual | Download zip from the release, unzip, drag to Applications, right-click → Open |

The curl and Homebrew methods strip the macOS quarantine flag automatically — users never see the "unidentified developer" warning.

## Required secrets

| Secret | Repo | Purpose |
|---|---|---|
| `HOMEBREW_TAP_TOKEN` | `kalpitt/VivoType` | Fine-grained PAT with Contents read+write on `kalpitt/homebrew-vivotype` |

If the cask-update step fails with `401 Bad credentials`, the PAT has expired or is corrupted. Recreate it:

1. [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new)
   - Name: `VivoType Homebrew updater`
   - Repository access: **Only** `homebrew-vivotype`
   - Permissions → Contents: **Read and write**
2. Run: `gh secret set HOMEBREW_TAP_TOKEN --repo kalpitt/VivoType`

## Code signing

CI builds are **ad-hoc signed** (`codesign --sign -`). This is fine because:
- `install.sh` calls `xattr -dr com.apple.quarantine` after copying the app
- The Homebrew cask does the same via `brew install --cask`
- Manual installs require right-click → Open once (standard Apple behaviour for non-App-Store apps)

No Apple Developer account or paid certificate is needed.

## Version numbering

Use `vMAJOR.MINOR.PATCH` tags (e.g. `v0.2.0`). The tag name becomes the release title and the Homebrew `version` field (with the `v` stripped).

## What to avoid

- **Do not create releases manually** via the GitHub web UI — the CI step that computes the SHA-256 and updates the Homebrew cask won't run.
- **Do not use `draft: true`** in the workflow — the GitHub API `/releases/latest` endpoint skips drafts, breaking `install.sh` and Homebrew.
- **Do not zip the app with plain `zip -r`** — use `ditto -c -k --sequesterRsrc --keepParent` to preserve macOS extended attributes, otherwise the app may behave oddly after install.
- **Do not push tags directly to the private repo and expect a public release** — you must push the tag to `kalpitt/VivoType` (the public repo). See the private-repo context notes for the full private→public sync workflow.
