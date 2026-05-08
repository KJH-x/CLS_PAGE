# Bilibili Dynamic Archive

A static website on **Cloudflare Pages** that archives image-heavy Bilibili user dynamics.
Collected by **GitHub Actions** on schedule, stored in **Cloudflare R2**.
**No Workers. No server-side database. No images in git.**

---

## Architecture

```
  Bilibili API
       |
       v
  GitHub Actions (collect.py, cron)
       |
       v   upload compressed images + JSON
  Cloudflare R2  ----------------------+
       |                               |
       v   fetch JSON & images at runtime
  Cloudflare Pages (static site) ------+
```

- Repository contains only frontend code, collection script, and workflow — no images.
- R2 stores index.json, search-index.json, manifests, and compressed images.
- Pages serves a vanilla HTML/CSS/JS site that reads everything directly from R2.

## R2 Directory Layout

```
/site/index.json                     -- Full data for the frontend
/site/search-index.json              -- Lightweight searchable index
/manifests/current.json              -- Object manifest of latest run
/manifests/previous.json             -- Backup manifest (for rollback)
/images/{dynamicId}/{0,1,2...}.jpg   -- Compressed narrow images
```

## Prerequisites

- A **Cloudflare account** with R2 enabled
- A **GitHub account**
- A **Bilibili account** (to obtain the cookie for API access)
- The target **Bilibili UID** (the user whose dynamics to archive)

---

## 1. Cloudflare R2 Setup

### 1.1 Create a Bucket

1. Go to Cloudflare Dashboard → **R2** → **Create bucket**.
2. Name it (e.g. `bilibili-archive`).
3. Choose a region hint if desired.
4. Click **Create bucket**.

### 1.2 Configure CORS

Go to your bucket → **Settings** → **CORS Policy** → **Add CORS policy**:

```json
[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

If you want to restrict origins, replace `"*"` with your Pages domain (e.g. `https://your-site.pages.dev`).

### 1.3 Get R2 Credentials

1. Go to **R2** → **Manage R2 API Tokens** → **Create API token**.
2. Permissions: **Object Read & Write**.
3. Choose your bucket under "Specify bucket(s)".
4. Copy and save:
   - **Access Key ID**
   - **Secret Access Key**
   - **Account ID** (from the Cloudflare dashboard URL: `https://dash.cloudflare.com/{ACCOUNT_ID}`)

### 1.4 Public Access

Configure a custom domain or use the r2.dev subdomain:

- Go to your bucket → **Settings** → **Public access** → **Custom Domains**.
- Add a domain (e.g. `r2.nsapi.top`) and follow the DNS instructions.
- OR enable **R2.dev subdomain** for testing (note: rate-limited, not recommended for production).
- The **R2 public URL** is what you will use in the site config (e.g. `https://r2.nsapi.top`).

---

## 2. GitHub Secrets

Go to your GitHub repository → **Settings** → **Secrets and variables** → **Actions**.

Add the following **Repository secrets**:

| Secret Name              | Description                                      |
|--------------------------|--------------------------------------------------|
| `R2_ACCESS_KEY_ID`       | R2 API token Access Key ID                       |
| `R2_SECRET_ACCESS_KEY`   | R2 API token Secret Access Key                   |
| `R2_BUCKET`              | R2 bucket name (e.g. `bilibili-archive`)         |
| `R2_ACCOUNT_ID`          | Cloudflare Account ID                            |
| `BILIBILI_COOKIE`        | Full cookie string for Bilibili API access       |
| `BILIBILI_UID`           | Target Bilibili user ID (e.g. `3546864272017883`)|

Optionally, add a **Repository variable**:

| Variable       | Value |
|----------------|-------|
| `KEEP_RECENT`  | `10` (number of recent dynamics to keep) |

### Getting the Bilibili Cookie

