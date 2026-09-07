#!/usr/bin/env python3
"""Bilibili Dynamic Image Archiver — collects, compresses, and uploads to Cloudflare R2.

Data flow:
  1. Load the current manifest and index from R2
  2. Fetch Bilibili user dynamics via the paginated API
  3. Extract, merge, and deduplicate dynamics that contain images
  4. Download originals, compress at original width (q35 JPEG), and upload images
  5. After all integrity gates pass, upload the index and search index
  6. Upload the current manifest and retain the previous manifest as a backup

Images are uploaded before the index, and stale-object cleanup is deliberately disabled. A failed
run can therefore leave unreferenced objects, but it cannot publish an index with missing new images.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import random
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import boto3
import requests
from botocore.config import Config
from PIL import Image, UnidentifiedImageError

# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------


def _env_int(name: str, default: str) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc


def _env_float(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number") from exc


_R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY_ID", "")
_R2_SECRET_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
_R2_BUCKET = os.environ.get("R2_BUCKET", "")
_R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
_BILI_COOKIE = os.environ.get("BILIBILI_COOKIE", "")
_BILI_UID = os.environ.get("BILIBILI_UID", "")
_ARCHIVE_MODE = os.environ.get("ARCHIVE_MODE", "cls").strip().lower()
_KEEP_RECENT = _env_int("KEEP_RECENT", "10")
_OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "")
_DRY_RUN = os.environ.get("DRY_RUN", "") != ""
_EXTRACT_DEBUG = os.environ.get("EXTRACT_DEBUG", "") != ""
_MIN_DEDUP = _env_int("EXPECTED_MIN_DEDUP_DYNAMICS", "0")
_MIN_SURVIVED = _env_int("EXPECTED_MIN_SURVIVED_DYNAMICS", "0")
_MAX_ALL_SKIPPED = _env_int("EXPECTED_MAX_ALL_IMAGE_SKIPPED", "999")
_MIN_IMAGES = _env_int("EXPECTED_MIN_IMAGES", "0")
_MAX_IMAGE_FAILURES = _env_int("ALLOW_IMAGE_FAILURES", "0")
_REQUEST_MAX_ATTEMPTS = _env_int("REQUEST_MAX_ATTEMPTS", "3")
_BACKOFF_BASE_SECONDS = _env_float("BACKOFF_BASE_SECONDS", "1")
_API_PAGE_DELAY_SECONDS = _env_float("API_PAGE_DELAY_SECONDS", "0.4")
_IMAGE_DELAY_SECONDS = _env_float("IMAGE_DELAY_SECONDS", "0.15")
_LOCAL_ARCHIVE_DIR = os.environ.get(
    "LOCAL_ARCHIVE_DIR",
    str(pathlib.Path(__file__).resolve().parents[1] / "local_archive"),
)
# Publish original (uncompressed) bytes to R2 originals/ during collection.
# Default OFF: the public bucket must not redistribute third-party artwork;
# enable explicitly (e.g. a private bucket) via R2_UPLOAD_ORIGINALS=1.
_R2_UPLOAD_ORIGINALS = os.environ.get("R2_UPLOAD_ORIGINALS", "") != ""

DISPLAY_WIDTH_SCALE = 1  # no horizontal downsampling
JPEG_QUALITY = 35
THUMB_QUALITY = 40
THUMB_SCALE = 2  # thumbnail = 1/2 original width & height
SMALL_THUMB_SCALE = 8  # small thumb = 1/8 original width & height
MAX_API_PAGES = 20
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")

MANIFEST_CURRENT_KEY = "manifests/current.json"
MANIFEST_PREVIOUS_KEY = "manifests/previous.json"
INDEX_KEY = "site/index.json"
SEARCH_INDEX_KEY = "site/search-index.json"
FIGURES_INDEX_KEY = "site/figures-index.json"
FIGURES_SEARCH_INDEX_KEY = "site/figures-search-index.json"
LAST_ID_KEY = "config/last-dynamic-id.json"

def _pk(key: str) -> str:
    """Apply output prefix if set, otherwise return key unchanged."""
    return f"{_OUTPUT_PREFIX.rstrip('/')}/{key}" if _OUTPUT_PREFIX else key


def validate_environment() -> None:
    """Fail before any network access when required configuration is invalid."""
    required = {
        "R2_ACCESS_KEY_ID": _R2_ACCESS_KEY,
        "R2_SECRET_ACCESS_KEY": _R2_SECRET_KEY,
        "R2_BUCKET": _R2_BUCKET,
        "R2_ACCOUNT_ID": _R2_ACCOUNT_ID,
        "BILIBILI_COOKIE": _BILI_COOKIE,
        "BILIBILI_UID": _BILI_UID,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")
    if not _BILI_UID.isdigit():
        raise SystemExit("BILIBILI_UID must contain digits only")
    if _ARCHIVE_MODE not in ("cls", "endfield"):
        raise SystemExit("ARCHIVE_MODE must be 'cls' or 'endfield'")
    if _KEEP_RECENT <= 0:
        raise SystemExit("KEEP_RECENT must be greater than zero")
    if _MAX_IMAGE_FAILURES < 0:
        raise SystemExit("ALLOW_IMAGE_FAILURES cannot be negative")
    if _REQUEST_MAX_ATTEMPTS <= 0:
        raise SystemExit("REQUEST_MAX_ATTEMPTS must be greater than zero")
    if any(value < 0 for value in (_BACKOFF_BASE_SECONDS, _API_PAGE_DELAY_SECONDS, _IMAGE_DELAY_SECONDS)):
        raise SystemExit("Retry and request delay values cannot be negative")
    prefix_parts = [part for part in _OUTPUT_PREFIX.replace("\\", "/").split("/") if part]
    if _OUTPUT_PREFIX.startswith(("/", "\\")) or ".." in prefix_parts:
        raise SystemExit("OUTPUT_PREFIX must be a relative R2 key prefix")

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
    if _DRY_RUN:
        log(f"  DRY RUN: would upload {key}")
        return
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
    if _DRY_RUN:
        log(f"  DRY RUN: would upload {key} ({len(data):,} bytes)")
        return
    s3 = _get_s3()
    s3.put_object(
        Bucket=_R2_BUCKET,
        Key=key,
        Body=data,
        ContentType="image/jpeg",
        CacheControl="public, max-age=31536000, immutable",
    )


_ORIGINAL_CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "png": "image/png",
    "gif": "image/gif",
}


def _original_content_type(ext: str) -> str:
    return _ORIGINAL_CONTENT_TYPES.get((ext or "").lower(), "application/octet-stream")


def r2_put_original(key: str, data: bytes) -> None:
    """Upload original bytes to R2 originals/ with format-aware ContentType.

    Only ever called when R2_UPLOAD_ORIGINALS is enabled (default off)."""
    if _DRY_RUN:
        log(f"  DRY RUN: would upload {key} ({len(data):,} bytes)")
        return
    s3 = _get_s3()
    ext = key.rsplit(".", 1)[-1] if "." in key else ""
    s3.put_object(
        Bucket=_R2_BUCKET,
        Key=key,
        Body=data,
        ContentType=_original_content_type(ext),
        CacheControl="public, max-age=31536000, immutable",
    )


def r2_delete(key: str) -> bool:
    """Delete a single object from R2. Returns True on success."""
    if _DRY_RUN:
        log(f"  DRY RUN: would delete {key}")
        return True
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
    data = r2_get_json(_pk(LAST_ID_KEY))
    return data.get("lastDynamicId") if data else None


def r2_put_last_id(dyn_id: str) -> None:
    """Store the last processed dynamic ID on R2."""
    r2_put_json(_pk(LAST_ID_KEY), {"lastDynamicId": dyn_id})


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


def _retry_delay(attempt: int, retry_after: Optional[str] = None) -> float:
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.25)


def _request_with_retry(
    url: str,
    *,
    headers: dict[str, str],
    timeout: int,
    label: str,
) -> Optional[requests.Response]:
    """GET a URL with bounded retry/backoff for transient network and HTTP failures."""
    for attempt in range(1, _REQUEST_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(
                    f"transient HTTP {response.status_code}", response=response
                )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            if attempt >= _REQUEST_MAX_ATTEMPTS:
                log(f"  ERROR: {label} failed after {attempt} attempts: {exc}")
                return None
            response = getattr(exc, "response", None)
            retry_after = response.headers.get("Retry-After") if response is not None else None
            delay = _retry_delay(attempt, retry_after)
            log(f"  WARN: {label} attempt {attempt} failed; retrying in {delay:.1f}s")
            time.sleep(delay)
    return None


def fetch_dynamics(last_id: Optional[str] = None) -> tuple[list[dict], Optional[str], bool]:
    """Paginate through the user's dynamic feed. If last_id is provided, stop
    when that ID is encountered (incremental mode).
    Returns (items, newest_id, pagination_complete). The last flag is false after API failures,
    stalled offsets, or when MAX_API_PAGES is exhausted before reaching the cursor/feed end."""
    all_items: list[dict] = []
    newest_id: Optional[str] = None
    offset = ""
    page = 0
    pagination_complete = False

    while page < MAX_API_PAGES:
        page += 1
        params = f"host_mid={_BILI_UID}&offset={offset}&features=itemOpusStyle"
        url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?{params}"
        log(f"  Page {page}  offset={offset[:32] if offset else '(initial)'}")

        data: Optional[dict] = None
        for api_attempt in range(1, _REQUEST_MAX_ATTEMPTS + 1):
            resp = _request_with_retry(
                url, headers=_bili_headers(), timeout=30, label="Bilibili API request"
            )
            if resp is None:
                break
            try:
                candidate_data = resp.json()
            except requests.JSONDecodeError as exc:
                log(f"  ERROR: Bilibili API returned invalid JSON: {exc}")
                break

            code = candidate_data.get("code")
            if code == 0:
                data = candidate_data
                break
            log(f"  API returned code={code} message={candidate_data.get('message', '')}")
            if code not in (-352, -412) or api_attempt >= _REQUEST_MAX_ATTEMPTS:
                break
            delay = _retry_delay(api_attempt)
            log(f"  WARN: Bilibili risk control response; retrying in {delay:.1f}s")
            time.sleep(delay)

        if data is None:
            log("  ERROR: Bilibili API page could not be fetched; pagination is incomplete.")
            break

        page_data = data.get("data", {})
        items = page_data.get("items") or []
        if not items:
            log("  No more items.")
            pagination_complete = True
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
            pagination_complete = True
            break

        has_more = page_data.get("has_more", False)
        if not has_more:
            log("  has_more=false, stopping pagination.")
            pagination_complete = True
            break

        next_offset = page_data.get("offset", "")
        if not next_offset or next_offset == offset:
            log("  WARN: Offset did not advance; pagination is incomplete.")
            break
        offset = next_offset
        if _API_PAGE_DELAY_SECONDS:
            time.sleep(_API_PAGE_DELAY_SECONDS)

    if not pagination_complete and page >= MAX_API_PAGES:
        log(f"  WARN: Reached MAX_API_PAGES={MAX_API_PAGES} before the cursor/feed end; cursor will not advance.")
    return all_items, newest_id, pagination_complete


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"#(\S+?)#")

# Match fenced sale titles: 〓....｜name〓(上新|余量上架|复刻上新)
# (collab/product titles like "〓明日方舟 × 女神异闻录3 Reload｜月行水上〓上新"
#  have no 朝陇山+date prefix, so match any fenced name with a ｜ separator)
_TITLE_PATTERN = re.compile(r"〓[^〓\n]*[｜|][^〓\n]*〓(上新|余量上架|复刻上新)")

# 贩售情报 announcements (e.g. 〓明日方舟×... 现场&线上 贩售情报公开！) are archived under 上新
SALES_INFO_RE = re.compile(r".*贩售情报.*")

# 手办预售 posts (e.g. 〓明日方舟 1/7手办 XXX 预售开启!〓) are archived in the /figures section
FIGURE_PREORDER_RE = re.compile(r".*手办.*预售")
FIGURE_PREORDER_CATEGORY = "手办"


def extract_tags(text: str) -> list[str]:
    return _TAG_RE.findall(text)


def strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def first_text_line(text: str) -> str:
    """First non-empty line after hashtag stripping — candidate title for matching."""
    for line in text.split("\n"):
        line = strip_tags(line).strip()
        if line:
            return line
    return ""


def coerce_timestamp(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def format_archive_date(timestamp: Any) -> str:
    value = coerce_timestamp(timestamp)
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value, DISPLAY_TIMEZONE).strftime("%Y-%m-%d")


def archived_image_inputs(images: list[dict]) -> list[dict]:
    """Preserve sparse R2 keys when an old index entry is merged without API image URLs."""
    return [
        {
            "url": "",
            "width": image.get("originalWidth", 0),
            "height": image.get("originalHeight", 0),
            "r2Key": image.get("r2Key", ""),
            "_oldMeta": dict(image),
        }
        for image in images
        if image.get("r2Key")
    ]


def ensure_small_thumb_keys(images: list[dict]) -> list[dict]:
    """Guarantee every image object carries a smallThumbKey key.

    Older index entries may be sparse (missing the key entirely). Setting an
    empty-string default keeps the key present in every written index.json so
    the front-end fallback chain (smallThumbKey || thumbnailKey || r2Key) is
    unambiguous. Idempotent: existing non-empty values are preserved.
    """
    for img in images:
        img.setdefault("smallThumbKey", "")
    return images


def sha256_hex(data: bytes) -> str:
    """Hex digest of raw bytes (computed before any compression)."""
    return hashlib.sha256(data).hexdigest()


def detect_image_ext(raw: bytes) -> str:
    """Best-effort original extension from the decoded image format."""
    try:
        with Image.open(io.BytesIO(raw)) as im:
            fmt = (im.format or "").lower()
        return {"jpeg": "jpg", "webp": "webp", "png": "png", "gif": "gif"}.get(fmt, "img")
    except Exception:
        return "img"


def _debug_item(item: dict, msg: str) -> None:
    """EXTRACT_DEBUG helper: print one line per raw dynamic with identifying info."""
    modules = item.get("modules") or {}
    mod_dyn = modules.get("module_dynamic") or {}
    mod_auth = modules.get("module_author") or {}
    dyn_id = item.get("id_str", "")
    pub = mod_auth.get("pub_time", "") or ""
    major = (mod_dyn.get("major") or {}).get("type", "")
    if _ARCHIVE_MODE == "endfield":
        text = (mod_dyn.get("opus", {}) or {}).get("summary", {}) or {}
        snippet = _fence_line(text.get("text", ""))[:50]
    else:
        snippet = ""
    log(f"  [extract] {dyn_id} major={major} pub={pub} {msg}" + (f" title={snippet!r}" if snippet else ""))


def extract_dynamic(item: dict) -> Optional[dict]:
    """Convert a raw Bilibili dynamic item into our internal format.
    Returns None if the dynamic has no usable images or doesn't match filters."""
    if _EXTRACT_DEBUG:
        _debug_item(item, "start")
    if item.get("orig"):
        if _EXTRACT_DEBUG:
            _debug_item(item, "skip: forward/repost")
        return None

    modules = item.get("modules") or {}
    mod_dyn = modules.get("module_dynamic") or {}
    mod_auth = modules.get("module_author") or {}

    dyn_id = item.get("id_str", "")
    if not dyn_id:
        if _EXTRACT_DEBUG:
            _debug_item(item, "skip: no id_str")
        return None

    pub_ts = coerce_timestamp(mod_auth.get("pub_ts", 0))
    if pub_ts <= 0:
        if _EXTRACT_DEBUG:
            _debug_item(item, f"skip: no pub_ts (id={dyn_id})")
        return None

    major = mod_dyn.get("major") or {}
    major_type = major.get("type", "")

    image_urls: list[dict] = []
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
        search_text = ((opus.get("title") or "") + " " + summary.get("text", "")).strip()

    if not image_urls:
        if _EXTRACT_DEBUG:
            _debug_item(item, f"skip: no images (major={major_type}, id={dyn_id})")
        return None

    # Category detection — per-archive mode:
    #   cls     : 〓朝陇山{date}｜{name}〓{上新|余量上架|复刻上新} / 贩售情报 / 手办预售 / 余量上架
    #   endfield: action-word rules on the ▼...▼ title (上新/预售/余量上架/手办); no series split
    if _ARCHIVE_MODE == "endfield":
        category, title = _endfield_categorize(search_text)
        if category is None:
            if _EXTRACT_DEBUG:
                _debug_item(item, f"skip: endfield categorize=None (id={dyn_id}, title={title[:50]!r})")
            return None
    else:
        category = _cls_categorize(search_text)
        if category is None:
            if _EXTRACT_DEBUG:
                _debug_item(item, f"skip: cls categorize=None (id={dyn_id}, text={first_text_line(search_text)[:60]!r})")
            return None
        title = _cls_title(search_text, category)

    tags = extract_tags(search_text)
    if _EXTRACT_DEBUG:
        _debug_item(item, f"ACCEPT category={category} title={title[:60]!r} imgs={len(image_urls)} (id={dyn_id})")

    return {
        "id": dyn_id,
        "timestamp": pub_ts,
        "date": format_archive_date(pub_ts),
        "text": title,
        "fullText": search_text,
        "bilibiliUrl": f"https://t.bilibili.com/{dyn_id}",
        "tags": tags,
        "category": category,
        "imageUrls": image_urls,
    }


