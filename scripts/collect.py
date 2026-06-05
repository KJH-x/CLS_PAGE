#!/usr/bin/env python3
"""Bilibili Dynamic Image Archiver — collects, compresses, and uploads to Cloudflare R2.

Data flow:
  1. Load manifest from R2 (current.json) as old baseline
  2. Fetch Bilibili user dynamics via API (paginated)
  3. Extract dynamics that contain images
  4. Download original images, compress (1/3 width, q35 JPEG), upload to R2
  5. Generate index.json + search-index.json + new manifest
  6. Upload manifests/current.json, then delete stale R2 objects from old manifest

On failure before step 6 completes, the previous index.json remains intact → no broken site.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
import requests
from botocore.config import Config
from PIL import Image, UnidentifiedImageError

# ---------------------------------------------------------------------------
# Runtime configuration (overridden in main for real runs)
# ---------------------------------------------------------------------------

_R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY_ID", "")
_R2_SECRET_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
_R2_BUCKET = os.environ.get("R2_BUCKET", "")
_R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
_BILI_COOKIE = os.environ.get("BILIBILI_COOKIE", "")
_BILI_UID = os.environ.get("BILIBILI_UID", "")
_KEEP_RECENT = int(os.environ.get("KEEP_RECENT", "10"))
_OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "")
_DRY_RUN = os.environ.get("DRY_RUN", "") != ""
_MIN_DEDUP = int(os.environ.get("EXPECTED_MIN_DEDUP_DYNAMICS", "0"))
_MIN_SURVIVED = int(os.environ.get("EXPECTED_MIN_SURVIVED_DYNAMICS", "0"))
_MAX_ALL_SKIPPED = int(os.environ.get("EXPECTED_MAX_ALL_IMAGE_SKIPPED", "999"))
_MIN_IMAGES = int(os.environ.get("EXPECTED_MIN_IMAGES", "0"))

DISPLAY_WIDTH_SCALE = 1  # no horizontal downsampling
JPEG_QUALITY = 35
THUMB_QUALITY = 40
THUMB_SCALE = 2  # thumbnail = 1/2 original width & height
MAX_API_PAGES = 20

MANIFEST_CURRENT_KEY = "manifests/current.json"
MANIFEST_PREVIOUS_KEY = "manifests/previous.json"
INDEX_KEY = "site/index.json"
SEARCH_INDEX_KEY = "site/search-index.json"
LAST_ID_KEY = "config/last-dynamic-id.json"

def _pk(key: str) -> str:
    """Apply output prefix if set, otherwise return key unchanged."""
    return f"{_OUTPUT_PREFIX.rstrip('/')}/{key}" if _OUTPUT_PREFIX else key

# Private R2 client — lazy init
_s3 = None


def _get_s3():
    global _s3
    if _s3 is None:
        endpoint = f"https://{_R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        _s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=_R2_ACCESS_KEY,
            aws_secret_access_key=_R2_SECRET_KEY,
            config=Config(region_name="auto", retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _s3


def _bili_headers() -> dict[str, str]:
    h = dict(BILI_HEADERS_TEMPLATE)
    h["Referer"] = f"https://space.bilibili.com/{_BILI_UID}/dynamic"
    if _BILI_COOKIE:
        h["Cookie"] = _BILI_COOKIE
    return h

# ---------------------------------------------------------------------------
# R2 helpers
# ---------------------------------------------------------------------------

def r2_get_json(key: str) -> Optional[dict]:
    """Fetch and parse a JSON object from R2. Returns None if not found."""
    s3 = _get_s3()
    try:
        resp = s3.get_object(Bucket=_R2_BUCKET, Key=key)
        return json.loads(resp["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as exc:
        log(f"  WARN: Failed to read {key}: {exc}")
        return None


def r2_put_json(key: str, data: Any, cache_max_age: int = 300) -> None:
    """Upload a JSON-serialisable object to R2."""
    s3 = _get_s3()
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=_R2_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
        CacheControl=f"public, max-age={cache_max_age}",
    )


def r2_put_image(key: str, data: bytes) -> None:
    """Upload a JPEG image to R2 with long-lived cache."""
    s3 = _get_s3()
    s3.put_object(
        Bucket=_R2_BUCKET,
        Key=key,
        Body=data,
        ContentType="image/jpeg",
        CacheControl="public, max-age=31536000, immutable",
    )


def r2_delete(key: str) -> bool:
    """Delete a single object from R2. Returns True on success."""
    s3 = _get_s3()
    try:
        s3.delete_object(Bucket=_R2_BUCKET, Key=key)
        return True
    except Exception as exc:
        log(f"    WARN: Failed to delete {key}: {exc}")
        return False


def r2_get_bytes(key: str) -> Optional[bytes]:
    s3 = _get_s3()
    try:
        resp = s3.get_object(Bucket=_R2_BUCKET, Key=key)
        return resp["Body"].read()
    except Exception:
        return None


def get_image_size(data: bytes) -> tuple[int, int]:
    try:
        img = Image.open(io.BytesIO(data))
        return img.size
    except Exception:
        return 0, 0


def r2_list_all() -> dict[str, int]:
    """List R2 state — returns {prefix: object_count}."""
    s3 = _get_s3()
    prefixes: dict[str, int] = {}
    total = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_R2_BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            total += 1
            # Group by top-level prefix
            prefix = key.split("/")[0] if "/" in key else "(root)"
            prefixes[prefix] = prefixes.get(prefix, 0) + 1
    prefixes["(total)"] = total
    return prefixes


def r2_head(key: str) -> bool:
    """Check if an object exists in R2."""
    s3 = _get_s3()
    try:
        s3.head_object(Bucket=_R2_BUCKET, Key=key)
        return True
    except Exception:
        return False


def r2_get_last_id() -> Optional[str]:
    """Read the last processed dynamic ID from R2."""
    data = r2_get_json(LAST_ID_KEY)
    return data.get("lastDynamicId") if data else None


def r2_put_last_id(dyn_id: str) -> None:
    """Store the last processed dynamic ID on R2."""
    r2_put_json(LAST_ID_KEY, {"lastDynamicId": dyn_id})


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Bilibili API
# ---------------------------------------------------------------------------

BILI_HEADERS_TEMPLATE: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def fetch_dynamics(last_id: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
    """Paginate through the user's dynamic feed. If last_id is provided, stop
    when that ID is encountered (incremental mode).
    Returns (items, newest_id) where newest_id is the first item's ID."""
    all_items: list[dict] = []
    newest_id: Optional[str] = None
    offset = ""
    page = 0

    while page < MAX_API_PAGES:
        page += 1
        params = f"host_mid={_BILI_UID}&offset={offset}&features=itemOpusStyle"
        url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?{params}"
        log(f"  Page {page}  offset={offset[:32] if offset else '(initial)'}")

        try:
            resp = requests.get(url, headers=_bili_headers(), timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            log(f"  ERROR: API request failed: {exc}")
            break

        code = data.get("code")
        if code != 0:
            log(f"  API returned code={code} message={data.get('message', '')}")
            if code == -352:
                log("  ERROR: Captcha / risk-control triggered — cookie may be invalid.")
            break

        page_data = data.get("data", {})
        items = page_data.get("items") or []
        if not items:
            log("  No more items.")
            break

        # Check for last_id → stop when we hit already-processed content
        stop_early = False
        for item in items:
            item_id = item.get("id_str", "")
            if last_id and item_id == last_id:
                log(f"  Hit last_id={last_id[:16]} — stopping incremental fetch.")
                stop_early = True
                break
            all_items.append(item)
            if newest_id is None:
                newest_id = item_id

        if stop_early:
            break

        has_more = page_data.get("has_more", False)
        if not has_more:
            log("  has_more=false, stopping pagination.")
            break

        next_offset = page_data.get("offset", "")
        if not next_offset or next_offset == offset:
            log("  Offset did not advance, stopping.")
            break
        offset = next_offset

    return all_items, newest_id


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"#(\S+?)#")

