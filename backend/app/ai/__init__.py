"""Back-compat shim: app.ai has moved to app.ai_lab (XAR-416)."""
import warnings

warnings.warn(
    "app.ai has moved to app.ai_lab; update imports",
    DeprecationWarning,
    stacklevel=2,
)

from app.ai_lab import *  # noqa: F401,F403,E402
