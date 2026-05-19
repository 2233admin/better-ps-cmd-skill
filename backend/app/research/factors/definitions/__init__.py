"""Factor definitions and family metadata."""

from .families import (
    FAMILIES,
    MOMENTUM_RESID_VOL_SPEC,
    QUALITY_ROE_SPEC,
    VALUE_PB_SPEC,
    FamilySpec,
    momentum_resid_volatility,
    quality_factor_roe,
    value_factor_pb,
)

__all__ = [
    "FamilySpec",
    "VALUE_PB_SPEC",
    "QUALITY_ROE_SPEC",
    "MOMENTUM_RESID_VOL_SPEC",
    "FAMILIES",
    "value_factor_pb",
    "quality_factor_roe",
    "momentum_resid_volatility",
]