# Only match dynamics with title pattern: 〓朝陇山{date}｜{name}〓上新
_TITLE_PATTERN = re.compile(r"〓朝陇山\s*\d{1,2}\s*[A-Z][a-z]+\.?\s*[｜|].*〓(上新|余量上架|复刻上新)")


def extract_tags(text: str) -> list[str]:
    return _TAG_RE.findall(text)


def strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def extract_dynamic(item: dict) -> Optional[dict]:
    """Convert a raw Bilibili dynamic item into our internal format.
    Returns None if the dynamic has no usable images or doesn't match filters."""
    if item.get("orig"):
        return None

    modules = item.get("modules") or {}
    mod_dyn = modules.get("module_dynamic") or {}
    mod_auth = modules.get("module_author") or {}

    dyn_id = item.get("id_str", "")
    if not dyn_id:
        return None

    pub_ts = mod_auth.get("pub_ts", 0)
    pub_time = mod_auth.get("pub_time", "")
    if not pub_ts:
        return None

    major = mod_dyn.get("major") or {}
    major_type = major.get("type", "")

    image_urls: list[str] = []
    search_text = ""

    if major_type == "MAJOR_TYPE_DRAW":
        draw = major.get("draw") or {}
        for it in draw.get("items", []):
            src = it.get("src", "")
            if src:
                image_urls.append({"url": src, "width": it.get("width", 0), "height": it.get("height", 0)})

    elif major_type == "MAJOR_TYPE_OPUS":
        opus = major.get("opus") or {}
        for pic in opus.get("pics", []):
            url = pic.get("url", "")
            if url:
                image_urls.append({"url": url, "width": pic.get("width", 0), "height": pic.get("height", 0)})
        # Text lives in summary.text; fallback to title
        summary = opus.get("summary") or {}
        search_text = (opus.get("title", "") + " " + summary.get("text", "")).strip()

    if not image_urls:
        return None

    # Category detection: two patterns
    #   1) 〓朝陇山{date}｜{name}〓{上新|余量上架}
    #   2) Plain text with #余量上架# hashtag (no 〓 wrapper)
    title_match = _TITLE_PATTERN.search(search_text)
    if title_match:
        category = title_match.group(1)
    elif "余量上架" in search_text.replace("#", ""):
        category = "余量上架"
    else:
        return None

    # Title: for 〓 pattern, extract the matching line; for 余量上架, use first line
    title = ""
    if category == "上新" or (category == "余量上架" and _TITLE_PATTERN.search(search_text)):
        # Has 〓 pattern — find that line
        for line in search_text.split("\n"):
            line = line.strip()
            if _TITLE_PATTERN.search(line):
                title = strip_tags(line).strip()
                break
        if not title:
            for line in search_text.split("\n"):
                line = line.strip()
                if "〓" in line:
                    title = strip_tags(line).strip()
                    break
    if not title:
        # Fallback: first non-empty, non-hashtag-only line
        for line in search_text.split("\n"):
            line = line.strip()
            cleaned = strip_tags(line)
            if cleaned:
                title = cleaned
                break
    title = re.sub(r"^互动抽奖\s*", "", title).strip()
    if not title:
        title = "(no title)"

    tags = extract_tags(search_text)

    return {
        "id": dyn_id,
        "timestamp": pub_ts,
        "date": pub_time,
        "text": title,
        "fullText": search_text,
        "bilibiliUrl": f"https://t.bilibili.com/{dyn_id}",
        "tags": tags,
        "category": category,
        "imageUrls": image_urls,
    }


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------

