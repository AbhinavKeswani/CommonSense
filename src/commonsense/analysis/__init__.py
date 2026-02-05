"""Financial analysis: common-size and flux from SEC facts Parquet (per-company line item keys)."""

from commonsense.analysis.common_size_flux import (
    run_analysis_for_company,
    run_analysis_all,
)

__all__ = [
    "run_analysis_for_company",
    "run_analysis_all",
]
