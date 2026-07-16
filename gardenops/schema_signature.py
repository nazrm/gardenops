"""Backend schema signature checks shared by startup and integrity auditing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import psycopg

REQUIRED_TABLES = (
    "schema_migrations",
    "app_secrets",
    "auth_users",
    "auth_passkeys",
    "auth_passkey_challenges",
    "auth_password_reset_tokens",
    "auth_sessions",
    "audit_events",
    "gardens",
    "plots",
    "garden_map_objects",
    "garden_map_object_units",
    "plot_ownership",
    "plants",
    "plant_ownership",
    "garden_tasks",
    "garden_task_plots",
    "garden_issues",
    "garden_issue_plots",
    "garden_journal_entries",
    "garden_journal_entry_plots",
    "harvest_entries",
    "harvest_entry_plots",
    "inventory_items",
    "inventory_transactions",
    "procurement_items",
    "garden_calendar_events",
    "garden_calendar_event_plots",
    "media_assets",
    "media_links",
    "media_cleanup_jobs",
    "shademap_state",
    "shademap_obstacles",
    "user_attention_preferences",
    "user_attention_item_state",
    "attention_outcomes",
    "offline_create_operations",
)

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "schema_migrations": ("version", "applied_at"),
    "app_secrets": (
        "key",
        "encrypted_value",
        "encryption_key_id",
        "value_last4",
        "created_at_ms",
        "updated_at_ms",
        "updated_by_user_id",
    ),
    "auth_users": (
        "id",
        "username",
        "password_auth_disabled",
        "passkey_user_handle",
        "passkey_prompt_dismissed_until_ms",
        "role",
        "is_active",
        "must_change_password",
        "mfa_totp_enabled",
        "subscription_tier",
    ),
    "auth_passkeys": (
        "id",
        "user_id",
        "credential_id",
        "credential_public_key",
        "sign_count",
        "nickname",
        "transports",
        "credential_device_type",
        "credential_backed_up",
        "created_at_ms",
        "updated_at_ms",
        "last_used_at_ms",
    ),
    "auth_passkey_challenges": (
        "id",
        "token_hash",
        "challenge",
        "flow",
        "user_id",
        "session_token_hash",
        "invitation_token_hash",
        "invitation_scope",
        "invitation_id",
        "invitee_username",
        "invitation_user_handle",
        "created_at_ms",
        "expires_at_ms",
        "used_at_ms",
    ),
    "auth_password_reset_tokens": (
        "id",
        "token_hash",
        "user_id",
        "created_by_user_id",
        "created_at_ms",
        "expires_at_ms",
        "used_at_ms",
        "used_by_user_id",
        "metadata",
        "purpose",
    ),
    "auth_sessions": (
        "token_hash",
        "user_id",
        "expires_at_ms",
        "created_at_ms",
        "last_seen_at_ms",
        "reauthenticated_at_ms",
        "mfa_authenticated_at_ms",
        "mfa_setup_required",
        "device_label",
        "location_hint",
    ),
    "audit_events": (
        "id",
        "request_id",
        "actor_user_id",
        "actor_username",
        "garden_id",
    ),
    "gardens": ("id", "slug", "name", "owner_user_id"),
    "plots": ("plot_id", "garden_id", "zone_code", "zone_name", "grid_row", "grid_col"),
    "garden_map_objects": (
        "id",
        "public_id",
        "garden_id",
        "object_type",
        "name",
        "shape_type",
        "geometry_json",
        "style_json",
        "z_index",
        "has_internal_layout",
        "internal_layout_json",
        "created_by_user_id",
        "created_at_ms",
        "updated_at_ms",
    ),
    "garden_map_object_units": (
        "id",
        "public_id",
        "garden_id",
        "map_object_id",
        "unit_type",
        "name",
        "shape_type",
        "geometry_json",
        "style_json",
        "sort_order",
        "created_at_ms",
        "updated_at_ms",
    ),
    "plot_ownership": ("plot_id", "owner_user_id", "garden_id"),
    "plants": ("plt_id", "name", "category"),
    "plant_ownership": ("plt_id", "owner_user_id", "garden_id"),
    "garden_tasks": ("id", "garden_id", "created_by_user_id", "completed_by_user_id"),
    "garden_task_plots": ("task_id", "plot_id"),
    "garden_issues": ("id", "garden_id", "created_by_user_id", "resolved_by_user_id"),
    "garden_issue_plots": ("issue_id", "plot_id"),
    "garden_journal_entries": ("id", "garden_id", "actor_user_id"),
    "garden_journal_entry_plots": ("entry_id", "plot_id"),
    "harvest_entries": ("id", "garden_id", "actor_user_id"),
    "harvest_entry_plots": ("entry_id", "plot_id"),
    "inventory_items": ("id", "public_id", "garden_id"),
    "inventory_transactions": (
        "id",
        "item_id",
        "garden_id",
        "delta",
        "actor_user_id",
        "created_at_ms",
    ),
    "procurement_items": (
        "id",
        "public_id",
        "garden_id",
        "status",
        "quantity",
        "receipt_inventory_item_id",
        "receipt_inventory_transaction_id",
        "received_by_user_id",
        "received_at_ms",
    ),
    "garden_calendar_events": ("id", "garden_id"),
    "garden_calendar_event_plots": ("event_id", "plot_id"),
    "media_assets": ("asset_id", "garden_id", "actor_user_id", "preview_bytes"),
    "media_links": ("asset_id", "target_type", "target_id"),
    "media_cleanup_jobs": (
        "id",
        "storage_key",
        "preview_storage_key",
        "attempts",
        "last_error",
        "created_at_ms",
        "last_attempt_at_ms",
    ),
    "shademap_state": ("id", "garden_id", "selected_plot_id"),
    "shademap_obstacles": ("id", "garden_id", "linked_plot_id"),
    "user_attention_preferences": (
        "id",
        "user_id",
        "preset",
        "rules_json",
        "quiet_hours_json",
        "show_no_action_history",
        "metadata_json",
        "created_at_ms",
        "updated_at_ms",
    ),
    "user_attention_item_state": (
        "id",
        "user_id",
        "garden_id",
        "item_id",
        "user_state",
        "snoozed_until_ms",
        "reason",
        "metadata_json",
        "created_at_ms",
        "updated_at_ms",
    ),
    "attention_outcomes": (
        "id",
        "public_id",
        "garden_id",
        "provider",
        "outcome_type",
        "source_type",
        "source_id",
        "source_public_id",
        "title",
        "explanation",
        "reason",
        "target_type",
        "target_id",
        "plant_ids_json",
        "plot_ids_json",
        "recovery_action_json",
        "metadata_json",
        "occurred_at_ms",
        "expires_at_ms",
        "created_at_ms",
        "updated_at_ms",
    ),
    "offline_create_operations": (
        "id",
        "garden_id",
        "endpoint",
        "operation_id",
        "request_fingerprint",
        "target_type",
        "target_id",
        "result_id",
        "created_at_ms",
        "expires_at_ms",
    ),
}

REQUIRED_INDEXES = (
    "ux_audit_events_request_id",
    "ux_weather_alerts_identity",
    "idx_offline_create_operations_expiry",
    "ux_plots_garden_grid_cell",
    "idx_plots_garden",
    "idx_garden_map_objects_garden",
    "ux_garden_map_objects_id_garden",
    "idx_garden_map_object_units_object",
    "idx_garden_map_object_units_garden",
    "idx_plot_ownership_garden",
    "idx_plant_ownership_garden",
    "idx_gipl_plot",
    "idx_gtpl_plot",
    "idx_gjepl_plot",
    "idx_hepl_plot",
    "idx_garden_calendar_event_plots_plot",
    "idx_media_links_target",
    "idx_media_cleanup_jobs_created",
    "idx_shademap_state_garden",
    "idx_shademap_obstacles_garden",
    "idx_user_attention_item_state_garden_user",
    "idx_attention_outcomes_garden_expires",
    "idx_attention_outcomes_source",
    "ux_attention_outcomes_source_kind",
    "app_secrets_updated_by_user_id_idx",
    "ux_auth_passkeys_credential_id",
    "idx_auth_passkeys_user",
    "ux_auth_passkey_challenges_token_hash",
    "idx_auth_passkey_challenges_expires",
    "idx_auth_passkey_challenges_user",
    "idx_auth_passkey_challenges_invitation",
    "ux_auth_users_passkey_user_handle",
    "idx_auth_sessions_expires",
    "idx_auth_sessions_user",
)

REQUIRED_CONSTRAINTS = (
    "schema_migrations_pkey",
    "app_secrets_pkey",
    "app_secrets_updated_by_user_id_fkey",
    "auth_users_pkey",
    "ck_auth_users_password_auth_state",
    "auth_passkeys_pkey",
    "auth_passkeys_user_id_fkey",
    "auth_passkey_challenges_pkey",
    "auth_passkey_challenges_user_id_fkey",
    "auth_sessions_pkey",
    "fk_auth_sessions_user_id_auth_users",
    "gardens_pkey",
    "plots_pkey",
    "garden_map_objects_pkey",
    "garden_map_objects_public_id_key",
    "ck_garden_map_objects_internal_layout_bool",
    "garden_map_object_units_pkey",
    "garden_map_object_units_public_id_key",
    "plot_ownership_pkey",
    "fk_plots_garden_id_gardens",
    "fk_garden_map_objects_garden_id_gardens",
    "fk_garden_map_object_units_garden_id_gardens",
    "fk_garden_map_object_units_object_garden",
    "plants_pkey",
    "plant_ownership_pkey",
    "ux_inventory_items_id_garden",
    "ux_inventory_tx_id_item_garden",
    "fk_inventory_tx_garden",
    "fk_inventory_tx_item_garden",
    "ck_inventory_tx_delta_nonzero",
    "ux_procurement_receipt_transaction",
    "fk_procurement_receipt_inventory",
    "fk_procurement_received_by_user",
    "ck_procurement_receipt_provenance",
    "ck_procurement_quantity_positive",
    "media_links_pkey",
    "media_cleanup_jobs_pkey",
    "ux_media_cleanup_jobs_storage_keys",
    "ck_media_cleanup_jobs_attempts_nonnegative",
    "fk_plot_ownership_plot_id_plots",
    "fk_plant_ownership_plt_id_plants",
    "fk_media_links_asset_id_media_assets",
    "fk_gardens_owner_user_id_auth_users",
    "fk_garden_map_objects_created_by_auth_users",
    "fk_shademap_state_garden_id_gardens",
    "ux_user_attention_preferences_user",
    "fk_user_attention_preferences_user",
    "ck_user_attention_preferences_no_action_bool",
    "ux_user_attention_item_state_user_garden_item",
    "fk_user_attention_item_state_user",
    "fk_user_attention_item_state_garden",
    "attention_outcomes_public_id_key",
    "fk_attention_outcomes_garden",
    "offline_create_operations_pkey",
    "ux_offline_create_operations_garden_endpoint_operation",
    "ck_offline_create_operations_endpoint_target",
    "ck_offline_create_operations_operation_id_length",
    "ck_offline_create_operations_target_id_length",
    "ck_offline_create_operations_result_id_length",
    "ck_offline_create_operations_request_fingerprint",
    "ck_offline_create_operations_expiry",
    "fk_offline_create_operations_garden",
)

REQUIRED_COLUMN_NULLABILITY: dict[str, bool] = {
    "auth_users.password_hash": True,
    "auth_users.password_auth_disabled": False,
    "auth_users.passkey_user_handle": True,
    "auth_users.passkey_prompt_dismissed_until_ms": False,
    "auth_password_reset_tokens.purpose": False,
    "auth_passkey_challenges.invitation_user_handle": True,
    "inventory_transactions.garden_id": False,
    "auth_sessions.token_hash": False,
    "auth_sessions.user_id": False,
    "auth_sessions.expires_at_ms": False,
    "auth_sessions.created_at_ms": False,
    "auth_sessions.last_seen_at_ms": False,
    "auth_sessions.reauthenticated_at_ms": False,
    "auth_sessions.mfa_authenticated_at_ms": False,
    "auth_sessions.mfa_setup_required": False,
    "auth_sessions.device_label": False,
    "auth_sessions.location_hint": False,
}

REQUIRED_COLUMN_TYPES: dict[str, str] = {
    "auth_sessions.token_hash": "text",
    "auth_sessions.user_id": "bigint",
    "auth_sessions.expires_at_ms": "bigint",
    "auth_sessions.created_at_ms": "bigint",
    "auth_sessions.last_seen_at_ms": "bigint",
    "auth_sessions.reauthenticated_at_ms": "bigint",
    "auth_sessions.mfa_authenticated_at_ms": "bigint",
    "auth_sessions.mfa_setup_required": "bigint",
    "auth_sessions.device_label": "text",
    "auth_sessions.location_hint": "text",
}

REQUIRED_COLUMN_DEFAULTS: dict[str, str | None] = {
    "auth_sessions.token_hash": None,
    "auth_sessions.user_id": None,
    "auth_sessions.expires_at_ms": None,
    "auth_sessions.created_at_ms": None,
    "auth_sessions.last_seen_at_ms": None,
    "auth_sessions.reauthenticated_at_ms": "1",
    "auth_sessions.mfa_authenticated_at_ms": "0",
    "auth_sessions.mfa_setup_required": "0",
    "auth_sessions.device_label": "''::text",
    "auth_sessions.location_hint": "''::text",
}

REQUIRED_INDEX_DEFINITION_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "ux_audit_events_request_id": (
        "unique index",
        "audit_events",
        "using btree (request_id, id)",
        "where",
        "request_id <> ''",
    ),
    "ux_weather_alerts_identity": (
        "unique index",
        "weather_alerts",
        "garden_id",
        "alert_type",
        "valid_from",
    ),
    "idx_auth_passkey_challenges_invitation": (
        "auth_passkey_challenges",
        "invitation_token_hash",
        "expires_at_ms",
    ),
    "ux_auth_users_passkey_user_handle": (
        "unique index",
        "auth_users",
        "passkey_user_handle",
        "where",
        "passkey_user_handle is not null",
    ),
    "idx_auth_sessions_expires": (
        "auth_sessions",
        "using btree (expires_at_ms)",
    ),
    "idx_auth_sessions_user": (
        "auth_sessions",
        "using btree (user_id)",
    ),
}

REQUIRED_CONSTRAINT_DEFINITION_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "ck_auth_users_password_auth_state": (
        "check",
        "password_auth_disabled = 0",
        "password_hash is not null",
        "length(password_hash) > 0",
        "password_auth_disabled = 1",
        "password_hash is null",
    ),
    "auth_sessions_pkey": ("primary key (token_hash)",),
    "fk_auth_sessions_user_id_auth_users": (
        "foreign key (user_id)",
        "references auth_users(id)",
        "on delete cascade",
        "deferrable",
    ),
    "ck_procurement_receipt_provenance": (
        "status = 'received'",
        "status <> 'received'",
        "receipt_inventory_item_id is not null",
        "receipt_inventory_item_id is null",
        "receipt_inventory_transaction_id is not null",
        "receipt_inventory_transaction_id is null",
        "received_by_user_id is null",
        "received_at_ms is not null",
        "received_at_ms is null",
    ),
}

_IGNORED_BOOTSTRAP_TABLES = frozenset({"schema_migrations"})


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: set[str]
    columns: dict[str, set[str]]
    indexes: set[str]
    constraints: set[str]
    column_nullability: dict[str, bool] = field(default_factory=dict)
    column_types: dict[str, str] = field(default_factory=dict)
    column_defaults: dict[str, str | None] = field(default_factory=dict)
    index_definitions: dict[str, str] = field(default_factory=dict)
    constraint_definitions: dict[str, str] = field(default_factory=dict)


def _normalize_definition(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def collect_schema_snapshot(conn: psycopg.Connection[Any]) -> SchemaSnapshot:
    table_rows = conn.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        """,
    ).fetchall()
    tables = {str(row["table_name"]) for row in table_rows}

    column_rows = conn.execute(
        """
        SELECT table_name, column_name, data_type, column_default, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
        """,
    ).fetchall()
    columns: dict[str, set[str]] = {}
    column_nullability: dict[str, bool] = {}
    column_types: dict[str, str] = {}
    column_defaults: dict[str, str | None] = {}
    for row in column_rows:
        table_name = str(row["table_name"])
        column_name = str(row["column_name"])
        columns.setdefault(table_name, set()).add(column_name)
        qualified_name = f"{table_name}.{column_name}"
        column_nullability[qualified_name] = str(row["is_nullable"]) == "YES"
        column_types[qualified_name] = str(row["data_type"])
        default = row["column_default"]
        column_defaults[qualified_name] = None if default is None else str(default)

    index_rows = conn.execute(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        """,
    ).fetchall()
    indexes = {str(row["indexname"]) for row in index_rows}
    index_definitions = {str(row["indexname"]): str(row["indexdef"]) for row in index_rows}

    constraint_rows = conn.execute(
        """
        SELECT conname, pg_get_constraintdef(oid) AS condef
        FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace
        """,
    ).fetchall()
    constraints = {str(row["conname"]) for row in constraint_rows}
    constraint_definitions = {str(row["conname"]): str(row["condef"]) for row in constraint_rows}

    return SchemaSnapshot(
        tables=tables,
        columns=columns,
        indexes=indexes,
        constraints=constraints,
        column_nullability=column_nullability,
        column_types=column_types,
        column_defaults=column_defaults,
        index_definitions=index_definitions,
        constraint_definitions=constraint_definitions,
    )


def missing_schema_parts(
    snapshot: SchemaSnapshot,
    *,
    required_tables: tuple[str, ...] = REQUIRED_TABLES,
    required_columns: Mapping[str, tuple[str, ...]] = REQUIRED_COLUMNS,
    required_indexes: tuple[str, ...] = REQUIRED_INDEXES,
    required_constraints: tuple[str, ...] = REQUIRED_CONSTRAINTS,
    required_column_nullability: Mapping[str, bool] = REQUIRED_COLUMN_NULLABILITY,
    required_column_types: Mapping[str, str] = REQUIRED_COLUMN_TYPES,
    required_column_defaults: Mapping[str, str | None] = REQUIRED_COLUMN_DEFAULTS,
    required_index_definition_fragments: Mapping[
        str,
        tuple[str, ...],
    ] = REQUIRED_INDEX_DEFINITION_FRAGMENTS,
    required_constraint_definition_fragments: Mapping[
        str,
        tuple[str, ...],
    ] = REQUIRED_CONSTRAINT_DEFINITION_FRAGMENTS,
) -> list[dict[str, object]]:
    missing: list[dict[str, object]] = []
    for table in required_tables:
        if table not in snapshot.tables:
            missing.append({"kind": "table", "object": table})
    for table, columns in required_columns.items():
        actual = snapshot.columns.get(table, set())
        for column in columns:
            if column not in actual:
                missing.append({"kind": "column", "object": f"{table}.{column}"})
    for index in required_indexes:
        if index not in snapshot.indexes:
            missing.append({"kind": "index", "object": index})
    for constraint in required_constraints:
        if constraint not in snapshot.constraints:
            missing.append({"kind": "constraint", "object": constraint})
    for column, nullable in required_column_nullability.items():
        if snapshot.column_nullability.get(column) is not nullable:
            missing.append({"kind": "column-nullability", "object": column})
    for column, column_type in required_column_types.items():
        actual = _normalize_definition(snapshot.column_types.get(column, ""))
        if actual != _normalize_definition(column_type):
            missing.append({"kind": "column-type", "object": column})
    for column, default in required_column_defaults.items():
        if column not in snapshot.column_defaults:
            missing.append({"kind": "column-default", "object": column})
            continue
        actual = snapshot.column_defaults[column]
        if default is None:
            if actual is not None:
                missing.append({"kind": "column-default", "object": column})
        elif actual is None or _normalize_definition(actual) != _normalize_definition(default):
            missing.append({"kind": "column-default", "object": column})
    for index, fragments in required_index_definition_fragments.items():
        actual = _normalize_definition(snapshot.index_definitions.get(index, ""))
        if not actual or any(
            _normalize_definition(fragment) not in actual for fragment in fragments
        ):
            missing.append({"kind": "index-definition", "object": index})
    for constraint, fragments in required_constraint_definition_fragments.items():
        actual = _normalize_definition(snapshot.constraint_definitions.get(constraint, ""))
        if not actual or any(
            _normalize_definition(fragment) not in actual for fragment in fragments
        ):
            missing.append({"kind": "constraint-definition", "object": constraint})
    return missing


def format_schema_part(part: Mapping[str, object]) -> str:
    return f"{part['kind']}:{part['object']}"


def existing_public_schema_tables(snapshot: SchemaSnapshot) -> set[str]:
    return snapshot.tables - _IGNORED_BOOTSTRAP_TABLES


_MIGRATION_0021_INDEX = "ux_weather_alerts_identity"
_MIGRATION_0022_TABLE = "offline_create_operations"
_MIGRATION_0022_INDEXES = {"idx_offline_create_operations_expiry"}
_MIGRATION_0023_COLUMN = "audit_events.request_id"
_MIGRATION_0023_INDEX = "ux_audit_events_request_id"
_MIGRATION_0026_TABLE = "media_cleanup_jobs"
_MIGRATION_0026_COLUMN = "media_assets.preview_bytes"
_MIGRATION_0026_INDEXES = {"idx_media_cleanup_jobs_created"}
_MIGRATION_0026_CONSTRAINTS = {
    "media_cleanup_jobs_pkey",
    "ux_media_cleanup_jobs_storage_keys",
    "ck_media_cleanup_jobs_attempts_nonnegative",
}
_MIGRATION_0027_COLUMNS = {
    "inventory_transactions.garden_id",
    "procurement_items.receipt_inventory_item_id",
    "procurement_items.receipt_inventory_transaction_id",
    "procurement_items.received_by_user_id",
    "procurement_items.received_at_ms",
}
_MIGRATION_0027_CONSTRAINTS = {
    "ux_inventory_items_id_garden",
    "ux_inventory_tx_id_item_garden",
    "fk_inventory_tx_garden",
    "fk_inventory_tx_item_garden",
    "ck_inventory_tx_delta_nonzero",
    "ux_procurement_receipt_transaction",
    "fk_procurement_receipt_inventory",
    "fk_procurement_received_by_user",
    "ck_procurement_receipt_provenance",
    "ck_procurement_quantity_positive",
}
_MIGRATION_0028_COLUMNS = {
    "auth_sessions.device_label",
    "auth_sessions.location_hint",
}
_MIGRATION_0022_CONSTRAINTS = {
    constraint
    for constraint in REQUIRED_CONSTRAINTS
    if constraint.startswith("offline_create_operations_")
    or constraint.startswith("ux_offline_create_operations_")
    or constraint.startswith("ck_offline_create_operations_")
    or constraint.startswith("fk_offline_create_operations_")
}


def _is_migration_0021_part(part: Mapping[str, object]) -> bool:
    return str(part.get("object", "")) == _MIGRATION_0021_INDEX


def _is_migration_0022_part(part: Mapping[str, object]) -> bool:
    kind = str(part.get("kind", ""))
    obj = str(part.get("object", ""))
    if kind == "table":
        return obj == _MIGRATION_0022_TABLE
    if kind == "column":
        return obj.startswith(f"{_MIGRATION_0022_TABLE}.")
    if kind == "index":
        return obj in _MIGRATION_0022_INDEXES
    if kind == "constraint":
        return obj in _MIGRATION_0022_CONSTRAINTS
    return False


def _migration_0022_schema_is_absent(snapshot: SchemaSnapshot) -> bool:
    return (
        _MIGRATION_0022_TABLE not in snapshot.tables
        and _MIGRATION_0022_TABLE not in snapshot.columns
        and not (_MIGRATION_0022_INDEXES & snapshot.indexes)
        and not (_MIGRATION_0022_CONSTRAINTS & snapshot.constraints)
    )


def _is_migration_0023_part(part: Mapping[str, object]) -> bool:
    return str(part.get("object", "")) in {
        _MIGRATION_0023_COLUMN,
        _MIGRATION_0023_INDEX,
    }


def _migration_0023_schema_is_absent(snapshot: SchemaSnapshot) -> bool:
    return (
        "request_id" not in snapshot.columns.get("audit_events", set())
        and _MIGRATION_0023_INDEX not in snapshot.indexes
    )


def _is_migration_0026_part(part: Mapping[str, object]) -> bool:
    kind = str(part.get("kind", ""))
    obj = str(part.get("object", ""))
    if kind == "table":
        return obj == _MIGRATION_0026_TABLE
    if kind == "column":
        return obj == _MIGRATION_0026_COLUMN or obj.startswith(f"{_MIGRATION_0026_TABLE}.")
    if kind == "index":
        return obj in _MIGRATION_0026_INDEXES
    if kind == "constraint":
        return obj in _MIGRATION_0026_CONSTRAINTS
    return False


def _migration_0026_schema_is_absent(snapshot: SchemaSnapshot) -> bool:
    return (
        _MIGRATION_0026_TABLE not in snapshot.tables
        and "preview_bytes" not in snapshot.columns.get("media_assets", set())
        and not (_MIGRATION_0026_INDEXES & snapshot.indexes)
        and not (_MIGRATION_0026_CONSTRAINTS & snapshot.constraints)
    )


def _is_migration_0027_part(part: Mapping[str, object]) -> bool:
    obj = str(part.get("object", ""))
    return obj in _MIGRATION_0027_COLUMNS or obj in _MIGRATION_0027_CONSTRAINTS


def _migration_0027_schema_is_absent(snapshot: SchemaSnapshot) -> bool:
    return (
        "garden_id" not in snapshot.columns.get("inventory_transactions", set())
        and not (
            {
                "receipt_inventory_item_id",
                "receipt_inventory_transaction_id",
                "received_by_user_id",
                "received_at_ms",
            }
            & snapshot.columns.get("procurement_items", set())
        )
        and not (_MIGRATION_0027_CONSTRAINTS & snapshot.constraints)
    )


def _is_migration_0028_part(part: Mapping[str, object]) -> bool:
    return str(part.get("object", "")) in _MIGRATION_0028_COLUMNS


def _migration_0028_schema_is_absent(snapshot: SchemaSnapshot) -> bool:
    return not (
        {"device_label", "location_hint"}
        & snapshot.columns.get("auth_sessions", set())
    )


def bootstrap_schema_diagnostics_from_snapshot(
    snapshot: SchemaSnapshot,
) -> dict[str, object]:
    existing_tables = sorted(existing_public_schema_tables(snapshot))
    if not existing_tables:
        return {
            "mode": "empty",
            "can_stamp_migrations": False,
            "existing_tables": [],
            "missing": [],
        }

    missing = missing_schema_parts(snapshot)
    if missing:
        missing_weather_identity = _MIGRATION_0021_INDEX not in snapshot.indexes
        missing_offline_operations = _migration_0022_schema_is_absent(snapshot)
        missing_audit_request_id = _migration_0023_schema_is_absent(snapshot)
        missing_media_cleanup = _migration_0026_schema_is_absent(snapshot)
        missing_inventory_integrity = _migration_0027_schema_is_absent(snapshot)
        missing_auth_session_metadata = _migration_0028_schema_is_absent(snapshot)
        if (
            missing_offline_operations
            or missing_audit_request_id
            or missing_media_cleanup
            or missing_inventory_integrity
            or missing_auth_session_metadata
        ) and all(
            (missing_offline_operations and _is_migration_0022_part(part))
            or (missing_audit_request_id and _is_migration_0023_part(part))
            or (missing_weather_identity and _is_migration_0021_part(part))
            or (missing_media_cleanup and _is_migration_0026_part(part))
            or (missing_inventory_integrity and _is_migration_0027_part(part))
            or (missing_auth_session_metadata and _is_migration_0028_part(part))
            for part in missing
        ):
            stamp_through = 27
            if missing_inventory_integrity:
                stamp_through = 26
            if missing_media_cleanup:
                stamp_through = 25
            if missing_audit_request_id:
                stamp_through = 22
            if missing_offline_operations:
                stamp_through = 21
            if missing_weather_identity:
                stamp_through = 20
            return {
                "mode": "verified-upgrade-baseline",
                "can_stamp_migrations": True,
                "stamp_through": stamp_through,
                "existing_tables": existing_tables,
                "missing": missing,
            }
    return {
        "mode": "verified-baseline" if not missing else "incomplete-existing-schema",
        "can_stamp_migrations": not missing,
        "existing_tables": existing_tables,
        "missing": missing,
    }
