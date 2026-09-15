# 朝陇山 / 山团团 图片归档

Static archive site for **朝陇山** (Chaolongshan, `ak`) and **山团团** (Endfield, `ef`) Bilibili dynamics images.  
Cloudflare Pages + Cloudflare R2. No Workers.

## URLs

- Site: <https://cls.nslc.top/> (4 routes: `/ak/`, `/ak-figures/`, `/ef/`, `/ef-figures/`)
- R2 public: <https://cls.r2.nsapi.top>

## Features

- **Dual-account gallery** — 朝陇山 (ak) and 山团团 (ef, Endfield) archives, segmented account switch
- **4-route SPA** — `/ak/`, `/ak-figures/`, `/ef/`, `/ef-figures/`; route + per-route scroll position persisted in localStorage
- **Gallery-first layout** — preview grid with 6 images per card, expand to view all
- **Full-screen lightbox** — zoom, pan, pinch-to-zoom, thumbnail strip
- **Mobile reading mode** — vertical scroll with touch-friendly zoom
- **Search** — filter by title, tag, or date (per-account search index)
- **Dark theme** — auto / manual toggle
- **SPA routing** — direct-link to a dynamic via `/to/{code}/` (variable-length share code, see below), or `#id-{id}`; legacy `/to/{account}/{activity-slug}/` links still resolve

### Share Codes (variable-length, UTF-8 style)

Collect.py freezes a `code` field into each index entry; the copy link emits `/to/{code}/`.

- **4-char default**: uniform `sha256(account:id)` candidate inside a per-account first-char pool (ak `0-9a-h`, ef `i-z`; 18×36³ = 839,808 each, disjoint pools make cross-account collisions impossible)
- **8-char escape**: IDs claim candidates in ascending order; a later collision escapes to its own 8-char base36 tail. The rule is a pure function of the append-only ID set, so assigned codes are never reassigned and shared links stay valid
- **Resolution chain**: server `code` → server `slug` → client-derived slug → tail-8 → raw ID
- **Collision model** (sequential draws without replacement, P(k+1-th draw collides) = k/839,808):
  observed posting rates ak ≈ 46/yr (n₀=43), ef ≈ 32/yr (n₀=20); 63 codes measured, zero collisions.
  | Horizon | ak | ef | combined |
  |---|---|---|---|
  | 1 year | 0.36% | 0.14% | **≈0.5% (1/200)** |
  | 5 years | 4.2% | 1.9% | 6.0% |
  | 10 years | 13.9% | 6.6% | 19.6% |
  | 50% point | ~22.6 yr (n≈1080) | ~33 yr (n≈1080) | — |

  Escapes are soft: only the newer dynamic moves to 8 chars; existing 4-char codes and already-shared links are unaffected.
- **Lazy small thumbs** — grid loads 1/8 `smthumbs/`, full image only on lightbox open
- **Chinese localization** — full ZH-CN UI

## Stack

| Layer | Tech |
| ------- | ------ |
| Frontend | Vanilla HTML/CSS/JS, deployed to Cloudflare Pages |
| Storage | Cloudflare R2 (images, thumbs, JSON indexes) |
| Collection | Python script, run locally |
| Compression | Original dimensions, q35 progressive JPEG |

## R2 Layout

Two namespaces (mirrored structure): ak (朝陇山) at the **root**, ef (山团团/Endfield) under the **`endfield/`** prefix (`OUTPUT_PREFIX`).

**ak (root):**

```text
/site/index.json                     — Full data (main gallery)
/site/figures-index.json             — Figure pre-orders (手办) gallery
/site/search-index.json              — Search index
/site/figures-search-index.json      — Search index (figures)
/manifests/current.json              — Object manifest
/manifests/previous.json             — Backup manifest (rollback)
/images/{dynamicId}/{n}.jpg          — Full-size compressed images
/thumbs/{dynamicId}/{n}.jpg          — Thumbnails (1/2 original)
/smthumbs/{dynamicId}/{n}.jpg        — Small thumbs (1/8 original)
/originals/{dynamicId}/{n}.{ext}     — Original bytes (only when R2_UPLOAD_ORIGINALS=1; OFF by default)
```

**ef (endfield/) — same files under `endfield/` prefix:**

```text
/endfield/site/index.json            — ef main gallery
/endfield/site/figures-index.json    — ef figure pre-orders
/endfield/site/search-index.json     — ef search index
/endfield/site/figures-search-index.json
/endfield/manifests/current.json
/endfield/images/{dynamicId}/{n}.jpg
/endfield/thumbs/{dynamicId}/{n}.jpg
/endfield/smthumbs/{dynamicId}/{n}.jpg
/endfield/originals/{dynamicId}/{n}.{ext}   — Original bytes (only when R2_UPLOAD_ORIGINALS=1; OFF by default)
```

