#!/usr/bin/env python3
"""Audit or repair plant links using strict RHS identity matching."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from gardenops import db
from gardenops.services.rhs_plant_resolver import (
    RhsResolution,
    is_rhs_plant_url,
    planned_link_update,
    resolve_rhs_reference,
)

_REPORT_KIND = "gardenops_rhs_link_audit"
_REPORT_SCHEMA_VERSION = 1
_DEFAULT_MAX_REPORT_AGE_HOURS = 24.0
_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReportValidationError(RuntimeError):
    """A reviewed report cannot be trusted or safely applied."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve plant identities against RHS. The default is a read-only JSON dry run."
        )
    )
    parser.add_argument(
        "--scope",
        choices=("rhs", "all"),
        default="rhs",
        help="audit current RHS links only, or all plants (default: rhs)",
    )
    parser.add_argument("--plant-id", action="append", default=[], help="limit to a plant ID")
    parser.add_argument("--limit", type=int, default=0, help="maximum plants to process")
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.35,
        help="delay between plants to avoid sustained RHS traffic",
    )
    parser.add_argument(
        "--apply-report",
        type=Path,
        help="apply this exact, reviewed dry-run report without querying RHS again",
    )
    parser.add_argument(
        "--confirm-digest",
        default="",
        help="required with --apply-report; must equal the report SHA-256 digest",
    )
    parser.add_argument(
        "--max-report-age-hours",
        type=float,
        default=_DEFAULT_MAX_REPORT_AGE_HOURS,
        help="maximum age accepted by --apply-report (default: 24)",
    )
    parser.add_argument(
        "--replace-non-rhs",
        action="store_true",
        help="replace an existing non-RHS link when an exact RHS match is verified",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the complete JSON report to this path and print only its summary",
    )
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be zero or greater")
    if args.delay_seconds < 0:
        parser.error("--delay-seconds must be zero or greater")
    if args.max_report_age_hours <= 0:
        parser.error("--max-report-age-hours must be greater than zero")
    if args.apply_report and not _SHA256_RE.fullmatch(args.confirm_digest):
        parser.error("--apply-report requires --confirm-digest with 64 lowercase hex characters")
    if not args.apply_report and args.confirm_digest:
        parser.error("--confirm-digest requires --apply-report")
    if args.apply_report and (args.plant_id or args.limit or args.scope != "rhs"):
        parser.error("audit selection options cannot be combined with --apply-report")
    if args.replace_non_rhs and args.scope != "all":
        parser.error("--replace-non-rhs requires --scope all")
    if args.apply_report and args.replace_non_rhs:
        parser.error("--replace-non-rhs cannot be combined with --apply-report")
    return args


def _load_plants(*, scope: str, plant_ids: list[str], limit: int) -> list[dict[str, Any]]:
    conn = db.get_db()
    try:
        sql = (
            "SELECT plt_id, name, COALESCE(latin, '') AS latin, "
            "COALESCE(link, '') AS link FROM plants"
        )
        params: list[object] = []
        if plant_ids:
            placeholders = ",".join("%s" for _ in plant_ids)
            sql += f" WHERE plt_id IN ({placeholders})"
            params.extend(plant_ids)
        sql += " ORDER BY plt_id"
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        db.return_db(conn)

    if scope == "rhs":
        rows = [row for row in rows if is_rhs_plant_url(str(row["link"]))]
    if limit:
        rows = rows[:limit]
    return rows


