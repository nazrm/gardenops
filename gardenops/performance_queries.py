"""Request-scoped, parameter-free SQL execution accounting for test performance probes."""

from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Protocol


class QueryExecutionCollector(Protocol):
    def record(self, query: Any, *, batch_size: int | None = None) -> None: ...


_ACTIVE_COLLECTOR: ContextVar[QueryExecutionCollector | None] = ContextVar(
    "gardenops_query_execution_collector", default=None
)
_WHITESPACE = re.compile(r"\s+")


def activate_query_execution_collector(
    collector: QueryExecutionCollector,
) -> Token[QueryExecutionCollector | None]:
    """Attach a collector to the current request context."""
    return _ACTIVE_COLLECTOR.set(collector)


def reset_query_execution_collector(
    context_token: Token[QueryExecutionCollector | None],
) -> None:
    """Remove a collector installed for the current request context."""
    _ACTIVE_COLLECTOR.reset(context_token)


def active_query_execution_collector() -> QueryExecutionCollector | None:
    """Return the request collector, if a test-only probe enabled one."""
    return _ACTIVE_COLLECTOR.get()


def _fingerprint(query: Any) -> str:
    normalized = _WHITESPACE.sub(" ", str(query)).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class QueryEvidenceCollector:
    """Aggregate cursor executions without retaining statement text or parameters."""

    executions: int = 0
    batches: int = 0
    statements: dict[str, int] = field(default_factory=dict)

    def record(self, query: Any, *, batch_size: int | None = None) -> None:
        fingerprint = _fingerprint(query)
        self.executions += 1
        self.statements[fingerprint] = self.statements.get(fingerprint, 0) + 1
        if batch_size is not None:
            self.batches += max(0, batch_size)

    def snapshot(self) -> dict[str, object]:
        return {
            "batch_rows": self.batches,
            "query_count": self.executions,
            "statement_fingerprints": dict(sorted(self.statements.items())),
        }
