from __future__ import annotations

from datetime import UTC, datetime

from gardenops.branding import app_name, app_slug, app_user_agent
from gardenops.services.calendar_service import build_calendar_ics
from gardenops.services.notification_service import _build_digest_email_body


def test_default_branding_is_gardenops(monkeypatch) -> None:
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("APP_SLUG", raising=False)

    assert app_name() == "GardenOps"
    assert app_slug() == "gardenops"
    assert app_user_agent("Weather Service") == "gardenops/1.0 weather-service"


def test_branding_can_be_overridden_safely(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "  My\r\nForked Garden  ")
    monkeypatch.setenv("APP_SLUG", "  My Fork!  ")

    assert app_name() == "My Forked Garden"
    assert app_slug() == "my-fork"
    assert app_user_agent("Cover Importer") == "my-fork/1.0 cover-importer"


def test_calendar_and_digest_use_configured_product_name(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "PlotWorks")
    monkeypatch.setenv("APP_SLUG", "plotworks")

    ics, _etag, _last_modified = build_calendar_ics(
        garden_name="Kitchen Garden",
        events=[
            {
                "id": "EVT-1",
                "start_on": "2026-05-01",
                "end_on": "2026-05-02",
                "title": "Water",
                "source_key": "water",
                "updated_at_ms": 0,
            },
        ],
        generated_at=datetime(2026, 5, 12, tzinfo=UTC),
    )
    assert "PRODID:-//PlotWorks//Garden Calendar//EN" in ics
    assert "UID:EVT-1@plotworks" in ics

    body = _build_digest_email_body(
        [
            {
                "title": "Water",
                "body": "Bed A",
                "created_at_ms": 1_767_132_000_000,
            },
        ]
    )
    assert "You have new PlotWorks reminders:" in body
    assert "Open PlotWorks to review and resolve these items." in body