IMG_DL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


def download_image(url: str) -> Optional[bytes]:
    """Download raw image bytes from the given URL."""
    clean_url = re.sub(r"@\d+w.*$", "", url)  # strip Bilibili size suffix for max res
    try:
        resp = requests.get(clean_url, headers=IMG_DL_HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        log(f"      Download error: {exc}")
        return None


def compress_image(raw: bytes) -> Optional[tuple[bytes, dict]]:
    """Compress a JPEG image: convert to RGB, save progressive q35 at original dimensions.
    Returns (compressed_bytes, metadata_dict) or None on failure."""
    try:
        img = Image.open(io.BytesIO(raw))
        original_w, original_h = img.size

        if img.mode != "RGB":
            img = img.convert("RGB")

        # No resize — store at original dimensions

        buf = io.BytesIO()
        img.save(
            buf,
            format="JPEG",
            quality=JPEG_QUALITY,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
        )
        compressed = buf.getvalue()

        meta = {
            "originalWidth": original_w,
            "originalHeight": original_h,
            "storedWidth": original_w,
            "storedHeight": original_h,
            "displayWidthScale": 1,
            "compressionMode": "original-q35",
        }
        return compressed, meta

    except UnidentifiedImageError:
        log("      Not a recognised image format, skipping.")
        return None
    except Exception as exc:
        log(f"      Compression error: {exc}")
        traceback.print_exc()
        return None


def make_thumbnail(raw: bytes) -> Optional[bytes]:
    """Generate thumbnail at 1/2 original dimensions."""
    try:
        img = Image.open(io.BytesIO(raw))
        orig_w, orig_h = img.size
        thumb_w = max(1, orig_w // THUMB_SCALE)
        thumb_h = max(1, orig_h // THUMB_SCALE)

        if img.mode != "RGB":
            img = img.convert("RGB")

        img = img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True, progressive=True, subsampling="4:2:0")
        return buf.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    log("=" * 60)
    log("Bilibili Dynamic Archiver — Cloudflare R2 edition")
    log(f"  UID        : {_BILI_UID}")
    log(f"  KEEP_RECENT: {_KEEP_RECENT}")
    log(f"  R2 bucket  : {_R2_BUCKET}")
    log("=" * 60)

    # ---- 0. Check R2 state ---------------------------------------------------
    log("[Step 0] Checking R2 state...")
    r2_state = r2_list_all()
    for prefix, count in sorted(r2_state.items()):
        log(f"  {prefix}: {count} objects")
    last_id = r2_get_last_id()
    if last_id:
        log(f"  Last processed dynamic ID: {last_id[:16]}... (incremental mode)")
    else:
        log("  No last-dynamic-id found (full fetch)")

    # ---- 1. Load previous state ---------------------------------------------
    log("[Step 1] Loading previous state...")
    old_manifest = r2_get_json(MANIFEST_CURRENT_KEY)
    old_objects: set[str] = set()
    old_index: Optional[dict] = None
    if old_manifest:
        old_objects = set(old_manifest.get("objects", []))
        log(f"  Found previous manifest with {len(old_objects)} objects.")

    old_index = r2_get_json(INDEX_KEY)
    old_dynamics_map: dict[str, dict] = {}
    old_images_map: dict[str, dict] = {}
    if old_index:
        for od in old_index.get("dynamics", []):
            old_dynamics_map[od.get("id", "")] = od
            for oi in od.get("images", []):
                old_images_map[oi.get("r2Key", "")] = oi
        log(f"  Loaded old index with {len(old_dynamics_map)} dynamics for metadata recovery.")
    else:
        log("  No previous index.json (first run).")

    # ---- 2. Fetch dynamics ---------------------------------------------------
    log("[Step 2] Fetching Bilibili dynamics...")
    raw_items, newest_id = fetch_dynamics(last_id)
    log(f"  Fetched {len(raw_items)} raw items total.")
    if newest_id:
        log(f"  Newest dynamic ID: {newest_id[:16]}...")

    # ---- 3. Extract candidates -----------------------------------------------
    log("[Step 3] Extracting dynamics with images...")
    candidates: list[dict] = []
    for item in raw_items:
        info = extract_dynamic(item)
        if info:
            candidates.append(info)

    candidates.sort(key=lambda d: d["timestamp"])  # oldest first for dedup
    _ACTIVITY_RE = re.compile(r"[｜|](.+?)〓")
    seen_activities: set[tuple] = set()
    deduped: list[dict] = []
    for c in candidates:
        m = _ACTIVITY_RE.search(c["text"])
        if m:
            act_name = m.group(1).strip()
        else:
            # For non-〓 format (余量上架), use first 40 chars of title as key
            act_name = c["text"][:40].strip()
        key = (act_name, c.get("category", ""))
        if key not in seen_activities:
            seen_activities.add(key)
            deduped.append(c)
    log(f"  After dedup (by activity+category, kept earliest): {len(deduped)} unique")
    deduped.sort(key=lambda d: d["timestamp"], reverse=True)

    # Merge with old dynamics: keep existing ones not refreshed by this fetch.
    # Convert old format (has "images" with full metadata) to new candidate format
    # so they can be processed without re-downloading.
    merged_ids = {d["id"] for d in deduped}
    for od in old_dynamics_map.values():
        if od.get("id") not in merged_ids:
            old_imgs = od.get("images", [])
            od["imageUrls"] = [
                {"url": "", "width": img.get("originalWidth", 0), "height": img.get("originalHeight", 0)}
                for img in old_imgs
            ]
            od["_oldImages"] = {img["r2Key"]: img for img in old_imgs if img.get("r2Key")}
            deduped.append(od)

    deduped.sort(key=lambda d: d["timestamp"], reverse=True)
    selected = deduped[:_KEEP_RECENT]
    log(f"  Newly extracted: {len(candidates)}, merged from old index: {len(deduped) - len(candidates)}")
    log(f"  Selected (top {_KEEP_RECENT}): {len(selected)}")

    if not selected:
        log("WARNING: No image-containing dynamics found. Aborting to preserve existing index.")
        sys.exit(0)

    # ---- 4. Download, compress & upload images --------------------------------
    log("[Step 4] Processing images...")
    dynamics_data: list[dict] = []
    tracked_objects: set[str] = {
        INDEX_KEY,
        SEARCH_INDEX_KEY,
        MANIFEST_CURRENT_KEY,
        MANIFEST_PREVIOUS_KEY,
        LAST_ID_KEY,
    }
    stats_new = 0
    stats_fail = 0
    stats_recovered = 0

    for dyn in selected:
        images: list[dict] = []
        for idx, img_info in enumerate(dyn["imageUrls"]):
            img_url = img_info["url"]
            api_w = img_info.get("width", 0)
            api_h = img_info.get("height", 0)
            r2_key = f"images/{dyn['id']}/{idx}.jpg"

            # Skip square images (e.g. 500x500 icons) and banners (aspect > 0.3)
            if api_w > 0 and api_h > 0:
                if api_w == api_h:
                    log(f"  [{dyn['id'][:16]}] img {idx} — square ({api_w}x{api_h}), skipped")
                    continue
                if api_w / api_h >= 0.3:
                    log(f"  [{dyn['id'][:16]}] img {idx} — banner ({api_w}x{api_h}, ratio={api_w/api_h:.2f}), skipped")
                    continue

            # If image already exists in R2, recover metadata from old index
            if r2_key in old_objects and r2_key in old_images_map:
                old_meta = old_images_map[r2_key]
                meta = {
                    "index": idx,
                    "r2Key": r2_key,
                    "thumbnailKey": old_meta.get("thumbnailKey", ""),
                    "originalWidth": old_meta.get("originalWidth", 0),
                    "originalHeight": old_meta.get("originalHeight", 0),
                    "storedWidth": old_meta.get("storedWidth", 0),
                    "storedHeight": old_meta.get("storedHeight", 0),
                    "displayWidthScale": old_meta.get("displayWidthScale", DISPLAY_WIDTH_SCALE),
                    "compressionMode": old_meta.get("compressionMode", "original-q35"),
                }
                images.append(meta)
                tracked_objects.add(r2_key)
                if meta["thumbnailKey"]:
                    tracked_objects.add(meta["thumbnailKey"])
                stats_recovered += 1
                log(f"  [{dyn['id'][:16]}] img {idx} — recovered from old index")
                continue

            log(f"  [{dyn['id'][:16]}] img {idx+1}/{len(dyn['imageUrls'])} — processing...")

            # For merged old dynamics: recover metadata directly without downloading
            old_meta = dyn.get("_oldImages", {}).get(r2_key)
            if old_meta:
                old_meta["index"] = idx
                old_meta["r2Key"] = r2_key
                images.append(old_meta)
                tracked_objects.add(r2_key)
                tk = old_meta.get("thumbnailKey", "")
                if tk:
                    tracked_objects.add(tk)
                stats_recovered += 1
                log(f"    Recovered from old index metadata")
                continue

            # Check if image already exists in R2 — recover actual dimensions
            if r2_head(r2_key):
                existing_bytes = r2_get_bytes(r2_key)
                stored_w, stored_h = get_image_size(existing_bytes or b"")
                if stored_w <= 0 or stored_h <= 0:
                    stored_w, stored_h = api_w, api_h
                thumb_key = f"thumbs/{dyn['id']}/{idx}.jpg"
                thumb_exists = r2_head(thumb_key)
                meta = {
                    "index": idx,
                    "r2Key": r2_key,
                    "thumbnailKey": thumb_key if thumb_exists else "",
                    "originalWidth": max(api_w, stored_w),
                    "originalHeight": max(api_h, stored_h),
                    "storedWidth": stored_w,
                    "storedHeight": stored_h,
                    "displayWidthScale": DISPLAY_WIDTH_SCALE,
                    "compressionMode": "recovered-from-r2",
                }
                images.append(meta)
                tracked_objects.add(r2_key)
                if meta["thumbnailKey"]:
                    tracked_objects.add(meta["thumbnailKey"])
                stats_recovered += 1
                log(f"    Recovered from R2 ({stored_w}x{stored_h})")
                continue

            raw = download_image(img_url)
            if not raw:
                stats_fail += 1
                continue

            result = compress_image(raw)
            if not result:
                stats_fail += 1
                continue

            compressed, meta = result
            meta["index"] = idx
            meta["r2Key"] = r2_key

            # Generate and upload thumbnail
            thumb_key = f"thumbs/{dyn['id']}/{idx}.jpg"
            thumb_data = make_thumbnail(raw)
            if thumb_data:
                r2_put_image(thumb_key, thumb_data)
                meta["thumbnailKey"] = thumb_key
                tracked_objects.add(thumb_key)
                log(f"    Thumbnail: {thumb_key}")
            else:
                meta["thumbnailKey"] = ""

            r2_put_image(r2_key, compressed)
            images.append(meta)
            tracked_objects.add(r2_key)
            stats_new += 1

            log(f"    Compressed: {meta['originalWidth']}x{meta['originalHeight']}"
                f" -> {meta['storedWidth']}x{meta['storedHeight']}"
                f"  ({len(compressed):,} bytes)")

        if not images:
            # Skip dynamics where all images failed
            log(f"  SKIP [{dyn['id'][:16]}] — all images failed, removing from archive")
            continue

        # Re-number indices sequentially after square filtering
        for new_idx, img in enumerate(images):
            img["index"] = new_idx

        dyn_entry = {
            "id": dyn["id"],
            "timestamp": dyn["timestamp"],
            "date": dyn["date"],
            "text": dyn["text"],
            "fullText": dyn["fullText"],
            "bilibiliUrl": dyn["bilibiliUrl"],
            "tags": dyn["tags"],
            "category": dyn.get("category", ""),
            "imageCount": len(images),
            "images": images,
        }
        dynamics_data.append(dyn_entry)

    log(f"  Image summary: {stats_new} new, {stats_recovered} recovered, {stats_fail} failed")

    if not dynamics_data:
        log("WARNING: No dynamics with valid images after processing. Aborting to preserve existing index.")
        sys.exit(0)

    all_image_skipped = len([d for d in selected if d["id"] not in {e["id"] for e in dynamics_data}])
    total_imgs = sum(d["imageCount"] for d in dynamics_data)

    if _MIN_DEDUP and len(selected) < _MIN_DEDUP:
        log(f"ABORT: dedup dynamics {len(selected)} < {_MIN_DEDUP}")
        sys.exit(1)
    if _MAX_ALL_SKIPPED < 999 and all_image_skipped > _MAX_ALL_SKIPPED:
        log(f"ABORT: too many all-image-skipped dynamics: {all_image_skipped} > {_MAX_ALL_SKIPPED}")
        sys.exit(1)
    if _MIN_SURVIVED and len(dynamics_data) < _MIN_SURVIVED:
        log(f"ABORT: survived dynamics {len(dynamics_data)} < {_MIN_SURVIVED}")
        sys.exit(1)
    if _MIN_IMAGES and total_imgs < _MIN_IMAGES:
        log(f"ABORT: image count {total_imgs} < {_MIN_IMAGES}")
        sys.exit(1)
    if stats_fail > 0:
        log(f"ABORT: image failures detected: {stats_fail}")
        sys.exit(1)

    # Orphan check: report R2 images not referenced by new index
    r2_img_keys = {k for k in old_objects if k.startswith("images/")}
    referenced = {img["r2Key"] for d in dynamics_data for img in d["images"]}
    orphans = sorted(r2_img_keys - referenced)
    log(f"  Orphan R2 images (not referenced): {len(orphans)} / {len(r2_img_keys)}")
    for k in orphans:
        log(f"    ORPHAN {k}")

    # ---- 5. Build & upload index files ---------------------------------------
    log(f"[Step 5] Building & uploading index files...  (prefix: '{_OUTPUT_PREFIX or '(production)'}')")

    index_data: dict = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "keepRecent": _KEEP_RECENT,
        "totalDynamics": len(dynamics_data),
        "totalImages": total_imgs,
        "dynamics": dynamics_data,
    }

    search_index: list[dict] = []
    for d in dynamics_data:
        search_index.append({
            "dynamicId": d["id"],
            "text": d["text"],
            "date": d["date"],
            "tags": d["tags"],
            "category": d.get("category", ""),
            "imageCount": d["imageCount"],
        })

    r2_put_json(_pk(INDEX_KEY), index_data)
    log(f"  Uploaded {_pk(INDEX_KEY)}  ({len(dynamics_data)} dynamics, {index_data['totalImages']} images)")

    r2_put_json(_pk(SEARCH_INDEX_KEY), search_index)
    log(f"  Uploaded {_pk(SEARCH_INDEX_KEY)}  ({len(search_index)} entries)")

    # ---- 6. Build & upload manifest ------------------------------------------
    log("[Step 6] Building & uploading manifests...")

    new_manifest: dict = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "keepRecent": _KEEP_RECENT,
        "totalImages": index_data["totalImages"],
        "dynamics": [d["id"] for d in dynamics_data],
        "objects": sorted(tracked_objects),
    }

    r2_put_json(_pk(MANIFEST_CURRENT_KEY), new_manifest)
    log(f"  Uploaded {_pk(MANIFEST_CURRENT_KEY)}")

    if old_manifest:
        r2_put_json(_pk(MANIFEST_PREVIOUS_KEY), old_manifest)
        log(f"  Uploaded {_pk(MANIFEST_PREVIOUS_KEY)} (backup)")

    # ---- 7. Save last-dynamic-id for incremental runs ------------------------
    if newest_id and dynamics_data and not _OUTPUT_PREFIX:
        r2_put_last_id(newest_id)
        log(f"  Saved last-dynamic-id: {newest_id[:16]}...")
    else:
        log("  Skipped last-dynamic-id save (no new items or no newest_id)")

    # ---- 8. Skip stale cleanup (keep all objects for now) --------------------
    log("[Step 8] Cleanup skipped — retaining all objects.")

    # ---- 9. Final summary ----------------------------------------------------
    log("=" * 60)
    log("COLLECTION COMPLETE")
    log(f"  Dynamics archived  : {len(dynamics_data)}")
    log(f"  Total images       : {index_data['totalImages']}")
    log(f"  Newly uploaded     : {stats_new}")
    log(f"  Recovered (existing): {stats_recovered}")
    log(f"  Failed             : {stats_fail}")
    log(f"  Final R2 objects   : {len(tracked_objects)}")
    if dynamics_data:
        log(f"  Newest dynamic     : {dynamics_data[0]['date']}  ({dynamics_data[0]['text'][:40]}...)")
        log(f"  Oldest dynamic     : {dynamics_data[-1]['date']}  ({dynamics_data[-1]['text'][:40]}...)")
    log("=" * 60)


if __name__ == "__main__":
    main()