# ---------------------------------------------------------------------------
# Per-mode categorization
# ---------------------------------------------------------------------------

# endfield figure pre-order detection (title-line only)
_ENDFIELD_FIGURE_RE = re.compile(r"手办.*?(预售|企划公开|开订|再贩)")
# endfield "上新" action words
_ENDFIELD_UP_RE = re.compile(r"上新|周边介绍|贩售情报|场贩")
# endfield 余量/掉落
_ENDFIELD_SURPLUS_RE = re.compile(r"余量|掉落")


def _fence_line(text: str) -> str:
    """First line containing a 〓 or ▼ fence — the original title (unchanged)."""
    for line in text.split("\n"):
        line = line.strip()
        if line and ("〓" in line or "▼" in line):
            return line
    for line in text.split("\n"):
        line = line.strip()
        if line:
            return line
    return "(no title)"


def _endfield_categorize(search_text: str) -> tuple[Optional[str], str]:
    """Categorize an Endfield dynamic by action words on its original title.

    Returns (category, title) or (None, title) when the post should be excluded.
    Keeps the original title verbatim (no forced conversion)."""
    title = _fence_line(search_text)
    if _ENDFIELD_FIGURE_RE.search(title):
        return FIGURE_PREORDER_CATEGORY, title
    if "预售" in title:
        return "预售", title
    if _ENDFIELD_UP_RE.search(title):
        return "上新", title
    if _ENDFIELD_SURPLUS_RE.search(title):
        return "余量上架", title
    return None, title


