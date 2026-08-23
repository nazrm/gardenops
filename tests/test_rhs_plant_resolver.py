from __future__ import annotations

import urllib.error
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from gardenops.services.rhs_plant_resolver import (
    HttpRhsClient,
    RhsPlantDetails,
    RhsPlantNotFoundError,
    RhsResolution,
    RhsResolverError,
    RhsSearchHit,
    botanical_queries,
    normalize_botanical_name,
    planned_link_update,
    resolve_rhs_reference,
)


@dataclass
class FakeRhsClient:
    searches: dict[str, list[RhsSearchHit]]
    detail_rows: dict[int, RhsPlantDetails]
    urls: dict[int, str]
    requested_queries: list[str] = field(default_factory=list)

    def search(self, keywords: str) -> list[RhsSearchHit]:
        self.requested_queries.append(keywords)
        return self.searches.get(keywords, [])

    def details(self, plant_id: int) -> RhsPlantDetails:
        return self.detail_rows[plant_id]

    def canonical_url(self, plant_id: int, botanical_name: str) -> str:
        return self.urls[plant_id]


def _hit(
    plant_id: int,
    botanical_name: str,
    *,
    common_name: str = "",
    is_synonym: bool = False,
    synonym_parent_id: int = 0,
) -> RhsSearchHit:
    return RhsSearchHit(
        plant_id=plant_id,
        botanical_name=botanical_name,
        common_name=common_name,
        is_synonym=is_synonym,
        synonym_parent_id=synonym_parent_id,
    )


def _details(
    plant_id: int,
    botanical_name: str,
    *,
    common_name: str = "",
    entity_id: str = "entity-1",
    name_status: str = "Accepted",
) -> RhsPlantDetails:
    return RhsPlantDetails(
        plant_id=plant_id,
        botanical_name=botanical_name,
        common_name=common_name,
        entity_id=entity_id,
        name_status=name_status,
        is_synonym=False,
        synonym_parent_id=0,
        synonyms=(),
    )


def test_normalize_botanical_name_preserves_full_cultivar_identity() -> None:
    assert normalize_botanical_name("Lilium ‘Blacklist’ (Ia/b)") == "lilium blacklist"
    assert normalize_botanical_name("Lilium 'Black Beauty'") == "lilium black beauty"
    assert normalize_botanical_name("Lilium") != normalize_botanical_name("Lilium 'Blacklist'")


def test_botanical_queries_uses_common_name_cultivar_without_common_text() -> None:
    assert botanical_queries("Rudbeckia hirta", "Lodnesolhatt 'Kissing Smileyz'") == (
        "Rudbeckia hirta 'Kissing Smileyz'",
    )
    assert botanical_queries("Lilium 'Blacklist'", "Asiatisk lilje") == ("Lilium 'Blacklist'",)


def test_botanical_queries_bounds_untrusted_ai_output() -> None:
    assert botanical_queries("A / B / C / D / E", "") == ("A", "B", "C")
    assert botanical_queries("A" * 201, "") == ()


def test_botanical_queries_ignores_oversized_common_name_cultivar() -> None:
    common_name = f"{'A' * 200} 'Injected cultivar'"
    assert botanical_queries("Rosa canina", common_name) == ("Rosa canina",)


def test_resolves_unique_exact_cultivar_and_does_not_query_common_name() -> None:
    hit = _hit(506653, "Lilium 'Blacklist' (Ia/b)")
    client = FakeRhsClient(
        searches={"Lilium 'Blacklist'": [hit]},
        detail_rows={
            506653: _details(
                506653,
                "Lilium 'Blacklist' (Ia/b)",
                entity_id="E0293518",
            )
        },
        urls={506653: "https://www.rhs.org.uk/plants/506653/lilium-blacklist-iab/details"},
    )

    result = resolve_rhs_reference(
        latin="Lilium 'Blacklist'",
        common_name="Asiatisk lilje",
        client=client,
    )

    assert result.verified
    assert result.match_type == "exact"
    assert result.external_id == "506653"
    assert result.external_entity_id == "E0293518"
    assert client.requested_queries == ["Lilium 'Blacklist'"]


def test_rejects_genus_only_candidate_for_named_cultivar() -> None:
    client = FakeRhsClient(
        searches={"Lilium 'Blacklist'": [_hit(1, "Lilium")]},
        detail_rows={},
        urls={},
    )

    result = resolve_rhs_reference(
        latin="Lilium 'Blacklist'",
        common_name="Asiatisk lilje",
        client=client,
    )

    assert result.status == "needs_review"
    assert result.match_type == "none"
    assert result.reason == "no_exact_botanical_match"
    assert not result.canonical_url


