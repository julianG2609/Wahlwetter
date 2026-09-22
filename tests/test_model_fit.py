"""Fit-layer tests.

The diagnostics gate is tested against fake summaries, so the thresholds are
verified without paying for a sampler run. Tests that need a real CmdStan
toolchain are marked and skipped when it is absent, so the ordinary CI job
stays fast.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from wahlwetter.model import fit as F
from wahlwetter.model.data import build_model_data
from wahlwetter.polls import PollObservation

needs_cmdstan = pytest.mark.skipif(
    not F.cmdstan_available(), reason="CmdStan toolchain not available"
)


class FakeFit:
    """Minimal stand-in for CmdStanMCMC, enough for the diagnostics gate."""

    def __init__(
        self, rhat=1.001, ess_bulk=1500.0, ess_tail=1500.0, divergent=0, treedepth=5, n=100
    ):
        import numpy as np

        self._summary = pd.DataFrame(
            {
                "R_hat": [rhat, 1.0],
                "ESS_bulk": [ess_bulk, 2000.0],
                "ESS_tail": [ess_tail, 2000.0],
            },
            index=["sigma_rw[1]", "share[1,1]"],
        )
        self._vars = {
            "divergent__": np.array([1.0] * divergent + [0.0] * (n - divergent)),
            "treedepth__": np.full(n, float(treedepth)),
        }

    def summary(self):
        return self._summary

    def method_variables(self):
        return self._vars


def test_clean_fit_passes_the_gate():
    d = F.collect_diagnostics(FakeFit())
    assert d.ok
    assert d.problems == []


def test_high_rhat_fails():
    d = F.collect_diagnostics(FakeFit(rhat=1.05))
    assert not d.ok
    assert any("R-hat" in p for p in d.problems)
    assert d.worst_rhat_parameter == "sigma_rw[1]"


def test_rhat_exactly_at_threshold_passes():
    assert F.collect_diagnostics(FakeFit(rhat=F.MAX_RHAT)).ok


def test_low_bulk_ess_fails():
    d = F.collect_diagnostics(FakeFit(ess_bulk=50.0))
    assert any("bulk ESS" in p for p in d.problems)


def test_low_tail_ess_fails():
    d = F.collect_diagnostics(FakeFit(ess_tail=50.0))
    assert any("tail ESS" in p for p in d.problems)


def test_any_divergence_fails():
    """Divergences are not tolerated at all: the default threshold is zero."""
    d = F.collect_diagnostics(FakeFit(divergent=1, n=1000))
    assert not d.ok
    assert any("divergent" in p for p in d.problems)
    assert d.n_divergent == 1


def test_max_treedepth_saturation_fails():
    config = F.SamplingConfig(max_treedepth=10)
    d = F.collect_diagnostics(FakeFit(treedepth=10, n=100), config)
    assert any("treedepth" in p for p in d.problems)
    assert d.treedepth_fraction == pytest.approx(1.0)


def test_treedepth_below_the_limit_passes():
    config = F.SamplingConfig(max_treedepth=10)
    assert F.collect_diagnostics(FakeFit(treedepth=9), config).ok


def test_diagnostics_serialize():
    d = F.collect_diagnostics(FakeFit(rhat=1.05))
    payload = d.to_dict()
    assert payload["ok"] is False
    assert payload["max_rhat"] == pytest.approx(1.05)
    assert isinstance(payload["problems"], list)


def test_multiple_problems_are_all_reported():
    d = F.collect_diagnostics(FakeFit(rhat=1.2, ess_bulk=10.0, divergent=5, n=100))
    assert len(d.problems) >= 3


# --- end-to-end, only where a toolchain exists -----------------------------


def tiny_polls() -> list[PollObservation]:
    polls = []
    for i in range(12):
        day = date(2025, 6, 1 + i)
        polls.append(
            PollObservation(
                survey_id=f"s{i}",
                institute_id=str(1 + i % 3),
                published_at=day,
                fieldwork_start=day,
                fieldwork_end=day,
                fieldwork_midpoint=day,
                sample_size=1000,
                shares={"1": 30.0, "2": 25.0, "0": 45.0},
                method_id="3",
            )
        )
    return polls


@needs_cmdstan
def test_model_compiles():
    model = F.compile_model()
    assert model.exe_file


@needs_cmdstan
def test_tiny_fit_runs_and_produces_shares():
    md = build_model_data(tiny_polls(), date(2025, 6, 1), date(2025, 6, 12), min_party_coverage=0.0)
    config = F.SamplingConfig(chains=2, parallel_chains=2, iter_warmup=200, iter_sampling=200)
    fit = F.sample(md, config)
    shares = F.latest_share_summary(fit, md)
    assert set(shares) == set(md.parties)
    total = sum(v["median"] for v in shares.values())
    assert total == pytest.approx(100.0, abs=1.0)


@needs_cmdstan
def test_shares_recover_a_flat_truth():
    """With constant input the latent path should sit on the input values."""
    md = build_model_data(tiny_polls(), date(2025, 6, 1), date(2025, 6, 12), min_party_coverage=0.0)
    config = F.SamplingConfig(chains=2, parallel_chains=2, iter_warmup=300, iter_sampling=300)
    fit = F.sample(md, config)
    shares = F.latest_share_summary(fit, md)
    assert shares["1"]["median"] == pytest.approx(30.0, abs=2.0)
    assert shares["2"]["median"] == pytest.approx(25.0, abs=2.0)


@needs_cmdstan
def test_ppc_reports_every_observation():
    md = build_model_data(tiny_polls(), date(2025, 6, 1), date(2025, 6, 12), min_party_coverage=0.0)
    fit = F.sample(
        md, F.SamplingConfig(chains=2, parallel_chains=2, iter_warmup=200, iter_sampling=200)
    )
    ppc = F.posterior_predictive_check(fit, md)
    assert ppc["n_observations"] == md.stan_data["n_obs"]
    assert 0.0 <= ppc["outside_95_fraction"] <= 1.0