def _resolve_rows(
    rows: list[dict[str, Any]],
    *,
    delay_seconds: float,
    replace_non_rhs: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        resolution = resolve_rhs_reference(
            latin=str(row["latin"]),
            common_name=str(row["name"]),
            current_link=str(row["link"]),
        )
        action, proposed_link = planned_link_update(
            str(row["link"]),
            resolution,
            replace_non_rhs=replace_non_rhs,
        )
        results.append(
            {
                "plt_id": str(row["plt_id"]),
                "name": str(row["name"]),
                "latin": str(row["latin"]),
                "current_link": str(row["link"]),
                "action": action,
                "proposed_link": proposed_link,
                "resolution": resolution.as_dict(),
                "apply_status": "not_requested",
            }
        )
        if delay_seconds and index + 1 < len(rows):
            time.sleep(delay_seconds)
    return results


def _upsert_reference(
    conn: db.DbConn,
    *,
    item: dict[str, Any],
    resolution: RhsResolution,
    verified_at_ms: int,
) -> None:
    metadata = json.dumps(
        {
            "query": resolution.query,
            "candidate_count": resolution.candidate_count,
            "candidates": resolution.candidates,
            "previous_url": item["current_link"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    conn.execute(
        """
        INSERT INTO plant_external_references (
            plt_id, source, external_id, external_entity_id, canonical_url,
            matched_botanical_name, matched_common_name, match_type,
            verification_status, verification_reason, metadata_json, verified_at_ms
        ) VALUES (%s, 'rhs', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (plt_id, source) DO UPDATE SET
            external_id = EXCLUDED.external_id,
            external_entity_id = EXCLUDED.external_entity_id,
            canonical_url = EXCLUDED.canonical_url,
            matched_botanical_name = EXCLUDED.matched_botanical_name,
            matched_common_name = EXCLUDED.matched_common_name,
            match_type = EXCLUDED.match_type,
            verification_status = EXCLUDED.verification_status,
            verification_reason = EXCLUDED.verification_reason,
            metadata_json = EXCLUDED.metadata_json,
            verified_at_ms = EXCLUDED.verified_at_ms
        """,
        (
            item["plt_id"],
            resolution.external_id,
            resolution.external_entity_id,
            resolution.canonical_url,
            resolution.matched_botanical_name,
            resolution.matched_common_name,
            resolution.match_type,
            resolution.status,
            resolution.reason,
            metadata,
            verified_at_ms,
        ),
    )


def _apply_results(results: list[dict[str, Any]]) -> None:
    conn = db.get_db()
    try:
        verified_at_ms = db.current_timestamp_ms()
        for item in results:
            current = conn.execute(
                """
                SELECT name, COALESCE(latin, '') AS latin, COALESCE(link, '') AS link
                FROM plants WHERE plt_id = %s FOR UPDATE
                """,
                (item["plt_id"],),
            ).fetchone()
            if current is None:
                raise ReportValidationError(
                    f"plant {item['plt_id']} no longer exists; no repairs were committed"
                )
            if (
                str(current["name"]) != item["name"]
                or str(current["latin"]) != item["latin"]
                or str(current["link"]) != item["current_link"]
            ):
                raise ReportValidationError(
                    f"plant {item['plt_id']} changed after audit; no repairs were committed"
                )

            resolution = RhsResolution(**item["resolution"])
            _upsert_reference(
                conn,
                item=item,
                resolution=resolution,
                verified_at_ms=verified_at_ms,
            )
            if item["action"] in {"replace", "clear"}:
                conn.execute(
                    "UPDATE plants SET link = %s WHERE plt_id = %s",
                    (item["proposed_link"], item["plt_id"]),
                )
                item["apply_status"] = "updated"
            else:
                item["apply_status"] = "recorded"
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db.return_db(conn)


def _result_summary(results: list[dict[str, Any]]) -> dict[str, object]:
    status_counts = Counter(str(item["resolution"]["status"]) for item in results)
    action_counts = Counter(str(item["action"]) for item in results)
    apply_counts = Counter(str(item["apply_status"]) for item in results)
    return {
        "processed": len(results),
        "resolution_statuses": dict(sorted(status_counts.items())),
        "actions": dict(sorted(action_counts.items())),
        "apply_statuses": dict(sorted(apply_counts.items())),
    }


def _canonical_report_bytes(report: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in report.items() if key != "report_digest_sha256"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _report_digest(report: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_report_bytes(report)).hexdigest()


def _audit_report(
    results: list[dict[str, Any]],
    *,
    scope: str,
    replace_non_rhs: bool,
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "report_kind": _REPORT_KIND,
        "schema_version": _REPORT_SCHEMA_VERSION,
        "mode": "dry-run",
        "generated_at_ms": (
            db.current_timestamp_ms() if generated_at_ms is None else generated_at_ms
        ),
        "audit_options": {
            "scope": scope,
            "replace_non_rhs": replace_non_rhs,
        },
        **_result_summary(results),
        "items": results,
    }
    report["report_digest_sha256"] = _report_digest(report)
    return report


def _require_dict(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportValidationError(f"{description} must be a JSON object")
    return cast(dict[str, Any], value)


def _validate_report_item(item: object, *, replace_non_rhs: bool) -> dict[str, Any]:
    row = _require_dict(item, "report item")
    required_strings = ("plt_id", "name", "latin", "current_link", "action", "proposed_link")
    for field in required_strings:
        if not isinstance(row.get(field), str):
            raise ReportValidationError(f"report item {field} must be a string")
    if not row["plt_id"]:
        raise ReportValidationError("report item plant ID cannot be empty")
    if row.get("apply_status") != "not_requested":
        raise ReportValidationError(f"plant {row['plt_id']} is not an unapplied audit result")

    resolution_raw = _require_dict(row.get("resolution"), "item resolution")
    try:
        resolution = RhsResolution(**resolution_raw)
    except (TypeError, ValueError) as exc:
        raise ReportValidationError(f"plant {row['plt_id']} has an invalid resolution") from exc
    if resolution.status not in {"verified", "needs_review", "not_found", "error"}:
        raise ReportValidationError(f"plant {row['plt_id']} has an invalid resolution status")
    if resolution.match_type not in {"exact", "synonym", "none"}:
        raise ReportValidationError(f"plant {row['plt_id']} has an invalid match type")
    if resolution.candidate_count < 0:
        raise ReportValidationError(f"plant {row['plt_id']} has an invalid candidate count")
    if resolution.verified and not is_rhs_plant_url(resolution.canonical_url):
        raise ReportValidationError(f"plant {row['plt_id']} has an invalid verified RHS URL")

    expected_action, expected_link = planned_link_update(
        row["current_link"],
        resolution,
        replace_non_rhs=replace_non_rhs,
    )
    if row["action"] != expected_action or row["proposed_link"] != expected_link:
        raise ReportValidationError(f"plant {row['plt_id']} action does not match its resolution")
    return row


def _load_and_validate_report(
    path: Path,
    *,
    confirmed_digest: str,
    max_age_hours: float,
    now_ms: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportValidationError(f"could not read valid JSON report: {path}") from exc
    report = _require_dict(raw, "report")
    if report.get("report_kind") != _REPORT_KIND:
        raise ReportValidationError("report kind is not supported")
    if report.get("schema_version") != _REPORT_SCHEMA_VERSION:
        raise ReportValidationError("report schema version is not supported")
    if report.get("mode") != "dry-run":
        raise ReportValidationError("only a dry-run audit report can be applied")

    stored_digest = report.get("report_digest_sha256")
    if not isinstance(stored_digest, str) or not _SHA256_RE.fullmatch(stored_digest):
        raise ReportValidationError("report is missing a valid SHA-256 digest")
    actual_digest = _report_digest(report)
    if not hmac.compare_digest(stored_digest, actual_digest):
        raise ReportValidationError("report content does not match its SHA-256 digest")
    if not hmac.compare_digest(confirmed_digest, stored_digest):
        raise ReportValidationError("confirmed digest does not match the reviewed report")

    generated_at_ms = report.get("generated_at_ms")
    if not isinstance(generated_at_ms, int) or isinstance(generated_at_ms, bool):
        raise ReportValidationError("report generation time is invalid")
    effective_now_ms = now_ms if now_ms is not None else db.current_timestamp_ms()
    if generated_at_ms > effective_now_ms + _MAX_CLOCK_SKEW_MS:
        raise ReportValidationError("report generation time is in the future")
    max_age_ms = int(max_age_hours * 60 * 60 * 1000)
    if effective_now_ms - generated_at_ms > max_age_ms:
        raise ReportValidationError("report is stale; generate and review a new audit")

    options = _require_dict(report.get("audit_options"), "audit options")
    scope = options.get("scope")
    replace_non_rhs = options.get("replace_non_rhs")
    if scope not in {"rhs", "all"} or not isinstance(replace_non_rhs, bool):
        raise ReportValidationError("report audit options are invalid")

    raw_items = report.get("items")
    if not isinstance(raw_items, list):
        raise ReportValidationError("report items must be a JSON array")
    results = [_validate_report_item(item, replace_non_rhs=replace_non_rhs) for item in raw_items]
    plant_ids = [item["plt_id"] for item in results]
    if len(plant_ids) != len(set(plant_ids)):
        raise ReportValidationError("report contains duplicate plant IDs")
    if any(item["resolution"]["status"] == "error" for item in results):
        raise ReportValidationError("report contains resolver errors; generate a clean audit")

    expected_summary = _result_summary(results)
    for key, value in expected_summary.items():
        if report.get(key) != value:
            raise ReportValidationError(f"report {key} summary does not match its items")
    return report, results


def _application_receipt(
    results: list[dict[str, Any]],
    *,
    source_digest: str,
) -> dict[str, Any]:
    return {
        "report_kind": "gardenops_rhs_link_application_receipt",
        "schema_version": _REPORT_SCHEMA_VERSION,
        "mode": "apply",
        "source_report_digest_sha256": source_digest,
        "applied_at_ms": db.current_timestamp_ms(),
        **_result_summary(results),
        "items": results,
    }


def _emit_report(report: dict[str, Any], output: Path | None) -> None:
    if output:
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summary = {key: value for key, value in report.items() if key != "items"}
        summary["output"] = str(output)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> int:
    args = _parse_args()
    if args.apply_report:
        try:
            source_report, source_results = _load_and_validate_report(
                args.apply_report,
                confirmed_digest=args.confirm_digest,
                max_age_hours=args.max_report_age_hours,
            )
            results = deepcopy(source_results)
            _apply_results(results)
        except ReportValidationError as exc:
            print(f"RHS repair refused: {exc}", file=sys.stderr)
            return 2
        receipt = _application_receipt(
            results,
            source_digest=str(source_report["report_digest_sha256"]),
        )
        _emit_report(receipt, args.output)
        return 0

    rows = _load_plants(scope=args.scope, plant_ids=args.plant_id, limit=args.limit)
    results = _resolve_rows(
        rows,
        delay_seconds=args.delay_seconds,
        replace_non_rhs=args.replace_non_rhs,
    )
    report = _audit_report(
        results,
        scope=args.scope,
        replace_non_rhs=args.replace_non_rhs,
    )
    _emit_report(report, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
