# 朝陇山图片归档

Static archive site for **朝陇山** (Chaolongshan) Bilibili dynamics images.  
Cloudflare Pages + GitHub Actions + Cloudflare R2. No Workers.

## URLs

- Site: https://cls.nslc.top/
- R2: https://cls.r2.nsapi.top/

## Features

- **Gallery-first layout** — preview grid with 6 images per card, expand to view all
- **Full-screen lightbox** — zoom, pan, pinch-to-zoom, thumbnail strip
- **Mobile reading mode** — vertical scroll with touch-friendly zoom
- **Search** — filter by title, tag, or date
- **Dark theme** — auto / manual toggle
- **SPA routing** — direct-link to a dynamic via `/to/{activity-slug}/`
- **Chinese localization** — full ZH-CN UI

## Stack

| Layer | Tech |
|-------|------|
| Frontend | Vanilla HTML/CSS/JS, deployed to Cloudflare Pages |
| Storage | Cloudflare R2 (images, thumbs, JSON indexes) |
| Collection | Python script via GitHub Actions (schedule + manual trigger) |
| Compression | Original dimensions, q35 progressive JPEG |

## R2 Layout

```
/site/index.json                     — Full data for the frontend
/site/search-index.json              — Lightweight searchable index
/config/last-dynamic-id.json         — Cursor for incremental fetch
/manifests/current.json              — Object manifest of latest run
/manifests/previous.json             — Backup manifest (for rollback)
/images/{dynamicId}/{n}.jpg          — Full-size compressed images
/thumbs/{dynamicId}/{n}.jpg          — Thumbnails (1/2 original)
```

## Local Testing

```bash
# Dev server (serves site/ on http://localhost:17099 with SPA fallback)
python scripts/serve.py

# Run collection (requires env vars)
export R2_ACCESS_KEY_ID="..."
export R2_SECRET_ACCESS_KEY="..."
export R2_BUCKET="cls-page-data"
export R2_ACCOUNT_ID="..."
export BILIBILI_COOKIE="..."
export BILIBILI_UID="3546864272017883"
export KEEP_RECENT="10"

pip install -r scripts/requirements.txt
python scripts/collect.py
```

## GitHub Secrets

| Secret | Description |
|--------|-------------|
| `R2_ACCESS_KEY_ID` | R2 API token Access Key ID |
| `R2_SECRET_ACCESS_KEY` | R2 API token Secret Access Key |
| `R2_BUCKET` | R2 bucket name |
| `R2_ACCOUNT_ID` | Cloudflare Account ID |
| `BILIBILI_COOKIE` | Bilibili API cookie |
| `BILIBILI_UID` | Target Bilibili user ID |

Optional variable: `KEEP_RECENT` (default 10).

## Manual Trigger

GitHub repo → Actions → Collect Bilibili Dynamics → Run workflow.  
Also runs daily at 13:00 Beijing time (05:00 UTC).
