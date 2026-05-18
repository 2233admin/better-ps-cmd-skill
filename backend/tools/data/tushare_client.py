from __future__ import annotations

import os
import time
import threading
from typing import Any

import chinadata.ca_data as ts
import pandas as pd


DEFAULT_TOKEN = "i9f28c195ac5448c044b5dbd8ad3819f795"
_THREAD_LOCAL = threading.local()


def resolve_token() -> str:
    token = (
        os.getenv("CHINADATA_TOKEN")
        or os.getenv("TUSHARE_TOKEN")
        or DEFAULT_TOKEN
    )
    return token.strip()


def get_tushare_pro(token: str | None = None):
    resolved = (token or resolve_token()).strip()
    if not resolved:
        raise RuntimeError("missing CHINADATA_TOKEN/TUSHARE_TOKEN")
    cached_token = getattr(_THREAD_LOCAL, "token", None)
    cached_client = getattr(_THREAD_LOCAL, "client", None)
    if cached_token == resolved and cached_client is not None:
        return cached_client
    ts.set_token(resolved)
    client = ts.pro_api(resolved)
    _THREAD_LOCAL.token = resolved
    _THREAD_LOCAL.client = client
    return client


def fetch_tushare_dataframe(
    api_name: str,
    *,
    params: dict[str, Any] | None = None,
    fields: str = "",
    retries: int = 5,
    token: str | None = None,
) -> pd.DataFrame:
    pro = get_tushare_pro(token)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            frame = pro.query(api_name, fields=fields, **(params or {}))
            if frame is None:
                return pd.DataFrame()
            if isinstance(frame, pd.DataFrame):
                return frame
            if isinstance(frame, dict):
                if not frame:
                    return pd.DataFrame()
                if all(not isinstance(value, (list, tuple, dict, pd.Series, pd.DataFrame)) for value in frame.values()):
                    return pd.DataFrame([frame])
                return pd.DataFrame(frame)
            if isinstance(frame, list):
                return pd.DataFrame(frame)
            return pd.DataFrame([frame])
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(3)
    if last_error is not None:
        raise last_error
    return pd.DataFrame()
