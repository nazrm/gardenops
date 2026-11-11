"""Disposable complete-journey ASGI app with the fixture's seasonal date."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from gardenops.routers import workflows as workflow_routes
from gardenops.services import workflow_service

if (
    os.environ.get("APP_ENV") != "test"
    or os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD") != "1"
):
    raise RuntimeError("Seasonal workflow test app requires disposable complete-journey mode")
if not os.environ.get("GARDENOPS_DISPOSABLE_POSTGRES_URL") or (
    os.environ.get("DATABASE_URL") != os.environ.get("GARDENOPS_DISPOSABLE_POSTGRES_URL")
):
    raise RuntimeError("Seasonal workflow test app requires the disposable database")

fixture_path = Path(os.environ["GARDENOPS_COMPLETE_JOURNEYS_E2E_FIXTURE_PATH"])
fixture_date = date.fromisoformat(json.loads(fixture_path.read_text())["phase_four"]["date"])


class FixtureDate(date):
    @classmethod
    def today(cls) -> FixtureDate:
        return cls(fixture_date.year, fixture_date.month, fixture_date.day)


workflow_routes.date = FixtureDate
workflow_service.date = FixtureDate

if int(os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_THROUGH_PHASE", "0")) >= 9:
    from scripts.e2e.performanceFastapiApp import app as app
else:
    from gardenops.main import app as app
