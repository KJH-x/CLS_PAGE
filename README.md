# 朝陇山图片归档

Static archive site for **朝陇山** (Chaolongshan) Bilibili dynamics images.  
Cloudflare Pages + Cloudflare R2. No Workers.

## URLs

- Site: https://cls.nslc.top/

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
| Collection | Python script, run locally |
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
```

## Collection

Collection is run locally. Requires Python 3.10+ and dependencies:

```bash
pip install -r scripts/requirements.txt
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `R2_ACCESS_KEY_ID` | R2 API token Access Key ID |
| `R2_SECRET_ACCESS_KEY` | R2 API token Secret Access Key |
| `R2_BUCKET` | R2 bucket name |
| `R2_ACCOUNT_ID` | Cloudflare Account ID |
| `BILIBILI_COOKIE` | Bilibili cookie string (`key=val; key=val; ...`) |
| `BILIBILI_UID` | Target Bilibili user ID |
| `KEEP_RECENT` | Max dynamics to keep in index (default 10) |

### Incremental Run (default)

When `config/last-dynamic-id.json` exists in R2, the script stops fetching when it hits that ID. Only new dynamics are processed; existing images in R2 are recovered without re-downloading.

```bash
# Set env vars, then:
python scripts/collect.py
```

### Full Scan

To re-fetch all dynamics from the API (e.g. after adding new title filters), remove the cursor first:

```bash
# 1. Delete cursor from R2
python -c "
import boto3, os
from botocore.config import Config
s3 = boto3.client('s3',
    endpoint_url=f'https://{os.environ[\"R2_ACCOUNT_ID\"]}.r2.cloudflarestorage.com',
    aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
    config=Config(region_name='auto'))
s3.delete_object(Bucket=os.environ['R2_BUCKET'], Key='config/last-dynamic-id.json')
print('Deleted last-dynamic-id')
"

# 2. Run with high KEEP_RECENT to retain all existing dynamics
KEEP_RECENT=999 python scripts/collect.py
```

The script merges old dynamics from the existing R2 index with new API results, so existing images are preserved without re-downloading.

### Cookie

The cookie must be a simple `key=val; key=val; ...` string. To convert from a Netscape cookie file (e.g. browser export):

```bash
# PowerShell
$lines = (Get-Content cookie.txt -Raw) -split "`n" | Where-Object { $_ -and !$_.StartsWith("#") }
$cookies = foreach ($line in $lines) {
    $p = $line -split "`t"
    if ($p.Count -ge 7) { "$($p[5])=$($p[6])" }
}
$env:BILIBILI_COOKIE = $cookies -join "; "
```

### Data Integrity Check

After collection, verify R2 state:

```bash
python -c "
import json, boto3, os
from botocore.config import Config
s3 = boto3.client('s3',
    endpoint_url=f'https://{os.environ[\"R2_ACCOUNT_ID\"]}.r2.cloudflarestorage.com',
    aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
    config=Config(region_name='auto'))
idx = json.loads(s3.get_object(Bucket=os.environ['R2_BUCKET'], Key='site/index.json')['Body'].read())
imgs = sum(d['imageCount'] for d in idx['dynamics'])
print(f'Dynamics: {idx[\"totalDynamics\"]}, Images: {imgs}')
assert imgs == idx['totalImages'], 'image count mismatch'
assert len(idx['dynamics']) == idx['totalDynamics'], 'dynamic count mismatch'
print('OK')
"
```
