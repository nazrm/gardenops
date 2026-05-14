"""Tests for the /api/ai/identify-plant and /api/ai/diagnose-plant endpoints."""

from __future__ import annotations

import io
import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from gardenops.services.plantnet import PlantNetResult

from PIL import Image

from tests.base import BaseApiTest


def _make_jpeg(width: int = 100, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 128, 0)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _make_png(width: int = 100, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (0, 128, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _plantnet_result() -> PlantNetResult:
    """Return a mock PlantNetResult."""
    from gardenops.services.plantnet import PlantNetCandidate, PlantNetResult

    return PlantNetResult(
        candidates=[
            PlantNetCandidate(
                score=0.907,
                scientific_name="Rosa canina L.",
                latin="Rosa canina",
                genus="Rosa",
                family="Rosaceae",
                common_names=["Nyperose", "Dog rose"],
                gbif_id="5202424",
            ),
        ],
        remaining_requests=498,
        best_match="Rosa canina L.",
    )


def _plantnet_result_low_confidence() -> PlantNetResult:
    from gardenops.services.plantnet import PlantNetCandidate, PlantNetResult

    return PlantNetResult(
        candidates=[
            PlantNetCandidate(
                score=0.25,
                scientific_name="Taraxacum officinale L.",
                latin="Taraxacum officinale",
                genus="Taraxacum",
                family="Asteraceae",
                common_names=["Løvetann"],
                gbif_id="123",
            ),
        ],
        remaining_requests=497,
        best_match="Taraxacum officinale L.",
    )


_CLAUDE_IDENTIFY_RESPONSE = [
    {
        "name": "Nyperose",
        "latin": "Rosa canina",
        "scientific_name": "Rosa canina",
        "family": "Rosaceae",
        "confidence": 0.8,
        "source": "claude",
        "gbif_id": "",
    },
]

_CLAUDE_DIAGNOSE_RESPONSE = [
    {
        "issue_type": "fungal",
        "likely_cause": "Powdery mildew",
        "confidence": "high",
        "description": "White powdery coating on leaves.",
        "suggested_treatment": "Apply neem oil.",
        "reasoning": "Classic powdery mildew symptoms.",
        "related_history": "",
    },
]

_IDENTIFY_ENV = {
    "PLANTNET_API_KEY": "test-plantnet-key",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "PLANTNET_CONFIDENCE_THRESHOLD": "0.40",
    "PLANTNET_API_TIMEOUT_SECONDS": "2",
}

_DIAGNOSE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
}


class TestIdentifyPlant(BaseApiTest):
    @patch.dict(os.environ, {"PLANTNET_API_KEY": "", "ANTHROPIC_API_KEY": ""})
    def test_no_api_keys_returns_503(self) -> None:
        img = _make_jpeg()
        resp = self.client.post(
            "/api/ai/identify-plant?organ=leaf",
            content=img,
            headers={"Content-Type": "image/jpeg"},
        )
        self.assertEqual(resp.status_code, 503)

    def test_no_image_body_returns_400(self) -> None:
        with patch.dict(os.environ, _IDENTIFY_ENV):
            resp = self.client.post(
                "/api/ai/identify-plant?organ=leaf",
                content=b"",
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 400)

    def test_bad_content_type_returns_415(self) -> None:
        with patch.dict(os.environ, _IDENTIFY_ENV):
            resp = self.client.post(
                "/api/ai/identify-plant?organ=leaf",
                content=b"something",
                headers={"Content-Type": "text/plain"},
            )
        self.assertEqual(resp.status_code, 415)

    def test_oversized_image_returns_413(self) -> None:
        big = _make_jpeg(3000, 3000)
        with patch.dict(os.environ, _IDENTIFY_ENV):
            resp = self.client.post(
                "/api/ai/identify-plant?organ=leaf",
                content=big + b"\x00" * (5 * 1024 * 1024),
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 413)

    def test_identify_uses_ai_photo_body_limit_instead_of_generic_api_limit(self) -> None:
        with patch.dict(
            os.environ,
            {
                **_IDENTIFY_ENV,
                "MAX_API_BODY_BYTES": "24",
                "MAX_AI_PHOTO_BODY_BYTES": "64",
            },
        ):
            resp = self.client.post(
                "/api/ai/identify-plant?organ=leaf",
                content=b"x" * 32,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 415)
        self.assertIn("valid image", resp.json()["detail"])

    def test_identify_rejects_payload_above_configured_ai_photo_limit(self) -> None:
        with patch.dict(
            os.environ,
            {
                **_IDENTIFY_ENV,
                "MAX_AI_PHOTO_BODY_BYTES": "32",
            },
        ):
            resp = self.client.post(
                "/api/ai/identify-plant?organ=leaf",
                content=b"x" * 64,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 413)

    def test_invalid_organ_returns_400(self) -> None:
        with patch.dict(os.environ, _IDENTIFY_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/identify-plant?organ=root",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid organ", resp.json()["detail"])

    @patch("gardenops.routers.ai._claude_identify_plant")
    @patch("gardenops.services.plantnet.identify")
    def test_plantnet_success(
        self,
        mock_pn: MagicMock,
        mock_claude: MagicMock,
    ) -> None:
        mock_pn.return_value = _plantnet_result()
        mock_claude.return_value = []

        with patch.dict(os.environ, _IDENTIFY_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/identify-plant?organ=flower",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("candidates", data)
        self.assertEqual(len(data["candidates"]), 1)
        self.assertEqual(data["candidates"][0]["latin"], "Rosa canina")
        self.assertEqual(data["candidates"][0]["source"], "plantnet")
        self.assertIn("attribution", data)
        self.assertEqual(data["plantnet_remaining"], 498)

    @patch("gardenops.routers.ai._claude_identify_plant")
    @patch("gardenops.services.plantnet.identify")
    def test_plantnet_timeout_falls_back_to_claude(
        self,
        mock_pn: MagicMock,
        mock_claude: MagicMock,
    ) -> None:
        from gardenops.services.plantnet import PlantNetError

        mock_pn.side_effect = PlantNetError(0, "timeout")
        mock_claude.return_value = _CLAUDE_IDENTIFY_RESPONSE

        with patch.dict(os.environ, _IDENTIFY_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/identify-plant?organ=leaf",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["candidates"]), 1)
        self.assertEqual(data["candidates"][0]["source"], "claude")

    @patch("gardenops.routers.ai._claude_identify_plant")
    @patch("gardenops.services.plantnet.identify")
    def test_both_fail_returns_502(
        self,
        mock_pn: MagicMock,
        mock_claude: MagicMock,
    ) -> None:
        from gardenops.services.plantnet import PlantNetError

        mock_pn.side_effect = PlantNetError(0, "timeout")
        mock_claude.side_effect = Exception("claude down")

        with patch.dict(os.environ, _IDENTIFY_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/identify-plant?organ=leaf",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 502)

    @patch("gardenops.routers.ai._claude_identify_plant")
    @patch("gardenops.services.plantnet.identify")
    def test_low_confidence_triggers_claude_enrichment(
        self,
        mock_pn: MagicMock,
        mock_claude: MagicMock,
    ) -> None:
        mock_pn.return_value = _plantnet_result_low_confidence()
        mock_claude.return_value = [
            {
                "name": "Hundekjeks",
                "latin": "Anthriscus sylvestris",
                "scientific_name": "Anthriscus sylvestris",
                "family": "Apiaceae",
                "confidence": 0.6,
                "source": "claude",
                "gbif_id": "",
            },
        ]

        with patch.dict(os.environ, _IDENTIFY_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/identify-plant?organ=leaf",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        sources = {c["source"] for c in data["candidates"]}
        self.assertIn("plantnet", sources)
        self.assertIn("claude", sources)

    @patch("gardenops.routers.ai._claude_identify_plant")
    @patch("gardenops.services.plantnet.identify")
    def test_duplicate_latin_deduplication(
        self,
        mock_pn: MagicMock,
        mock_claude: MagicMock,
    ) -> None:
        mock_pn.return_value = _plantnet_result_low_confidence()
        mock_claude.return_value = [
            {
                "name": "Løvetann",
                "latin": "Taraxacum officinale",
                "scientific_name": "Taraxacum officinale",
                "family": "Asteraceae",
                "confidence": 0.7,
                "source": "claude",
                "gbif_id": "",
            },
        ]

        with patch.dict(os.environ, _IDENTIFY_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/identify-plant?organ=leaf",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        latins = [c["latin"] for c in data["candidates"]]
        self.assertEqual(len(latins), len({latin.lower() for latin in latins}))

    def test_default_organ_auto(self) -> None:
        """The organ parameter should default to 'auto' when not provided."""
        with (
            patch.dict(os.environ, _IDENTIFY_ENV),
            patch("gardenops.services.plantnet.identify") as mock_pn,
            patch("gardenops.routers.ai._claude_identify_plant") as mock_claude,
        ):
            mock_pn.return_value = _plantnet_result()
            mock_claude.return_value = []
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/identify-plant",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 200)


class TestDiagnosePlant(BaseApiTest):
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
    def test_no_api_key_returns_503(self) -> None:
        img = _make_jpeg()
        resp = self.client.post(
            "/api/ai/diagnose-plant",
            content=img,
            headers={"Content-Type": "image/jpeg"},
        )
        self.assertEqual(resp.status_code, 503)

    def test_no_image_body_returns_400(self) -> None:
        with patch.dict(os.environ, _DIAGNOSE_ENV):
            resp = self.client.post(
                "/api/ai/diagnose-plant",
                content=b"",
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 400)

    @patch("gardenops.routers.ai._claude_diagnose")
    def test_diagnosis_success_with_context(self, mock_diagnose: MagicMock) -> None:
        mock_diagnose.return_value = _CLAUDE_DIAGNOSE_RESPONSE

        with patch.dict(os.environ, _DIAGNOSE_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/diagnose-plant?plt_id=PLT-TEST&symptoms=white+spots",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("diagnoses", data)
        self.assertIn("context_used", data)
        self.assertIn("disclaimer", data)
        self.assertEqual(len(data["diagnoses"]), 1)
        self.assertEqual(data["diagnoses"][0]["issue_type"], "fungal")
        self.assertEqual(data["diagnoses"][0]["confidence"], "high")

    @patch("gardenops.routers.ai._claude_diagnose")
    def test_diagnosis_context_omits_journal_notes_by_default(
        self,
        mock_diagnose: MagicMock,
    ) -> None:
        mock_diagnose.return_value = _CLAUDE_DIAGNOSE_RESPONSE
        secret_note = "private mildew diary detail"
        create = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-06-01",
                "title": "Private note",
                "notes": secret_note,
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(create.status_code, 201, create.text)

        with patch.dict(
            os.environ,
            {**_DIAGNOSE_ENV, "AI_RICH_CONTEXT_ENABLED": "false"},
            clear=False,
        ):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/diagnose-plant?plt_id=PLT-TEST&symptoms=white+spots",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )

        self.assertEqual(resp.status_code, 200)
        prompt = mock_diagnose.call_args.args[1]
        self.assertIn("Recent journal entries:", prompt)
        self.assertIn("(observed)", prompt)
        self.assertNotIn(secret_note, prompt)

    @patch("gardenops.routers.ai._claude_diagnose")
    def test_diagnosis_success_no_context(self, mock_diagnose: MagicMock) -> None:
        mock_diagnose.return_value = _CLAUDE_DIAGNOSE_RESPONSE

        with patch.dict(os.environ, _DIAGNOSE_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/diagnose-plant",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["context_used"]["plant_name"], "")

    @patch("gardenops.routers.ai._claude_diagnose")
    def test_viewer_cannot_exfiltrate_another_users_plant_context(
        self,
        mock_diagnose: MagicMock,
    ) -> None:
        os.environ.update(
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                **_DIAGNOSE_ENV,
            },
        )
        try:
            self._create_test_user("diagnose_viewer", "diagnose-viewer-pass", "viewer")
            client, headers = self._authenticated_client(
                "diagnose_viewer",
                "diagnose-viewer-pass",
                garden_id=self._get_default_garden_id(),
            )
            img = _make_jpeg()
            resp = client.post(
                "/api/ai/diagnose-plant?plt_id=PLT-TEST&symptoms=white+spots",
                content=img,
                headers={
                    **headers,
                    "Content-Type": "image/jpeg",
                },
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

        self.assertEqual(resp.status_code, 404)
        self.assertIn("not found", resp.json()["detail"])
        mock_diagnose.assert_not_called()

    @patch("gardenops.routers.ai._claude_diagnose")
    def test_diagnosis_healthy_plant_empty_array(self, mock_diagnose: MagicMock) -> None:
        mock_diagnose.return_value = []

        with patch.dict(os.environ, _DIAGNOSE_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/diagnose-plant",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["diagnoses"], [])

    @patch("gardenops.routers.ai._claude_diagnose")
    def test_diagnosis_claude_failure_returns_502(self, mock_diagnose: MagicMock) -> None:
        mock_diagnose.side_effect = Exception("claude down")

        with patch.dict(os.environ, _DIAGNOSE_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                "/api/ai/diagnose-plant",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 502)

    def test_symptoms_too_long_rejected(self) -> None:
        long_symptoms = "x" * 501
        with patch.dict(os.environ, _DIAGNOSE_ENV):
            img = _make_jpeg()
            resp = self.client.post(
                f"/api/ai/diagnose-plant?symptoms={long_symptoms}",
                content=img,
                headers={"Content-Type": "image/jpeg"},
            )
        self.assertEqual(resp.status_code, 422)