def test_resolves_explicit_rhs_synonym_to_accepted_parent() -> None:
    synonym = _hit(
        900,
        "Aster dumosus 'Mittelmeer'",
        is_synonym=True,
        synonym_parent_id=901,
    )
    client = FakeRhsClient(
        searches={"Aster dumosus 'Mittelmeer'": [synonym]},
        detail_rows={901: _details(901, "Symphyotrichum novi-belgii 'Mittelmeer'")},
        urls={
            901: "https://www.rhs.org.uk/plants/901/symphyotrichum-novi-belgii-mittelmeer/details"
        },
    )

    result = resolve_rhs_reference(
        latin="Aster dumosus 'Mittelmeer'",
        common_name="Buksasters",
        client=client,
    )

    assert result.verified
    assert result.match_type == "synonym"
    assert result.external_id == "901"
    assert result.matched_botanical_name == "Symphyotrichum novi-belgii 'Mittelmeer'"


def test_multiple_exact_targets_require_review() -> None:
    client = FakeRhsClient(
        searches={
            "Allium example": [
                _hit(10, "Allium example"),
                _hit(11, "Allium example"),
            ]
        },
        detail_rows={},
        urls={},
    )

    result = resolve_rhs_reference(
        latin="Allium example",
        common_name="Prydlok",
        client=client,
    )

    assert result.status == "needs_review"
    assert result.reason == "multiple_exact_rhs_matches"
    assert not result.canonical_url


def test_nonaccepted_exact_name_requires_review() -> None:
    hit = _hit(12, "Rosa example")
    client = FakeRhsClient(
        searches={"Rosa example": [hit]},
        detail_rows={12: _details(12, "Rosa example", name_status="Unresolved")},
        urls={12: "https://www.rhs.org.uk/plants/12/rosa-example/details"},
    )

    result = resolve_rhs_reference(
        latin="Rosa example",
        common_name="Rose",
        client=client,
    )

    assert result.status == "needs_review"
    assert result.reason == "rhs_name_not_accepted"
    assert result.match_type == "exact"
    assert result.external_id == "12"


def test_empty_rhs_name_status_is_allowed_for_exact_nonsynonym_record() -> None:
    hit = _hit(13, "Camassia cusickii")
    client = FakeRhsClient(
        searches={"Camassia cusickii": [hit]},
        detail_rows={13: _details(13, "Camassia cusickii", name_status="")},
        urls={13: "https://www.rhs.org.uk/plants/13/camassia-cusickii/details"},
    )

    result = resolve_rhs_reference(
        latin="Camassia cusickii",
        common_name="Prairielilje",
        client=client,
    )

    assert result.verified


def test_rhs_correct_name_status_is_allowed() -> None:
    hit = _hit(14, "Camassia cusickii")
    client = FakeRhsClient(
        searches={"Camassia cusickii": [hit]},
        detail_rows={14: _details(14, "Camassia cusickii", name_status="Correct")},
        urls={14: "https://www.rhs.org.uk/plants/14/camassia-cusickii/details"},
    )

    result = resolve_rhs_reference(
        latin="Camassia cusickii",
        common_name="Prairielilje",
        client=client,
    )

    assert result.verified


def test_unique_genus_and_cultivar_match_accepts_rhs_species_qualification() -> None:
    candidate = _hit(326510, "Allium amethystinum 'Red Mohican'PBR")
    client = FakeRhsClient(
        searches={
            "Allium 'Red Mohican'": [candidate],
            "Red Mohican": [candidate],
        },
        detail_rows={326510: _details(326510, "Allium amethystinum 'Red Mohican'PBR")},
        urls={
            326510: "https://www.rhs.org.uk/plants/326510/"
            "allium-amethystinum-red-mohican-pbr/details"
        },
    )

    result = resolve_rhs_reference(
        latin="Allium 'Red Mohican'",
        common_name="Prydlok",
        client=client,
    )

    assert result.verified
    assert result.external_id == "326510"


def test_existing_rhs_id_can_be_verified_from_explicit_detail_synonym() -> None:
    client = FakeRhsClient(
        searches={},
        detail_rows={
            340489: RhsPlantDetails(
                plant_id=340489,
                botanical_name="Symphyotrichum novi-belgii 'Schneekissen'",
                common_name="",
                entity_id="entity-340489",
                name_status="Accepted",
                is_synonym=False,
                synonym_parent_id=0,
                synonyms=("Aster dumosus 'Schneekissen'",),
            )
        },
        urls={
            340489: "https://www.rhs.org.uk/plants/340489/"
            "symphyotrichum-novi-belgii-schneekissen/details"
        },
    )

    result = resolve_rhs_reference(
        latin="Aster dumosus 'Schneekissen'",
        common_name="Buskaster",
        current_link=(
            "https://www.rhs.org.uk/plants/340489/symphyotrichum-novi-belgii-schneekissen/details"
        ),
        client=client,
    )

    assert result.verified
    assert result.match_type == "synonym"
    assert client.requested_queries == []