**Cursor:** `config/last-dynamic-id.json` — incremental-fetch cursor, stored **under the account prefix** (ak: `/config/last-dynamic-id.json`, ef: `/endfield/config/last-dynamic-id.json`), so each account tracks its own feed position.

## Local Archive (on-disk mirror)

Newly downloaded images are also written locally under `local_archive/` (gitignored):

```text
local_archive/images/{dynamicId}/{n}.jpg   — compressed copy (same as R2 images/)
local_archive/cache/{sha256}-{dynamicId}-{n}.{ext} — original bytes, keyed by
                                                     content hash + dynamic id
```

Set `LOCAL_ARCHIVE_DIR` to override the default `local_archive/` directory.

## Categorization & Image Filters

### Endfield categories (`ARCHIVE_MODE=endfield`)

Titles are read via `_fence_line()`: the first line containing a `〓` or `▼` fence character (verbatim), falling back to the first non-empty line. `_endfield_categorize()` assigns a category in this priority:

| Priority | Rule | Category |
| --------- | ----- | -------- |
| 1 | `手办` + one of `预售\|企划公开\|开订\|再贩` | `手办` (figure pre-order → figures gallery) |
| 2 | title contains `预售` | `预售` (non-figure pre-order) |
| 3 | one of `上新\|周边介绍\|贩售情报\|场贩` | `上新` |
| 4 | one of `余量\|掉落` | `余量上架` |
| 5 | none of the above | **excluded** (实物展示/日常 posts) |

`手办` posts go to the separate figures index; `figure_category` splits main vs figures. No series split in endfield mode.

### Image filters (both modes) — BY DESIGN

Applied per image after dimensions are known (`collect.py`):

- **Square images dropped** — `width == height` (e.g. 500x500 icons)
- **Banner/wide images dropped** — `width/height >= 0.3` (aspect ≥ 0.3)

Only narrow/tall images are archived. A dynamic whose images are all dropped is removed from the archive. These rules are intentional; do not weaken them without explicit user decision.

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
| ---------- | ------------- |
| `R2_ACCESS_KEY_ID` | R2 API token Access Key ID |
| `R2_SECRET_ACCESS_KEY` | R2 API token Secret Access Key |
| `R2_BUCKET` | R2 bucket name |
| `R2_ACCOUNT_ID` | Cloudflare Account ID |
| `BILIBILI_COOKIE` | Bilibili cookie string (`key=val; key=val; ...`) |
| `BILIBILI_UID` | Target Bilibili user ID |
| `ARCHIVE_MODE` | `cls` (朝陇山, default) or `endfield` (山团团); controls title/category rules |
| `LOCAL_ARCHIVE_DIR` | On-disk mirror dir (default `local_archive/` under repo) |
| `R2_UPLOAD_ORIGINALS` | If non-empty, publish uncompressed originals to R2 `originals/` during collection. **Default OFF** — the public bucket must not redistribute third-party artwork; enable only for a private bucket |
| `KEEP_RECENT` | Max dynamics to keep in index (default 10) |
| `OUTPUT_PREFIX` | Optional isolated R2 namespace for staging runs (e.g. `endfield`) |
| `DRY_RUN` | If non-empty, perform reads/downloads but skip every R2 write |
| `EXTRACT_DEBUG` | If non-empty, log per-item extraction decisions (accept / reject reason) plus an accept/reject summary during Step 3 — useful to see why a dynamic was or wasn't archived |
| `ALLOW_IMAGE_FAILURES` | Maximum tolerated image failures before aborting (default 0) |
| `REQUEST_MAX_ATTEMPTS` | Attempts for transient API/image failures (default 3) |
| `BACKOFF_BASE_SECONDS` | Base delay for exponential retry backoff (default 1) |

### Incremental Run (default)

When the account's `config/last-dynamic-id.json` exists in R2, the script stops fetching when it hits that ID. Only new dynamics are processed; existing images in R2 are recovered without re-downloading.

```bash
# Preserve the full existing archive while appending new dynamics (ak):
KEEP_RECENT=999 ARCHIVE_MODE=cls python scripts/collect.py

# Endfield account (山团团):
KEEP_RECENT=999 ARCHIVE_MODE=endfield OUTPUT_PREFIX=endfield python scripts/collect.py
```

