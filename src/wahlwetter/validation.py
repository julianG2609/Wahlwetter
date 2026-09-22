"""Validation rules.

Each rule is a small named function taking a candidate survey and returning
findings, so that every rule can be tested in isolation against a deliberately
malformed fixture.

Severity policy:

* ERROR   keeps the survey out of the clean tables. The raw record is preserved
  verbatim in the quarantine report. Nothing is ever silently dropped.
* WARNING keeps the survey, flagged in the ``warnings`` column.
"""

from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from wahlwetter.config import ValidationConfig
from wahlwetter.findings import Finding, Severity


@dataclass(frozen=True, slots=True)
class Dimensions:
    """Known dimension IDs from the payload, for referential checks."""

    parliaments: frozenset[str] = frozenset()
    institutes: frozenset[str] = frozenset()
    taskers: frozenset[str] = frozenset()
    methods: frozenset[str] = frozenset()
    parties: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ValidationContext:
    dimensions: Dimensions
    config: ValidationConfig
    today: date
    # parliament_id -> set of party IDs previously reviewed as valid there.
    parties_by_parliament: dict[str, set[str]] = field(default_factory=dict)


@dataclass(slots=True)
class SurveyCandidate:
    """A survey after date/number parsing but before acceptance."""

    source: str
    source_id: str
    parliament_id: str
    institute_id: str
    tasker_id: str
    method_id: str
    published_at_raw: str
    fieldwork_start_raw: str
    fieldwork_end_raw: str
    sample_size_raw: str
    results: dict[str, float]
    published_at: date | None = None
    fieldwork_start: date | None = None
    fieldwork_end: date | None = None
    sample_size: int | None = None

    @property
    def share_sum(self) -> float:
        return round(sum(self.results.values()), 6)

    @property
    def fieldwork_midpoint(self) -> date | None:
        """Midpoint of the fieldwork period, where the poll is placed.

        Falls back to the publication date when fieldwork is unavailable; the
        caller flags that case. As of 2026-09 no dawum survey needs it.
        """
        if self.fieldwork_start and self.fieldwork_end:
            span = (self.fieldwork_end - self.fieldwork_start).days
            # Floor for even spans: a 2-day field period is placed on day 1,
            # which is the earlier of the two equally valid midpoints.
            return self.fieldwork_start + timedelta(days=span // 2)
        return self.published_at

    @property
    def used_publication_fallback(self) -> bool:
        return not (self.fieldwork_start and self.fieldwork_end)


def parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def parse_sample_size(value: str | int) -> int | None:
    """Sample size may legitimately be absent; it must never be silently 0."""
    text = str(value).strip()
    if text == "":
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _f(rule: str, severity: Severity, c: SurveyCandidate, msg: str, **ctx: object) -> Finding:
    return Finding(
        rule=rule,
        severity=severity,
        source=c.source,
        source_id=c.source_id,
        message=msg,
        context=dict(ctx),
    )


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def rule_dates_parseable(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    out: list[Finding] = []
    if c.published_at is None:
        out.append(
            _f(
                "dates_parseable",
                Severity.ERROR,
                c,
                f"publication date not parseable: {c.published_at_raw!r}",
                field="Date",
                value=c.published_at_raw,
            )
        )
    for label, raw, parsed in (
        ("Date_Start", c.fieldwork_start_raw, c.fieldwork_start),
        ("Date_End", c.fieldwork_end_raw, c.fieldwork_end),
    ):
        if raw.strip() and parsed is None:
            out.append(
                _f(
                    "dates_parseable",
                    Severity.ERROR,
                    c,
                    f"fieldwork date not parseable: {raw!r}",
                    field=label,
                    value=raw,
                )
            )
    return out


def rule_fieldwork_present(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    """Missing fieldwork is tolerated but must be visible.

    Every dawum survey currently carries both dates; this rule exists so that
    a regression upstream shows up rather than quietly shifting polls onto
    their publication date.
    """
    if c.used_publication_fallback:
        return [
            _f(
                "fieldwork_present",
                Severity.WARNING,
                c,
                "fieldwork period missing; poll placed at publication date",
                start=c.fieldwork_start_raw,
                end=c.fieldwork_end_raw,
            )
        ]
    return []


def rule_fieldwork_order(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    if c.fieldwork_start and c.fieldwork_end and c.fieldwork_start > c.fieldwork_end:
        return [
            _f(
                "fieldwork_order",
                Severity.ERROR,
                c,
                f"fieldwork start {c.fieldwork_start} after end {c.fieldwork_end}",
                start=str(c.fieldwork_start),
                end=str(c.fieldwork_end),
            )
        ]
    return []


def rule_fieldwork_before_publication(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    if c.fieldwork_end and c.published_at and c.fieldwork_end > c.published_at:
        return [
            _f(
                "fieldwork_before_publication",
                Severity.ERROR,
                c,
                f"fieldwork ended {c.fieldwork_end}, after publication {c.published_at}",
                end=str(c.fieldwork_end),
                published=str(c.published_at),
            )
        ]
    return []


def rule_publication_plausible(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    if c.published_at is None:
        return []
    out: list[Finding] = []
    if c.published_at > ctx.today:
        out.append(
            _f(
                "publication_plausible",
                Severity.ERROR,
                c,
                f"publication date {c.published_at} is in the future",
                published=str(c.published_at),
                today=str(ctx.today),
            )
        )
    if c.published_at.year < ctx.config.earliest_publication_year:
        out.append(
            _f(
                "publication_plausible",
                Severity.ERROR,
                c,
                f"publication date {c.published_at} is implausibly old",
                published=str(c.published_at),
            )
        )
    return out


def rule_fieldwork_span(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    if not (c.fieldwork_start and c.fieldwork_end):
        return []
    span = (c.fieldwork_end - c.fieldwork_start).days
    if span > ctx.config.max_fieldwork_span_days:
        return [
            _f(
                "fieldwork_span",
                Severity.WARNING,
                c,
                f"fieldwork span of {span} days exceeds {ctx.config.max_fieldwork_span_days}",
                span_days=span,
            )
        ]
    return []


def rule_publication_lag(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    if not (c.fieldwork_end and c.published_at):
        return []
    lag = (c.published_at - c.fieldwork_end).days
    if lag > ctx.config.max_publication_lag_days:
        return [
            _f(
                "publication_lag",
                Severity.WARNING,
                c,
                f"published {lag} days after fieldwork ended",
                lag_days=lag,
            )
        ]
    return []


def rule_sample_size(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    """Plausible or missing -- but a present-and-unparseable value is an error."""
    if c.sample_size is None:
        if c.sample_size_raw.strip() not in ("", "0"):
            return [
                _f(
                    "sample_size",
                    Severity.ERROR,
                    c,
                    f"sample size not parseable: {c.sample_size_raw!r}",
                    value=c.sample_size_raw,
                )
            ]
        return [
            _f("sample_size", Severity.WARNING, c, "sample size missing", value=c.sample_size_raw)
        ]
    cfg = ctx.config
    if not (cfg.min_plausible_sample_size <= c.sample_size <= cfg.max_plausible_sample_size):
        return [
            _f(
                "sample_size",
                Severity.WARNING,
                c,
                f"sample size {c.sample_size} outside "
                f"[{cfg.min_plausible_sample_size}, {cfg.max_plausible_sample_size}]",
                value=c.sample_size,
            )
        ]
    return []


def rule_results_present(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    if not c.results:
        return [_f("results_present", Severity.ERROR, c, "survey has no results")]
    return []


def rule_share_range(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    cfg = ctx.config
    return [
        _f(
            "share_range",
            Severity.ERROR,
            c,
            f"share {share} for party {party} outside [{cfg.min_share}, {cfg.max_share}]",
            party_id=party,
            share=share,
        )
        for party, share in sorted(c.results.items())
        if not (cfg.min_share <= share <= cfg.max_share)
    ]


def rule_share_sum(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    if not c.results:
        return []
    deviation = abs(c.share_sum - 100.0)
    cfg = ctx.config
    if deviation > cfg.share_sum_error_tolerance:
        severity = Severity.ERROR
    elif deviation > cfg.share_sum_warning_tolerance:
        severity = Severity.WARNING
    else:
        return []
    return [
        _f(
            "share_sum",
            Severity(severity),
            c,
            f"shares sum to {c.share_sum} (deviation {deviation:.3g})",
            share_sum=c.share_sum,
            deviation=deviation,
        )
    ]


def rule_referential_integrity(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    dims = ctx.dimensions
    out: list[Finding] = []
    for label, value, known in (
        ("Parliament_ID", c.parliament_id, dims.parliaments),
        ("Institute_ID", c.institute_id, dims.institutes),
        ("Tasker_ID", c.tasker_id, dims.taskers),
        ("Method_ID", c.method_id, dims.methods),
    ):
        if known and value not in known:
            out.append(
                _f(
                    "referential_integrity",
                    Severity.ERROR,
                    c,
                    f"{label} {value!r} not present in the dimension table",
                    field=label,
                    value=value,
                )
            )
    for party in sorted(c.results):
        if dims.parties and party not in dims.parties:
            out.append(
                _f(
                    "referential_integrity",
                    Severity.ERROR,
                    c,
                    f"result party ID {party!r} not present in Parties",
                    field="Results",
                    value=party,
                )
            )
    return out


def rule_known_parties_for_parliament(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    """A party not previously seen in this parliament is flagged, not dropped.

    Deliberate trade-off: quarantining an otherwise-valid poll because a new
    party appeared would lose real data at exactly the moment a new party
    becomes interesting. The survey is kept and flagged; the reviewed config
    in config/parties_by_parliament.json is then updated by hand.
    """
    known = ctx.parties_by_parliament.get(c.parliament_id)
    if not known:
        return []
    unexpected = sorted(set(c.results) - known)
    if not unexpected:
        return []
    return [
        _f(
            "known_parties_for_parliament",
            Severity.WARNING,
            c,
            f"party IDs {unexpected} not yet reviewed for parliament {c.parliament_id}",
            parliament_id=c.parliament_id,
            unexpected_party_ids=unexpected,
        )
    ]


#: Rules applied to each survey independently, in order.
SURVEY_RULES = (
    rule_dates_parseable,
    rule_fieldwork_present,
    rule_fieldwork_order,
    rule_fieldwork_before_publication,
    rule_publication_plausible,
    rule_fieldwork_span,
    rule_publication_lag,
    rule_sample_size,
    rule_results_present,
    rule_share_range,
    rule_share_sum,
    rule_referential_integrity,
    rule_known_parties_for_parliament,
)


def validate_survey(c: SurveyCandidate, ctx: ValidationContext) -> list[Finding]:
    """Run every per-survey rule."""
    out: list[Finding] = []
    for rule in SURVEY_RULES:
        out.extend(rule(c, ctx))
    return out


def find_duplicates(candidates: list[SurveyCandidate], ctx: ValidationContext) -> list[Finding]:
    """Cross-survey duplicate detection.

    An exact repeat (same parliament, institute, all three dates and identical
    results) is a genuine double entry: the first by source ID is kept and the
    rest quarantined. A softer collision -- same institute and parliament
    reporting the same fieldwork end twice -- is only flagged, since a pollster
    can legitimately publish two polls for different parliaments on one day.
    """
    out: list[Finding] = []

    exact: dict[tuple, list[SurveyCandidate]] = collections.defaultdict(list)
    soft: dict[tuple, list[SurveyCandidate]] = collections.defaultdict(list)
    for c in candidates:
        exact[
            (
                c.parliament_id,
                c.institute_id,
                c.published_at_raw,
                c.fieldwork_start_raw,
                c.fieldwork_end_raw,
                json.dumps(c.results, sort_keys=True),
            )
        ].append(c)
        soft[(c.parliament_id, c.institute_id, c.fieldwork_end_raw)].append(c)

    for group in exact.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda c: _sort_key(c.source_id))
        keeper = ordered[0]
        for dup in ordered[1:]:
            out.append(
                _f(
                    "duplicate_survey",
                    Severity.ERROR,
                    dup,
                    f"exact duplicate of survey {keeper.source_id}",
                    duplicate_of=keeper.source_id,
                )
            )

    quarantined = {(f.source, f.source_id) for f in out}
    for group in soft.values():
        if len(group) < 2:
            continue
        ids = sorted((c.source_id for c in group), key=_sort_key)
        for c in group:
            if (c.source, c.source_id) in quarantined:
                continue
            others = [i for i in ids if i != c.source_id]
            out.append(
                _f(
                    "repeated_fieldwork_end",
                    Severity.WARNING,
                    c,
                    f"same institute and parliament also reports fieldwork "
                    f"ending {c.fieldwork_end_raw} in survey(s) {others}",
                    others=others,
                )
            )
    return out


def _sort_key(source_id: str) -> tuple[int, object]:
    """Numeric source IDs sort numerically; anything else sorts as text."""
    return (0, int(source_id)) if source_id.isdigit() else (1, source_id)