1. Open Chrome/Firefox and log into [bilibili.com](https://www.bilibili.com).
2. Visit the target user's dynamic page: `https://space.bilibili.com/{UID}/dynamic`.
3. Open DevTools (F12) → **Network** tab.
4. Refresh the page.
5. Find a request to `api.bilibili.com` (e.g. `feed/space`).
6. In the request headers, copy the full **Cookie** value.
7. Store the entire string as the `BILIBILI_COOKIE` secret.

**Note:** The cookie will expire periodically. When collection starts failing with code `-352`, update the secret with a fresh cookie.

---

## 3. Configure the Site

Edit `site/js/config.js` and set your R2 public URL:

```js
window.ARCHIVE_CONFIG = {
  R2_PUBLIC_URL: "https://r2.nsapi.top",
};
```

---

## 4. Cloudflare Pages Deployment

### 4.1 Connect Repository

1. Go to Cloudflare Dashboard → **Workers & Pages** → **Pages**.
2. Click **Create a project** → **Connect to Git**.
3. Select your GitHub repository.
4. Configure build settings:
   - **Build command**: (leave empty)
   - **Build output directory**: `site`
   - **Root directory**: (leave empty)
5. Click **Save and Deploy**.

The site will deploy from the `site/` folder. No build step is needed.

### 4.2 (Optional) Custom Domain

In Pages project → **Custom domains**, add your domain (e.g. `archive.example.com`).

Don't forget to update the CORS AllowedOrigins in R2 to include your custom domain.

---

## 5. Data Structures

### index.json (for frontend)

```json
{
  "generatedAt": "2025-01-01T08:00:00+00:00",
  "keepRecent": 10,
  "totalDynamics": 10,
  "totalImages": 45,
  "dynamics": [
    {
      "id": "9378123456789",
      "timestamp": 1704067200,
      "date": "2024-01-01",
      "text": "First line text without tags",
      "fullText": "Complete dynamic text...",
      "bilibiliUrl": "https://t.bilibili.com/9378123456789",
      "tags": ["tag1", "tag2"],
      "imageCount": 3,
      "images": [
        {
          "index": 0,
          "r2Key": "images/9378123456789/0.jpg",
          "originalWidth": 1080,
          "originalHeight": 2400,
          "storedWidth": 360,
          "storedHeight": 2400,
          "displayWidthScale": 3,
          "compressionMode": "horizontal-downsample-x0333-q35"
        }
      ]
    }
  ]
}
```

### search-index.json (for search)

```json
[
  {
    "dynamicId": "9378123456789",
    "text": "First line text",
    "date": "2024-01-01",
    "tags": ["tag1", "tag2"],
    "imageCount": 3
  }
]
```

### manifests/current.json (for rollback & cleanup)

```json
{
  "generatedAt": "2025-01-01T08:00:00+00:00",
  "keepRecent": 10,
  "totalImages": 45,
  "dynamics": ["9378123456789", "..."],
  "objects": [
    "images/9378123456789/0.jpg",
    "manifests/current.json",
    "site/index.json",
    "site/search-index.json"
  ]
}
```

---

## 6. Image Compression Strategy

Images are compressed using **horizontal downsampling + browser horizontal backfill**:

1. Open JPEG, convert to RGB.
2. Record original dimensions.
3. Resize width to **1/3** of original, height unchanged (LANCZOS resampling).
4. Save as JPEG: quality=35, optimize=True, progressive=True, subsampling="4:2:0".
5. Store metadata (originalWidth, originalHeight, storedWidth, storedHeight, displayWidthScale=3).

The **frontend stretches** the narrow stored image horizontally by 3x, restoring the original aspect ratio visually. R2 only stores the narrow images.

---

## 7. Rolling Retention

- `KEEP_RECENT` (default 10) controls how many recent image-containing dynamics are kept.
- Each run: the script selects the top KEEP_RECENT dynamics (by timestamp), generates new indexes, and uploads.
- After successful upload, it compares the new manifest with the previous one and deletes R2 objects that are no longer referenced.
- If any upload fails, the old `index.json` and manifest remain untouched — the site stays functional.

---

## 8. Manual Trigger

Go to your GitHub repository → **Actions** → **Collect Bilibili Dynamics** → **Run workflow**.

The workflow also runs on schedule (daily at 13:00 Beijing time = 05:00 UTC).

---

## 9. Local Testing

### 9.1 Test the Collection Script

```bash
# Set environment variables
export R2_ACCESS_KEY_ID="xxx"
export R2_SECRET_ACCESS_KEY="xxx"
export R2_BUCKET="bilibili-archive"
export R2_ACCOUNT_ID="xxx"
export BILIBILI_COOKIE="xxx"
export BILIBILI_UID="3546864272017883"
export KEEP_RECENT="10"

# Install dependencies
pip install -r scripts/requirements.txt

# Run the script
python scripts/collect.py
```

### 9.2 Test the Frontend Locally

```bash
# Serve the site directory with any static server
python -m http.server 8080 -d site
# Or: npx serve site
```

The frontend will fetch data from R2 at the configured `R2_PUBLIC_URL`.
Make sure CORS is properly configured on the R2 bucket.

---

## 10. Troubleshooting

| Symptom | Possible Cause | Fix |
|---------|---------------|-----|
| API returns code -352 | Cookie expired or captcha | Refresh cookie in GitHub Secrets |
| No dynamics collected | UID has no image dynamics recently | Check UID manually on Bilibili |
| Frontend shows "Could not load" | CORS not configured | Check R2 CORS settings |
| Images don't load in lightbox | R2 public URL wrong | Verify `R2_PUBLIC_URL` in `config.js` |
| Stale images remain in R2 | Deletion skipped due to upload failure | Re-run workflow |
| Pages deployment 404 | Build output dir wrong | Set to `site` in Pages settings |

---

## 11. Customisation

- **Schedule**: Edit the `cron` line in `.github/workflows/collect.yml`.
- **Retention**: Set `KEEP_RECENT` via GitHub repository variable.
- **Tile height**: Change `--tile-height` in `site/css/style.css`.
- **Compression quality**: Edit `JPEG_QUALITY` in `scripts/collect.py`.
- **Bilibili UID**: Set via `BILIBILI_UID` GitHub secret.

---

## License

MIT
