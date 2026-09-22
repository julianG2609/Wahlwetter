"""Precomputed JSON for the static site.

The site is static: no backend, no queries at render time. Everything a page
needs is computed here and written as JSON the browser reads directly.

Nothing here invents a number. Every value traces to the model output or to the
ingested tables, and the attribution both sources require travels with it.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from wahlwetter.polls import BUNDESTAG_PARLIAMENT_ID, PollObservation

#: dawum and the Bundeswahlleiterin both require attribution wherever their
#: data appears. It travels with the data rather than living only in a footer
#: that could be dropped in a redesign.
ATTRIBUTION = {
    "polls": {
        "text": "Daten von dawum.de (Open Database License (ODbL))",
        "url": "https://dawum.de",
        "license": "ODC Open Database License (ODbL) v1.0",
        "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
    },
    "election_results": {
        "text": (
            "Amtliche Wahlergebnisse: Die Bundeswahlleiterin, Statistisches Bundesamt (Destatis)"
        ),
        "url": "https://www.bundeswahlleiterin.de",
        # EN DASHes are part of the official licence name; do not "fix" them.
        "license": "Datenlizenz Deutschland \u2013 Namensnennung \u2013 Version 2.0",
        "license_url": "https://www.govdata.de/dl-de/by-2-0",
    },
}


def poll_sd_pp(share_pct: float, sample_size: int | None, design_effect: float) -> float:
    """Standard deviation of a single published value, in percentage points.

    Uses the model's estimated design effect rather than assuming simple random
    sampling, because the model found published values vary far less than that
    would imply.
    """
    if not sample_size or sample_size <= 0:
        return float("nan")
    p = max(min(share_pct / 100.0, 1.0), 0.0)
    variance = design_effect * p * (1 - p) / sample_size
    return math.sqrt(max(variance, 0.0)) * 100.0


def poll_deviation(
    poll: PollObservation,
    trend_on_day: dict[str, float],
    design_effect: float,
) -> list[dict[str, Any]]:
    """How far a poll sits from the trend, in its own standard deviations.

    This is what answers "is this new poll's change meaningful?". A poll two
    points above the trend is unremarkable for a small party and notable for a
    large one, so the difference is expressed relative to the uncertainty of
    that particular value rather than in raw points.
    """
    out: list[dict[str, Any]] = []
    for party, value in sorted(poll.shares.items(), key=lambda kv: kv[0]):
        expected = trend_on_day.get(party)
        if expected is None:
            continue
        sd = poll_sd_pp(value, poll.sample_size, design_effect)
        difference = value - expected
        z = difference / sd if sd and not math.isnan(sd) and sd > 0 else None
        out.append(
            {
                "party_id": party,
                "value": round(value, 2),
                "trend": round(expected, 2),
                "difference_pp": round(difference, 2),
                "sd_pp": None if math.isnan(sd) else round(sd, 3),
                "z": None if z is None else round(z, 2),
                # Deliberately not "significant". A single poll two standard
                # deviations from trend is expected roughly one time in twenty
                # even when nothing has changed, and dozens of poll-party
                # combinations are published every month.
                "notable": bool(z is not None and abs(z) >= 2.0),
            }
        )
    return out


def trend_on(output: dict[str, Any], day: str) -> dict[str, float]:
    """Median latent share per party on one date."""
    trend = output["trend"]
    try:
        index = trend["series"][0]["dates"].index(day)
    except (ValueError, IndexError, KeyError):
        return {}
    return {s["party_id"]: s["median"][index] for s in trend["series"]}


def latest_estimates(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Current estimate per party, sorted by size, with intervals."""
    rows: list[dict[str, Any]] = []
    for series in output["trend"]["series"]:
        rows.append(
            {
                "party_id": series["party_id"],
                "party_shortcut": series["party_shortcut"],
                "median": series["median"][-1],
                "q10": series["q10"][-1],
                "q90": series["q90"][-1],
                "q2_5": series["q2_5"][-1],
                "q97_5": series["q97_5"][-1],
            }
        )
    rows.sort(key=lambda r: -r["median"])
    return rows


def thinned_trend(output: dict[str, Any], keep_every: int = 1) -> dict[str, Any]:
    """The trend, optionally thinned to keep the page payload small."""
    trend = output["trend"]
    if keep_every <= 1:
        return trend
    series = []
    for s in trend["series"]:
        entry = {k: v for k, v in s.items() if not isinstance(v, list)}
        for key, values in s.items():
            if isinstance(values, list):
                # Always keep the final point: it is the current estimate.
                kept = values[::keep_every]
                if kept[-1] != values[-1]:
                    kept = [*kept, values[-1]]
                entry[key] = kept
        series.append(entry)
    return {**trend, "series": series}


