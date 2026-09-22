"""Turning polls into the arrays the Stan model consumes.

Pure Python, no Stan, no network -- so the fiddly parts (party selection,
grouping of unreported parties, the rounding grid, day indexing) are testable
on their own. This is where modeling bugs actually hide.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from wahlwetter.polls import PollObservation

#: Parties are modelled on the log-ratio scale relative to this reference
#: category, which is also where every unreported party is pooled.
REFERENCE_PARTY_ID = "0"

#: A party must be broken out by at least this fraction of the polls in the
#: window to get its own latent series. Below that its path would be almost
#: entirely prior-driven.
DEFAULT_MIN_PARTY_COVERAGE = 0.5

#: Grid the published values sit on, in percentage points, largest first.
ROUNDING_GRIDS = (1.0, 0.5, 0.1)


@dataclass(frozen=True, slots=True)
class ModelData:
    """Everything the Stan model needs, plus the labels to read it back."""

    parties: list[str]
    institutes: list[str]
    methods: list[str]
    start_date: date
    end_date: date
    polls: list[PollObservation]
    stan_data: dict = field(default_factory=dict)

    @property
    def n_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def n_parties(self) -> int:
        return len(self.parties)

    def day_index(self, day: date) -> int:
        """1-based day index, as Stan counts."""
        return (day - self.start_date).days + 1

    def date_for_index(self, index: int) -> date:
        return self.start_date + timedelta(days=index - 1)


def detect_rounding_grid(shares: Sequence[float]) -> float:
    """Smallest grid in percentage points that every published value sits on.

    dawum publishes most values on a 1.0 or 0.5 grid (see docs/data_source.md).
    The grid determines how much variance rounding contributes.
    """
    for grid in ROUNDING_GRIDS:
        if all(abs(v / grid - round(v / grid)) < 1e-6 for v in shares):
            return grid
    return ROUNDING_GRIDS[-1]


def select_parties(
    polls: Sequence[PollObservation],
    min_coverage: float = DEFAULT_MIN_PARTY_COVERAGE,
) -> list[str]:
    """Parties that get their own latent series.

    The reference category always qualifies. Everything else must clear the
    coverage threshold; parties below it stay pooled into the reference, which
    is exactly what the institutes themselves do with them.
    """
    if not polls:
        return [REFERENCE_PARTY_ID]
    counts: dict[str, int] = {}
    for poll in polls:
        for party in poll.shares:
            counts[party] = counts.get(party, 0) + 1
    threshold = min_coverage * len(polls)
    selected = {
        party
        for party, count in counts.items()
        if count >= threshold and party != REFERENCE_PARTY_ID
    }
    return [REFERENCE_PARTY_ID, *sorted(selected, key=int)]


def poll_groups(poll: PollObservation, parties: Sequence[str]) -> list[tuple[list[int], float]]:
    """Partition the modelled parties into the groups this poll reports.

    A party the poll broke out is its own group. Every modelled party the poll
    did **not** break out was folded into that poll's Sonstige, so it joins the
    reference group and the model compares the *sum* of those shares against
    the single reported value. No imputation is needed, and no information is
    invented.

    Returns (party indices, observed share as a proportion) per group.
    """
    index = {party: i for i, party in enumerate(parties)}
    reference_members = [index[REFERENCE_PARTY_ID]]
    groups: list[tuple[list[int], float]] = []

    for party in parties:
        if party == REFERENCE_PARTY_ID:
            continue
        if party in poll.shares:
            groups.append(([index[party]], poll.shares[party] / 100.0))
        else:
            # Folded into this poll's Sonstige.
            reference_members.append(index[party])

    # Anything the poll reported that we do not model also sits in the
    # reference group's observed value.
    reference_value = sum(share for party, share in poll.shares.items() if party not in index)
    reference_value += poll.shares.get(REFERENCE_PARTY_ID, 0.0)
    groups.append((sorted(reference_members), reference_value / 100.0))
    return groups


def build_model_data(
    polls: Sequence[PollObservation],
    start_date: date,
    end_date: date,
    *,
    min_party_coverage: float = DEFAULT_MIN_PARTY_COVERAGE,
    include_method_effects: bool = False,
    design_effect: float = 1.0,
) -> ModelData:
    """Assemble the Stan input.

    `include_method_effects` is off by default. With 9 of 11 institutes using a
    single survey method, a global method effect is identified almost entirely
    through the one institute that varies its method, and would then be
    extrapolated to every other institute. See docs/model.md.
    """
    window = sorted(
        (p for p in polls if start_date <= p.fieldwork_midpoint <= end_date),
        key=lambda p: (p.fieldwork_midpoint, p.survey_id),
    )
    if not window:
        raise ValueError(f"no polls with a fieldwork midpoint in {start_date}..{end_date}")

    parties = select_parties(window, min_party_coverage)
    institutes = sorted({p.institute_id for p in window}, key=int)
    methods = sorted({p.method_id for p in window}, key=int)
    institute_index = {v: i + 1 for i, v in enumerate(institutes)}
    method_index = {v: i + 1 for i, v in enumerate(methods)}

    n_days = (end_date - start_date).days + 1
    n_parties = len(parties)

    obs_poll: list[int] = []
    obs_value: list[float] = []
    obs_mask: list[list[float]] = []
    poll_day: list[int] = []
    poll_institute: list[int] = []
    poll_method: list[int] = []
    poll_n_eff: list[float] = []
    poll_rounding_sd: list[float] = []

    for i, poll in enumerate(window, start=1):
        poll_day.append((poll.fieldwork_midpoint - start_date).days + 1)
        poll_institute.append(institute_index[poll.institute_id])
        poll_method.append(method_index[poll.method_id])

        sample_size = poll.sample_size or 1000
        poll_n_eff.append(max(sample_size / design_effect, 1.0))

        grid = detect_rounding_grid(list(poll.shares.values()))
        # A value rounded to a grid of width d carries uniform error with
        # variance d^2/12. Expressed as a proportion, not a percentage.
        poll_rounding_sd.append((grid / 100.0) / math.sqrt(12.0))

        for members, value in poll_groups(poll, parties):
            mask = [0.0] * n_parties
            for m in members:
                mask[m] = 1.0
            obs_poll.append(i)
            obs_value.append(value)
            obs_mask.append(mask)

    stan_data = {
        "n_days": n_days,
        "n_parties": n_parties,
        "n_polls": len(window),
        "n_institutes": len(institutes),
        "n_methods": len(methods),
        "n_obs": len(obs_poll),
        "poll_day": poll_day,
        "poll_institute": poll_institute,
        "poll_method": poll_method,
        "poll_n_eff": poll_n_eff,
        "poll_rounding_sd": poll_rounding_sd,
        "obs_poll": obs_poll,
        "obs_value": obs_value,
        "obs_mask": obs_mask,
        "include_method_effects": int(include_method_effects),
    }

    return ModelData(
        parties=parties,
        institutes=institutes,
        methods=methods,
        start_date=start_date,
        end_date=end_date,
        polls=list(window),
        stan_data=stan_data,
    )
