"""Conservative RHS plant identity resolution.

RHS search is candidate discovery only. A link is returned only when the complete
botanical identity matches one unique accepted RHS name or an explicit RHS synonym.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from gardenops.branding import app_user_agent

_RHS_SEARCH_API_URL = "https://lwapp-uks-prod-psearch-01.azurewebsites.net/api/v1/plants/search"
_RHS_DETAILS_API_URL = "https://lwapp-uks-prod-psearch-01.azurewebsites.net/api/v1/plants/details"
_RHS_PUBLIC_HOST = "www.rhs.org.uk"
_RHS_RESPONSE_LIMIT = 1_000_000
_RHS_RESULT_LIMIT = 10
_MAX_BOTANICAL_NAME_LENGTH = 200
_MAX_BOTANICAL_QUERIES = 3
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_TRAILING_ANNOTATION_RE = re.compile(r"\s*\([^()]*\)\s*$")
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_NON_SLUG_RE = re.compile(r"[^a-z0-9_()]+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_QUOTE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u02bc": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00d7": " x ",
        "\u00a0": " ",
    }
)

ResolutionStatus = Literal["verified", "needs_review", "not_found", "error"]
MatchType = Literal["exact", "synonym", "none"]
LinkRepairAction = Literal["keep", "replace", "clear"]


class RhsResolverError(RuntimeError):
    """RHS could not be queried or returned an invalid response."""


class RhsPlantNotFoundError(RhsResolverError):
    """RHS confirmed that a requested plant ID does not exist."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass(frozen=True)
class RhsSearchHit:
    plant_id: int
    botanical_name: str
    common_name: str
    is_synonym: bool
    synonym_parent_id: int


@dataclass(frozen=True)
class RhsPlantDetails:
    plant_id: int
    botanical_name: str
    common_name: str
    entity_id: str
    name_status: str
    is_synonym: bool
    synonym_parent_id: int
    synonyms: tuple[str, ...]


@dataclass(frozen=True)
class RhsResolution:
    status: ResolutionStatus
    match_type: MatchType
    reason: str
    query: str
    candidate_count: int
    external_id: str = ""
    external_entity_id: str = ""
    canonical_url: str = ""
    matched_botanical_name: str = ""
    matched_common_name: str = ""
    candidates: tuple[dict[str, object], ...] = ()

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class RhsClient(Protocol):
    def search(self, keywords: str) -> list[RhsSearchHit]: ...

    def details(self, plant_id: int) -> RhsPlantDetails: ...

    def canonical_url(self, plant_id: int, botanical_name: str) -> str: ...


def _plain_name(value: str) -> str:
    return html.unescape(_HTML_TAG_RE.sub("", value)).translate(_QUOTE_TRANSLATION).strip()


def _without_trailing_annotations(value: str) -> str:
    result = _plain_name(value)
    while True:
        stripped = _TRAILING_ANNOTATION_RE.sub("", result).strip()
        if stripped == result:
            return stripped
        result = stripped


def normalize_botanical_name(value: str) -> str:
    """Normalize punctuation and RHS display annotations without dropping identity tokens."""
    normalized = unicodedata.normalize("NFKD", _without_trailing_annotations(value))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(_TOKEN_RE.findall(normalized.lower()))


def _cultivar_text(value: str) -> str:
    plain = _plain_name(value)
    start = plain.find("'")
    end = plain.rfind("'")
    if start < 0 or end <= start:
        return ""
    return plain[start + 1 : end].strip()


def _genus_and_species(value: str) -> tuple[str, str]:
    plain = _plain_name(value)
    cultivar_start = plain.find("'")
    prefix = plain if cultivar_start < 0 else plain[:cultivar_start]
    tokens = normalize_botanical_name(prefix).split()
    genus = tokens[0] if tokens else ""
    species = tokens[1] if len(tokens) > 1 and tokens[1] not in {"x", "group"} else ""
    return genus, species


