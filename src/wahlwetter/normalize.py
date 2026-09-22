"""Turn a parsed dawum payload into tidy tables.

Pure: no network, no clock, no filesystem. Everything time-dependent is taken
from the snapshot, which is what makes ingestion idempotent.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pandas as pd

from wahlwetter.config import DEFAULT_VALIDATION, ValidationConfig, load_parties_by_parliament
from wahlwetter.findings import Finding, Severity
from wahlwetter.sources.base import NormalizedTables, RawSnapshot, make_survey_id
from wahlwetter.validation import (
    Dimensions,
    SurveyCandidate,
    ValidationContext,
    find_duplicates,
    parse_iso_date,
    parse_sample_size,
    validate_survey,
)

if TYPE_CHECKING:
    from wahlwetter.sources.dawum import DawumPayload

SURVEY_COLUMNS = [
    "survey_id",
    "source",
    "source_id",
    "parliament_id",
    "institute_id",
    "tasker_id",
    "method_id",
    "published_at",
    "fieldwork_start",
    "fieldwork_end",
    "fieldwork_midpoint",
    "used_publication_fallback",
    "sample_size",
    "share_sum",
    "n_parties",
    "warnings",
    "retrieved_at",
]

RESULT_COLUMNS = ["survey_id", "party_id", "share"]


def _dim_frame(rows: list[dict[str, Any]], id_column: str) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    # Sorting by the numeric value of the ID keeps output stable regardless of
    # the order the source happened to emit (dawum's key order is not sorted).
    frame = frame.sort_values(by=id_column, key=lambda s: s.map(_id_sort_key), ignore_index=True)
    return frame


def _id_sort_key(value: str) -> tuple[int, int, str]:
    return (0, int(value), "") if str(value).isdigit() else (1, 0, str(value))


def build_dimensions(payload: DawumPayload) -> dict[str, pd.DataFrame]:
    return {
        "parliaments": _dim_frame(
            [
                {
                    "parliament_id": k,
                    "shortcut": v.Shortcut,
                    "name": v.Name,
                    "election": v.Election,
                }
                for k, v in payload.Parliaments.items()
            ],
            "parliament_id",
        ),
        "institutes": _dim_frame(
            [{"institute_id": k, "name": v.Name} for k, v in payload.Institutes.items()],
            "institute_id",
        ),
        "taskers": _dim_frame(
            [{"tasker_id": k, "name": v.Name} for k, v in payload.Taskers.items()],
            "tasker_id",
        ),
        "methods": _dim_frame(
            [{"method_id": k, "name": v.Name} for k, v in payload.Methods.items()],
            "method_id",
        ),
        "parties": _dim_frame(
            [
                {"party_id": k, "shortcut": v.Shortcut, "name": v.Name}
                for k, v in payload.Parties.items()
            ],
            "party_id",
        ),
    }


def build_candidates(payload: DawumPayload, source: str) -> list[SurveyCandidate]:
    return [
        SurveyCandidate(
            source=source,
            source_id=source_id,
            parliament_id=s.Parliament_ID,
            institute_id=s.Institute_ID,
            tasker_id=s.Tasker_ID,
            method_id=s.Method_ID,
            published_at_raw=s.Date,
            fieldwork_start_raw=s.Survey_Period.Date_Start,
            fieldwork_end_raw=s.Survey_Period.Date_End,
            sample_size_raw=str(s.Surveyed_Persons),
            results=dict(s.Results),
            published_at=parse_iso_date(s.Date),
            fieldwork_start=parse_iso_date(s.Survey_Period.Date_Start),
            fieldwork_end=parse_iso_date(s.Survey_Period.Date_End),
            sample_size=parse_sample_size(s.Surveyed_Persons),
        )
        for source_id, s in payload.Surveys.items()
    ]


def normalize_dawum(
    payload: DawumPayload,
    snapshot: RawSnapshot,
    config: ValidationConfig | None = None,
    today: date | None = None,
    parties_by_parliament: dict[str, set[str]] | None = None,
) -> NormalizedTables:
    config = config or DEFAULT_VALIDATION
    source = snapshot.source
    dimensions = build_dimensions(payload)

    ctx = ValidationContext(
        dimensions=Dimensions(
            parliaments=frozenset(payload.Parliaments),
            institutes=frozenset(payload.Institutes),
            taskers=frozenset(payload.Taskers),
            methods=frozenset(payload.Methods),
            parties=frozenset(payload.Parties),
        ),
        config=config,
        # Defaults to the snapshot's own date, never to the wall clock, so that
        # re-normalizing an old snapshot gives the same answer forever.
        today=today or snapshot.retrieved_at.date(),
        parties_by_parliament=(
            parties_by_parliament
            if parties_by_parliament is not None
            else load_parties_by_parliament()
        ),
    )

    candidates = sorted(build_candidates(payload, source), key=lambda c: _id_sort_key(c.source_id))

    findings: list[Finding] = []
    per_survey: dict[str, list[Finding]] = {}
    for c in candidates:
        found = validate_survey(c, ctx)
        per_survey.setdefault(c.source_id, []).extend(found)
        findings.extend(found)

    for f in find_duplicates(candidates, ctx):
        per_survey.setdefault(f.source_id, []).append(f)
        findings.append(f)

    retrieved_at = pd.Timestamp(snapshot.retrieved_at).tz_convert("UTC")

    survey_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []

    for c in candidates:
        found = per_survey.get(c.source_id, [])
        errors = [f for f in found if f.severity is Severity.ERROR]
        if errors:
            quarantined.append(
                {
                    "source": c.source,
                    "source_id": c.source_id,
                    "reasons": [f.to_dict() for f in errors],
                    # The record is preserved verbatim so nothing is lost.
                    "record": _raw_record(payload, c.source_id),
                }
            )
            continue

        warnings = sorted({f.rule for f in found if f.severity is Severity.WARNING})
        survey_id = make_survey_id(c.source, c.source_id)
        survey_rows.append(
            {
                "survey_id": survey_id,
                "source": c.source,
                "source_id": c.source_id,
                "parliament_id": c.parliament_id,
                "institute_id": c.institute_id,
                "tasker_id": c.tasker_id,
                "method_id": c.method_id,
                "published_at": c.published_at,
                "fieldwork_start": c.fieldwork_start,
                "fieldwork_end": c.fieldwork_end,
                "fieldwork_midpoint": c.fieldwork_midpoint,
                "used_publication_fallback": c.used_publication_fallback,
                "sample_size": c.sample_size,
                "share_sum": c.share_sum,
                "n_parties": len(c.results),
                "warnings": ",".join(warnings),
                "retrieved_at": retrieved_at,
            }
        )
        result_rows.extend(
            {"survey_id": survey_id, "party_id": party, "share": float(share)}
            for party, share in sorted(c.results.items(), key=lambda kv: _id_sort_key(kv[0]))
        )

    surveys = pd.DataFrame(survey_rows, columns=SURVEY_COLUMNS)
    results = pd.DataFrame(result_rows, columns=RESULT_COLUMNS)
    surveys = _coerce_survey_types(surveys)
    if not results.empty:
        results = results.sort_values(
            by=["survey_id", "party_id"],
            key=lambda s: s.map(_id_sort_key) if s.name == "party_id" else s,
            ignore_index=True,
        )

    return NormalizedTables(
        surveys=surveys,
        results=results,
        dimensions=dimensions,
        findings=findings,
        quarantined=quarantined,
    )


def _coerce_survey_types(frame: pd.DataFrame) -> pd.DataFrame:
    """Fixed dtypes, so an empty run writes the same schema as a full one."""
    if frame.empty:
        frame = frame.astype(
            {
                "survey_id": "string",
                "source": "string",
                "source_id": "string",
                "parliament_id": "string",
                "institute_id": "string",
                "tasker_id": "string",
                "method_id": "string",
                "used_publication_fallback": "boolean",
                "sample_size": "Int64",
                "share_sum": "float64",
                "n_parties": "Int64",
                "warnings": "string",
            }
        )
        for col in ("published_at", "fieldwork_start", "fieldwork_end", "fieldwork_midpoint"):
            frame[col] = pd.to_datetime(frame[col]).dt.date
        frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
        return frame

    for col in (
        "survey_id",
        "source",
        "source_id",
        "parliament_id",
        "institute_id",
        "tasker_id",
        "method_id",
        "warnings",
    ):
        frame[col] = frame[col].astype("string")
    frame["sample_size"] = frame["sample_size"].astype("Int64")
    frame["n_parties"] = frame["n_parties"].astype("Int64")
    frame["used_publication_fallback"] = frame["used_publication_fallback"].astype("boolean")
    return frame


def _raw_record(payload: DawumPayload, source_id: str) -> dict[str, Any]:
    survey = payload.Surveys.get(source_id)
    return survey.model_dump() if survey is not None else {}
