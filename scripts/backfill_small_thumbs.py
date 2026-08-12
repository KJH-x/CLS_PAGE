#!/usr/bin/env python3
"""Backfill / regenerate 1/8 small thumbs (smthumbs/) for all archived images.

For every image in the R2 indexes it ensures an 1/8 small thumb exists, reading
source bytes from the local compressed mirror when available (local_archive/)
and falling back to R2 otherwise. Updates index JSON + manifest objects list.

Idempotent: images that already have a valid smallThumbKey are skipped.

Usage:
    python scripts/backfill_small_thumbs.py                 # both accounts
    python scripts/backfill_small_thumbs.py --accounts=ef   # endfield only
    python scripts/backfill_small_thumbs.py --accounts=ak   # 朝陇山 only
    python scripts/backfill_small_thumbs.py --limit=20      # cap images processed
    python scripts/backfill_small_thumbs.py --dry-run       # preview only
"""
import io
import json
import os
import pathlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config
from PIL import Image

SMALL_SCALE = 8
QUALITY = 40
WORKERS = 8
BUCKET = os.environ["R2_BUCKET"]
LOCAL_DIR = pathlib.Path(
    os.environ.get("LOCAL_ARCHIVE_DIR", pathlib.Path(__file__).resolve().parents[1] / "local_archive")
)

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    config=Config(region_name="auto", retries={"max_attempts": 3, "mode": "standard"}),
)

DRY = "--dry-run" in sys.argv
LIMIT = None
ACCOUNTS = {"ak", "ef"}
for a in sys.argv:
    if a.startswith("--limit="):
        LIMIT = int(a.split("=")[1])
    if a.startswith("--accounts="):
        ACCOUNTS = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}

ACCOUNT_SETS = {
    "ak": {
        "manifest": "manifests/current.json",
        "indexes": ["site/index.json", "site/figures-index.json"],
    },
    "ef": {
        "manifest": "endfield/manifests/current.json",
        "indexes": ["endfield/site/index.json", "endfield/site/figures-index.json"],
    },
}

_lock = threading.Lock()
stats = {"done": 0, "skipped": 0, "err": 0}
new_small_keys = []


def get_json(key):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def put_json(key, data):
    if DRY:
        print(f"  DRY: would upload {key}")
        return
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
        CacheControl="public, max-age=300",
    )


def make_small(raw):
    img = Image.open(io.BytesIO(raw))
    w, h = img.size
    sw, sh = max(1, w // SMALL_SCALE), max(1, h // SMALL_SCALE)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img = img.resize((sw, sh), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=QUALITY, optimize=True, progressive=True, subsampling="4:2:0")
    return buf.getvalue()


def process_img(dyn_id, img):
    r2_key = img.get("r2Key", "")
    if not r2_key:
        return
    small_key = img.get("smallThumbKey", "")
    if small_key:
        try:
            s3.head_object(Bucket=BUCKET, Key=small_key)
            with _lock:
                stats["skipped"] += 1
            return
        except Exception:
            small_key = ""
    if not small_key:
        small_key = f"smthumbs/{dyn_id}/{img.get('index', 0)}.jpg"
    try:
        local_f = LOCAL_DIR / "images" / str(dyn_id) / f"{img.get('index', 0)}.jpg"
        body = None
        if local_f.is_file():
            body = local_f.read_bytes()
        else:
            body = s3.get_object(Bucket=BUCKET, Key=r2_key)["Body"].read()
        small = make_small(body)
        if not DRY:
            s3.put_object(
                Bucket=BUCKET, Key=small_key, Body=small,
                ContentType="image/jpeg", CacheControl="public, max-age=31536000, immutable",
            )
        else:
            print(f"  DRY: would generate {small_key} from {r2_key}")
        img["smallThumbKey"] = small_key
        with _lock:
            new_small_keys.append(small_key)
            stats["done"] += 1
            if stats["done"] % 10 == 0:
                print(f"  done={stats['done']} skipped={stats['skipped']}", flush=True)
    except Exception as e:
        with _lock:
            stats["err"] += 1
            print(f"  ERR {r2_key}: {e}", flush=True)


def main():
    targets = [ACCOUNT_SETS[acc] for acc in sorted(ACCOUNTS) if acc in ACCOUNT_SETS]
    if not targets:
        raise SystemExit("No valid --accounts (use ak, ef, or both)")

    for m in targets:
        manifest = get_json(m["manifest"])
        if not manifest:
            print(f"no manifest {m['manifest']}")
            continue
        print(f"== {m['manifest']}")
        pending = []  # (idx_key, dyn_id, img)
        for idx_key in m["indexes"]:
            index = get_json(idx_key)
            if not index:
                print(f"   no index {idx_key}")
                continue
            for dyn in index.get("dynamics", []):
                for img in dyn.get("images", []):
                    pending.append((idx_key, dyn.get("id", "?"), img))
        if LIMIT is not None:
            pending = pending[:LIMIT]
            print(f"   limit={LIMIT} images")
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(process_img, did, img) for _, did, img in pending]
            for _ in as_completed(futs):
                pass
        # write back indexes that changed
        for idx_key in m["indexes"]:
            index = get_json(idx_key)
            if not index:
                continue
            changed = False
            for dyn in index.get("dynamics", []):
                for img in dyn.get("images", []):
                    if img.get("smallThumbKey"):
                        changed = True
            if changed:
                put_json(idx_key, index)
                print(f"   updated {idx_key}")
        # refresh manifest objects list
        objs = set(manifest.get("objects", []))
        for k in new_small_keys:
            objs.add(k)
        manifest["objects"] = sorted(objs)
        put_json(m["manifest"], manifest)

    print(f"\nDone. {stats['done']} generated, {stats['skipped']} skipped, {stats['err']} errors")


if __name__ == "__main__":
    main()
