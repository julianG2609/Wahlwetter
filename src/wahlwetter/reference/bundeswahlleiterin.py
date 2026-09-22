"""Official Bundestag election results from Die Bundeswahlleiterin.

The dawum API carries no election results (see docs/data_source.md), so ground
truth for Phase 2 onwards comes from the federal returning officer's open data:
a single CSV covering every Bundestag election since 1949, with second votes,
seat totals and the split between constituency and list seats.

Licence: Datenlizenz Deutschland - Namensnennung - Version 2.0
(https://www.govdata.de/dl-de/by-2-0). Attribution is required wherever these
numbers are shown.

Nothing here is written from memory. Election dates carry the URL they were
read from, and every output row carries its source_url.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from wahlwetter.config import CONFIG_DIR, USER_AGENT

RESULTS_CSV_URL = (
    "https://www.bundeswahlleiterin.de/dam/jcr/"
    "24d8e745-920d-431a-893a-12805bc7ef40/btw_ab49_datenbank_ergebnisse.csv"
)

LICENSE = "Datenlizenz Deutschland - Namensnennung - Version 2.0"
LICENSE_URL = "https://www.govdata.de/dl-de/by-2-0"
ATTRIBUTION = "Die Bundeswahlleiterin, Statistisches Bundesamt (Destatis)"

PARTY_MAPPING_PATH = CONFIG_DIR / "party_mapping.json"

#: Election day per Bundestag election, each with the official page it was read
#: from. The results CSV carries only the year, so the day has to come from
#: elsewhere -- and it matters, because backtests fit on polls up to N days
#: before the election.
ELECTION_DATES: dict[int, dict[str, str]] = {
    2017: {
        "election_date": "2017-09-24",
        "bundestag_number": "19",
        "source_url": (
            "https://www.bundeswahlleiterin.de/dam/jcr/"
            "72f186bb-aa56-47d3-b24c-6a46f5de22d0/btw17_kerg.csv"
        ),
        "source_quote": "Wahl zum 19. Deutschen Bundestag (24. September 2017)",
    },
    2021: {
        "election_date": "2021-09-26",
        "bundestag_number": "20",
        "source_url": "https://www.bundeswahlleiterin.de/bundestagswahlen/2021/ergebnisse.html",
        "source_quote": "Deutschen Bundestag vom 26. September 2021",
    },
    2025: {
        "election_date": "2025-02-23",
        "bundestag_number": "21",
        "source_url": "https://www.bundeswahlleiterin.de/bundestagswahlen/2025.html",
        "source_quote": "Wahl zum 21. Deutschen Bundestag am 23. Februar 2025",
    },
}

# Column positions in the wide CSV, verified against the three header rows.
COL_LABEL = 0
COL_YEAR = 1
COL_REMARKS = 2
COL_DE_SECOND_VOTES = 4
COL_DE_SECOND_PCT = 6
COL_DE_SEATS_TOTAL = 79
COL_DE_SEATS_CONSTITUENCY = 81
COL_DE_SEATS_LIST = 83
COL_DE_SEATS_TOTAL_EXCL_BE = 85

TOTAL_ROW_LABEL = "gültige Stimmen/Sitze insgesamt"
NON_PARTY_LABELS = frozenset({"Wahlberechtigte", "Wählende", "ungültige Stimmen", TOTAL_ROW_LABEL})

MISSING = frozenset({"", "-", "\u2013"})  # \u2013 = EN DASH, used for "no value"


@dataclass(frozen=True, slots=True)
class ElectionResultRow:
    """One party's national result at one election."""

    election_date: str
    election_year: int
    bundestag_number: str
    parliament_id: str
    party_id: str
    party_shortcut: str
    official_labels: str
    second_votes: int | None
    share_pct_official: float | None
    share_pct_computed: float | None
    seats_total: int | None
    seats_constituency: int | None
    seats_list: int | None
    valid_votes_total: int | None
    seats_total_all_parties: int | None
    remarks: str
    source_url: str
    election_date_source_url: str
    license: str
    license_url: str
    attribution: str
    retrieved_at: str


def _parse_int(value: str) -> int | None:
    text = value.strip().replace("\u00a0", "")  # NO-BREAK SPACE
    if text in MISSING:
        return None
    try:
        return int(text.replace(".", "").replace(" ", ""))
    except ValueError:
        return None


