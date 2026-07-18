#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGETS = ROOT / "tests" / "performance_budgets.yaml"
DEFAULT_MEASUREMENTS = ROOT / "research" / "optimization-map" / "performance_measurements.json"

BUDGET_FIELDS = frozenset(
    {
        "name",
        "journey",
        "fixture",
        "profile",
        "metric",
        "baseline",
        "limit",
        "sampling",
        "source",
        "rationale",
        "owner",
        "command",
    }
)
MEASUREMENT_FIELDS = frozenset(
    {"budget", "journey", "fixture", "profile", "metric", "unit", "samples"}
)
MEASUREMENT_ROOT_FIELDS = frozenset({"version", "provenance", "measurements"})
MEASUREMENT_PROVENANCE_FIELDS = frozenset(
    {"generator", "git_dirty", "git_revision", "recorded_at", "run_id"}
)
MEASUREMENT_GENERATOR = "gardenops-performance-capture-v1"


class BudgetSchemaError(ValueError):
    pass


class MeasurementError(ValueError):
    pass


class _StrictYamlLoader(yaml.SafeLoader):
    pass


def _construct_unique_yaml_mapping(
    loader: _StrictYamlLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise yaml.YAMLError("YAML mapping keys must be strings")
        if key in mapping:
            raise yaml.YAMLError(f"duplicate YAML mapping key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_yaml_mapping
)


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    detail: str


def _require_exact_fields(
    value: Any, expected: frozenset[str], *, location: str, error_type: type[ValueError]
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise error_type(f"{location} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise error_type(f"{location} keys must be strings")
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing:
        raise error_type(f"{location} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise error_type(f"{location} has unknown fields: {', '.join(sorted(unknown))}")
    return value


def _require_text(
    value: Any, *, location: str, error_type: type[ValueError] = BudgetSchemaError
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{location} must be a non-empty string")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _require_non_negative_number(value: Any, *, location: str) -> float:
    if not _is_number(value) or value < 0:
        raise BudgetSchemaError(f"{location} must be a finite non-negative number")
    return float(value)


def load_budgets(path: Path = DEFAULT_BUDGETS) -> list[dict[str, Any]]:
    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictYamlLoader)
    except (OSError, UnicodeError) as exc:
        raise BudgetSchemaError(f"budget file cannot be read: {path}") from exc
    except yaml.YAMLError as exc:
        raise BudgetSchemaError(f"budget file is invalid YAML: {exc}") from exc

    root = _require_exact_fields(
        payload,
        frozenset({"version", "budgets"}),
        location="budget root",
        error_type=BudgetSchemaError,
    )
    if (
        not isinstance(root["version"], int)
        or isinstance(root["version"], bool)
        or root["version"] != 1
    ):
        raise BudgetSchemaError("budget version must be 1")
    if not isinstance(root["budgets"], list) or not root["budgets"]:
        raise BudgetSchemaError("budgets must be a non-empty list")

    names: set[str] = set()
    for index, raw_budget in enumerate(root["budgets"]):
        location = f"budget at index {index}"
        budget = _require_exact_fields(
            raw_budget, BUDGET_FIELDS, location=location, error_type=BudgetSchemaError
        )
        name = _require_text(budget["name"], location=f"{location}.name")
        if name in names:
            raise BudgetSchemaError(f"duplicate budget name: {name}")
        names.add(name)
        for field in ("journey", "fixture", "profile", "rationale", "owner", "command"):
            _require_text(budget[field], location=f"{name}.{field}")

        source = _require_exact_fields(
            budget["source"],
            frozenset({"api_mode", "artifact", "device_profile", "scenario"}),
            location=f"{name}.source",
            error_type=BudgetSchemaError,
        )
        if source["artifact"] != "page-performance-v6":
            raise BudgetSchemaError(f"{name}.source.artifact must be page-performance-v6")
        for field in ("api_mode", "device_profile", "scenario"):
            _require_text(source[field], location=f"{name}.source.{field}")

        metric = _require_exact_fields(
            budget["metric"],
            frozenset({"name", "unit"}),
            location=f"{name}.metric",
            error_type=BudgetSchemaError,
        )
        _require_text(metric["name"], location=f"{name}.metric.name")
        _require_text(metric["unit"], location=f"{name}.metric.unit")

        baseline = _require_exact_fields(
            budget["baseline"],
            frozenset({"status", "median", "p75"}),
            location=f"{name}.baseline",
            error_type=BudgetSchemaError,
        )
        if baseline["status"] not in {"pending", "established"}:
            raise BudgetSchemaError(f"{name}.baseline.status must be pending or established")
        if baseline["status"] == "pending":
            if baseline["median"] is not None or baseline["p75"] is not None:
                raise BudgetSchemaError(f"{name}: pending baseline median and p75 must be null")
        else:
            _require_non_negative_number(baseline["median"], location=f"{name}.baseline.median")
            _require_non_negative_number(baseline["p75"], location=f"{name}.baseline.p75")
            if baseline["p75"] < baseline["median"]:
                raise BudgetSchemaError(f"{name}.baseline.p75 must be at least baseline.median")

        limit = budget["limit"]
        if not isinstance(limit, dict):
            raise BudgetSchemaError(f"{name}.limit must be a mapping")
        limit_type = limit.get("type")
        if limit_type == "regression_percent":
            _require_exact_fields(
                limit,
                frozenset({"type", "max_percent"}),
                location=f"{name}.limit",
                error_type=BudgetSchemaError,
            )
            if (
                _require_non_negative_number(
                    limit["max_percent"], location=f"{name}.limit.max_percent"
                )
                > 100
            ):
                raise BudgetSchemaError(f"{name}.limit.max_percent must not exceed 100")
        elif limit_type == "absolute_ceiling":
            _require_exact_fields(
                limit,
                frozenset({"type", "value"}),
                location=f"{name}.limit",
                error_type=BudgetSchemaError,
            )
            _require_non_negative_number(limit["value"], location=f"{name}.limit.value")
        else:
            raise BudgetSchemaError(
                f"{name}.limit.type must be regression_percent or absolute_ceiling"
            )

        sampling = _require_exact_fields(
            budget["sampling"],
            frozenset({"minimum_samples", "max_coefficient_of_variation"}),
            location=f"{name}.sampling",
            error_type=BudgetSchemaError,
        )
        minimum_samples = sampling["minimum_samples"]
        if (
            not isinstance(minimum_samples, int)
            or isinstance(minimum_samples, bool)
            or minimum_samples < 2
        ):
            raise BudgetSchemaError(f"{name}.sampling.minimum_samples must be an integer >= 2")
        max_cv = _require_non_negative_number(
            sampling["max_coefficient_of_variation"],
            location=f"{name}.sampling.max_coefficient_of_variation",
        )
        if max_cv > 1:
            raise BudgetSchemaError(
                f"{name}.sampling.max_coefficient_of_variation must not exceed 1"
            )
    return root["budgets"]


def _reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise MeasurementError(f"duplicate JSON mapping key: {key}")
        payload[key] = value
    return payload


def _validate_measurement_provenance(value: Any) -> dict[str, Any]:
    provenance = _require_exact_fields(
        value,
        MEASUREMENT_PROVENANCE_FIELDS,
        location="measurement provenance",
        error_type=MeasurementError,
    )
    if provenance["generator"] != MEASUREMENT_GENERATOR:
        raise MeasurementError(
            f"measurement provenance.generator must be {MEASUREMENT_GENERATOR!r}"
        )
    _require_text(
        provenance["git_revision"],
        location="measurement provenance.git_revision",
        error_type=MeasurementError,
    )
    if not isinstance(provenance["git_dirty"], bool) or provenance["git_dirty"]:
        raise MeasurementError("measurement provenance.git_dirty must be false")
    _require_text(
        provenance["recorded_at"],
        location="measurement provenance.recorded_at",
        error_type=MeasurementError,
    )
    try:
        recorded_at = datetime.fromisoformat(provenance["recorded_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise MeasurementError("measurement provenance.recorded_at must be ISO-8601") from exc
    if recorded_at.tzinfo is None:
        raise MeasurementError("measurement provenance.recorded_at must include a timezone")
    _require_text(
        provenance["run_id"],
        location="measurement provenance.run_id",
        error_type=MeasurementError,
    )
    try:
        uuid.UUID(provenance["run_id"])
    except (AttributeError, ValueError) as exc:
        raise MeasurementError("measurement provenance.run_id must be a UUID") from exc
    return provenance


def load_measurement_document(path: Path) -> tuple[list[Any], dict[str, Any]]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_json_pairs
        )
    except (OSError, UnicodeError) as exc:
        raise MeasurementError(f"measurement file cannot be read: {path}") from exc
    except (json.JSONDecodeError, MeasurementError) as exc:
        raise MeasurementError(f"measurement file is invalid JSON: {exc}") from exc
    root = _require_exact_fields(
        payload,
        MEASUREMENT_ROOT_FIELDS,
        location="measurement root",
        error_type=MeasurementError,
    )
    if (
        not isinstance(root["version"], int)
        or isinstance(root["version"], bool)
        or root["version"] != 1
    ):
        raise MeasurementError("measurement version must be 1")
    if not isinstance(root["measurements"], list):
        raise MeasurementError("measurements must be a list")
    return root["measurements"], _validate_measurement_provenance(root["provenance"])


def load_measurements(path: Path) -> list[Any]:
    measurements, _provenance = load_measurement_document(path)
    return measurements


def validate_measurement_provenance(provenance: dict[str, Any], *, max_age_hours: float) -> None:
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise MeasurementError("maximum measurement age must be a positive finite number")
    try:
        current_revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise MeasurementError("current Git revision is unavailable") from exc
    if provenance["git_revision"] != current_revision:
        raise MeasurementError(
            "measurement provenance.git_revision does not match the current Git revision"
        )
    recorded_at = datetime.fromisoformat(provenance["recorded_at"].replace("Z", "+00:00"))
    age = datetime.now(UTC) - recorded_at.astimezone(UTC)
    if age < timedelta(minutes=-5):
        raise MeasurementError("measurement provenance.recorded_at is in the future")
    if age > timedelta(hours=max_age_hours):
        raise MeasurementError(f"measurement provenance is older than {max_age_hours:g} hours")


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _evaluate_budget(budget: dict[str, Any], raw_measurement: Any) -> Result:
    name = budget["name"]
    try:
        measurement = _require_exact_fields(
            raw_measurement,
            MEASUREMENT_FIELDS,
            location=f"measurement for {name}",
            error_type=MeasurementError,
        )
    except MeasurementError as exc:
        return Result(name, "INCONCLUSIVE", str(exc))

    expected = {
        "budget": name,
        "journey": budget["journey"],
        "fixture": budget["fixture"],
        "profile": budget["profile"],
        "metric": budget["metric"]["name"],
        "unit": budget["metric"]["unit"],
    }
    for field, expected_value in expected.items():
        if measurement[field] != expected_value:
            return Result(
                name,
                "INCONCLUSIVE",
                f"measurement {field} is {measurement[field]!r}; expected {expected_value!r}",
            )

    raw_samples = measurement["samples"]
    if not isinstance(raw_samples, list) or any(
        not _is_number(sample) or sample < 0 for sample in raw_samples
    ):
        return Result(name, "INCONCLUSIVE", "samples must be finite non-negative numbers")
    samples = [float(sample) for sample in raw_samples]
    minimum = budget["sampling"]["minimum_samples"]
    if len(samples) < minimum:
        return Result(name, "INCONCLUSIVE", f"received {len(samples)} samples; requires {minimum}")

    mean = statistics.fmean(samples)
    coefficient_of_variation = statistics.pstdev(samples) / mean if mean else 0.0
    max_cv = budget["sampling"]["max_coefficient_of_variation"]
    if coefficient_of_variation > max_cv:
        return Result(
            name,
            "INCONCLUSIVE",
            f"coefficient of variation {coefficient_of_variation:.4f} exceeds {max_cv:.4f}",
        )

    if budget["baseline"]["status"] == "pending":
        return Result(name, "INCONCLUSIVE", "baseline median and p75 are pending review")

    median = statistics.median(samples)
    p75 = _percentile(samples, 0.75)
    limit = budget["limit"]
    if limit["type"] == "regression_percent":
        multiplier = 1 + limit["max_percent"] / 100
        median_ceiling = budget["baseline"]["median"] * multiplier
        p75_ceiling = budget["baseline"]["p75"] * multiplier
    else:
        median_ceiling = p75_ceiling = limit["value"]

    failures = []
    if median > median_ceiling:
        failures.append(f"median {median:.3f} > {median_ceiling:.3f}")
    if p75 > p75_ceiling:
        failures.append(f"p75 {p75:.3f} > {p75_ceiling:.3f}")
    detail = (
        "; ".join(failures)
        if failures
        else f"median {median:.3f} <= {median_ceiling:.3f}; p75 {p75:.3f} <= {p75_ceiling:.3f}"
    )
    return Result(name, "FAIL" if failures else "PASS", detail)


def evaluate(budgets: list[dict[str, Any]], measurements: list[Any]) -> list[Result]:
    by_name: dict[str, list[Any]] = {}
    invalid_entries = 0
    for measurement in measurements:
        if isinstance(measurement, dict) and isinstance(measurement.get("budget"), str):
            by_name.setdefault(measurement["budget"], []).append(measurement)
        else:
            invalid_entries += 1

    results: list[Result] = []
    known_names = {budget["name"] for budget in budgets}
    if invalid_entries:
        results.append(
            Result(
                "measurements",
                "INCONCLUSIVE",
                f"{invalid_entries} entries lack a valid budget name",
            )
        )
    for unknown_name in sorted(set(by_name) - known_names):
        results.append(Result(unknown_name, "INCONCLUSIVE", "measurement names no defined budget"))
    for budget in budgets:
        matches = by_name.get(budget["name"], [])
        if not matches:
            if budget["baseline"]["status"] == "pending":
                results.append(
                    Result(
                        budget["name"],
                        "INCONCLUSIVE",
                        "baseline median and p75 are pending review",
                    )
                )
            else:
                results.append(Result(budget["name"], "INCONCLUSIVE", "measurement is missing"))
        elif len(matches) > 1:
            results.append(Result(budget["name"], "INCONCLUSIVE", "multiple measurements supplied"))
        else:
            results.append(_evaluate_budget(budget, matches[0]))
    return results


def _exit_code(results: list[Result]) -> int:
    if any(result.status == "FAIL" for result in results):
        return 1
    if any(result.status == "INCONCLUSIVE" for result in results):
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check measured performance against durable budgets."
    )
    parser.add_argument("measurement", nargs="?", type=Path, help="measurement JSON path")
    parser.add_argument("--measurements", dest="measurement_option", type=Path)
    parser.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    parser.add_argument("--max-evidence-age-hours", type=float, default=24.0)
    args = parser.parse_args(argv)
    measurement_path = args.measurement_option or args.measurement or DEFAULT_MEASUREMENTS

    try:
        budgets = load_budgets(args.budgets)
    except BudgetSchemaError as exc:
        print(f"INCONCLUSIVE budget-schema: {exc}")
        print("OVERALL INCONCLUSIVE")
        return 2
    try:
        measurements, provenance = load_measurement_document(measurement_path)
        validate_measurement_provenance(provenance, max_age_hours=args.max_evidence_age_hours)
    except MeasurementError as exc:
        print(f"INCONCLUSIVE measurements: {exc}")
        for budget in budgets:
            print(f"INCONCLUSIVE {budget['name']}: no valid measurement input")
        print("OVERALL INCONCLUSIVE")
        return 2

    results = evaluate(budgets, measurements)
    for result in results:
        print(f"{result.status} {result.name}: {result.detail}")
    exit_code = _exit_code(results)
    overall = "FAIL" if exit_code == 1 else "INCONCLUSIVE" if exit_code == 2 else "PASS"
    print(f"OVERALL {overall}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
