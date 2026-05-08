# Bilibili Dynamic Archive

Static archive site for Bilibili user dynamics images.  
Cloudflare Pages + GitHub Actions + Cloudflare R2. No Workers.

## URLs

- Site: https://cls.nslc.top/
- R2: https://cls.r2.nsapi.top/

## Stack

- **Frontend**: Vanilla HTML/CSS/JS, deployed to Cloudflare Pages
- **Storage**: Cloudflare R2 (images + JSON indexes)
- **Collection**: Python script via GitHub Actions (schedule + manual trigger)
- **Image compression**: 1/3 horizontal downsample, q35 progressive JPEG, browser stretch back 3x

## R2 Layout

```
/site/index.json
/site/search-index.json
/manifests/current.json
/manifests/previous.json
/images/{dynamicId}/{n}.jpg
```

## Local Testing

```bash
# Set env vars (see GitHub Secrets)
export R2_ACCESS_KEY_ID="..."
export R2_SECRET_ACCESS_KEY="..."
export R2_BUCKET="cls-page-data"
export R2_ACCOUNT_ID="6a63f05750cd3b14d3c335f6cb6af793"
export BILIBILI_COOKIE="..."
export BILIBILI_UID="3546864272017883"
export KEEP_RECENT="10"

pip install -r scripts/requirements.txt
python scripts/collect.py
```

## Manual Trigger

GitHub repo → Actions → Collect Bilibili Dynamics → Run workflow.

The workflow also runs daily at 13:00 Beijing time (05:00 UTC).