On PowerShell use `$env:KEEP_RECENT = "999"` before invoking the script. Cursor behavior on
partial runs: if the API fails completely before any dynamics are fetched (zero items), the run
aborts and the cursor is left untouched. Otherwise the cursor advances to the **newest** processed
dynamic, even when pagination hit the page cap before reaching the previous cursor — so a stalled/
capped run will skip back-window items that were never scanned. Use `DRY_RUN=1` to exercise the
full pipeline without changing R2; for a true full re-scan, delete the cursor first (see below).

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

**Dual-account full scan:** each account has its own cursor under its prefix. For ak delete
`config/last-dynamic-id.json`; for ef delete `endfield/config/last-dynamic-id.json` (same snippet,
Key = `'endfield/config/last-dynamic-id.json'`), then run with the account's `ARCHIVE_MODE`/`OUTPUT_PREFIX`
(`ARCHIVE_MODE=endfield OUTPUT_PREFIX=endfield KEEP_RECENT=999`).

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

After collection, verify R2 state — counts, and (when the sha256 field is
present) per-image integrity. Each image is verified against the local original
cache first (`local_archive/cache/`), falling back to the R2 `originals/` copy
if it was published. Any SHA-256 mismatch aborts with a non-zero exit code.

```bash
python -c "
import hashlib, json, pathlib, boto3, os
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

cache = pathlib.Path(os.environ.get('LOCAL_ARCHIVE_DIR', 'local_archive')) / 'cache'
checked = 0
for d in idx['dynamics']:
    for img in d.get('images', []):
        want = img.get('sha256', '')
        if not want:
            continue
        n = img.get('index', 0)
        hits = sorted(cache.glob(f'{want}-{d[\"id\"]}-{n}.*')) if cache.is_dir() else []
        if hits:
            got = hashlib.sha256(hits[0].read_bytes()).hexdigest()
        elif img.get('originalKey'):
            body = s3.get_object(Bucket=os.environ['R2_BUCKET'], Key=img['originalKey'])['Body'].read()
            got = hashlib.sha256(body).hexdigest()
        else:
            print(f'  SKIP (no local cache / originals): {d[\"id\"]} img {n}')
            continue
        checked += 1
        if got != want:
            raise SystemExit(f'SHA-256 MISMATCH for {d[\"id\"]} img {n}: {got} != {want}')
print(f'Verified sha256 for {checked} images')
print('OK')
"
```

### Original Bytes, sha256 & Copyright Decision

For every newly downloaded image `collect.py` records its **SHA-256 digest of the
raw bytes before compression** and stores `sha256` / `originalSize` /
`originalExt` in `index.json`'s `images[]`. The original (uncompressed) bytes are
always kept in the **local cache** (`local_archive/cache/{sha256}-{id}-{n}.{ext}`),
which doubles as a content-addressed backup.

**Publishing originals to R2 is OFF by default.** The public bucket must not
redistribute third-party artwork, so automatic collection never uploads
`originals/` unless `R2_UPLOAD_ORIGINALS=1` is set explicitly (recommended only
for a private bucket). The local cache is the primary anti-loss fallback.

To backfill `originals/` for historically archived images from the local cache:

```bash
python scripts/backfill_originals.py                 # both accounts
python scripts/backfill_originals.py --accounts=ak   # 朝陇山 only
python scripts/backfill_originals.py --accounts=ef   # 山团团 only
python scripts/backfill_originals.py --limit=20      # cap images processed
python scripts/backfill_originals.py --dry-run       # preview without writing
```

**Prerequisite:** this needs the local original cache (`local_archive/cache/`,
written by `collect.py`). If it is absent (e.g. a machine that never ran a full
collection), the script warns and can only backfill images whose `originalKey`
was already recorded. It is idempotent — images that already carry an
`originalKey` are skipped — and updates the index JSON + manifest object list
after completion.

### Small Thumb Backfill

Grid tiles lazily load an 1/8-resolution `smthumbs/` image; the full original is
fetched only when the lightbox is opened. New collections generate these
automatically, but for images archived before this feature you can backfill:

```bash
python scripts/backfill_small_thumbs.py                 # both accounts
python scripts/backfill_small_thumbs.py --accounts=ef   # 山团团 only
python scripts/backfill_small_thumbs.py --accounts=ak   # 朝陇山 only
python scripts/backfill_small_thumbs.py --dry-run       # preview without writing
```

The script reads source bytes from the local compressed mirror
(`local_archive/images/...`) when available and falls back to R2. It is
idempotent — images that already have a valid `smallThumbKey` are skipped — and
updates the index JSON + manifest object list after completion.
