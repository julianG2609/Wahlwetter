"""Configuration for ingestion and validation.

Thresholds live here rather than as literals scattered through the rules, so
that every tolerance is visible in one place and can be justified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Repository root, derived from this file's location (src/wahlwetter/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = REPO_ROOT / "data"
TABLES_DIR = DATA_DIR / "tables"
STATE_DIR = DATA_DIR / "state"
QUARANTINE_DIR = DATA_DIR / "quarantine"
RAW_DIR = DATA_DIR / "raw"
CONFIG_DIR = REPO_ROOT / "config"

PARTIES_BY_PARLIAMENT_PATH = CONFIG_DIR / "parties_by_parliament.json"

# Identifies us to upstream. The brief requires a User-Agent naming the repo.
USER_AGENT = "wahlwetter/0.1 (+https://github.com/julianG2609/Wahlwetter)"


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """Tolerances for the validation rules.

    Defaults were chosen against the full dawum snapshot of 2026-09-21
    (see docs/data_source.md): observed share sums span 98..102, with 3897 of
    3956 surveys at exactly 100. A +/-2 error band therefore accepts everything
    currently published, while +/-1 flags the ~59 surveys that round unusually
    so that a future drift becomes visible without failing the run.
    """

    # Absolute deviation from 100 at which a survey is quarantined.
    share_sum_error_tolerance: float = 2.0
    # Absolute deviation from 100 at which a survey is flagged but kept.
    share_sum_warning_tolerance: float = 1.0

    # Observed sample sizes run 500..12297. The bounds below are deliberately
    # wider than that: they exist to catch a decimal-point error upstream, not
    # to second-guess a pollster.
    min_plausible_sample_size: int = 100
    max_plausible_sample_size: int = 100_000

    # A fieldwork period longer than this is suspicious. Observed max is 74
    # days, so this only fires on something genuinely new.
    max_fieldwork_span_days: int = 120
    # Gap between end of fieldwork and publication. Observed max is 75 days.
    max_publication_lag_days: int = 180

    # Individual party shares outside this range are impossible.
    min_share: float = 0.0
    max_share: float = 100.0

    # Earliest plausible publication date; dawum's coverage starts in 2017.
    earliest_publication_year: int = 2000


DEFAULT_VALIDATION = ValidationConfig()


def load_parties_by_parliament(
    path: Path | None = None,
) -> dict[str, set[str]]:
    """Known party IDs per parliament.

    This is a *reviewed* config file, not something derived on the fly. A
    genuinely new party must fail validation once and then be added by hand,
    which is the only way a silent upstream party-ID change becomes visible.
    """
    path = path or PARTIES_BY_PARLIAMENT_PATH
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: set(v["party_ids"]) for k, v in raw["parliaments"].items()}