def build_site_data(
    model_output: dict[str, Any],
    polls: list[PollObservation],
    *,
    party_names: dict[str, str],
    institute_names: dict[str, str],
    tasker_names: dict[str, str],
    method_names: dict[str, str],
    recent_polls: int = 60,
    trend_keep_every: int = 1,
) -> dict[str, Any]:
    design_effect = model_output["parameters"]["design_effect"]["median"]
    end = model_output["trend"]["end_date"]

    ordered = sorted(
        polls, key=lambda p: (p.fieldwork_end, p.published_at, p.survey_id), reverse=True
    )
    listed = ordered[:recent_polls]

    poll_rows = [
        {
            "survey_id": p.survey_id,
            "institute": institute_names.get(p.institute_id, p.institute_id),
            "institute_id": p.institute_id,
            "tasker": tasker_names.get(p.tasker_id, p.tasker_id) if hasattr(p, "tasker_id") else "",
            "method": method_names.get(p.method_id, p.method_id),
            "published_at": p.published_at.isoformat(),
            "fieldwork_start": p.fieldwork_start.isoformat(),
            "fieldwork_end": p.fieldwork_end.isoformat(),
            "sample_size": p.sample_size,
            "shares": {k: round(v, 2) for k, v in sorted(p.shares.items())},
        }
        for p in listed
    ]

    newest = listed[0] if listed else None
    newest_block = None
    if newest is not None:
        day = min(newest.fieldwork_midpoint.isoformat(), end)
        newest_block = {
            "survey_id": newest.survey_id,
            "institute": institute_names.get(newest.institute_id, newest.institute_id),
            "fieldwork_end": newest.fieldwork_end.isoformat(),
            "compared_on": day,
            "design_effect": design_effect,
            "parties": poll_deviation(newest, trend_on(model_output, day), design_effect),
        }

    house = model_output["house_effects"]
    # Fail loudly rather than open. `reportable` withholds house effects fitted
    # to too few polls; defaulting a missing flag to True would silently
    # publish exactly the estimates the flag exists to suppress whenever the
    # model output predates it.
    missing = [h for h in house if "reportable" not in h]
    if missing:
        raise ValueError(
            f"{len(missing)} house_effects rows have no 'reportable' flag. "
            "This model output predates the under-powered-institute guard; "
            "re-run `wahlwetter model` before building the site."
        )
    reportable = [h for h in house if h["reportable"]]

    return {
        "generated_at": model_output["generated_at"],
        "window": model_output["window"],
        "diagnostics": model_output["diagnostics"],
        "posterior_predictive_check": model_output["posterior_predictive_check"],
        "parameters": model_output["parameters"],
        "latest": latest_estimates(model_output),
        "trend": thinned_trend(model_output, trend_keep_every),
        "house_effects": reportable,
        "house_effects_withheld": [h for h in house if not h["reportable"]],
        "polls": poll_rows,
        "newest_poll": newest_block,
        "party_names": party_names,
        "attribution": ATTRIBUTION,
    }


def write_site_data(payload: dict[str, Any], directory: Path) -> list[Path]:
    """Split into per-page files so no page downloads what it does not use."""
    from wahlwetter.storage import write_json

    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    overview = {
        k: payload[k]
        for k in (
            "generated_at",
            "window",
            "latest",
            "newest_poll",
            "party_names",
            "attribution",
        )
    }
    files: dict[str, Any] = {
        "overview.json": overview,
        "trend.json": {
            "generated_at": payload["generated_at"],
            "trend": payload["trend"],
            "party_names": payload["party_names"],
            "attribution": payload["attribution"],
        },
        "pollsters.json": {
            "generated_at": payload["generated_at"],
            "house_effects": payload["house_effects"],
            "house_effects_withheld": payload["house_effects_withheld"],
            "polls": payload["polls"],
            "party_names": payload["party_names"],
            "attribution": payload["attribution"],
        },
        "diagnostics.json": {
            "generated_at": payload["generated_at"],
            "window": payload["window"],
            "diagnostics": payload["diagnostics"],
            "posterior_predictive_check": payload["posterior_predictive_check"],
            "parameters": payload["parameters"],
            "attribution": payload["attribution"],
        },
    }
    for name, content in files.items():
        path = directory / name
        write_json(content, path)
        written.append(path)
    return written


def load_polls_for_site(
    tables_dir: Path, parliament_id: str = BUNDESTAG_PARLIAMENT_ID
) -> list[PollObservation]:
    from wahlwetter.polls import load_polls

    return load_polls(parliament_id, tables_dir)
