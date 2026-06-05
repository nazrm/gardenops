"""User lifecycle safety helpers."""

from __future__ import annotations

from dataclasses import dataclass

from gardenops.db import DbConn

TRANSFER_REQUIRED_RESOURCES = frozenset({"gardens_owned", "plants_owned", "plots_owned"})
RETENTION_REQUIRED_RESOURCES = frozenset(
    {
        "tasks_created",
        "tasks_completed",
        "journal_entries",
        "harvest_entries",
        "issues_created",
        "issues_resolved",
        "media_assets",
        "audit_events",
    },
)

USER_REFERENCE_QUERIES: tuple[tuple[str, str], ...] = (
    ("gardens_owned", "SELECT COUNT(*) AS c FROM gardens WHERE owner_user_id = %s"),
    ("plants_owned", "SELECT COUNT(*) AS c FROM plant_ownership WHERE owner_user_id = %s"),
    ("plots_owned", "SELECT COUNT(*) AS c FROM plot_ownership WHERE owner_user_id = %s"),
    ("tasks_created", "SELECT COUNT(*) AS c FROM garden_tasks WHERE created_by_user_id = %s"),
    ("tasks_completed", "SELECT COUNT(*) AS c FROM garden_tasks WHERE completed_by_user_id = %s"),
    (
        "journal_entries",
        "SELECT COUNT(*) AS c FROM garden_journal_entries WHERE actor_user_id = %s",
    ),
    ("harvest_entries", "SELECT COUNT(*) AS c FROM harvest_entries WHERE actor_user_id = %s"),
    ("issues_created", "SELECT COUNT(*) AS c FROM garden_issues WHERE created_by_user_id = %s"),
    ("issues_resolved", "SELECT COUNT(*) AS c FROM garden_issues WHERE resolved_by_user_id = %s"),
    ("media_assets", "SELECT COUNT(*) AS c FROM media_assets WHERE actor_user_id = %s"),
    ("audit_events", "SELECT COUNT(*) AS c FROM audit_events WHERE actor_user_id = %s"),
)


@dataclass(frozen=True)
class UserDeletionImpact:
    reference_counts: dict[str, int]

    @property
    def hard_delete_blocked(self) -> bool:
        return any(count > 0 for count in self.reference_counts.values())

    @property
    def transfer_required(self) -> bool:
        return any(
            self.reference_counts.get(resource, 0) > 0 for resource in TRANSFER_REQUIRED_RESOURCES
        )

    @property
    def retention_required(self) -> bool:
        return any(
            self.reference_counts.get(resource, 0) > 0 for resource in RETENTION_REQUIRED_RESOURCES
        )

    @property
    def blocking_resources(self) -> list[str]:
        return sorted(resource for resource, count in self.reference_counts.items() if count > 0)

    def response_fields(self) -> dict[str, object]:
        return {
            "hard_delete_blocked": self.hard_delete_blocked,
            "transfer_required": self.transfer_required,
            "retention_required": self.retention_required,
            "blocking_resources": self.blocking_resources,
            "reference_counts": {
                resource: count
                for resource, count in sorted(self.reference_counts.items())
                if count > 0
            },
        }


def load_user_deletion_impact(db: DbConn, user_id: int) -> UserDeletionImpact:
    counts: dict[str, int] = {}
    for resource, query in USER_REFERENCE_QUERIES:
        row = db.execute(query, (user_id,)).fetchone()
        counts[resource] = int(row["c"] if row else 0)
    return UserDeletionImpact(reference_counts=counts)
