"""Transaction-scoped locks for garden layout mutations."""

from __future__ import annotations

from gardenops.db import DbConn

_LAYOUT_LOCK_NAMESPACE = 0x4841474500000000


def lock_garden_layout(db: DbConn, garden_id: int) -> None:
    """Serialize layout writes for one garden within the current transaction."""
    lock_key = _LAYOUT_LOCK_NAMESPACE + int(garden_id)
    db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
