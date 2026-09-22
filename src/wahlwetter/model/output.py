"""Turning a fit into the JSON the site reads.

Everything published carries the interval it was estimated with. A median
without an interval is the single most misleading thing a poll aggregator can
put on a page, so the schema makes intervals mandatory rather than optional.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from wahlwetter.model.data import ModelData

if TYPE_CHECKING:  # pragma: no cover
    from cmdstanpy import CmdStanMCMC

#: Published quantiles. 50% and 80% are the honest working intervals; 95% is
#: included because readers expect it, not because it is more informative.
QUANTILES = {
    "q2_5": 2.5,
    "q10": 10.0,
    "q25": 25.0,
    "median": 50.0,
    "q75": 75.0,
    "q90": 90.0,
    "q97_5": 97.5,
}


def daily_trend(
    fit: CmdStanMCMC,
    model_data: ModelData,
    *,
    party_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Posterior summary of the latent share per party per day."""
    import numpy as np

    share = fit.stan_variable("share") * 100.0  # draws x days x parties
    names = party_names or {}

    series: list[dict[str, Any]] = []
    for k, party in enumerate(model_data.parties):
        values = share[:, :, k]
        entry: dict[str, Any] = {
            "party_id": party,
            "party_shortcut": names.get(party, party),
            "dates": [
                model_data.date_for_index(t + 1).isoformat() for t in range(model_data.n_days)
            ],
        }
        for label, q in QUANTILES.items():
            entry[label] = [round(float(v), 3) for v in np.percentile(values, q, axis=0)]
        series.append(entry)

    return {
        "start_date": model_data.start_date.isoformat(),
        "end_date": model_data.end_date.isoformat(),
        "n_days": model_data.n_days,
        "parties": model_data.parties,
        "series": series,
    }


def house_effects(
    fit: CmdStanMCMC,
    model_data: ModelData,
    *,
    institute_names: dict[str, str] | None = None,
    party_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """House effects per institute per party, on the log-ratio scale.

    Reported both as the raw log-ratio shift and as an approximate effect in
    percentage points at that party's current level, because a log-ratio is
    not something a reader can interpret.
    """
    import numpy as np

    house = fit.stan_variable("house")  # draws x institutes x free parties
    share = fit.stan_variable("share")  # draws x days x parties
    latest = share[:, -1, :].mean(axis=0)

    inames = institute_names or {}
    pnames = party_names or {}
    out: list[dict[str, Any]] = []

    for j, institute in enumerate(model_data.institutes):
        for k, party in enumerate(model_data.parties[1:]):  # reference excluded
            values = house[:, j, k]
            level = float(latest[k + 1])
            # d(share)/d(log-ratio) = share * (1 - share) for small shifts.
            approx_pp = float(np.median(values)) * level * (1 - level) * 100.0
            out.append(
                {
                    "institute_id": institute,
                    "institute_name": inames.get(institute, institute),
                    "party_id": party,
                    "party_shortcut": pnames.get(party, party),
                    "median_log_ratio": round(float(np.median(values)), 4),
                    "q10_log_ratio": round(float(np.percentile(values, 10)), 4),
                    "q90_log_ratio": round(float(np.percentile(values, 90)), 4),
                    "approx_effect_pp": round(approx_pp, 3),
                    # Whether the 80% interval excludes zero -- a weak claim,
                    # deliberately not called "significant".
                    "excludes_zero_80": bool(
                        np.percentile(values, 10) > 0 or np.percentile(values, 90) < 0
                    ),
                }
            )
    return out


def model_parameters(fit: CmdStanMCMC, model_data: ModelData) -> dict[str, Any]:
    """The structural parameters, for the methodology page."""
    import numpy as np

    def summarize(name: str) -> dict[str, float]:
        values = fit.stan_variable(name)
        flat = np.asarray(values).reshape(values.shape[0], -1)
        return {
            "median": round(float(np.median(flat)), 5),
            "q10": round(float(np.percentile(flat, 10)), 5),
            "q90": round(float(np.percentile(flat, 90)), 5),
        }

    out = {
        "tau_extra_error": summarize("tau"),
        "design_effect": summarize("design_effect"),
        "sigma_random_walk": summarize("sigma_rw"),
        "sigma_house": summarize("sigma_house"),
        "include_method_effects": bool(model_data.stan_data["include_method_effects"]),
    }
    return out


def build_output(
    fit: CmdStanMCMC,
    model_data: ModelData,
    *,
    diagnostics: dict[str, Any],
    ppc: dict[str, Any],
    party_names: dict[str, str] | None = None,
    institute_names: dict[str, str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    from datetime import UTC, datetime

    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "parliament_id": "0",
        "window": {
            "start_date": model_data.start_date.isoformat(),
            "end_date": model_data.end_date.isoformat(),
            "n_polls": model_data.stan_data["n_polls"],
            "n_institutes": len(model_data.institutes),
        },
        "diagnostics": diagnostics,
        "posterior_predictive_check": ppc,
        "parameters": model_parameters(fit, model_data),
        "trend": daily_trend(fit, model_data, party_names=party_names),
        "house_effects": house_effects(
            fit, model_data, institute_names=institute_names, party_names=party_names
        ),
        "attribution": {
            "polls": "Daten von dawum.de (Open Database License (ODbL))",
            "polls_url": "https://dawum.de",
            "polls_license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        },
    }


def latest_estimates(output: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Convenience view of the final day, for a quick console summary."""
    out: dict[str, dict[str, float]] = {}
    for series in output["trend"]["series"]:
        out[series["party_id"]] = {
            label: series[label][-1] for label in QUANTILES if label in series
        }
    return out


def trend_date_range(output: dict[str, Any]) -> tuple[date, date]:
    return (
        date.fromisoformat(output["trend"]["start_date"]),
        date.fromisoformat(output["trend"]["end_date"]),
    )
