from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gardenops.services.rhs_plant_resolver import RhsResolution
from scripts import repair_rhs_plant_links as repair


class _Cursor:
    def __init__(self, row: dict[str, str] | None = None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, str] | None:
        return self._row


class _FakeConnection:
    def __init__(self, rows: dict[str, dict[str, str]]) -> None:
        self.rows = rows
        self.executions: list[tuple[str, object]] = []
        self.committed = False
        self.rolled_back = False

    def execute(self, query: str, params: object = None) -> _Cursor:
        self.executions.append((query, params))
        if "FROM plants WHERE plt_id" in query:
            assert isinstance(params, tuple)
            return _Cursor(self.rows.get(str(params[0])))
        return _Cursor()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def _verified_item() -> dict[str, object]:
    resolution = RhsResolution(
        status="verified",
        match_type="exact",
        reason="unique_exact_botanical_match",
        query="Lilium 'Blacklist'",
        candidate_count=1,
        external_id="506653",
        external_entity_id="E0293518",
        canonical_url="https://www.rhs.org.uk/plants/506653/lilium-blacklist-iab/details",
        matched_botanical_name="Lilium 'Blacklist' (Ia/b)",
    )
    return {
        "plt_id": "PLT-001",
        "name": "Asiatisk lilje",
        "latin": "Lilium 'Blacklist'",
        "current_link": "https://www.rhs.org.uk/plants/lilium",
        "action": "replace",
        "proposed_link": resolution.canonical_url,
        "resolution": resolution.as_dict(),
        "apply_status": "not_requested",
    }


def _write_report(path: Path, *, generated_at_ms: int = 1_000_000) -> dict[str, object]:
    report = repair._audit_report(
        [_verified_item()],
        scope="rhs",
        replace_non_rhs=False,
        generated_at_ms=generated_at_ms,
    )
    path.write_text(json.dumps(report), encoding="utf-8")
    return report


def test_reviewed_report_rejects_tampered_content(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    report = _write_report(path)
    report["items"][0]["proposed_link"] = "https://www.rhs.org.uk/plants/999/wrong/details"
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(repair.ReportValidationError, match="does not match its SHA-256"):
        repair._load_and_validate_report(
            path,
            confirmed_digest=str(report["report_digest_sha256"]),
            max_age_hours=24,
            now_ms=1_000_001,
        )


def test_reviewed_report_rejects_stale_audit(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    report = _write_report(path)

    with pytest.raises(repair.ReportValidationError, match="report is stale"):
        repair._load_and_validate_report(
            path,
            confirmed_digest=str(report["report_digest_sha256"]),
            max_age_hours=1,
            now_ms=1_000_000 + 60 * 60 * 1000 + 1,
        )


def test_reviewed_report_rejects_resolution_errors(tmp_path: Path) -> None:
    path = tmp_path / "audit.json"
    item = _verified_item()
    item["action"] = "keep"
    item["proposed_link"] = item["current_link"]
    item["resolution"] = RhsResolution(
        status="error",
        match_type="none",
        reason="temporary RHS failure",
        query="Lilium 'Blacklist'",
        candidate_count=0,
    ).as_dict()
    report = repair._audit_report(
        [item],
        scope="rhs",
        replace_non_rhs=False,
        generated_at_ms=1_000_000,
    )
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(repair.ReportValidationError, match="contains resolver errors"):
        repair._load_and_validate_report(
            path,
            confirmed_digest=str(report["report_digest_sha256"]),
            max_age_hours=24,
            now_ms=1_000_001,
        )


def test_apply_uses_report_without_resolving_again(tmp_path: Path) -> None:
    now_ms = repair.db.current_timestamp_ms()
    path = tmp_path / "audit.json"
    report = _write_report(path, generated_at_ms=now_ms)
    output = tmp_path / "receipt.json"
    conn = _FakeConnection(
        {
            "PLT-001": {
                "name": "Asiatisk lilje",
                "latin": "Lilium 'Blacklist'",
                "link": "https://www.rhs.org.uk/plants/lilium",
            }
        }
    )

    argv = [
        "repair_rhs_plant_links.py",
        "--apply-report",
        str(path),
        "--confirm-digest",
        str(report["report_digest_sha256"]),
        "--output",
        str(output),
    ]
    with (
        patch("sys.argv", argv),
        patch.object(repair.db, "get_db", return_value=conn),
        patch.object(repair.db, "return_db"),
        patch.object(
            repair,
            "resolve_rhs_reference",
            side_effect=AssertionError("apply must not query RHS"),
        ),
    ):
        assert repair.main() == 0

    assert conn.committed
    assert not conn.rolled_back
    assert any("UPDATE plants SET link" in query for query, _ in conn.executions)
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["source_report_digest_sha256"] == report["report_digest_sha256"]
    assert receipt["apply_statuses"] == {"updated": 1}


def test_changed_plant_aborts_and_rolls_back_everything() -> None:
    first = _verified_item()
    second = _verified_item()
    second["plt_id"] = "PLT-002"
    conn = _FakeConnection(
        {
            "PLT-001": {
                "name": first["name"],
                "latin": first["latin"],
                "link": first["current_link"],
            },
            "PLT-002": {
                "name": second["name"],
                "latin": second["latin"],
                "link": "https://www.rhs.org.uk/plants/changed",
            },
        }
    )

    with (
        patch.object(repair.db, "get_db", return_value=conn),
        patch.object(repair.db, "return_db"),
        pytest.raises(repair.ReportValidationError, match="no repairs were committed"),
    ):
        repair._apply_results([first, second])

    assert conn.rolled_back
    assert not conn.committed
