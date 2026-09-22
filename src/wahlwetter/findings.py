"""Validation findings.

Kept in its own module so that both the source interfaces and the validation
rules can depend on it without a circular import.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Severity(enum.StrEnum):
    """How a validation failure is handled.

    ERROR   -- the row is kept out of the clean tables and preserved verbatim
               in the quarantine report. Never silently dropped.
    WARNING -- the row stays in the tables, flagged in the `warnings` column.
    """

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Finding:
    """A single validation rule firing on a single record."""

    rule: str
    severity: Severity
    source: str
    source_id: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": str(self.severity),
            "source": self.source,
            "source_id": self.source_id,
            "message": self.message,
            "context": self.context,
        }


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity is Severity.ERROR for f in findings)
