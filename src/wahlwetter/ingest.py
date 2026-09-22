"""Ingestion orchestration.

One run: check for an update, fetch if warranted, normalize, validate, write
tables and a quarantine report, persist state.
"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wahlwetter.config import (
    QUARANTINE_DIR,
    RAW_DIR,
    STATE_DIR,
    TABLES_DIR,
)
from wahlwetter.findings import Finding, Severity
from wahlwetter.sources.base import NormalizedTables, RawSnapshot, Source, SourceState
from wahlwetter.storage import read_json, write_json, write_parquet

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestResult:
    source: str
    fetched: bool
    reason: str
    snapshot: RawSnapshot | None = None
    tables: NormalizedTables | None = None
    snapshot_path: Path | None = None
    quarantine_path: Path | None = None

    @property
    def n_surveys(self) -> int:
        return 0 if self.tables is None else len(self.tables.surveys)

    @property
    def n_quarantined(self) -> int:
        return 0 if self.tables is None else len(self.tables.quarantined)

    @property
    def n_warnings(self) -> int:
        if self.tables is None:
            return 0
        return sum(1 for f in self.tables.findings if f.severity is Severity.WARNING)


def state_path(source: str, state_dir: Path | None = None) -> Path:
    return (state_dir or STATE_DIR) / f"{source}.json"


def load_state(source: str, state_dir: Path | None = None) -> SourceState:
    return SourceState.from_dict(read_json(state_path(source, state_dir)))


def save_state(source: str, state: SourceState, state_dir: Path | None = None) -> None:
    write_json(state.to_dict(), state_path(source, state_dir))


def _summarize(findings: list[Finding]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for f in findings:
        bucket = summary.setdefault(f.rule, {"error": 0, "warning": 0})
        bucket[str(f.severity)] += 1
    return summary


def write_quarantine_report(
    result_source: str,
    tables: NormalizedTables,
    snapshot: RawSnapshot,
    quarantine_dir: Path | None = None,
) -> Path:
    """Always written, even when empty, so its absence means the run failed."""
    directory = quarantine_dir or QUARANTINE_DIR
    path = directory / f"{result_source}-latest.json"
    write_json(
        {
            "source": result_source,
            "snapshot_sha256": snapshot.sha256,
            "snapshot_update_token": snapshot.update_token,
            "retrieved_at": snapshot.retrieved_at.astimezone(UTC).isoformat(),
            "n_surveys_accepted": len(tables.surveys),
            "n_surveys_quarantined": len(tables.quarantined),
            "findings_by_rule": _summarize(tables.findings),
            "quarantined": tables.quarantined,
        },
        path,
    )
    return path


def write_tables(tables: NormalizedTables, tables_dir: Path | None = None) -> list[Path]:
    directory = tables_dir or TABLES_DIR
    written: list[Path] = []
    for name, frame in [("surveys", tables.surveys), ("results", tables.results)]:
        path = directory / f"{name}.parquet"
        write_parquet(frame, path)
        written.append(path)
    for name, frame in sorted(tables.dimensions.items()):
        path = directory / f"{name}.parquet"
        write_parquet(frame, path)
        written.append(path)
    return written


def archive_snapshot(snapshot: RawSnapshot, raw_dir: Path | None = None) -> Path:
    """Write the gzipped snapshot locally; the workflow moves it to data-raw."""
    directory = raw_dir or RAW_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / snapshot.snapshot_name
    # mtime=0 so the gzip header does not embed a timestamp, which would make
    # an otherwise-identical snapshot produce a different file.
    with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0) as fh:
        fh.write(snapshot.content)
    return path


def load_snapshot(source_name: str, path: Path, state: SourceState) -> RawSnapshot:
    """Rebuild a RawSnapshot from an archived file, without any network use.

    Lets a run be replayed exactly -- for the idempotency check in CI, and for
    local development against a known payload.
    """
    content = gzip.decompress(path.read_bytes())
    retrieved_at = (
        datetime.fromisoformat(state.retrieved_at)
        if state.retrieved_at
        else datetime.strptime(path.name.split("-")[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    )
    try:
        token = json.loads(content)["Database"]["Last_Update"]
    except (ValueError, KeyError, TypeError):
        token = state.last_update
    return RawSnapshot(
        source=source_name,
        content=content,
        retrieved_at=retrieved_at,
        update_token=token,
        etag=state.etag,
    )


def run_ingest(
    source: Source,
    *,
    force: bool = False,
    from_snapshot: Path | None = None,
    tables_dir: Path | None = None,
    state_dir: Path | None = None,
    quarantine_dir: Path | None = None,
    raw_dir: Path | None = None,
) -> IngestResult:
    state = load_state(source.name, state_dir)

    if from_snapshot is not None:
        # Replay: no request at all.
        snapshot = load_snapshot(source.name, from_snapshot, state)
        tables = source.normalize(snapshot)
        write_tables(tables, tables_dir)
        quarantine_path = write_quarantine_report(source.name, tables, snapshot, quarantine_dir)
        return IngestResult(
            source=source.name,
            fetched=False,
            reason=f"replayed {from_snapshot.name}",
            snapshot=snapshot,
            tables=tables,
            quarantine_path=quarantine_path,
        )

    decision = source.check_for_update(state)

    if not decision.should_fetch and not force:
        log.info("%s: no update (%s)", source.name, decision.reason)
        return IngestResult(source=source.name, fetched=False, reason=decision.reason)

    reason = "forced" if force and not decision.should_fetch else decision.reason
    log.info("%s: fetching (%s)", source.name, reason)

    snapshot = source.fetch()

    # Idempotency: if the bytes are identical to what we already have, reuse
    # the original retrieval timestamp. Otherwise a forced or redundant fetch
    # would rewrite every table row with a new `retrieved_at` and produce a
    # spurious diff for content that did not change.
    if state.content_sha256 == snapshot.sha256 and state.retrieved_at is not None:
        snapshot = replace(snapshot, retrieved_at=datetime.fromisoformat(state.retrieved_at))

    tables = source.normalize(snapshot)

    snapshot_path = archive_snapshot(snapshot, raw_dir)
    write_tables(tables, tables_dir)
    quarantine_path = write_quarantine_report(source.name, tables, snapshot, quarantine_dir)

    save_state(
        source.name,
        SourceState(
            last_update=snapshot.update_token or decision.remote_token,
            etag=decision.etag,
            content_sha256=snapshot.sha256,
            retrieved_at=snapshot.retrieved_at.astimezone(UTC).isoformat(),
        ),
        state_dir,
    )

    return IngestResult(
        source=source.name,
        fetched=True,
        reason=reason,
        snapshot=snapshot,
        tables=tables,
        snapshot_path=snapshot_path,
        quarantine_path=quarantine_path,
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


def result_summary(result: IngestResult) -> dict[str, Any]:
    return {
        "source": result.source,
        "fetched": result.fetched,
        "reason": result.reason,
        "surveys": result.n_surveys,
        "quarantined": result.n_quarantined,
        "warnings": result.n_warnings,
    }