def _cls_categorize(search_text: str) -> Optional[str]:
    """cls categorization: returns category or None."""
    title_match = _TITLE_PATTERN.search(search_text)
    if title_match:
        return title_match.group(1)
    title_line = _fence_line(search_text)
    if SALES_INFO_RE.search(title_line):
        return "上新"
    if FIGURE_PREORDER_RE.search(title_line):
        return FIGURE_PREORDER_CATEGORY
    if "余量上架" in search_text.replace("#", ""):
        return "余量上架"
    return None


def _cls_title(search_text: str, category: str) -> str:
    """cls title extraction (unchanged behaviour)."""
    title = ""
    if category == "上新" or category == FIGURE_PREORDER_CATEGORY or (category == "余量上架" and _TITLE_PATTERN.search(search_text)):
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
        for line in search_text.split("\n"):
            line = line.strip()
            cleaned = strip_tags(line)
            if cleaned:
                title = cleaned
                break
    title = re.sub(r"^互动抽奖\s*", "", title).strip()
    if not title:
        title = "(no title)"
    return title


# ---------------------------------------------------------------------------
# Stable shareable slug (server-authoritative, mirrored by client buildSlug)
# ---------------------------------------------------------------------------

def _normalize_slug(source: str) -> str:
    """Client-mirrored normalization: first non-empty line after #tag# strip,
    whitespace collapse, then any char outside [A-Za-z0-9_\\u4e00-\\u9fa5-] -> '-'.

    The safe class is deliberately ASCII-only: JS `\\w` is ASCII-only while
    Python `\\w` is unicode-aware, so an explicit class keeps both sides
    byte-identical for the same input."""
    line = ""
    for raw in (source or "").split("\n"):
        cleaned = re.sub(r"#[^#]+#", "", raw).strip()
        if cleaned:
            line = cleaned
            break
    line = re.sub(r"\s+", " ", line).strip()
    line = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fa5-]", "-", line)
    line = re.sub(r"-+", "-", line).strip("-")
    return line


