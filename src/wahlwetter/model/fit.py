"""Fitting the trend model and checking that the fit can be trusted.

Convergence is gated, not merely reported: a fit that fails its diagnostics
must not silently become numbers on a website.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wahlwetter.model.data import ModelData

if TYPE_CHECKING:  # pragma: no cover
    from cmdstanpy import CmdStanMCMC, CmdStanModel

STAN_FILE = Path(__file__).parent / "stan" / "trend.stan"

#: Thresholds for accepting a fit. These are the conventional values; the point
#: of writing them down is that CI fails on them rather than a human squinting
#: at a table.
MAX_RHAT = 1.01
MIN_ESS_BULK = 400
MIN_ESS_TAIL = 400
MAX_DIVERGENT_FRACTION = 0.0
MAX_TREEDEPTH_FRACTION = 0.05


@dataclass(frozen=True, slots=True)
class SamplingConfig:
    chains: int = 4
    parallel_chains: int = 4
    iter_warmup: int = 500
    iter_sampling: int = 500
    seed: int = 20250223
    adapt_delta: float = 0.9
    max_treedepth: int = 10


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """Whether the sampler produced something usable."""

    max_rhat: float
    min_ess_bulk: float
    min_ess_tail: float
    n_divergent: int
    n_transitions: int
    n_max_treedepth: int
    worst_rhat_parameter: str
    worst_ess_parameter: str
    problems: list[str] = field(default_factory=list)

    @property
    def divergent_fraction(self) -> float:
        return self.n_divergent / self.n_transitions if self.n_transitions else 0.0

    @property
    def treedepth_fraction(self) -> float:
        return self.n_max_treedepth / self.n_transitions if self.n_transitions else 0.0

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_rhat": round(self.max_rhat, 4),
            "min_ess_bulk": round(self.min_ess_bulk, 1),
            "min_ess_tail": round(self.min_ess_tail, 1),
            "n_divergent": self.n_divergent,
            "n_transitions": self.n_transitions,
            "divergent_fraction": round(self.divergent_fraction, 5),
            "n_max_treedepth": self.n_max_treedepth,
            "treedepth_fraction": round(self.treedepth_fraction, 5),
            "worst_rhat_parameter": self.worst_rhat_parameter,
            "worst_ess_parameter": self.worst_ess_parameter,
            "ok": self.ok,
            "problems": list(self.problems),
        }


def cmdstan_available() -> bool:
    """True when a usable CmdStan toolchain is present."""
    try:
        import cmdstanpy
    except ImportError:
        return False
    try:
        path = Path(cmdstanpy.cmdstan_path())
    except Exception:
        return False
    return path.exists() and shutil.which("make") is not None


def compile_model(stan_file: Path | None = None) -> CmdStanModel:
    from cmdstanpy import CmdStanModel

    return CmdStanModel(stan_file=str(stan_file or STAN_FILE))


def sample(
    model_data: ModelData,
    config: SamplingConfig | None = None,
    *,
    model: CmdStanModel | None = None,
    output_dir: Path | None = None,
) -> CmdStanMCMC:
    config = config or SamplingConfig()
    model = model or compile_model()
    return model.sample(
        data=model_data.stan_data,
        chains=config.chains,
        parallel_chains=min(config.parallel_chains, os.cpu_count() or 1),
        iter_warmup=config.iter_warmup,
        iter_sampling=config.iter_sampling,
        seed=config.seed,
        adapt_delta=config.adapt_delta,
        max_treedepth=config.max_treedepth,
        show_progress=False,
        output_dir=str(output_dir) if output_dir else None,
    )


#: Diagnostics are read off these parameters only. The per-day latent states
#: number in the thousands and their tails are noisy by construction; what must
#: converge is the structural parameters and the shares people actually read.
DIAGNOSTIC_PREFIXES = (
    "sigma_rw",
    "sigma_house",
    "sigma_method",
    "tau",
    "design_effect",
    "house",
    "share",
)


def collect_diagnostics(fit: CmdStanMCMC, config: SamplingConfig | None = None) -> Diagnostics:
    config = config or SamplingConfig()
    summary = fit.summary()

    rows = summary[summary.index.map(lambda name: name.startswith(DIAGNOSTIC_PREFIXES))]
    if rows.empty:  # pragma: no cover - would mean the model changed shape
        raise ValueError("no diagnostic parameters found in the fit summary")

    rhat_column = next(c for c in rows.columns if c.lower().startswith("r_hat"))
    bulk_column = next(c for c in rows.columns if "ess_bulk" in c.lower())
    tail_column = next(c for c in rows.columns if "ess_tail" in c.lower())

    rhat = rows[rhat_column].dropna()
    bulk = rows[bulk_column].dropna()
    tail = rows[tail_column].dropna()

    max_rhat = float(rhat.max())
    min_bulk = float(bulk.min())
    min_tail = float(tail.min())

    method_vars = fit.method_variables()
    divergent = method_vars["divergent__"]
    treedepth = method_vars["treedepth__"]
    n_transitions = int(divergent.size)
    n_divergent = int(divergent.sum())
    n_max_treedepth = int((treedepth >= config.max_treedepth).sum())

    problems: list[str] = []
    if max_rhat > MAX_RHAT:
        problems.append(f"max R-hat {max_rhat:.4f} exceeds {MAX_RHAT} (worst: {rhat.idxmax()})")
    if min_bulk < MIN_ESS_BULK:
        problems.append(
            f"min bulk ESS {min_bulk:.0f} below {MIN_ESS_BULK} (worst: {bulk.idxmin()})"
        )
    if min_tail < MIN_ESS_TAIL:
        problems.append(
            f"min tail ESS {min_tail:.0f} below {MIN_ESS_TAIL} (worst: {tail.idxmin()})"
        )
    if n_divergent / max(n_transitions, 1) > MAX_DIVERGENT_FRACTION:
        problems.append(f"{n_divergent} divergent transitions of {n_transitions}")
    if n_max_treedepth / max(n_transitions, 1) > MAX_TREEDEPTH_FRACTION:
        problems.append(f"{n_max_treedepth} of {n_transitions} transitions hit max treedepth")

    return Diagnostics(
        max_rhat=max_rhat,
        min_ess_bulk=min_bulk,
        min_ess_tail=min_tail,
        n_divergent=n_divergent,
        n_transitions=n_transitions,
        n_max_treedepth=n_max_treedepth,
        worst_rhat_parameter=str(rhat.idxmax()),
        worst_ess_parameter=str(bulk.idxmin()),
        problems=problems,
    )


def posterior_predictive_check(fit: CmdStanMCMC, model_data: ModelData) -> dict[str, Any]:
    """How often the observed value falls outside its predictive interval.

    With well-calibrated uncertainty roughly 5% of observations should land
    outside a 95% interval. Far below that means the model is over-dispersed
    and its intervals are too wide to be useful; far above means it is
    overconfident, which is the more dangerous direction for a public site.
    """
    import numpy as np

    y_rep = fit.stan_variable("y_rep")
    observed = np.asarray(model_data.stan_data["obs_value"], dtype=float)

    lower = np.percentile(y_rep, 2.5, axis=0)
    upper = np.percentile(y_rep, 97.5, axis=0)
    outside = int(((observed < lower) | (observed > upper)).sum())

    lower50 = np.percentile(y_rep, 25.0, axis=0)
    upper50 = np.percentile(y_rep, 75.0, axis=0)
    inside50 = int(((observed >= lower50) & (observed <= upper50)).sum())

    n = observed.size
    return {
        "n_observations": n,
        "outside_95_interval": outside,
        "outside_95_fraction": round(outside / n, 4),
        "inside_50_interval": inside50,
        "inside_50_fraction": round(inside50 / n, 4),
        "mean_absolute_residual_pp": round(
            float(np.abs(y_rep.mean(axis=0) - observed).mean() * 100), 4
        ),
    }


def latest_share_summary(
    fit: CmdStanMCMC, model_data: ModelData, day: date | None = None
) -> dict[str, dict[str, float]]:
    """Posterior summary of shares on one day, in percentage points."""
    import numpy as np

    share = fit.stan_variable("share")  # draws x days x parties
    index = model_data.day_index(day or model_data.end_date) - 1
    slice_ = share[:, index, :] * 100.0
    out: dict[str, dict[str, float]] = {}
    for k, party in enumerate(model_data.parties):
        values = slice_[:, k]
        out[party] = {
            "median": round(float(np.median(values)), 3),
            "mean": round(float(values.mean()), 3),
            "q2_5": round(float(np.percentile(values, 2.5)), 3),
            "q10": round(float(np.percentile(values, 10)), 3),
            "q25": round(float(np.percentile(values, 25)), 3),
            "q75": round(float(np.percentile(values, 75)), 3),
            "q90": round(float(np.percentile(values, 90)), 3),
            "q97_5": round(float(np.percentile(values, 97.5)), 3),
        }
    return out