def _parse_pct(value: str) -> float | None:
    text = value.strip()
    if text in MISSING:
        return None
    try:
        return float(text.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def fetch_results_csv(client: httpx.Client | None = None) -> bytes:
    """One polite request for the official results database."""
    owns = client is None
    client = client or httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=60.0, follow_redirects=True
    )
    try:
        response = client.get(RESULTS_CSV_URL)
        response.raise_for_status()
        return response.content
    finally:
        if owns:
            client.close()


def parse_results_csv(content: bytes) -> list[list[str]]:
    """Split the CSV into data rows, skipping the comment and header block."""
    text = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    header_index = next(
        (i for i, r in enumerate(rows) if r and r[COL_LABEL].strip() == "Merkmal/Partei"),
        None,
    )
    if header_index is None:
        raise ValueError("could not locate the 'Merkmal/Partei' header row")
    # Three header rows: region, vote type, unit.
    return [r for r in rows[header_index + 3 :] if r and r[COL_LABEL].strip()]


def load_party_mapping(path: Path | None = None) -> dict:
    return json.loads((path or PARTY_MAPPING_PATH).read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class _RowContext:
    """Per-election values shared by every row of that election."""

    meta: dict[str, str]
    year: int
    parliament_id: str
    valid_votes: int | None
    seats_all: int | None
    remarks: str
    year_rows: list[list[str]]
    stamp: str


def _build_row(party_id: str, agg: dict, ctx: _RowContext) -> ElectionResultRow:
    share = round(100.0 * agg["votes"] / ctx.valid_votes, 4) if ctx.valid_votes else None
    # Only meaningful for a single-label party: a combined CDU/CSU share has no
    # published counterpart to compare against.
    official_pct = None
    if len(agg["labels"]) == 1:
        row = next(r for r in ctx.year_rows if r[COL_LABEL].strip() == agg["labels"][0])
        official_pct = _parse_pct(row[COL_DE_SECOND_PCT])
    return ElectionResultRow(
        election_date=ctx.meta["election_date"],
        election_year=ctx.year,
        bundestag_number=ctx.meta["bundestag_number"],
        parliament_id=ctx.parliament_id,
        party_id=party_id,
        party_shortcut=agg["shortcut"],
        official_labels="+".join(agg["labels"]),
        second_votes=agg["votes"],
        share_pct_official=official_pct,
        share_pct_computed=share,
        seats_total=agg["seats_total"],
        seats_constituency=agg["seats_constituency"],
        seats_list=agg["seats_list"],
        valid_votes_total=ctx.valid_votes,
        seats_total_all_parties=ctx.seats_all,
        remarks=ctx.remarks,
        source_url=RESULTS_CSV_URL,
        election_date_source_url=ctx.meta["source_url"],
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution=ATTRIBUTION,
        retrieved_at=ctx.stamp,
    )


def to_rows(
    data_rows: list[list[str]],
    *,
    mapping: dict | None = None,
    years: list[int] | None = None,
    retrieved_at: datetime | None = None,
) -> list[ElectionResultRow]:
    """Aggregate official party rows onto dawum's Bundestag party IDs.

    Shares are recomputed from absolute vote counts rather than summed from the
    published percentages: the published values are rounded to one decimal, and
    CDU/CSU has to be combined, so summing rounded numbers would compound the
    error.
    """
    mapping = mapping or load_party_mapping()
    parliament_id = mapping["parliament_id"]
    residual_id = mapping["residual_party_id"]
    stamp = (retrieved_at or datetime.now(UTC)).isoformat()
    years = years if years is not None else sorted(ELECTION_DATES)

    label_to_party: dict[str, str] = {}
    for party_id, spec in mapping["mapping"].items():
        for label in spec["official_labels"]:
            label_to_party[label] = party_id

    out: list[ElectionResultRow] = []
    for year in years:
        if year not in ELECTION_DATES:
            raise KeyError(f"no verified election date recorded for {year}")
        meta = ELECTION_DATES[year]
        year_rows = [r for r in data_rows if r[COL_YEAR].strip() == str(year)]
        if not year_rows:
            raise ValueError(f"no rows found for election year {year}")

        total_row = next((r for r in year_rows if r[COL_LABEL].strip() == TOTAL_ROW_LABEL), None)
        if total_row is None:
            raise ValueError(f"no '{TOTAL_ROW_LABEL}' row for {year}")
        valid_votes = _parse_int(total_row[COL_DE_SECOND_VOTES])
        seats_all = _parse_int(total_row[COL_DE_SEATS_TOTAL])
        remarks = total_row[COL_REMARKS].strip()
        if remarks in MISSING:
            remarks = ""

        # Fail loudly if a mapped label vanished or was renamed upstream.
        present = {r[COL_LABEL].strip() for r in year_rows}
        aggregated: dict[str, dict] = {}
        for party_id, spec in mapping["mapping"].items():
            labels = [lbl for lbl in spec["official_labels"] if lbl in present]
            if not labels:
                continue
            votes = 0
            seats_total = seats_wk = seats_ll = 0
            saw_seats = False
            for label in labels:
                row = next(r for r in year_rows if r[COL_LABEL].strip() == label)
                votes += _parse_int(row[COL_DE_SECOND_VOTES]) or 0
                st = _parse_int(row[COL_DE_SEATS_TOTAL])
                if st is not None:
                    saw_seats = True
                    seats_total += st
                    seats_wk += _parse_int(row[COL_DE_SEATS_CONSTITUENCY]) or 0
                    seats_ll += _parse_int(row[COL_DE_SEATS_LIST]) or 0
            aggregated[party_id] = {
                "shortcut": spec["shortcut"],
                "labels": labels,
                "votes": votes,
                "seats_total": seats_total if saw_seats else 0,
                "seats_constituency": seats_wk if saw_seats else 0,
                "seats_list": seats_ll if saw_seats else 0,
            }

        mapped_votes = sum(a["votes"] for a in aggregated.values())
        mapped_seats = sum(a["seats_total"] for a in aggregated.values())

        context = _RowContext(
            meta=meta,
            year=year,
            parliament_id=parliament_id,
            valid_votes=valid_votes,
            seats_all=seats_all,
            remarks=remarks,
            year_rows=year_rows,
            stamp=stamp,
        )

        for party_id in sorted(aggregated, key=int):
            out.append(_build_row(party_id, aggregated[party_id], context))

        # Residual: every party the institutes would report as "Sonstige".
        residual_votes = (valid_votes or 0) - mapped_votes
        residual_seats = (seats_all or 0) - mapped_seats
        out.append(
            _build_row(
                residual_id,
                {
                    "shortcut": "Sonstige",
                    "labels": [],
                    "votes": residual_votes,
                    "seats_total": residual_seats,
                    "seats_constituency": 0,
                    "seats_list": 0,
                },
                context,
            )
        )
    return out


def rows_to_csv(rows: list[ElectionResultRow], path: Path) -> None:
    """Write the reviewed reference table. CSV so it stays human-verifiable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(rows[0]).keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def earliest_election_date() -> date:
    return min(date.fromisoformat(m["election_date"]) for m in ELECTION_DATES.values())


# The published percentages are rounded to one decimal, so a correctly
# recomputed share can differ by at most half a grid step.
MAX_ROUNDING_DELTA = 0.05 + 1e-9
MAX_SHARE_SUM_DELTA = 0.01


def validate_rows(rows: list[ElectionResultRow]) -> list[str]:
    """Reconcile the derived table against the official published figures.

    Returns a list of problems; empty means the table checks out. These are
    hard consistency checks, not tolerances to be tuned: if any of them fails,
    the parse is wrong and the numbers must not be used.
    """
    problems: list[str] = []
    years = sorted({r.election_year for r in rows})
    for year in years:
        yr = [r for r in rows if r.election_year == year]

        share_sum = sum(r.share_pct_computed or 0.0 for r in yr)
        if abs(share_sum - 100.0) > MAX_SHARE_SUM_DELTA:
            problems.append(f"{year}: computed shares sum to {share_sum:.4f}, not 100")

        seats_sum = sum(r.seats_total or 0 for r in yr)
        official_seats = yr[0].seats_total_all_parties
        if official_seats is not None and seats_sum != official_seats:
            problems.append(
                f"{year}: party seats sum to {seats_sum}, official total is {official_seats}"
            )

        votes_sum = sum(r.second_votes or 0 for r in yr)
        official_votes = yr[0].valid_votes_total
        if official_votes is not None and votes_sum != official_votes:
            problems.append(
                f"{year}: party votes sum to {votes_sum}, official total is {official_votes}"
            )

        for r in yr:
            if r.share_pct_official is None or r.share_pct_computed is None:
                continue
            delta = abs(r.share_pct_computed - r.share_pct_official)
            if delta > MAX_ROUNDING_DELTA:
                problems.append(
                    f"{year} {r.party_shortcut}: recomputed share "
                    f"{r.share_pct_computed:.4f} differs from published "
                    f"{r.share_pct_official} by {delta:.4f}"
                )

        if any(r.second_votes is not None and r.second_votes < 0 for r in yr):
            problems.append(f"{year}: negative vote count (residual underflow?)")

    return problems
