"""Thin crypto market data adapter for k-atana research and HTTP endpoints."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests
from loguru import logger

from .okx_client import OKXClient


def _split_pair(inst_id: str) -> tuple[str, str]:
    symbol = inst_id.replace("-SWAP", "")
    if "-" in symbol:
        base, quote = symbol.split("-", 1)
    else:
        base, quote = symbol[:-4], symbol[-4:]
    return base.upper(), quote.upper()


def _to_okx_like_bar(bar: dict[str, Any]) -> list[str]:
    return [
        str(int(bar["ts_ms"])),
        f"{bar['open']}",
        f"{bar['high']}",
        f"{bar['low']}",
        f"{bar['close']}",
        f"{bar['volume']}",
    ]


@dataclass
class CryptoMarketDataClient:
    preferred_source: str = "auto"
    fallback_source: str = "cryptocompare"
    timeout: int = 10

    def __post_init__(self) -> None:
        self.okx_client = OKXClient()
        self.session = requests.Session()

    def get_kline(self, inst_id: str, bar: str = "1m", limit: int = 100) -> tuple[list[list[str]], str]:
        for source in self._source_order():
            try:
                if source == "okx":
                    data = self._get_okx_kline(inst_id, bar, limit)
                elif source == "cryptocompare":
                    data = self._get_cryptocompare_kline(inst_id, bar, limit)
                else:
                    continue
                if data:
                    return data, source
            except Exception as exc:
                logger.warning(f"market data source {source} failed for {inst_id} {bar}: {exc}")
        return [], "unavailable"

    def get_ticker(self, inst_id: str) -> tuple[dict[str, Any] | None, str]:
        for source in self._source_order():
            try:
                if source == "okx":
                    data = self._get_okx_ticker(inst_id)
                elif source == "cryptocompare":
                    data = self._get_cryptocompare_ticker(inst_id)
                else:
                    continue
                if data:
                    return data, source
            except Exception as exc:
                logger.warning(f"ticker source {source} failed for {inst_id}: {exc}")
        return None, "unavailable"

    def get_tickers(self, inst_ids: list[str]) -> tuple[list[dict[str, Any]], str]:
        if not inst_ids:
            return [], "unavailable"

        for source in self._source_order():
            try:
                if source == "okx":
                    data = self._get_okx_tickers(inst_ids)
                elif source == "cryptocompare":
                    data = self._get_cryptocompare_tickers(inst_ids)
                else:
                    continue
                if data:
                    return data, source
            except Exception as exc:
                logger.warning(f"tickers source {source} failed: {exc}")
        return [], "unavailable"

    def _source_order(self) -> list[str]:
        preferred = (self.preferred_source or "auto").lower()
        fallback = (self.fallback_source or "cryptocompare").lower()
        if preferred == "auto":
            ordered = ["okx", fallback]
        else:
            ordered = [preferred, fallback]
        result = []
        for source in ordered:
            if source and source not in result:
                result.append(source)
        return result

    def _request_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _get_okx_kline(self, inst_id: str, bar: str, limit: int) -> list[list[str]]:
        data = self.okx_client.get_kline(inst_id if inst_id.endswith("-SWAP") else inst_id, bar=bar, limit=limit)
        return data or []

    def _get_okx_ticker(self, inst_id: str) -> dict[str, Any] | None:
        okx_id = inst_id if inst_id.endswith("-SWAP") else f"{inst_id}-SWAP"
        ticker = self.okx_client.get_ticker(okx_id)
        if not ticker:
            return None
        last = float(ticker.get("last", 0))
        open_24h = float(ticker.get("sodUtc8", 0)) or last
        change = ((last - open_24h) / open_24h * 100.0) if open_24h else 0.0
        return {
            "instId": okx_id,
            "pair": okx_id.replace("-SWAP", ""),
            "last": last,
            "askPx": float(ticker.get("askPx", last)),
            "bidPx": float(ticker.get("bidPx", last)),
            "high24h": float(ticker.get("high24h", last)),
            "low24h": float(ticker.get("low24h", last)),
            "vol24h": float(ticker.get("volCcy24h", 0)),
            "change": round(change, 2),
        }

    def _get_okx_tickers(self, inst_ids: list[str]) -> list[dict[str, Any]]:
        requested = {f"{inst_id}-SWAP" if not inst_id.endswith("-SWAP") else inst_id for inst_id in inst_ids}
        rows: list[dict[str, Any]] = []
        for ticker in self.okx_client.get_tickers("SWAP"):
            if ticker.get("instId") not in requested:
                continue
            last = float(ticker.get("last", 0))
            open_24h = float(ticker.get("sodUtc8", 0)) or last
            change = ((last - open_24h) / open_24h * 100.0) if open_24h else 0.0
            rows.append({
                "instId": ticker["instId"],
                "pair": ticker["instId"].replace("-SWAP", ""),
                "last": last,
                "askPx": float(ticker.get("askPx", last)),
                "bidPx": float(ticker.get("bidPx", last)),
                "high24h": float(ticker.get("high24h", last)),
                "low24h": float(ticker.get("low24h", last)),
                "vol24h": float(ticker.get("volCcy24h", 0)),
                "change": round(change, 2),
            })
        rows.sort(key=lambda item: abs(item["change"]), reverse=True)
        return rows

    def _get_cryptocompare_ticker(self, inst_id: str) -> dict[str, Any] | None:
        rows = self._get_cryptocompare_tickers([inst_id])
        return rows[0] if rows else None

    def _get_cryptocompare_tickers(self, inst_ids: list[str]) -> list[dict[str, Any]]:
        bases = []
        for inst_id in inst_ids:
            base, _quote = _split_pair(inst_id)
            bases.append(base)
        unique_bases = sorted(set(bases))
        payload = self._request_json(
            "https://min-api.cryptocompare.com/data/pricemultifull",
            {"fsyms": ",".join(unique_bases), "tsyms": "USD"},
        )
        raw = payload.get("RAW", {})
        rows: list[dict[str, Any]] = []
        for inst_id in inst_ids:
            base, _quote = _split_pair(inst_id)
            item = raw.get(base, {}).get("USD")
            if not item:
                continue
            last = float(item.get("PRICE", 0))
            rows.append({
                "instId": inst_id if inst_id.endswith("-SWAP") else f"{base}-USDT-SWAP",
                "pair": f"{base}-USDT",
                "last": last,
                "askPx": last,
                "bidPx": last,
                "high24h": float(item.get("HIGH24HOUR", last)),
                "low24h": float(item.get("LOW24HOUR", last)),
                "vol24h": float(item.get("VOLUME24HOURTO", 0)),
                "change": round(float(item.get("CHANGEPCT24HOUR", 0)), 2),
            })
        rows.sort(key=lambda item: abs(item["change"]), reverse=True)
        return rows

    def _get_cryptocompare_kline(self, inst_id: str, bar: str, limit: int) -> list[list[str]]:
        base, quote = _split_pair(inst_id)
        endpoint, aggregate = self._bar_to_cryptocompare(bar)
        payload = self._request_json(
            f"https://min-api.cryptocompare.com/data/v2/{endpoint}",
            {
                "fsym": base,
                "tsym": "USD" if quote == "USDT" else quote,
                "limit": max(1, min(limit, 2000)),
                "aggregate": aggregate,
            },
        )
        if payload.get("Response") != "Success":
            raise RuntimeError(payload.get("Message", "cryptocompare error"))
        data = payload.get("Data", {}).get("Data", [])
        bars = []
        for item in data:
            bars.append(_to_okx_like_bar({
                "ts_ms": int(item["time"]) * 1000,
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item.get("volumefrom", 0)),
            }))
        return bars

    def _bar_to_cryptocompare(self, bar: str) -> tuple[str, int]:
        normalized = bar.upper()
        mapping = {
            "1M": ("histominute", 1),
            "3M": ("histominute", 3),
            "5M": ("histominute", 5),
            "15M": ("histominute", 15),
            "30M": ("histominute", 30),
            "1H": ("histohour", 1),
            "2H": ("histohour", 2),
            "4H": ("histohour", 4),
            "6H": ("histohour", 6),
            "12H": ("histohour", 12),
            "1D": ("histoday", 1),
            "1W": ("histoday", 7),
        }
        if normalized not in mapping:
            logger.warning(f"unsupported bar {bar}, fallback to 1H")
            return "histohour", 1
        return mapping[normalized]


def get_market_data_client() -> CryptoMarketDataClient:
    return CryptoMarketDataClient(
        preferred_source=os.getenv("CRYPTO_MARKET_DATA_SOURCE", "auto"),
        fallback_source=os.getenv("CRYPTO_MARKET_FALLBACK_SOURCE", "cryptocompare"),
        timeout=int(os.getenv("CRYPTO_MARKET_TIMEOUT_SECS", "10")),
    )