def server_slug(text: str, full_text: str, dyn_id: str) -> str:
    """Stable shareable slug, mirrored 1:1 by the client buildSlug() (app.js).

    Order:
      1. fence 〓…〓 (ak):  [｜|]([^〓▼]+)〓  ->  "7周年庆典"
      2. fence ▼…▼ (ef):   ▼(.+?)▼          ->  "相伴庆典开幕！"
      3. normalized first line of text/fullText
      4. fallback: dyn id
    The value is frozen into index.json so already-shared links stay valid
    even if upstream text or future derivation rules change."""
    source = ((text or "").strip() or (full_text or "").strip() or "")
    m = re.search(r"[｜|]([^〓▼]+)〓", source)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.search(r"▼(.+?)▼", source)
    if m and m.group(1).strip():
        return m.group(1).strip()
    norm = _normalize_slug(source)
    if norm:
        return norm
    return str(dyn_id or "")


def make_dyn_entry(dyn: dict, images: list[dict]) -> dict:
    """Build the persisted index entry for one dynamic.

    Hoisted from main() so it is importable and testable; behavior identical
    to the original inline dict construction plus the server-authoritative
    `slug` field."""
    return {
        "id": dyn["id"],
        "timestamp": dyn["timestamp"],
        "date": dyn["date"],
        "text": dyn["text"],
        "fullText": dyn.get("fullText", ""),
        "bilibiliUrl": dyn["bilibiliUrl"],
        "tags": dyn["tags"],
        "category": dyn.get("category", ""),
        "imageCount": len(images),
        "slug": server_slug(dyn.get("text", ""), dyn.get("fullText", ""), dyn.get("id", "")),
        "images": images,
    }