def test_existing_rhs_id_with_conflicting_species_does_not_bypass_search() -> None:
    current_id = 100
    replacement_id = 101
    client = FakeRhsClient(
        searches={"Lilium martagon 'Example'": [_hit(replacement_id, "Lilium martagon 'Example'")]},
        detail_rows={
            current_id: _details(current_id, "Lilium candidum 'Example'"),
            replacement_id: _details(replacement_id, "Lilium martagon 'Example'"),
        },
        urls={
            replacement_id: ("https://www.rhs.org.uk/plants/101/lilium-martagon-example/details")
        },
    )

    result = resolve_rhs_reference(
        latin="Lilium martagon 'Example'",
        common_name="Lilje",
        current_link=("https://www.rhs.org.uk/plants/100/lilium-candidum-example/details"),
        client=client,
    )

    assert result.verified
    assert result.external_id == str(replacement_id)
    assert client.requested_queries == ["Lilium martagon 'Example'"]


def test_transient_current_id_failure_never_becomes_a_clear_decision() -> None:
    class FailingCurrentClient(FakeRhsClient):
        def details(self, plant_id: int) -> RhsPlantDetails:
            raise RhsResolverError("temporary RHS failure")

    current_link = "https://www.rhs.org.uk/plants/100/lilium-example/details"
    result = resolve_rhs_reference(
        latin="Lilium 'Example'",
        common_name="Lilje",
        current_link=current_link,
        client=FailingCurrentClient(searches={}, detail_rows={}, urls={}),
    )

    assert result.status == "error"
    assert planned_link_update(current_link, result) == ("keep", current_link)


def test_confirmed_stale_current_id_can_be_cleared_after_empty_search() -> None:
    class MissingCurrentClient(FakeRhsClient):
        def details(self, plant_id: int) -> RhsPlantDetails:
            raise RhsPlantNotFoundError("missing RHS plant")

    current_link = "https://www.rhs.org.uk/plants/100/lilium-example/details"
    result = resolve_rhs_reference(
        latin="Lilium 'Example'",
        common_name="Lilje",
        current_link=current_link,
        client=MissingCurrentClient(searches={}, detail_rows={}, urls={}),
    )

    assert result.status == "not_found"
    assert planned_link_update(current_link, result) == ("clear", "")


def test_repair_replaces_verified_rhs_and_clears_only_deterministic_failures() -> None:
    verified = resolve_rhs_reference(
        latin="Lilium 'Blacklist'",
        common_name="Asiatisk lilje",
        client=FakeRhsClient(
            searches={"Lilium 'Blacklist'": [_hit(506653, "Lilium 'Blacklist' (Ia/b)")]},
            detail_rows={506653: _details(506653, "Lilium 'Blacklist' (Ia/b)")},
            urls={506653: "https://www.rhs.org.uk/plants/506653/lilium-blacklist-iab/details"},
        ),
    )
    old_rhs = "https://www.rhs.org.uk/plants/lilium"
    assert planned_link_update(old_rhs, verified) == (
        "replace",
        verified.canonical_url,
    )

    no_match = resolve_rhs_reference(
        latin="Lilium 'Missing'",
        common_name="Asiatisk lilje",
        client=FakeRhsClient(searches={}, detail_rows={}, urls={}),
    )
    assert planned_link_update(old_rhs, no_match) == ("clear", "")
    assert planned_link_update("https://example.com/plant", no_match) == (
        "keep",
        "https://example.com/plant",
    )

    unresolved = RhsResolution(
        status="needs_review",
        match_type="exact",
        reason="rhs_name_not_accepted",
        query="Lilium 'Example'",
        candidate_count=1,
    )
    assert planned_link_update(old_rhs, unresolved) == ("keep", old_rhs)


def test_genus_page_is_valid_only_for_a_genus_level_record() -> None:
    genus_url = "https://www.rhs.org.uk/plants/crocus"
    client = FakeRhsClient(searches={}, detail_rows={}, urls={})

    genus = resolve_rhs_reference(
        latin="Crocus",
        common_name="Krokus miks",
        current_link=genus_url,
        client=client,
    )
    cultivar = resolve_rhs_reference(
        latin="Crocus 'Example'",
        common_name="Krokus",
        current_link=genus_url,
        client=client,
    )

    assert genus.verified
    assert genus.reason == "exact_rhs_genus_page"
    assert cultivar.status == "not_found"


def test_canonical_url_rejects_redirect_outside_rhs() -> None:
    class RedirectingOpener:
        def open(self, request, timeout=0):  # type: ignore[no-untyped-def]
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "https://example.invalid/plant"},
                None,
            )

    with patch(
        "gardenops.services.rhs_plant_resolver.urllib.request.build_opener",
        return_value=RedirectingOpener(),
    ):
        client = HttpRhsClient()
        with pytest.raises(RhsResolverError, match="outside the expected plant"):
            client.canonical_url(506653, "Lilium 'Blacklist' (Ia/b)")
