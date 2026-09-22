"""Loading polls out of the Parquet tables into plain objects.

Keeps the estimators independent of pandas and of the on-disk layout.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from wahlwetter.config import DATA_DIR, TABLES_DIR
from wahlwetter.storage import read_parquet

BUNDESTAG_PARLIAMENT_ID = "0"
ELECTION_RESULTS_PATH = DATA_DIR / "reference" / "bundestag_election_results.csv"


@dataclass(frozen=True, slots=True)
class PollObservation:
    """One poll, reduced to what an aggregator needs."""

    survey_id: str
    institute_id: str
    published_at: date
    fieldwork_start: date
    fieldwork_end: date
    fieldwork_midpoint: date
    sample_size: int | None
    shares: dict[str, float]
    # Defaulted so the estimators, which never look at it, can build a poll
    # without one. The model does use it.
    method_id: str = "0"

    def is_available_on(self, as_of: date) -> bool:
        """Availability is publication, not fieldwork.

        A poll fielded before the cutoff but published after it could not have
        informed a forecast made at the cutoff. Using fieldwork here would leak
        future information into every backtest.
        """
        return self.published_at <= as_of


def load_polls(
    parliament_id: str = BUNDESTAG_PARLIAMENT_ID,
    tables_dir: Path | None = None,
) -> list[PollObservation]:
    directory = tables_dir or TABLES_DIR
    surveys = read_parquet(directory / "surveys.parquet")
    results = read_parquet(directory / "results.parquet")

    surveys = surveys[surveys["parliament_id"] == parliament_id]
    shares: dict[str, dict[str, float]] = {}
    wanted = set(surveys["survey_id"])
    for survey_id, party_id, share in results.itertuples(index=False):
        if survey_id in wanted:
            shares.setdefault(survey_id, {})[party_id] = float(share)

    polls = [
        PollObservation(
            survey_id=row.survey_id,
            institute_id=row.institute_id,
            published_at=row.published_at,
            fieldwork_start=row.fieldwork_start,
            fieldwork_end=row.fieldwork_end,
            fieldwork_midpoint=row.fieldwork_midpoint,
            sample_size=None if row.sample_size is None else int(row.sample_size),
            shares=shares.get(row.survey_id, {}),
            method_id=row.method_id,
        )
        for row in surveys.itertuples(index=False)
    ]
    # Deterministic order; ties broken by ID so results never depend on the
    # order rows happened to come out of Parquet.
    return sorted(polls, key=lambda p: (p.fieldwork_end, p.published_at, p.survey_id))


@dataclass(frozen=True, slots=True)
class ElectionResult:
    """Official outcome of one election, on dawum's party IDs."""

    election_date: date
    election_year: int
    shares: dict[str, float]

    @property
    def parties(self) -> list[str]:
        return sorted(self.shares, key=int)


def load_election_results(path: Path | None = None) -> list[ElectionResult]:
    source = path or ELECTION_RESULTS_PATH
    by_year: dict[int, dict[str, float]] = {}
    dates: dict[int, date] = {}
    with source.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            year = int(row["election_year"])
            dates[year] = date.fromisoformat(row["election_date"])
            by_year.setdefault(year, {})[row["party_id"]] = float(row["share_pct_computed"])
    return [
        ElectionResult(election_date=dates[y], election_year=y, shares=by_year[y])
        for y in sorted(by_year)
    ]
