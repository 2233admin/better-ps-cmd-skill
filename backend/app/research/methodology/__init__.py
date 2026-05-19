"""Statistical methodology layer: DSR, PBO, multiple-testing correction.

Ship gate alpha filter for k-atana W1 (6/15 deadline).
See dsr_pbo.py for full implementation and references.
"""

from .dsr_pbo import (
    benjamini_hochberg,
    cscv_pbo,
    deflated_sharpe_ratio,
    dsr_filter,
)
from .regime import expanding_fit_hmm

__all__ = [
    "deflated_sharpe_ratio",
    "cscv_pbo",
    "benjamini_hochberg",
    "dsr_filter",
    "expanding_fit_hmm",
]