def _identity_match_score(query: str, candidate: str) -> int:
    if normalize_botanical_name(query) == normalize_botanical_name(candidate):
        return 30
    query_cultivar = normalize_botanical_name(_cultivar_text(query))
    candidate_cultivar = normalize_botanical_name(_cultivar_text(candidate))
    if not query_cultivar or query_cultivar != candidate_cultivar:
        return 0
    query_genus, query_species = _genus_and_species(query)
    candidate_genus, candidate_species = _genus_and_species(candidate)
    if not query_genus or query_genus != candidate_genus:
        return 0
    if query_species and query_species == candidate_species:
        return 20
    return 10


def botanical_queries(latin: str, common_name: str) -> tuple[str, ...]:
    """Build botanical queries while using a common-name cultivar as extra identity data."""
    if len(latin) > _MAX_BOTANICAL_NAME_LENGTH:
        return ()
    latin_variants = [
        part.strip()
        for part in re.split(r"\s+/\s+", latin, maxsplit=_MAX_BOTANICAL_QUERIES)[
            :_MAX_BOTANICAL_QUERIES
        ]
        if part.strip()
    ]
    if not latin_variants:
        return ()

    common_cultivar = (
        _cultivar_text(common_name) if len(common_name) <= _MAX_BOTANICAL_NAME_LENGTH else ""
    )
    common_cultivar_key = normalize_botanical_name(common_cultivar)
    queries: list[str] = []
    for variant in latin_variants:
        query = _plain_name(variant)
        if common_cultivar_key:
            latin_key = normalize_botanical_name(query)
            cultivar_tokens = set(common_cultivar_key.split())
            if not cultivar_tokens.issubset(set(latin_key.split())):
                query = f"{query} '{common_cultivar}'"
        if query and query not in queries:
            queries.append(query)
    return tuple(queries)


def is_rhs_plant_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return parsed.scheme == "https" and host == "rhs.org.uk" and parsed.path.startswith("/plants/")


def rhs_plant_id_from_url(value: str) -> int | None:
    if not is_rhs_plant_url(value):
        return None
    parts = urllib.parse.urlparse(value.strip()).path.split("/")
    if len(parts) < 3 or not parts[2].isdigit():
        return None
    return int(parts[2])


def _exact_genus_page(latin: str, current_link: str) -> str:
    latin_key = normalize_botanical_name(latin)
    if len(latin_key.split()) != 1 or not is_rhs_plant_url(current_link):
        return ""
    parsed = urllib.parse.urlparse(current_link.strip())
    if parsed.path.lower().rstrip("/") != f"/plants/{latin_key}":
        return ""
    return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))


def planned_link_update(
    current_link: str,
    resolution: RhsResolution,
    *,
    replace_non_rhs: bool = False,
) -> tuple[LinkRepairAction, str]:
    """Choose a conservative data repair without clearing links on transient errors."""
    current = current_link.strip()
    if resolution.verified:
        if current == resolution.canonical_url:
            return "keep", current
        if not current or is_rhs_plant_url(current) or replace_non_rhs:
            return "replace", resolution.canonical_url
        return "keep", current
    if resolution.status == "needs_review" and resolution.reason == "rhs_name_not_accepted":
        return "keep", current
    if resolution.status in {"needs_review", "not_found"} and is_rhs_plant_url(current):
        return "clear", ""
    return "keep", current


def _slugify_rhs_name(value: str) -> str:
    # Mirrors the formatter used by the current RHS plant-search frontend.
    slug = _NON_SLUG_RE.sub("-", _plain_name(value).strip("-"))
    return slug.rstrip("-").lower().strip("-")


