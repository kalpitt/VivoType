# VivoType — Brand Assets

Canonical artwork: **`VivoType App Icons/`** (the glossy app icon) and **`VivoType Icon Composer.icon/`**
(Apple Icon Composer source). Everything below is generated from that artwork — do not
hand-edit the PNGs; re-run the generator instead.

Type: **Inter** (Latin) · accent purple `#8C66F7` (fallback, sampled when the tinted icon is unavailable).

## `wordmark/` — logo lockups (icon + text, transparent)
| File | Use on |
|------|--------|
| `vivotype_on-dark.png` | dark backgrounds (white text) |
| `vivotype_on-light.png` | light backgrounds (black text) |

GitHub README tip — auto-swap light/dark:
```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="branding/wordmark/vivotype_on-dark.png">
  <img alt="VivoType AI" src="branding/wordmark/vivotype_on-light.png" width="320">
</picture>
```

## `github/`
- `social-preview.png` (1280×640) — repo **Settings → Social preview**.
- `icon-512.png` — square avatar / org logo.

## `web/`
- `favicon-16/32/48.png`, `favicon-180.png` (apple-touch), `favicon-192/512.png` (PWA).
- `og-image.png` (1200×630) — Open Graph / Twitter card.

```html
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/favicon-180.png">
<meta property="og:image" content="/og-image.png">
```
(A multi-size `favicon.ico` needs a separate tool; the PNG set above covers all modern browsers.)

## Regenerate
```bash
swift branding/_staging/make_brand.swift
```
Pure AppKit, no dependencies. Edit that file to tweak type, spacing, or colors.
