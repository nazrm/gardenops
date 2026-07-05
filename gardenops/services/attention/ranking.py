from __future__ import annotations

from typing import Any

from gardenops.services.attention.types import (
    SEVERITY_RANK,
    AttentionAction,
    AttentionDelivery,
    AttentionItem,
    AttentionSeverity,
    normalize_severity,
    stable_group_id,
)

_LOW_PRIORITY_GROUP_CATEGORIES = {"needs_action", "upcoming", "no_action_needed"}
_DELIVERY_ORDER: tuple[AttentionDelivery, ...] = (
    "panel_only",
    "inbox",
    "digest",
    "interruptive",
)


def _category_rank(item: AttentionItem) -> int:
    severity_rank = SEVERITY_RANK[normalize_severity(item.severity)]
    if item.category == "warning" and severity_rank >= SEVERITY_RANK["high"]:
        return 0
    if item.category == "needs_action":
        return 1
    if item.category == "system":
        return 2
    if item.category == "warning":
        return 3
    if item.category == "upcoming":
        return 4
    if item.category == "no_action_needed":
        return 5
    return 99


def _item_date_key(item: AttentionItem) -> str:
    return item.due_on or item.valid_from or item.valid_until or "9999-12-31"


def _item_rank_key(item: AttentionItem) -> tuple[int, int, int, str, int, str]:
    severity = normalize_severity(item.severity)
    return (
        _category_rank(item),
        -SEVERITY_RANK[severity],
        item.rank,
        _item_date_key(item),
        -item.updated_at_ms,
        item.id,
    )


def rank_attention_items(items: list[AttentionItem]) -> list[AttentionItem]:
    return sorted(items, key=_item_rank_key)


def _is_groupable(item: AttentionItem) -> bool:
    if not item.group_key:
        return False
    if item.category not in _LOW_PRIORITY_GROUP_CATEGORIES:
        return False
    return SEVERITY_RANK[normalize_severity(item.severity)] < SEVERITY_RANK["high"]


def _ordered_unique(values: list[str]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}))


def _first_date(items: list[AttentionItem], attr: str) -> str | None:
    values = [value for item in items if (value := getattr(item, attr))]
    return min(values) if values else None


def _delivery_eligibility(items: list[AttentionItem]) -> tuple[AttentionDelivery, ...]:
    values = {delivery for item in items for delivery in item.delivery_eligibility}
    return tuple(delivery for delivery in _DELIVERY_ORDER if delivery in values)


def _highest_severity(items: list[AttentionItem]) -> AttentionSeverity:
    return max(
        (normalize_severity(item.severity) for item in items),
        key=lambda severity: SEVERITY_RANK[severity],
    )


def _action_summary(action: AttentionAction | None) -> dict[str, Any] | None:
    if action is None:
        return None
    return {
        "kind": action.kind,
        "label": action.label,
        "target_type": action.target_type,
        "target_id": action.target_id,
    }


def _child_summary(item: AttentionItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "reason": item.reason,
        "severity": item.severity,
        "category": item.category,
        "type": item.type,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "plot_ids": list(item.plot_ids),
        "plant_ids": list(item.plant_ids),
        "primary_action": _action_summary(item.primary_action),
        "due_on": item.due_on,
        "updated_at_ms": item.updated_at_ms,
    }


def _group_item(provider: str, group_key: str, children: list[AttentionItem]) -> AttentionItem:
    ranked_children = rank_attention_items(children)
    representative = ranked_children[0]
    child_ids = sorted(item.id for item in children)
    group_id = stable_group_id(provider, group_key, child_ids)
    plot_ids = _ordered_unique([plot_id for item in children for plot_id in item.plot_ids])
    plant_ids = _ordered_unique([plant_id for item in children for plant_id in item.plant_ids])
    return AttentionItem(
        id=group_id,
        provider=representative.provider,
        type=f"{representative.type}_group",
        category=representative.category,
        severity=_highest_severity(children),
        title=f"{len(children)} related items",
        body=representative.body,
        reason=representative.reason,
        target_type="attention_group",
        target_id=group_id,
        garden_id=representative.garden_id,
        audience_user_id=representative.audience_user_id,
        plant_ids=plant_ids,
        plot_ids=plot_ids,
        due_on=_first_date(children, "due_on"),
        valid_from=_first_date(children, "valid_from"),
        valid_until=_first_date(children, "valid_until"),
        domain_state=representative.domain_state,
        user_state=representative.user_state,
        lifecycle_scope=representative.lifecycle_scope,
        delivery_eligibility=_delivery_eligibility(children),
        rank=min(item.rank for item in children),
        group_key=group_key,
        primary_action=AttentionAction(
            kind="open_attention_detail",
            label="View items",
            target_type="attention_group",
            target_id=group_id,
        ),
        explanation=representative.explanation,
        source_label=representative.source_label,
        updated_at_ms=max(item.updated_at_ms for item in children),
        metadata={
            "child_count": len(children),
            "child_ids": child_ids,
            "children": [_child_summary(item) for item in ranked_children],
            "group_key": group_key,
        },
    )


def group_attention_items(items: list[AttentionItem]) -> list[AttentionItem]:
    grouped_candidates: dict[tuple[str, str], list[AttentionItem]] = {}
    visible_items: list[AttentionItem] = []
    for item in items:
        if not _is_groupable(item):
            visible_items.append(item)
            continue
        assert item.group_key is not None
        grouped_candidates.setdefault((item.provider, item.group_key), []).append(item)

    for (provider, group_key), children in grouped_candidates.items():
        if len(children) < 2:
            visible_items.extend(children)
            continue
        visible_items.append(_group_item(provider, group_key, children))

    return rank_attention_items(visible_items)