def _read_json_response(response: Any) -> dict[str, Any]:
    raw = response.read(_RHS_RESPONSE_LIMIT + 1)
    if len(raw) > _RHS_RESPONSE_LIMIT:
        raise RhsResolverError("RHS response exceeded size limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RhsResolverError("RHS returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RhsResolverError("RHS returned an invalid response object")
    return value


class HttpRhsClient:
    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._api_opener = urllib.request.build_opener(_NoRedirectHandler())

    def _request_json(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with self._api_opener.open(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                return _read_json_response(response)
        except RhsResolverError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise RhsPlantNotFoundError("RHS plant record was not found") from exc
            raise RhsResolverError("RHS request failed") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RhsResolverError("RHS request failed") from exc

    def search(self, keywords: str) -> list[RhsSearchHit]:
        payload = json.dumps(
            {"pageSize": _RHS_RESULT_LIMIT, "startFrom": 0, "keywords": keywords},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            _RHS_SEARCH_API_URL,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": app_user_agent("rhs-plant-resolver"),
            },
        )
        response = self._request_json(request)
        raw_hits = response.get("hits")
        if not isinstance(raw_hits, list):
            raise RhsResolverError("RHS search response omitted hits")

        hits: list[RhsSearchHit] = []
        for raw in raw_hits:
            if not isinstance(raw, dict):
                continue
            try:
                plant_id = int(raw["id"])
            except KeyError, TypeError, ValueError:
                continue
            botanical_name = _plain_name(str(raw.get("botanicalName") or ""))
            if not botanical_name:
                continue
            hits.append(
                RhsSearchHit(
                    plant_id=plant_id,
                    botanical_name=botanical_name,
                    common_name=str(raw.get("commonName") or "").strip(),
                    is_synonym=bool(raw.get("isSynonym")),
                    synonym_parent_id=int(raw.get("synonymParentPlantId") or 0),
                )
            )
        return hits

    def details(self, plant_id: int) -> RhsPlantDetails:
        request = urllib.request.Request(
            f"{_RHS_DETAILS_API_URL}/{plant_id}",
            headers={
                "Accept": "application/json",
                "User-Agent": app_user_agent("rhs-plant-resolver"),
            },
        )
        raw = self._request_json(request)
        synonyms_raw = raw.get("synonyms")
        synonyms: list[str] = []
        if isinstance(synonyms_raw, list):
            for synonym in synonyms_raw:
                if isinstance(synonym, str):
                    name = synonym
                elif isinstance(synonym, dict):
                    name = str(synonym.get("name") or "")
                else:
                    continue
                if name.strip():
                    synonyms.append(_plain_name(name))
        botanical_name = _plain_name(
            str(raw.get("botanicalNameUnFormatted") or raw.get("botanicalName") or "")
        )
        if not botanical_name:
            raise RhsResolverError("RHS detail response omitted botanical name")
        return RhsPlantDetails(
            plant_id=int(raw.get("id") or plant_id),
            botanical_name=botanical_name,
            common_name=str(raw.get("commonName") or "").strip(),
            entity_id=str(raw.get("plantEntityId") or "").strip(),
            name_status=str(raw.get("nameStatus") or "").strip(),
            is_synonym=bool(raw.get("isSynonym")),
            synonym_parent_id=int(raw.get("synonymParentPlantId") or 0),
            synonyms=tuple(synonyms),
        )

    def canonical_url(self, plant_id: int, botanical_name: str) -> str:
        proposed = (
            f"https://{_RHS_PUBLIC_HOST}/plants/{plant_id}/"
            f"{_slugify_rhs_name(botanical_name)}/details"
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        current_url = proposed
        expected_prefix = f"/plants/{plant_id}/"
        for _ in range(4):
            request = urllib.request.Request(
                current_url,
                method="HEAD",
                headers={"User-Agent": app_user_agent("rhs-plant-resolver")},
            )
            try:
                with opener.open(request, timeout=self.timeout_seconds):  # noqa: S310
                    parsed = urllib.parse.urlparse(current_url)
                    return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))
            except urllib.error.HTTPError as exc:
                if exc.code not in _REDIRECT_CODES:
                    raise RhsResolverError("RHS detail page could not be verified") from exc
                location = str(exc.headers.get("Location") or "").strip()
                if not location:
                    raise RhsResolverError("RHS detail redirect omitted its target") from exc
                next_url = urllib.parse.urljoin(current_url, location)
                parsed = urllib.parse.urlparse(next_url)
                if (
                    parsed.scheme != "https"
                    or (parsed.hostname or "").lower() != _RHS_PUBLIC_HOST
                    or not parsed.path.lower().startswith(expected_prefix)
                    or not parsed.path.lower().endswith("/details")
                ):
                    raise RhsResolverError(
                        "RHS detail page redirected outside the expected plant"
                    ) from exc
                current_url = next_url
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise RhsResolverError("RHS detail page could not be verified") from exc
        raise RhsResolverError("RHS detail page exceeded the redirect limit")


def _candidate_summary(hit: RhsSearchHit) -> dict[str, object]:
    return {
        "id": hit.plant_id,
        "botanical_name": hit.botanical_name,
        "common_name": hit.common_name,
        "is_synonym": hit.is_synonym,
        "synonym_parent_id": hit.synonym_parent_id,
    }


def _error_resolution(reason: str, query: str, candidates: list[RhsSearchHit]) -> RhsResolution:
    return RhsResolution(
        status="error",
        match_type="none",
        reason=reason,
        query=query,
        candidate_count=len(candidates),
        candidates=tuple(_candidate_summary(hit) for hit in candidates[:5]),
    )


def _details_match_type(
    queries: tuple[str, ...],
    details: RhsPlantDetails,
) -> MatchType:
    synonym_keys = {normalize_botanical_name(name) for name in details.synonyms}
    botanical_key = normalize_botanical_name(details.botanical_name)
    for query in queries:
        query_key = normalize_botanical_name(query)
        if query_key in synonym_keys:
            return "synonym"
        if query_key == botanical_key:
            return "exact"
    return "none"


def _name_is_accepted(details: RhsPlantDetails) -> bool:
    # Older RHS records often omit Name Status while explicitly marking the
    # result as non-synonymous. Reject only an explicit non-accepted status.
    return not details.name_status or details.name_status.lower() in {"accepted", "correct"}


def _verified_from_details(
    *,
    rhs: RhsClient,
    details: RhsPlantDetails,
    match_type: MatchType,
    query: str,
    candidate_count: int,
    candidates: list[RhsSearchHit],
) -> RhsResolution:
    target = details
    if target.is_synonym and target.synonym_parent_id:
        target = rhs.details(target.synonym_parent_id)
        match_type = "synonym"
    canonical_url = rhs.canonical_url(target.plant_id, target.botanical_name)
    if not _name_is_accepted(target):
        return RhsResolution(
            status="needs_review",
            match_type=match_type,
            reason="rhs_name_not_accepted",
            query=query,
            candidate_count=candidate_count,
            external_id=str(target.plant_id),
            external_entity_id=target.entity_id,
            canonical_url=canonical_url,
            matched_botanical_name=target.botanical_name,
            matched_common_name=target.common_name,
            candidates=tuple(_candidate_summary(item) for item in candidates[:5]),
        )
    reason = (
        "explicit_rhs_synonym_match" if match_type == "synonym" else "unique_exact_botanical_match"
    )
    return RhsResolution(
        status="verified",
        match_type=match_type,
        reason=reason,
        query=query,
        candidate_count=candidate_count,
        external_id=str(target.plant_id),
        external_entity_id=target.entity_id,
        canonical_url=canonical_url,
        matched_botanical_name=target.botanical_name,
        matched_common_name=target.common_name,
        candidates=tuple(_candidate_summary(item) for item in candidates[:5]),
    )


def resolve_rhs_reference(
    *,
    latin: str,
    common_name: str,
    current_link: str = "",
    client: RhsClient | None = None,
) -> RhsResolution:
    """Resolve one plant to a unique, verified RHS reference or explicitly abstain."""
    queries = botanical_queries(latin, common_name)
    if not queries:
        return RhsResolution(
            status="not_found",
            match_type="none",
            reason="missing_botanical_name",
            query="",
            candidate_count=0,
        )

    rhs = client or HttpRhsClient()
    genus_url = _exact_genus_page(latin, current_link)
    if genus_url:
        return RhsResolution(
            status="verified",
            match_type="exact",
            reason="exact_rhs_genus_page",
            query=queries[0],
            candidate_count=1,
            canonical_url=genus_url,
            matched_botanical_name=_plain_name(latin),
        )
    current_plant_id = rhs_plant_id_from_url(current_link)
    current_detail_error = ""
    if current_plant_id is not None:
        try:
            current_details = rhs.details(current_plant_id)
            current_match_type = _details_match_type(queries, current_details)
            if current_match_type != "none":
                return _verified_from_details(
                    rhs=rhs,
                    details=current_details,
                    match_type=current_match_type,
                    query=queries[0],
                    candidate_count=1,
                    candidates=[],
                )
        except RhsPlantNotFoundError:
            # A confirmed stale ID should not prevent discovery of a replacement.
            pass
        except RhsResolverError as exc:
            current_detail_error = str(exc)

    candidates_by_id: dict[int, RhsSearchHit] = {}
    ranked_hits: list[tuple[int, str, RhsSearchHit]] = []
    try:
        for query in queries:
            for hit in rhs.search(query):
                candidates_by_id.setdefault(hit.plant_id, hit)
                score = _identity_match_score(query, hit.botanical_name)
                if score:
                    ranked_hits.append((score, query, hit))

        if not ranked_hits:
            cultivar_queries = {
                cultivar for query in queries if (cultivar := _cultivar_text(query))
            }
            for cultivar in sorted(cultivar_queries):
                for hit in rhs.search(cultivar):
                    candidates_by_id.setdefault(hit.plant_id, hit)
                    for query in queries:
                        score = _identity_match_score(query, hit.botanical_name)
                        if score:
                            ranked_hits.append((score, query, hit))
    except RhsResolverError as exc:
        return _error_resolution(str(exc), queries[0], list(candidates_by_id.values()))

    if ranked_hits:
        top_score = max(score for score, _, _ in ranked_hits)
        ranked_hits = [item for item in ranked_hits if item[0] == top_score]

    unique_exact: dict[tuple[int, int], tuple[str, RhsSearchHit]] = {}
    for _, query, hit in ranked_hits:
        target_id = (
            hit.synonym_parent_id if hit.is_synonym and hit.synonym_parent_id else hit.plant_id
        )
        unique_exact[(hit.plant_id, target_id)] = (query, hit)

    target_ids = {target_id for _, target_id in unique_exact}
    if len(target_ids) > 1:
        if current_detail_error:
            return _error_resolution(
                current_detail_error,
                queries[0],
                list(candidates_by_id.values()),
            )
        return RhsResolution(
            status="needs_review",
            match_type="none",
            reason="multiple_exact_rhs_matches",
            query=queries[0],
            candidate_count=len(candidates_by_id),
            candidates=tuple(_candidate_summary(hit) for hit in candidates_by_id.values()),
        )

    if not unique_exact:
        candidates = list(candidates_by_id.values())
        if current_detail_error:
            return _error_resolution(current_detail_error, queries[0], candidates)
        status: ResolutionStatus = "needs_review" if candidates else "not_found"
        reason = "no_exact_botanical_match" if candidates else "no_rhs_candidates"
        return RhsResolution(
            status=status,
            match_type="none",
            reason=reason,
            query=queries[0],
            candidate_count=len(candidates),
            candidates=tuple(_candidate_summary(hit) for hit in candidates[:5]),
        )

    query, hit = next(iter(unique_exact.values()))
    match_type: MatchType = "synonym" if hit.is_synonym else "exact"
    target_id = hit.synonym_parent_id if match_type == "synonym" else hit.plant_id
    try:
        details = rhs.details(target_id)
        return _verified_from_details(
            rhs=rhs,
            details=details,
            match_type=match_type,
            query=query,
            candidate_count=len(candidates_by_id),
            candidates=list(candidates_by_id.values()),
        )
    except RhsResolverError as exc:
        return _error_resolution(str(exc), query, list(candidates_by_id.values()))
