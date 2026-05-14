"""Tests for the PlantNet client module and image preprocessing."""

import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from PIL import Image

from gardenops.services.plantnet import (
    PlantNetError,
    PlantNetResult,
    _build_multipart,
    identify,
    preprocess_image_for_identification,
)


def _make_jpeg(width: int = 100, height: int = 100) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), (0, 128, 0)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _make_png(width: int = 100, height: int = 100) -> bytes:
    buf = BytesIO()
    Image.new("RGBA", (width, height), (0, 128, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _make_webp(width: int = 100, height: int = 100) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), (0, 128, 0)).save(buf, format="WEBP")
    return buf.getvalue()


def _plantnet_response(
    score: float = 0.9,
    latin: str = "Rosa canina",
    remaining: int = 498,
) -> bytes:
    return json.dumps(
        {
            "bestMatch": f"{latin} L.",
            "results": [
                {
                    "score": score,
                    "species": {
                        "scientificNameWithoutAuthor": latin,
                        "scientificName": f"{latin} L.",
                        "genus": {
                            "scientificNameWithoutAuthor": latin.split()[0],
                        },
                        "family": {
                            "scientificNameWithoutAuthor": "Rosaceae",
                        },
                        "commonNames": ["Nyperose", "Dog rose"],
                    },
                    "gbif": {"id": "5202424"},
                },
            ],
            "remainingIdentificationRequests": remaining,
        },
    ).encode()


class TestBuildMultipart(unittest.TestCase):
    def test_produces_valid_multipart(self) -> None:
        body, ct = _build_multipart(b"fake-image", "flower", "image/jpeg")
        self.assertIn("multipart/form-data; boundary=", ct)
        boundary = ct.split("boundary=")[1]
        self.assertIn(f"--{boundary}".encode(), body)
        self.assertIn(b'name="images"', body)
        self.assertIn(b'name="organs"', body)
        self.assertIn(b"flower", body)
        self.assertIn(b"fake-image", body)
        self.assertIn(f"--{boundary}--".encode(), body)


