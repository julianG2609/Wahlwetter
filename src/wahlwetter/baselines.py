"""Simple baselines.

These are the benchmarks the Bayesian model has to beat. They are deliberately
naive; the point is that "naive but honest" is a surprisingly hard target, and
a model that cannot beat them is not earning its complexity.

All three return a point estimate per party, summing to 100.

A note on missing parties. A party absent from a poll's results has been folded
into that institute's "Sonstige" (see docs/data_source.md) -- it is not zero.
Treating it as zero would bias small parties downwards, and dropping the poll
would throw away most of the data. Instead we use the rule dawum documents:
work out what fraction of the "other" bucket the party takes among institutes
that do break it out, and apply that fraction to the institutes that do not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol, runtime_checkable

from wahlwetter.polls import PollObservation

RESIDUAL_PARTY_ID = "0"


@runtime_checkable
class Baseline(Protocol):
    """A point estimator of current vote shares."""

    name: str

    def estimate(
        self,
        polls: Sequence[PollObservation],
        as_of: date,
        parties: Sequence[str],
    ) -> dict[str, float]:
        """Estimate shares for `parties`, using only polls available at `as_of`."""
        ...


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def available(polls: Sequence[PollObservation], as_of: date) -> list[PollObservation]:
    return [p for p in polls if p.is_available_on(as_of)]


def newest_per_institute(polls: Sequence[PollObservation]) -> list[PollObservation]:
    """Keep only each institute's most recent poll, by end of fieldwork."""
    best: dict[str, PollObservation] = {}
    for poll in polls:
        current = best.get(poll.institute_id)
        key = (poll.fieldwork_end, poll.published_at, poll.survey_id)
        if current is None or key > (
            current.fieldwork_end,
            current.published_at,
            current.survey_id,
        ):
            best[poll.institute_id] = poll
    return sorted(best.values(), key=lambda p: (p.fieldwork_end, p.survey_id))


def residual_ratios(polls: Sequence[PollObservation], parties: Sequence[str]) -> dict[str, float]:
    """For each party, its mean share of the 'other' bucket where it is broken out.

    The bucket is the party's own share plus the reported Sonstige, which is
    what it would have been folded into had the institute not listed it.
    """
    ratios: dict[str, float] = {}
    for party in parties:
        if party == RESIDUAL_PARTY_ID:
            continue
        fractions = []
        for poll in polls:
            if party not in poll.shares:
                continue
            own = poll.shares[party]
            bucket = own + poll.shares.get(RESIDUAL_PARTY_ID, 0.0)
            if bucket > 0:
                fractions.append(own / bucket)
        if fractions:
            ratios[party] = sum(fractions) / len(fractions)
    return ratios


def complete_vector(
    poll: PollObservation, parties: Sequence[str], ratios: dict[str, float]
) -> dict[str, float]:
    """Fill in parties this poll did not break out, taking them from Sonstige."""
    vector = {p: poll.shares.get(p, 0.0) for p in parties}
    residual = poll.shares.get(RESIDUAL_PARTY_ID, 0.0)

    missing = [
        p for p in parties if p != RESIDUAL_PARTY_ID and p not in poll.shares and p in ratios
    ]
    imputed_total = 0.0
    for party in missing:
        # Each missing party takes its usual fraction of this poll's residual.
        imputed = ratios[party] * residual
        vector[party] = imputed
        imputed_total += imputed

    if RESIDUAL_PARTY_ID in vector:
        # Never let imputation push Sonstige negative; scale back instead.
        if imputed_total > residual and imputed_total > 0:
            scale = residual / imputed_total
            for party in missing:
                vector[party] *= scale
            imputed_total = residual
        vector[RESIDUAL_PARTY_ID] = residual - imputed_total
    return vector


def normalize(vector: dict[str, float]) -> dict[str, float]:
    total = sum(vector.values())
    if total <= 0:
        return dict(vector)
    return {k: 100.0 * v / total for k, v in vector.items()}


def weighted_mean(
    vectors: Sequence[tuple[dict[str, float], float]], parties: Sequence[str]
) -> dict[str, float]:
    total_weight = sum(w for _, w in vectors)
    if total_weight <= 0:
        return dict.fromkeys(parties, 0.0)
    out = {
        party: sum(v.get(party, 0.0) * w for v, w in vectors) / total_weight for party in parties
    }
    return normalize(out)


