import importlib.util
import pathlib
import unittest
from unittest import mock

import requests


MODULE_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "collect.py"
SPEC = importlib.util.spec_from_file_location("cls_collect", MODULE_PATH)
collect = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collect)


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

    def test_archive_date_accepts_historic_string_timestamp(self):
        self.assertEqual(collect.coerce_timestamp("1767225600"), 1767225600)
        self.assertEqual(collect.format_archive_date("1767225600"), "2026-01-01")
        self.assertEqual(collect.format_archive_date("invalid"), "")

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


if __name__ == "__main__":
    unittest.main()