def build_search_index(dynamics: list[dict]) -> list[dict]:
    """Search index entries, extended with fullText/timestamp/bilibiliUrl.

    Old merged entries may lack fullText, so every optional key is read with
    .get() and defaults to ""/0."""
    return [
        {
            "dynamicId": d["id"],
            "text": d["text"],
            "date": d["date"],
            "tags": d["tags"],
            "category": d.get("category", ""),
            "imageCount": d["imageCount"],
            "fullText": (d.get("fullText") or "")[:200],
            "timestamp": d.get("timestamp", 0),
            "bilibiliUrl": d.get("bilibiliUrl", ""),
        }
        for d in dynamics
    ]


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
    if not url:
        log("      Download error: image URL is empty")
        return None
    clean_url = re.sub(r"@\d+w.*$", "", url)  # strip Bilibili size suffix for max res
    resp = _request_with_retry(clean_url, headers=IMG_DL_HEADERS, timeout=60, label="image download")
    if resp is None:
        return None
    if _IMAGE_DELAY_SECONDS:
        time.sleep(_IMAGE_DELAY_SECONDS)
    return resp.content


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


def make_small_thumbnail(raw: bytes) -> Optional[bytes]:
    """Generate a low-detail 1/8 thumbnail for lazy-loaded grid tiles."""
    try:
        img = Image.open(io.BytesIO(raw))
        orig_w, orig_h = img.size
        small_w = max(1, orig_w // SMALL_THUMB_SCALE)
        small_h = max(1, orig_h // SMALL_THUMB_SCALE)

        if img.mode != "RGB":
            img = img.convert("RGB")

        img = img.resize((small_w, small_h), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True, progressive=True, subsampling="4:2:0")
        return buf.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Local archive (compressed copy + original cache keyed by hash+dynamic id)
# ---------------------------------------------------------------------------

def _local_path(*parts: str) -> pathlib.Path:
    return pathlib.Path(_LOCAL_ARCHIVE_DIR, *parts)


def local_save_compressed(dyn_id: str, idx: int, data: bytes) -> Optional[pathlib.Path]:
    """Write the compressed image to local_archive/images/{dynId}/{idx}.jpg."""
    try:
        p = _local_path("images", dyn_id, f"{idx}.jpg")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p
    except Exception as exc:
        log(f"      WARN: local compressed save failed: {exc}")
        return None


def local_save_original(dyn_id: str, idx: int, raw: bytes) -> Optional[pathlib.Path]:
    """Cache the original bytes as local_archive/cache/{sha256}-{dynId}-{idx}.{ext}."""
    try:
        digest = hashlib.sha256(raw).hexdigest()
        fmt = ""
        try:
            with Image.open(io.BytesIO(raw)) as im:
                fmt = (im.format or "").lower()
        except Exception:
            fmt = ""
        ext = {"jpeg": "jpg", "webp": "webp", "png": "png", "gif": "gif"}.get(fmt, "img")
        p = _local_path("cache", f"{digest}-{dyn_id}-{idx}.{ext}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return p
    except Exception as exc:
        log(f"      WARN: local original save failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    validate_environment()
    log("=" * 60)
    log("Bilibili Dynamic Archiver — Cloudflare R2 edition")
    log(f"  UID        : {_BILI_UID}")
    log(f"  Mode       : {_ARCHIVE_MODE}")
    log(f"  KEEP_RECENT: {_KEEP_RECENT}")
    log(f"  R2 bucket  : {_R2_BUCKET}")
    log(f"  Prefix     : {_OUTPUT_PREFIX or '(production)'}")
    log(f"  Dry run    : {_DRY_RUN}")
    log(f"  Publish originals: {'ON (R2_UPLOAD_ORIGINALS)' if _R2_UPLOAD_ORIGINALS else 'OFF (private by default)'}")
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
    old_manifest = r2_get_json(_pk(MANIFEST_CURRENT_KEY))
    old_objects: set[str] = set()
    old_index: Optional[dict] = None
    if old_manifest:
        old_objects = set(old_manifest.get("objects", []))
        log(f"  Found previous manifest with {len(old_objects)} objects.")

    old_index = r2_get_json(_pk(INDEX_KEY))
    old_dynamics_map: dict[str, dict] = {}
    old_images_map: dict[str, dict] = {}
    if old_index:
        for od in old_index.get("dynamics", []):
            od["timestamp"] = coerce_timestamp(od.get("timestamp"))
            normalized_date = format_archive_date(od["timestamp"])
            if normalized_date:
                od["date"] = normalized_date
            old_dynamics_map[od.get("id", "")] = od
            for oi in od.get("images", []):
                old_images_map[oi.get("r2Key", "")] = oi
        log(f"  Loaded old index with {len(old_dynamics_map)} dynamics for metadata recovery.")
    else:
        log("  No previous index.json (first run).")

    old_figures_index = r2_get_json(_pk(FIGURES_INDEX_KEY))
    if old_figures_index:
        figure_count = 0
        for od in old_figures_index.get("dynamics", []):
            od["timestamp"] = coerce_timestamp(od.get("timestamp"))
            normalized_date = format_archive_date(od["timestamp"])
            if normalized_date:
                od["date"] = normalized_date
            old_dynamics_map[od.get("id", "")] = od
            figure_count += 1
            for oi in od.get("images", []):
                old_images_map[oi.get("r2Key", "")] = oi
        log(f"  Loaded figures index with {figure_count} dynamics for metadata recovery.")

    # ---- 2. Fetch dynamics ---------------------------------------------------
    log("[Step 2] Fetching Bilibili dynamics...")
    raw_items, newest_id, pagination_complete = fetch_dynamics(last_id)
    log(f"  Fetched {len(raw_items)} raw items total.")
    if newest_id:
        log(f"  Newest dynamic ID: {newest_id[:16]}...")
    if not pagination_complete and not raw_items:
        log("ABORT: Bilibili pagination failed before any items were fetched; existing index is unchanged.")
        sys.exit(1)

    # ---- 3. Extract candidates -----------------------------------------------
    log("[Step 3] Extracting dynamics with images...")
    candidates: list[dict] = []
    rejected = 0
    for item in raw_items:
        info = extract_dynamic(item)
        if info:
            candidates.append(info)
        else:
            rejected += 1
    if _EXTRACT_DEBUG:
        log(f"  Extract summary: {len(candidates)} accepted, {rejected} rejected (of {len(raw_items)} raw)")

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
    merged_from_old = 0
    for od in old_dynamics_map.values():
        if od.get("id") not in merged_ids:
            old_imgs = od.get("images", [])
            od["imageUrls"] = archived_image_inputs(old_imgs)
            od["_oldImages"] = {img["r2Key"]: img for img in old_imgs if img.get("r2Key")}
            deduped.append(od)
            merged_from_old += 1

    # Re-apply activity/category dedup after merging old entries. Otherwise an older archived
    # activity and a newly fetched duplicate can both survive despite the initial candidate dedup.
    deduped.sort(key=lambda d: d["timestamp"])
    seen_activities.clear()
    merged_deduped: list[dict] = []
    for dynamic in deduped:
        match = _ACTIVITY_RE.search(dynamic["text"])
        activity = match.group(1).strip() if match else dynamic["text"][:40].strip()
        key = (activity, dynamic.get("category", ""))
        if key not in seen_activities:
            seen_activities.add(key)
            merged_deduped.append(dynamic)
    deduped = sorted(merged_deduped, key=lambda d: d["timestamp"], reverse=True)
    figure_category = FIGURE_PREORDER_CATEGORY
    main_selected = [d for d in deduped if d.get("category") != figure_category][:_KEEP_RECENT]
    figure_selected = [d for d in deduped if d.get("category") == figure_category]
    selected = main_selected + figure_selected
    log(f"  Newly extracted: {len(candidates)}, merged from old index: {merged_from_old}")
    log(f"  Selected (top {_KEEP_RECENT} main + all figures): {len(main_selected)} main, {len(figure_selected)} figures")

    if not selected:
        log("WARNING: No image-containing dynamics found. Aborting to preserve existing index.")
        sys.exit(0)

    # ---- 4. Download, compress & upload images --------------------------------
    log("[Step 4] Processing images...")
    dynamics_data: list[dict] = []
    tracked_objects: set[str] = {
        _pk(INDEX_KEY),
        _pk(SEARCH_INDEX_KEY),
        _pk(FIGURES_INDEX_KEY),
        _pk(FIGURES_SEARCH_INDEX_KEY),
        _pk(MANIFEST_CURRENT_KEY),
        _pk(MANIFEST_PREVIOUS_KEY),
        _pk(LAST_ID_KEY),
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
            r2_key = img_info.get("r2Key") or _pk(f"images/{dyn['id']}/{idx}.jpg")

            # Skip square images (e.g. 500x500 icons) and banners (aspect >= 0.3)
            if api_w > 0 and api_h > 0:
                if api_w == api_h:
                    log(f"  [{dyn['id'][:16]}] img {idx} — square ({api_w}x{api_h}), skipped")
                    continue
                if api_w / api_h >= 0.3:
                    log(f"  [{dyn['id'][:16]}] img {idx} — banner ({api_w}x{api_h}, ratio={api_w/api_h:.2f}), skipped")
                    continue

            # If image already exists in R2, recover metadata from old index
            if r2_key in old_objects and r2_key in old_images_map and r2_head(r2_key):
                old_meta = old_images_map[r2_key]
                old_thumb_key = old_meta.get("thumbnailKey", "")
                if old_thumb_key and not r2_head(old_thumb_key):
                    log(f"  [{dyn['id'][:16]}] img {idx} — thumbnail missing; using full image fallback")
                    old_thumb_key = ""
                old_small_key = old_meta.get("smallThumbKey", "")
                if old_small_key and not r2_head(old_small_key):
                    old_small_key = ""
                if not old_small_key:
                    # Backfill: generate 1/8 small thumb from the full R2 image
                    full_bytes = r2_get_bytes(r2_key)
                    small_key = _pk(f"smthumbs/{dyn['id']}/{idx}.jpg")
                    small_data = make_small_thumbnail(full_bytes or b"")
                    if small_data:
                        r2_put_image(small_key, small_data)
                        old_small_key = small_key
                        log(f"  [{dyn['id'][:16]}] img {idx} — small thumb backfilled")
                meta = {
                    "index": idx,
                    "r2Key": r2_key,
                    "thumbnailKey": old_thumb_key,
                    "smallThumbKey": old_small_key,
                    "originalWidth": old_meta.get("originalWidth", 0),
                    "originalHeight": old_meta.get("originalHeight", 0),
                    "storedWidth": old_meta.get("storedWidth", 0),
                    "storedHeight": old_meta.get("storedHeight", 0),
                    "displayWidthScale": old_meta.get("displayWidthScale", DISPLAY_WIDTH_SCALE),
                    "compressionMode": old_meta.get("compressionMode", "original-q35"),
                    "sha256": old_meta.get("sha256", ""),
                    "originalSize": old_meta.get("originalSize", 0),
                    "originalExt": old_meta.get("originalExt", ""),
                    "originalKey": old_meta.get("originalKey", ""),
                }
                images.append(meta)
                tracked_objects.add(r2_key)
                if meta["thumbnailKey"]:
                    tracked_objects.add(meta["thumbnailKey"])
                if meta["smallThumbKey"]:
                    tracked_objects.add(meta["smallThumbKey"])
                stats_recovered += 1
                log(f"  [{dyn['id'][:16]}] img {idx} — recovered from old index")
                continue

            log(f"  [{dyn['id'][:16]}] img {idx+1}/{len(dyn['imageUrls'])} — processing...")

            # For merged old dynamics: recover metadata directly without downloading
            old_meta = img_info.get("_oldMeta") or dyn.get("_oldImages", {}).get(r2_key)
            if old_meta and r2_head(r2_key):
                old_meta = dict(old_meta)
                old_meta["index"] = idx
                old_meta["r2Key"] = r2_key
                old_thumb_key = old_meta.get("thumbnailKey", "")
                if old_thumb_key and not r2_head(old_thumb_key):
                    old_meta["thumbnailKey"] = ""
                old_small_key = old_meta.get("smallThumbKey", "")
                if old_small_key and not r2_head(old_small_key):
                    old_meta["smallThumbKey"] = ""
                if not old_meta.get("smallThumbKey"):
                    full_bytes = r2_get_bytes(r2_key)
                    small_key = _pk(f"smthumbs/{dyn['id']}/{idx}.jpg")
                    small_data = make_small_thumbnail(full_bytes or b"")
                    if small_data:
                        r2_put_image(small_key, small_data)
                        old_meta["smallThumbKey"] = small_key
                        log(f"    Small thumb backfilled")
                images.append(old_meta)
                tracked_objects.add(r2_key)
                tk = old_meta.get("thumbnailKey", "")
                if tk:
                    tracked_objects.add(tk)
                sk = old_meta.get("smallThumbKey", "")
                if sk:
                    tracked_objects.add(sk)
                stats_recovered += 1
                log(f"    Recovered from old index metadata")
                continue

            # Check if image already exists in R2 — recover actual dimensions
            if r2_head(r2_key):
                existing_bytes = r2_get_bytes(r2_key)
                stored_w, stored_h = get_image_size(existing_bytes or b"")
                if stored_w <= 0 or stored_h <= 0:
                    stored_w, stored_h = api_w, api_h
                thumb_key = _pk(f"thumbs/{dyn['id']}/{idx}.jpg")
                thumb_exists = r2_head(thumb_key)
                small_key = _pk(f"smthumbs/{dyn['id']}/{idx}.jpg")
                small_exists = r2_head(small_key)
                if not small_exists:
                    small_data = make_small_thumbnail(existing_bytes or b"")
                    if small_data:
                        r2_put_image(small_key, small_data)
                        small_exists = True
                        log(f"    Small thumb backfilled")
                old_meta_r = old_images_map.get(r2_key, {})
                meta = {
                    "index": idx,
                    "r2Key": r2_key,
                    "thumbnailKey": thumb_key if thumb_exists else "",
                    "smallThumbKey": small_key if small_exists else "",
                    "originalWidth": max(api_w, stored_w),
                    "originalHeight": max(api_h, stored_h),
                    "storedWidth": stored_w,
                    "storedHeight": stored_h,
                    "displayWidthScale": DISPLAY_WIDTH_SCALE,
                    "compressionMode": "recovered-from-r2",
                    "sha256": old_meta_r.get("sha256", ""),
                    "originalSize": old_meta_r.get("originalSize", 0),
                    "originalExt": old_meta_r.get("originalExt", ""),
                    "originalKey": old_meta_r.get("originalKey", ""),
                }
                images.append(meta)
                tracked_objects.add(r2_key)
                if meta["thumbnailKey"]:
                    tracked_objects.add(meta["thumbnailKey"])
                if meta["smallThumbKey"]:
                    tracked_objects.add(meta["smallThumbKey"])
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
            meta["sha256"] = sha256_hex(raw)
            meta["originalSize"] = len(raw)
            meta["originalExt"] = detect_image_ext(raw)

            # Local archive: compressed copy + original cache (hash + dynamic id)
            if not _DRY_RUN:
                local_save_compressed(dyn["id"], idx, compressed)
                local_save_original(dyn["id"], idx, raw)

            # Optional: publish original bytes to R2 originals/ (default OFF —
            # public bucket must not redistribute copyrighted originals).
            if _R2_UPLOAD_ORIGINALS:
                orig_key = _pk(f"originals/{dyn['id']}/{idx}.{meta['originalExt']}")
                r2_put_original(orig_key, raw)
                meta["originalKey"] = orig_key
                tracked_objects.add(orig_key)
                log(f"    Original: {orig_key}")

            # Generate and upload thumbnail
            thumb_key = _pk(f"thumbs/{dyn['id']}/{idx}.jpg")
            thumb_data = make_thumbnail(raw)
            if thumb_data:
                r2_put_image(thumb_key, thumb_data)
                meta["thumbnailKey"] = thumb_key
                tracked_objects.add(thumb_key)
                log(f"    Thumbnail: {thumb_key}")
            else:
                meta["thumbnailKey"] = ""

            # Generate and upload low-detail small thumbnail (1/8)
            small_key = _pk(f"smthumbs/{dyn['id']}/{idx}.jpg")
            small_data = make_small_thumbnail(raw)
            if small_data:
                r2_put_image(small_key, small_data)
                meta["smallThumbKey"] = small_key
                tracked_objects.add(small_key)
                log(f"    Small thumb: {small_key}")
            else:
                meta["smallThumbKey"] = ""

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

        # Guarantee smallThumbKey key exists on every image (older entries may
        # be sparse); empty string triggers the front-end fallback chain.
        ensure_small_thumb_keys(images)

        dyn_entry = make_dyn_entry(dyn, images)
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
    if stats_fail > _MAX_IMAGE_FAILURES:
        log(f"ABORT: image failures detected: {stats_fail} > allowed {_MAX_IMAGE_FAILURES}")
        sys.exit(1)

    # Orphan check: report R2 images not referenced by new index
    image_prefix = _pk("images/")
    r2_img_keys = {k for k in old_objects if k.startswith(image_prefix)}
    referenced = {img["r2Key"] for d in dynamics_data for img in d["images"]}
    orphans = sorted(r2_img_keys - referenced)
    log(f"  Orphan R2 images (not referenced): {len(orphans)} / {len(r2_img_keys)}")
    for k in orphans:
        log(f"    ORPHAN {k}")

    # ---- 5. Build & upload index files ---------------------------------------
    log(f"[Step 5] Building & uploading index files...  (prefix: '{_OUTPUT_PREFIX or '(production)'}')")

    figure_category = FIGURE_PREORDER_CATEGORY
    main_dynamics = [d for d in dynamics_data if d.get("category") != figure_category]
    figure_dynamics = [d for d in dynamics_data if d.get("category") == figure_category]
    main_imgs = sum(d["imageCount"] for d in main_dynamics)
    figure_imgs = sum(d["imageCount"] for d in figure_dynamics)

    index_data: dict = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "keepRecent": _KEEP_RECENT,
        "totalDynamics": len(main_dynamics),
        "totalImages": main_imgs,
        "dynamics": main_dynamics,
    }

    figures_index_data: dict = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "keepRecent": _KEEP_RECENT,
        "totalDynamics": len(figure_dynamics),
        "totalImages": figure_imgs,
        "dynamics": figure_dynamics,
    }

    # build_search_index is module-level (see top-level definition) so it is
    # importable and testable; the old nested copy has been removed.
    search_index = build_search_index(main_dynamics)
    figures_search_index = build_search_index(figure_dynamics)

    r2_put_json(_pk(INDEX_KEY), index_data)
    log(f"  Uploaded {_pk(INDEX_KEY)}  ({len(main_dynamics)} dynamics, {main_imgs} images)")

    r2_put_json(_pk(SEARCH_INDEX_KEY), search_index)
    log(f"  Uploaded {_pk(SEARCH_INDEX_KEY)}  ({len(search_index)} entries)")

    r2_put_json(_pk(FIGURES_INDEX_KEY), figures_index_data)
    log(f"  Uploaded {_pk(FIGURES_INDEX_KEY)}  ({len(figure_dynamics)} dynamics, {figure_imgs} images)")

    r2_put_json(_pk(FIGURES_SEARCH_INDEX_KEY), figures_search_index)
    log(f"  Uploaded {_pk(FIGURES_SEARCH_INDEX_KEY)}  ({len(figures_search_index)} entries)")

    # ---- 6. Build & upload manifest ------------------------------------------
    log("[Step 6] Building & uploading manifests...")

    new_manifest: dict = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "keepRecent": _KEEP_RECENT,
        "totalImages": main_imgs + figure_imgs,
        "dynamics": [d["id"] for d in dynamics_data],
        "objects": sorted(tracked_objects),
    }

    r2_put_json(_pk(MANIFEST_CURRENT_KEY), new_manifest)
    log(f"  Uploaded {_pk(MANIFEST_CURRENT_KEY)}")

    if old_manifest:
        r2_put_json(_pk(MANIFEST_PREVIOUS_KEY), old_manifest)
        log(f"  Uploaded {_pk(MANIFEST_PREVIOUS_KEY)} (backup)")

    # ---- 7. Save last-dynamic-id for incremental runs ------------------------
    # The newest archived dynamic is treated as the max node; future runs scan only
    # forward from here and never re-scan the full history.
    if dynamics_data:
        newest_dyn = max(dynamics_data, key=lambda d: d["timestamp"])
        r2_put_last_id(newest_dyn["id"])
        note = "" if pagination_complete else " (pagination reached the page cap; not scanning further back)"
        log(f"  Saved last-dynamic-id: {newest_dyn['id'][:16]}... (max node){note}")
    else:
        log("  Skipped last-dynamic-id save (no output dynamics)")

    # ---- 8. Skip stale cleanup (keep all objects for now) --------------------
    log("[Step 8] Cleanup skipped — retaining all objects.")

    # ---- 9. Final summary ----------------------------------------------------
    log("=" * 60)
    log("COLLECTION COMPLETE")
    log(f"  Dynamics archived  : {len(main_dynamics)} main + {len(figure_dynamics)} figures")
    log(f"  Total images       : {main_imgs + figure_imgs}")
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
