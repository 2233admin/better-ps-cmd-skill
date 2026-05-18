"""Compatibility wrapper for OKX client.

Set KATANA_OKX_ADAPTER=legacy to fall back to the hand-rolled OKXClient.
Default (ccxt) routes to OKXCCXTAdapter with backwards-compat name aliases.

XAR-426: feature flag added 2026-05-18.
"""

import os

if os.environ.get("KATANA_OKX_ADAPTER", "ccxt") == "legacy":
    from app.markets.crypto.okx_client import *  # noqa: F401,F403
else:
    from app.markets.crypto.okx_ccxt_adapter import OKXCCXTAdapter  # noqa: F401
    from app.markets.crypto.okx_ccxt_adapter import OKXCCXTAdapter as OKXClient  # noqa: F401
    from app.markets.crypto.okx_ccxt_adapter import get_okx_ccxt_adapter  # noqa: F401
    from app.markets.crypto.okx_ccxt_adapter import get_okx_ccxt_adapter as get_okx_client  # noqa: F401
