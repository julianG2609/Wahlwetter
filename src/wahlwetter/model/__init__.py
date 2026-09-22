"""The Bayesian trend model."""

from wahlwetter.model.data import ModelData, build_model_data
from wahlwetter.model.fit import (
    Diagnostics,
    SamplingConfig,
    cmdstan_available,
    collect_diagnostics,
    compile_model,
    posterior_predictive_check,
    sample,
)
from wahlwetter.model.output import build_output, latest_estimates

__all__ = [
    "Diagnostics",
    "ModelData",
    "SamplingConfig",
    "build_model_data",
    "build_output",
    "cmdstan_available",
    "collect_diagnostics",
    "compile_model",
    "latest_estimates",
    "posterior_predictive_check",
    "sample",
]
