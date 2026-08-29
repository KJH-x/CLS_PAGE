import hashlib
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

import requests


MODULE_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "collect.py"
SPEC = importlib.util.spec_from_file_location("cls_collect", MODULE_PATH)
collect = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collect)

BACKFILL_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "backfill_originals.py"
BACKFILL_SPEC = importlib.util.spec_from_file_location("cls_backfill_originals", BACKFILL_PATH)
backfill = importlib.util.module_from_spec(BACKFILL_SPEC)
assert BACKFILL_SPEC.loader is not None
BACKFILL_SPEC.loader.exec_module(backfill)


class FakeResponse:
    def __init__(self, *, status=200, payload=None, content=b"image", headers=None):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            response.headers.update(self.headers)
            raise requests.HTTPError(f"HTTP {self.status_code}", response=response)

    def json(self):
        return self._payload


class CollectTests(unittest.TestCase):
    def test_output_prefix_is_applied_once_by_callers(self):
        with mock.patch.object(collect, "_OUTPUT_PREFIX", "staging/review"):
            self.assertEqual(
                collect._pk("images/123/0.jpg"),
                "staging/review/images/123/0.jpg",
            )

    def test_extract_dynamic_uses_stable_absolute_date(self):
        item = {
            "id_str": "123",
            "modules": {
                "module_author": {"pub_ts": "1767225600", "pub_time": "2 days ago"},
                "module_dynamic": {
                    "major": {
                        "type": "MAJOR_TYPE_OPUS",
                        "opus": {
                            "title": "CLS",
                            "summary": {"text": "\u3013\u671d\u9647\u5c7126 Jan.\uff5cTest\u3013\u4e0a\u65b0"},
                            "pics": [{"url": "https://example.test/a.jpg", "width": 100, "height": 500}],
                        },
                    }
                },
            },
        }

        dynamic = collect.extract_dynamic(item)

        self.assertIsNotNone(dynamic)
        self.assertEqual(dynamic["date"], "2026-01-01")

    def test_extract_dynamic_sales_info_archived_under_shangxin(self):
        item = {
            "id_str": "1221797962460430352",
            "modules": {
                "module_author": {"pub_ts": "1783310406"},
                "module_dynamic": {
                    "major": {
                        "type": "MAJOR_TYPE_OPUS",
                        "opus": {
                            "title": "",
                            "summary": {
                                "text": (
                                    "#明日方舟##音律联觉##鹰角嘉年华##转发抽奖# \n"
                                    "〓明日方舟| 音律联觉-昔时我见&2026鹰角嘉年华 主题周边〓现场&线上 贩售情报公开！\n"
                                    "博士博士，兔兔送来全新的主题周边情报啦~"
                                )
                            },
                            "pics": [
                                {"url": "https://example.test/a.jpg", "width": 100, "height": 500}
                            ],
                        },
                    }
                },
            },
        }

        dynamic = collect.extract_dynamic(item)

        self.assertIsNotNone(dynamic)
        self.assertEqual(dynamic["category"], "上新")
        self.assertIn("贩售情报公开", dynamic["text"])

    def test_extract_dynamic_sales_info_ignores_body_only_mention(self):
        item = {
            "id_str": "1220000000000000000",
            "modules": {
                "module_author": {"pub_ts": "1783310406"},
                "module_dynamic": {
                    "major": {
                        "type": "MAJOR_TYPE_OPUS",
                        "opus": {
                            "title": "",
                            "summary": {
                                "text": (
                                    "〓朝陇山26 Jul.｜夏荫同栖〓实物展示\n"
                                    "以下是完整的贩售情报公开，但不属于标题。"
                                )
                            },
                            "pics": [
                                {"url": "https://example.test/a.jpg", "width": 100, "height": 500}
                            ],
                        },
                    }
                },
            },
        }

        self.assertIsNone(collect.extract_dynamic(item))

    def test_extract_dynamic_figure_preorder_categorized_as_figure(self):
        item = {
            "id_str": "1199161743190786049",
            "modules": {
                "module_author": {"pub_ts": "1767225600"},
                "module_dynamic": {
                    "major": {
                        "type": "MAJOR_TYPE_OPUS",
                        "opus": {
                            "title": "",
                            "summary": {
                                "text": (
                                    "#明日方舟# #APEX-TOYS# \n"
                                    "〓明日方舟 1/7手办 Mon3tr 预售开启!〓 制作：APEX-TOYS\n"
                                    "商品名称：明日方舟 1/7手办 Mon3tr 官方定价：899元"
                                )
                            },
                            "pics": [
                                {"url": "https://example.test/a.jpg", "width": 100, "height": 500}
                            ],
                        },
                    }
                },
            },
        }

        dynamic = collect.extract_dynamic(item)

        self.assertIsNotNone(dynamic)
        self.assertEqual(dynamic["category"], "手办")
        self.assertIn("Mon3tr", dynamic["text"])

    def test_extract_dynamic_figure_preorder_title_only(self):
        item = {
            "id_str": "1220000000000000001",
            "modules": {
                "module_author": {"pub_ts": "1767225600"},
                "module_dynamic": {
                    "major": {
                        "type": "MAJOR_TYPE_OPUS",
                        "opus": {
                            "title": "",
                            "summary": {
                                "text": (
                                    "〓朝陇山26 Jul.｜夏荫同栖〓上新\n"
                                    "手办预售情报详见正文，但标题不匹配。"
                                )
                            },
                            "pics": [
                                {"url": "https://example.test/a.jpg", "width": 100, "height": 500}
                            ],
                        },
                    }
                },
            },
        }

        dynamic = collect.extract_dynamic(item)

        self.assertIsNotNone(dynamic)
        self.assertEqual(dynamic["category"], "上新")

    def test_archive_date_accepts_historic_string_timestamp(self):
        self.assertEqual(collect.coerce_timestamp("1767225600"), 1767225600)
        self.assertEqual(collect.format_archive_date("1767225600"), "2026-01-01")
        self.assertEqual(collect.format_archive_date("invalid"), "")

    # ------------------------------------------------------------------
    # Endfield (山团团) mode
    # ------------------------------------------------------------------

    def _endfield_item(self, summary_text, pics=None):
        return {
            "id_str": "1230000000000000000",
            "modules": {
                "module_author": {"pub_ts": "1767225600"},
                "module_dynamic": {
                    "major": {
                        "type": "MAJOR_TYPE_OPUS",
                        "opus": {
                            "title": "",
                            "summary": {"text": summary_text},
                            "pics": pics
                            or [{"url": "https://example.test/a.jpg", "width": 100, "height": 500}],
                        },
                    }
                },
            },
        }

    def test_endfield_new_arrival_categorized_shangxin_keeps_original_title(self):
        item = self._endfield_item(
            "▼相伴庆典开幕！▼山团团上新： #渊客停# 周边介绍\n哇！转眼间，团团迎来了第一个庆典"
        )
        with mock.patch.object(collect, "_ARCHIVE_MODE", "endfield"):
            dynamic = collect.extract_dynamic(item)

        self.assertIsNotNone(dynamic)
        self.assertEqual(dynamic["category"], "上新")
        self.assertEqual(dynamic["text"], "▼相伴庆典开幕！▼山团团上新： #渊客停# 周边介绍")

    def test_endfield_preorder_non_figure_categorized_yushou(self):
        item = self._endfield_item(
            "▼明日方舟：终末地纪念插画集Vol.1 预售开启▼\n详情见正文"
        )
        with mock.patch.object(collect, "_ARCHIVE_MODE", "endfield"):
            dynamic = collect.extract_dynamic(item)

        self.assertIsNotNone(dynamic)
        self.assertEqual(dynamic["category"], "预售")
        self.assertIn("预售开启", dynamic["text"])

    def test_endfield_figure_preorder_categorized_figure(self):
        item = self._endfield_item(
            "▼明日方舟：终末地 1/7手办 陈千语 预售开启▼\n商品信息"
        )
        with mock.patch.object(collect, "_ARCHIVE_MODE", "endfield"):
            dynamic = collect.extract_dynamic(item)

        self.assertIsNotNone(dynamic)
        self.assertEqual(dynamic["category"], "手办")

    def test_endfield_surplus_categorized_yuliangshangjia(self):
        item = self._endfield_item(
            "#启程补给#系列商品，余量掉落！ 欢迎管理员前来看看呀~"
        )
        with mock.patch.object(collect, "_ARCHIVE_MODE", "endfield"):
            dynamic = collect.extract_dynamic(item)

        self.assertIsNotNone(dynamic)
        self.assertEqual(dynamic["category"], "余量上架")

    def test_endfield_merchandise_display_is_excluded(self):
        # 实物展示图片 post — no action word → excluded
        item = self._endfield_item(
            "▼山团团#渊客停#▼山团团毛绒玩偶&挂件-梨诺VER. 实物展示图片\n₊ ⊹ 闪亮登场"
        )
        with mock.patch.object(collect, "_ARCHIVE_MODE", "endfield"):
            self.assertIsNone(collect.extract_dynamic(item))

    def test_endfield_daily_post_is_excluded(self):
        item = self._endfield_item(
            "▼打灰？ING！▼ EP14 现在！立刻！我要知道这期纪念品的全部信息！\n友情提示：纯属娱乐"
        )
        with mock.patch.object(collect, "_ARCHIVE_MODE", "endfield"):
            self.assertIsNone(collect.extract_dynamic(item))

    def test_endfield_figure_presale_not_series_split(self):
        # "手办" + "企划公开" in title → figures, even without 预售 word
        item = self._endfield_item(
            "▼明日方舟：终末地 1/7手办 莱万汀 企划公开▼\n实体化企划进行中"
        )
        with mock.patch.object(collect, "_ARCHIVE_MODE", "endfield"):
            dynamic = collect.extract_dynamic(item)

        self.assertIsNotNone(dynamic)
        self.assertEqual(dynamic["category"], "手办")

    def test_archived_image_inputs_preserve_sparse_r2_keys(self):
        old_images = [
            {
                "index": 0,
                "r2Key": "images/123/9.jpg",
                "thumbnailKey": "thumbs/123/9.jpg",
                "originalWidth": 100,
                "originalHeight": 900,
            },
            {
                "index": 1,
                "r2Key": "images/123/11.jpg",
                "thumbnailKey": "thumbs/123/11.jpg",
                "originalWidth": 100,
                "originalHeight": 1000,
            },
        ]

        inputs = collect.archived_image_inputs(old_images)

        self.assertEqual([item["r2Key"] for item in inputs], ["images/123/9.jpg", "images/123/11.jpg"])
        self.assertEqual(inputs[0]["_oldMeta"]["thumbnailKey"], "thumbs/123/9.jpg")

    def test_request_retries_transient_http_status(self):
        responses = [
            FakeResponse(status=429, headers={"Retry-After": "0"}),
            FakeResponse(status=200),
        ]
        with (
            mock.patch.object(collect, "_REQUEST_MAX_ATTEMPTS", 2),
            mock.patch.object(collect.requests, "get", side_effect=responses) as get,
            mock.patch.object(collect.time, "sleep") as sleep,
        ):
            response = collect._request_with_retry(
                "https://example.test", headers={}, timeout=1, label="test"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(0.0)

    def test_make_small_thumbnail_is_one_eighth(self):
        import io as _io
        from PIL import Image as _Image
        buf = _io.BytesIO()
        _Image.new("RGB", (800, 1600), (10, 20, 30)).save(buf, format="JPEG")
        raw = buf.getvalue()

        small = collect.make_small_thumbnail(raw)
        self.assertIsNotNone(small)
        img = _Image.open(_io.BytesIO(small))
        self.assertEqual(img.size, (100, 200))  # 800/8 x 1600/8

    def test_page_cap_marks_pagination_incomplete(self):
        payload = {
            "code": 0,
            "data": {
                "items": [{"id_str": "newest"}],
                "has_more": True,
                "offset": "next",
            },
        }
        with (
            mock.patch.object(collect, "MAX_API_PAGES", 1),
            mock.patch.object(collect, "_API_PAGE_DELAY_SECONDS", 0),
            mock.patch.object(
                collect, "_request_with_retry", return_value=FakeResponse(payload=payload)
            ),
        ):
            items, newest, complete = collect.fetch_dynamics("old-cursor")

        self.assertEqual(len(items), 1)
        self.assertEqual(newest, "newest")
        self.assertFalse(complete)

    def test_environment_validation_lists_missing_names_without_values(self):
        patches = {
            "_R2_ACCESS_KEY": "",
            "_R2_SECRET_KEY": "",
            "_R2_BUCKET": "bucket",
            "_R2_ACCOUNT_ID": "account",
            "_BILI_COOKIE": "cookie-secret",
            "_BILI_UID": "123",
        }
        with mock.patch.multiple(collect, **patches):
            with self.assertRaises(SystemExit) as raised:
                collect.validate_environment()

        message = str(raised.exception)
        self.assertIn("R2_ACCESS_KEY_ID", message)
        self.assertIn("R2_SECRET_ACCESS_KEY", message)
        self.assertNotIn("cookie-secret", message)

    # ------------------------------------------------------------------
    # R1: build_search_index (fullText / timestamp / bilibiliUrl)
    # ------------------------------------------------------------------

    def _search_index_dyn(self, full_text):
        return {
            "id": "1220000000000000000",
            "timestamp": 1767225600,
            "date": "2026-01-01",
            "text": "〓朝陇山26 May.｜7周年庆典〓上新",
            "fullText": full_text,
            "bilibiliUrl": "https://t.bilibili.com/1220000000000000000",
            "tags": ["上新"],
            "category": "上新",
            "imageCount": 3,
        }

    def test_build_search_index_includes_fulltext_truncated(self):
        dyn = self._search_index_dyn("促销信息" * 120)
        entry = collect.build_search_index([dyn])[0]
        self.assertEqual(entry["fullText"], (dyn["fullText"] or "")[:200])
        self.assertEqual(len(entry["fullText"]), 200)
        self.assertEqual(entry["timestamp"], 1767225600)
        self.assertEqual(entry["bilibiliUrl"], "https://t.bilibili.com/1220000000000000000")

    def test_build_search_index_keeps_legacy_six_fields(self):
        dyn = self._search_index_dyn("促销信息")
        entry = collect.build_search_index([dyn])[0]
        self.assertEqual(entry["dynamicId"], "1220000000000000000")
        self.assertEqual(entry["text"], "〓朝陇山26 May.｜7周年庆典〓上新")
        self.assertEqual(entry["date"], "2026-01-01")
        self.assertEqual(entry["tags"], ["上新"])
        self.assertEqual(entry["category"], "上新")
        self.assertEqual(entry["imageCount"], 3)

    def test_build_search_index_fulltext_empty_or_missing(self):
        dyn = self._search_index_dyn(None)
        dyn.pop("fullText", None)
        dyn.pop("timestamp", None)
        dyn.pop("bilibiliUrl", None)
        entry = collect.build_search_index([dyn])[0]
        self.assertEqual(entry["fullText"], "")
        self.assertEqual(entry["timestamp"], 0)
        self.assertEqual(entry["bilibiliUrl"], "")

    # ------------------------------------------------------------------
    # R2: server slug (server_slug / make_dyn_entry)
    # ------------------------------------------------------------------

    def test_server_slug_cls_fence(self):
        slug = collect.server_slug(
            "〓朝陇山26 May.｜7周年庆典〓上新",
            "〓朝陇山26 May.｜7周年庆典〓上新 正文……",
            "1220000000000000001",
        )
        self.assertEqual(slug, "7周年庆典")

    def test_server_slug_endfield_fence(self):
        slug = collect.server_slug(
            "▼相伴庆典开幕！▼山团团上新： #渊客停# 周边介绍",
            "",
            "1220000000000000002",
        )
        self.assertEqual(slug, "相伴庆典开幕！")

    def test_server_slug_normalizes_plain_text(self):
        self.assertEqual(collect.server_slug(" #明日方舟# 7周年 庆典", "", "x"), "7周年-庆典")

    def test_server_slug_falls_back_to_id(self):
        self.assertEqual(collect.server_slug("", "", "789"), "789")
        self.assertEqual(collect.server_slug("(no title)", "", "789"), "no-title")

    def test_make_dyn_entry_includes_server_slug(self):
        dyn = {
            "id": "1",
            "timestamp": 1767225600,
            "date": "2026-01-01",
            "text": "〓朝陇山26 May.｜7周年庆典〓上新",
            "fullText": "正文",
            "bilibiliUrl": "https://t.bilibili.com/1",
            "tags": [],
            "category": "上新",
        }
        entry = collect.make_dyn_entry(dyn, [])
        self.assertEqual(entry["slug"], "7周年庆典")
        self.assertEqual(entry["imageCount"], 0)

    # ------------------------------------------------------------------
    # R3: ensure_small_thumb_keys
    # ------------------------------------------------------------------

    def test_ensure_small_thumb_keys_adds_missing_key(self):
        images = [
            {"r2Key": "images/1/0.jpg"},
            {"r2Key": "images/1/1.jpg", "smallThumbKey": "smthumbs/1/1.jpg"},
        ]
        collect.ensure_small_thumb_keys(images)
        self.assertIn("smallThumbKey", images[0])
        self.assertEqual(images[0]["smallThumbKey"], "")
        self.assertEqual(images[1]["smallThumbKey"], "smthumbs/1/1.jpg")

    def test_ensure_small_thumb_keys_is_idempotent(self):
        images = [{"r2Key": "images/1/0.jpg", "smallThumbKey": "smthumbs/1/0.jpg"}]
        collect.ensure_small_thumb_keys(images)
        collect.ensure_small_thumb_keys(images)
        self.assertEqual(images[0]["smallThumbKey"], "smthumbs/1/0.jpg")

    # ------------------------------------------------------------------
    # R4: sha256 / original bytes metadata
    # ------------------------------------------------------------------

    def test_sha256_matches_hashlib_of_raw_bytes(self):
        raw = b"fake-image-bytes-123"
        self.assertEqual(collect.sha256_hex(raw), hashlib.sha256(raw).hexdigest())

    def test_local_save_original_filename_encodes_sha256(self):
        import io as _io
        from PIL import Image as _Image
        buf = _io.BytesIO()
        _Image.new("RGB", (80, 160), (1, 2, 3)).save(buf, format="JPEG")
        raw = buf.getvalue()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(collect, "_LOCAL_ARCHIVE_DIR", tmp):
                p = collect.local_save_original("123", 0, raw)
                self.assertIsNotNone(p)
                digest = hashlib.sha256(raw).hexdigest()
                self.assertEqual(p.name, f"{digest}-123-0.jpg")
                self.assertTrue(p.exists())
                self.assertEqual(p.read_bytes(), raw)

    def test_download_image_strips_bilibili_size_suffix(self):
        with (
            mock.patch.object(
                collect, "_request_with_retry", return_value=FakeResponse(content=b"x")
            ) as req,
            mock.patch.object(collect, "_IMAGE_DELAY_SECONDS", 0),
        ):
            collect.download_image("https://i0.hdslb.com/x.jpg@768w_1s.jpg")
        self.assertEqual(req.call_args[0][0], "https://i0.hdslb.com/x.jpg")

    def test_original_meta_fields_in_images_entry(self):
        images = [{
            "index": 0,
            "r2Key": "images/1/0.jpg",
            "thumbnailKey": "thumbs/1/0.jpg",
            "smallThumbKey": "smthumbs/1/0.jpg",
            "originalWidth": 800,
            "originalHeight": 1200,
            "storedWidth": 800,
            "storedHeight": 1200,
            "sha256": "abc123",
            "originalSize": 12345,
            "originalExt": "jpg",
            "originalKey": "originals/1/0.jpg",
        }]
        dyn = {
            "id": "1",
            "timestamp": 1767225600,
            "date": "2026-01-01",
            "text": "t",
            "fullText": "f",
            "bilibiliUrl": "https://t.bilibili.com/1",
            "tags": [],
            "category": "上新",
        }
        entry = collect.make_dyn_entry(dyn, images)
        self.assertEqual(entry["images"][0]["sha256"], "abc123")
        self.assertEqual(entry["images"][0]["originalSize"], 12345)
        self.assertEqual(entry["images"][0]["originalExt"], "jpg")
        self.assertEqual(entry["images"][0]["originalKey"], "originals/1/0.jpg")

    # ------------------------------------------------------------------
    # R4: backfill_originals (idempotent + dry-run + missing cache)
    # ------------------------------------------------------------------

    def test_backfill_originals_skips_existing_and_dry_run(self):
        img = {"r2Key": "images/1/0.jpg", "originalKey": "originals/1/0.jpg"}
        s3 = mock.MagicMock()
        self.assertEqual(backfill.backfill_image(s3, img, "1", pathlib.Path("."), dry=True), "skipped")
        s3.put_object.assert_not_called()

    def test_backfill_originals_dry_run_does_not_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_dir = pathlib.Path(tmp)
            cache = local_dir / "cache"
            cache.mkdir()
            raw = b"original-bytes"
            digest = hashlib.sha256(raw).hexdigest()
            (cache / f"{digest}-2-0.jpg").write_bytes(raw)
            img = {"r2Key": "images/2/0.jpg", "index": 0}
            s3 = mock.MagicMock()
            status = backfill.backfill_image(s3, img, "2", local_dir, dry=True)
            self.assertEqual(status, "done")
            s3.put_object.assert_not_called()
            self.assertEqual(img["sha256"], digest)
            self.assertEqual(img["originalExt"], "jpg")
            self.assertEqual(img["originalKey"], "originals/2/0.jpg")

    def test_backfill_originals_missing_cache_returns_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = {"r2Key": "images/3/0.jpg", "index": 0}
            status = backfill.backfill_image(None, img, "3", pathlib.Path(tmp), dry=True)
            self.assertEqual(status, "missing")
            self.assertNotIn("originalKey", img)


if __name__ == "__main__":
    unittest.main()