def _aggregate(
    polls: Sequence[PollObservation],
    parties: Sequence[str],
    weights: Sequence[float],
) -> dict[str, float]:
    ratios = residual_ratios(polls, parties)
    vectors = [
        (complete_vector(p, parties, ratios), w) for p, w in zip(polls, weights, strict=True)
    ]
    return weighted_mean(vectors, parties)


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LastPollPerInstitute:
    """Unweighted mean of each institute's most recent poll.

    No recency weighting and no time window: an institute that last polled
    months ago still counts in full. That is the point -- it isolates the value
    of recency weighting when compared against the other two.
    """

    name: str = "last_poll_per_institute"

    def estimate(
        self, polls: Sequence[PollObservation], as_of: date, parties: Sequence[str]
    ) -> dict[str, float]:
        selected = newest_per_institute(available(polls, as_of))
        if not selected:
            return dict.fromkeys(parties, 0.0)
        return _aggregate(selected, parties, [1.0] * len(selected))


@dataclass(frozen=True, slots=True)
class RollingAverage:
    """Unweighted mean of every poll whose fieldwork ended in the last N days.

    Institutes that poll more often therefore count more, which is the obvious
    naive approach and a useful contrast to the per-institute baselines.
    """

    window_days: int = 14
    weight_by_sample_size: bool = False

    @property
    def name(self) -> str:
        suffix = "_n_weighted" if self.weight_by_sample_size else ""
        return f"rolling_average_{self.window_days}d{suffix}"

    def estimate(
        self, polls: Sequence[PollObservation], as_of: date, parties: Sequence[str]
    ) -> dict[str, float]:
        cutoff = as_of - timedelta(days=self.window_days)
        selected = [p for p in available(polls, as_of) if p.fieldwork_end >= cutoff]
        if not selected:
            return dict.fromkeys(parties, 0.0)
        if self.weight_by_sample_size:
            weights = [float(p.sample_size or 0) for p in selected]
            if sum(weights) <= 0:
                weights = [1.0] * len(selected)
        else:
            weights = [1.0] * len(selected)
        return _aggregate(selected, parties, weights)


@dataclass(frozen=True, slots=True)
class DawumStyleTrend:
    """dawum's own published method, as documented on dawum.de/Bundestag/.

    Documented there, and implemented here:

    * only each institute's newest poll is used;
    * a poll is included only while its last fieldwork day lies within 20 days
      of the most recent last fieldwork day in the set;
    * polls are weighted by that lag, never below one third;
    * sample size is not used at all.

    NOT documented, and therefore an assumption: the shape of the weight
    between 1.0 and 1/3. dawum states only the endpoints. We interpolate
    linearly. The weights panel on dawum's site is rendered client-side, so the
    shape could not be verified from the page source.
    """

    window_days: int = 20
    min_weight: float = 1.0 / 3.0

    name: str = "dawum_style_trend"

    def estimate(
        self, polls: Sequence[PollObservation], as_of: date, parties: Sequence[str]
    ) -> dict[str, float]:
        candidates = newest_per_institute(available(polls, as_of))
        if not candidates:
            return dict.fromkeys(parties, 0.0)

        latest = max(p.fieldwork_end for p in candidates)
        selected = [p for p in candidates if (latest - p.fieldwork_end).days <= self.window_days]
        if not selected:
            return dict.fromkeys(parties, 0.0)

        weights = [self.weight_for(latest, p.fieldwork_end) for p in selected]
        return _aggregate(selected, parties, weights)

    def weight_for(self, latest: date, fieldwork_end: date) -> float:
        lag = (latest - fieldwork_end).days
        if self.window_days <= 0:
            return 1.0
        fraction = min(max(lag / self.window_days, 0.0), 1.0)
        return 1.0 - fraction * (1.0 - self.min_weight)


def default_baselines() -> list[Baseline]:
    return [
        LastPollPerInstitute(),
        RollingAverage(window_days=14),
        RollingAverage(window_days=30),
        RollingAverage(window_days=14, weight_by_sample_size=True),
        DawumStyleTrend(),
    ]
