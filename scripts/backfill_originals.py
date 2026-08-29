#!/usr/bin/env python3
"""Backfill original bytes to R2 originals/ from the local archive cache.

Reads each image's raw bytes from local_archive/cache/ (named
{sha256}-{dynamicId}-{n}.{ext}) and publishes them to
originals/{dynamicId}/{n}.{ext}, then writes originalKey/sha256/originalSize/
originalExt into the index JSON and refreshes the manifest object list.

Copyright note: publishing original (uncompressed) bytes makes the public
bucket serve near-original copies of third-party artwork. This script is an
explicit, manual operation; R2_UPLOAD_ORIGINALS in collect.py is OFF by
default so automatic collection never publishes originals.

Idempotent: images that already carry a valid originalKey are skipped.

Usage:
    python scripts/backfill_originals.py                 # both accounts
    python scripts/backfill_originals.py --accounts=ef   # endfield only
    python scripts/backfill_originals.py --accounts=ak   # 朝陇山 only
    python scripts/backfill_originals.py --limit=20      # cap images processed
    python scripts/backfill_originals.py --dry-run       # preview only
"""
import hashlib
import json
import os
import pathlib
import sys

import boto3
from botocore.config import Config

BUCKET = os.environ.get("R2_BUCKET", "")
LOCAL_DIR = pathlib.Path(
    os.environ.get(
        "LOCAL_ARCHIVE_DIR",
        str(pathlib.Path(__file__).resolve().parents[1] / "local_archive"),
    )
)

ORIGINALS_PREFIX = "originals"
CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "png": "image/png",
    "gif": "image/gif",
}

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


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(region_name="auto", retries={"max_attempts": 3, "mode": "standard"}),
    )


def _content_type(ext: str) -> str:
    return CONTENT_TYPES.get((ext or "").lower(), "application/octet-stream")


def get_json(s3, key):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception:
        return None


def put_json(s3, key, data, dry: bool) -> None:
    if dry:
        print(f"  DRY: would upload {key}")
        return
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
        CacheControl="public, max-age=300",
    )


def find_local_original(local_dir: pathlib.Path, dyn_id: str, idx):
    """Return the cache file matching *-{dyn_id}-{idx}.* or None."""
    cache_dir = local_dir / "cache"
    if not cache_dir.is_dir():
        return None
    try:
        matches = sorted(cache_dir.glob(f"*-{dyn_id}-{idx}.*"))
    except Exception:
        return None
    return matches[0] if matches else None


def backfill_image(s3, img: dict, dyn_id: str, local_dir: pathlib.Path, dry: bool) -> str:
    """Backfill one image from the local cache.

    Returns 'skipped' (originalKey already present / idempotent),
    'done' (backfilled), or 'missing' (no local cache file / no r2Key)."""
    if not img.get("r2Key"):
        return "missing"
    if img.get("originalKey"):
        return "skipped"
    idx = img.get("index", 0)
    src = find_local_original(local_dir, dyn_id, idx)
    if src is None:
        return "missing"
    raw = src.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    ext = src.suffix.lstrip(".") or "img"
    orig_key = f"{ORIGINALS_PREFIX}/{dyn_id}/{idx}.{ext}"
    if not dry:
        s3.put_object(
            Bucket=BUCKET,
            Key=orig_key,
            Body=raw,
            ContentType=_content_type(ext),
            CacheControl="public, max-age=31536000, immutable",
        )
    img["originalKey"] = orig_key
    img["sha256"] = actual
    img["originalSize"] = len(raw)
    img["originalExt"] = ext
    return "done"


def main() -> None:
    dry = "--dry-run" in sys.argv
    accounts = {"ak", "ef"}
    limit = None
    for a in sys.argv:
        if a.startswith("--limit="):
            limit = int(a.split("=")[1])
        if a.startswith("--accounts="):
            accounts = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}

    if not os.environ.get("R2_ACCESS_KEY_ID") or not BUCKET:
        raise SystemExit(
            "R2 credentials missing (set R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/"
            "R2_ACCOUNT_ID/R2_BUCKET)"
        )

    cache_dir = LOCAL_DIR / "cache"
    if not cache_dir.is_dir():
        print(
            f"WARN: local archive cache not found at {cache_dir} — historical originals "
            "cannot be backfilled. Only originals published by collect.py "
            "(R2_UPLOAD_ORIGINALS=1) will exist in originals/."
        )

    s3 = _s3()
    stats = {"done": 0, "skipped": 0, "missing": 0}
    new_keys = []
    for acc in sorted(accounts):
        if acc not in ACCOUNT_SETS:
            continue
        target = ACCOUNT_SETS[acc]
        manifest = get_json(s3, target["manifest"])
        if not manifest:
            print(f"no manifest {target['manifest']}")
            continue
        print(f"== {target['manifest']}")
        pending = []
        for idx_key in target["indexes"]:
            index = get_json(s3, idx_key)
            if not index:
                print(f"   no index {idx_key}")
                continue
            for dyn in index.get("dynamics", []):
                for img in dyn.get("images", []):
                    pending.append((idx_key, dyn.get("id", "?"), img))
        if limit is not None:
            pending = pending[:limit]
            print(f"   limit={limit} images")
        for idx_key, did, img in pending:
            status = backfill_image(s3, img, did, LOCAL_DIR, dry)
            stats[status] += 1
            if status == "done":
                new_keys.append(img.get("originalKey"))
                if stats["done"] % 10 == 0:
                    print(
                        f"  done={stats['done']} skipped={stats['skipped']} "
                        f"missing={stats['missing']}",
                        flush=True,
                    )
        for idx_key in target["indexes"]:
            index = get_json(s3, idx_key)
            if not index:
                continue
            changed = any(
                img.get("originalKey")
                for dyn in index.get("dynamics", [])
                for img in dyn.get("images", [])
            )
            if changed:
                put_json(s3, idx_key, index, dry)
                print(f"   updated {idx_key}")
        objs = set(manifest.get("objects", []))
        for k in new_keys:
            objs.add(k)
        manifest["objects"] = sorted(objs)
        put_json(s3, target["manifest"], manifest, dry)

    print(
        f"\nDone. {stats['done']} backfilled, {stats['skipped']} skipped, "
        f"{stats['missing']} missing local cache"
    )


if __name__ == "__main__":
    main()