class TestPreprocessImage(unittest.TestCase):
    def test_valid_jpeg(self) -> None:
        raw = _make_jpeg(200, 200)
        result, mime = preprocess_image_for_identification(raw, "image/jpeg")
        self.assertEqual(mime, "image/jpeg")
        self.assertGreater(len(result), 0)
        with Image.open(BytesIO(result)) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertLessEqual(max(img.size), 1280)

    def test_valid_png_converts_to_jpeg(self) -> None:
        raw = _make_png()
        result, mime = preprocess_image_for_identification(raw, "image/png")
        self.assertEqual(mime, "image/jpeg")
        with Image.open(BytesIO(result)) as img:
            self.assertEqual(img.format, "JPEG")

    def test_valid_webp_converts_to_jpeg(self) -> None:
        raw = _make_webp()
        result, mime = preprocess_image_for_identification(raw, "image/webp")
        self.assertEqual(mime, "image/jpeg")

    def test_oversized_rejected(self) -> None:
        big = b"\xff\xd8\xff" + b"\x00" * (6 * 1024 * 1024)
        with self.assertRaises(HTTPException) as ctx:
            preprocess_image_for_identification(big, "image/jpeg")
        self.assertEqual(ctx.exception.status_code, 413)

    def test_bad_content_type_rejected(self) -> None:
        raw = _make_jpeg()
        with self.assertRaises(HTTPException) as ctx:
            preprocess_image_for_identification(raw, "text/plain")
        self.assertEqual(ctx.exception.status_code, 415)

    def test_empty_payload_rejected(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            preprocess_image_for_identification(b"", "image/jpeg")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_not_an_image_rejected(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            preprocess_image_for_identification(
                b"not an image at all",
                "image/jpeg",
            )
        self.assertEqual(ctx.exception.status_code, 415)

    def test_large_image_resized(self) -> None:
        raw = _make_jpeg(3000, 2000)
        result, _ = preprocess_image_for_identification(raw, "image/jpeg")
        with Image.open(BytesIO(result)) as img:
            self.assertLessEqual(img.size[0], 1280)
            self.assertLessEqual(img.size[1], 1280)


class TestIdentify(unittest.TestCase):
    def test_invalid_organ_raises(self) -> None:
        with self.assertRaises(ValueError):
            identify(b"img", "invalid", "key")

    def test_empty_api_key_raises(self) -> None:
        with self.assertRaises(PlantNetError) as ctx:
            identify(b"img", "leaf", "")
        self.assertEqual(ctx.exception.status_code, 0)

    def test_empty_image_raises(self) -> None:
        with self.assertRaises(PlantNetError):
            identify(b"", "leaf", "key")

    @patch("gardenops.services.plantnet.urllib.request.build_opener")
    def test_successful_identification(self, mock_build_opener: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = _plantnet_response(0.907, "Rosa canina", 498)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        result = identify(b"fake-jpeg", "flower", "test-key")

        self.assertIsInstance(result, PlantNetResult)
        request = mock_opener.open.call_args.args[0]
        self.assertIn("?api-key=test-key", request.full_url)
        self.assertNotIn("%s", request.full_url)
        self.assertEqual(len(result.candidates), 1)
        self.assertAlmostEqual(result.candidates[0].score, 0.907)
        self.assertEqual(result.candidates[0].latin, "Rosa canina")
        self.assertEqual(result.candidates[0].family, "Rosaceae")
        self.assertEqual(result.candidates[0].gbif_id, "5202424")
        self.assertEqual(result.remaining_requests, 498)

    @patch("gardenops.services.plantnet.urllib.request.build_opener")
    def test_timeout_raises_plantnet_error(self, mock_build_opener: MagicMock) -> None:
        mock_opener = MagicMock()
        mock_opener.open.side_effect = TimeoutError("timed out")
        mock_build_opener.return_value = mock_opener
        with self.assertRaises(PlantNetError) as ctx:
            identify(b"fake-jpeg", "leaf", "key")
        self.assertEqual(ctx.exception.status_code, 0)
        self.assertIn("timeout", ctx.exception.detail.lower())

    @patch("gardenops.services.plantnet.urllib.request.build_opener")
    def test_http_401_raises_plantnet_error(self, mock_build_opener: MagicMock) -> None:
        import urllib.error

        mock_opener = MagicMock()
        mock_opener.open.side_effect = urllib.error.HTTPError(
            url="",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=BytesIO(b"bad key"),  # type: ignore[arg-type]
        )
        mock_build_opener.return_value = mock_opener
        with self.assertRaises(PlantNetError) as ctx:
            identify(b"fake-jpeg", "leaf", "bad-key")
        self.assertEqual(ctx.exception.status_code, 401)

    @patch("gardenops.services.plantnet.urllib.request.build_opener")
    def test_http_429_raises_plantnet_error(self, mock_build_opener: MagicMock) -> None:
        import urllib.error

        mock_opener = MagicMock()
        mock_opener.open.side_effect = urllib.error.HTTPError(
            url="",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=BytesIO(b"quota"),  # type: ignore[arg-type]
        )
        mock_build_opener.return_value = mock_opener
        with self.assertRaises(PlantNetError) as ctx:
            identify(b"fake-jpeg", "leaf", "key")
        self.assertEqual(ctx.exception.status_code, 429)

    @patch("gardenops.services.plantnet.urllib.request.build_opener")
    def test_redirect_is_not_followed(self, mock_build_opener: MagicMock) -> None:
        import urllib.error

        mock_opener = MagicMock()
        mock_opener.open.side_effect = urllib.error.HTTPError(
            url="https://my-api.plantnet.org/v2/identify/all?api-key=secret-key",
            code=302,
            msg="Found",
            hdrs={"Location": "https://example.invalid/collect"},
            fp=BytesIO(b""),  # type: ignore[arg-type]
        )
        mock_build_opener.return_value = mock_opener

        with self.assertRaises(PlantNetError) as ctx:
            identify(b"fake-jpeg", "leaf", "secret-key")

        self.assertEqual(ctx.exception.status_code, 302)
        self.assertEqual(ctx.exception.detail, "PlantNet API redirected")

    @patch("gardenops.services.plantnet.urllib.request.build_opener")
    def test_invalid_json_raises_plantnet_error(self, mock_build_opener: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        with self.assertRaises(PlantNetError) as ctx:
            identify(b"fake-jpeg", "leaf", "key")
        self.assertEqual(ctx.exception.status_code, 0)
