"""Compare 停牌/涨跌停 across 4 数据源 -- A1 sensor calibration (XAR-480).

对 15 case (5 停牌 + 5 一字涨停 + 5 一字跌停) 跑 4 路:
  1. akshare    -- ak.stock_zh_a_hist + ak.stock_zt_pool_em/dtgc_em + ak.stock_zh_a_stop_em
  2. baostock   -- bs.query_history_k_data_plus (tradestatus, isST 直接字段) + query_all_stock
  3. TDX lake   -- .data/lake/pit/kline_daily_pit.parquet (派生: vol==0 / OHLC==prev_close==limit)
  4. SSE 官网   -- yunhq.sse.com.cn dayk endpoint (csv-like, 派生)

输出: docs/suspension-source-comparison.md + .data/a1-suspension-comparison.csv (60 行)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import traceback
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_JSON_DEFAULT = REPO_ROOT / ".tmp-port" / "a1-cases-final.json"
LAKE_KLINE = REPO_ROOT / ".data" / "lake" / "pit" / "kline_daily_pit.parquet"
OUT_CSV = REPO_ROOT / ".data" / "a1-suspension-comparison.csv"
OUT_JSON = REPO_ROOT / ".data" / "a1-suspension-comparison.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 涨跌停板块 limit_pct (主板 10%, ST 5%, 创业板 / 科创板 20%, 北交所 30%)
def board_limit_pct(symbol: str, is_st: bool) -> float:
    sym = symbol.replace("sh.", "").replace("sz.", "").replace("bj.", "")
    if is_st:
        return 0.05
    if sym.startswith(("300", "688")):
        return 0.20
    if sym.startswith(("4", "8")):
        return 0.30
    return 0.10


def sym_to_bs(code: str) -> str:
    """600519 -> sh.600519"""
    if code.startswith(("sh.", "sz.", "bj.")):
        return code
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    if code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sz.{code}"


def with_retry(fn, n: int = 3, backoff: list[float] | None = None, label: str = ""):
    backoff = backoff or [1.0, 3.0, 7.0]
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < n - 1:
                time.sleep(backoff[i])
    raise last


# ---------- 4 sources ----------

def query_akshare(case: dict[str, Any]) -> dict[str, Any]:
    """akshare: 历史 OHLC 派生 (无 tradestatus 字段). 当日停牌名单 (stop_em) 不接受日期参数."""
    import akshare as ak
    t0 = time.time()
    out: dict[str, Any] = {"source": "akshare"}
    code = case["symbol"]
    iso = case["date"]
    d_compact = iso.replace("-", "")
    try:
        df = with_retry(lambda: ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=d_compact, end_date=d_compact, adjust=""),
            n=3, label="ak_hist")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    if df is None or len(df) == 0:
        out["no_row"] = True
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    r = df.iloc[0].to_dict()
    o = float(r["开盘"]); h = float(r["最高"]); l = float(r["最低"]); c = float(r["收盘"])
    vol = int(r["成交量"]); pct = float(r["涨跌幅"])
    is_yizi = (o == h == l == c) and o > 0
    suspended = (vol == 0)
    out.update({
        "open": o, "high": h, "low": l, "close": c, "volume": vol, "pct": pct,
        "yizi": is_yizi, "vol_zero": suspended,
        "latency_s": round(time.time() - t0, 3),
    })
    return out


def query_baostock(case: dict[str, Any], bs_session) -> dict[str, Any]:
    """baostock: 直接 tradestatus 字段 + OHLC. T+1 (daily granularity)."""
    t0 = time.time()
    out: dict[str, Any] = {"source": "baostock"}
    code_bs = sym_to_bs(case["symbol"])
    iso = case["date"]
    try:
        rs = bs_session.query_history_k_data_plus(
            code_bs,
            "date,code,open,high,low,close,preclose,volume,tradestatus,pctChg,isST",
            start_date=iso, end_date=iso, frequency="d", adjustflag="3",
        )
        if rs.error_code != "0":
            out["error"] = f"bs error {rs.error_code}: {rs.error_msg}"
            out["latency_s"] = round(time.time() - t0, 3)
            return out
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            out["no_row"] = True
            out["latency_s"] = round(time.time() - t0, 3)
            return out
        rec = dict(zip(rs.fields, rows[0]))
        # tradestatus 优先 (停牌时 OHLC 字段可能空)
        out["tradestatus"] = rec.get("tradestatus")
        out["isST"] = rec.get("isST") == "1"
        out["raw_rec"] = rec
        def _f(k):
            v = rec.get(k)
            if v in (None, "", "nan"):
                return None
            try:
                return float(v)
            except Exception:
                return None
        def _i(k):
            v = rec.get(k)
            if v in (None, "", "nan"):
                return None
            try:
                return int(float(v))
            except Exception:
                return None
        o = _f("open"); h = _f("high"); l = _f("low"); c = _f("close")
        vol = _i("volume"); pct = _f("pctChg")
        out.update({
            "open": o, "high": h, "low": l, "close": c,
            "volume": vol, "pct": pct,
            "yizi": (o is not None and o == h == l == c and o > 0),
            "vol_zero": (vol == 0),
            "latency_s": round(time.time() - t0, 3),
        })
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["latency_s"] = round(time.time() - t0, 3)
    return out


def query_tdx_lake(case: dict[str, Any], lake_df_lazy) -> dict[str, Any]:
    """TDX lake: 派生 (kline_daily_pit, 无直接停牌/涨跌停字段).
    Spec 提到的 tradability_status_pit.parquet 不存在 (producer未跑且 producer 头部声明
    suspension/limit fields = False low-fidelity). 此处用 kline_daily_pit 派生.
    """
    import polars as pl
    t0 = time.time()
    out: dict[str, Any] = {"source": "tdx-lake"}
    code = case["symbol"]
    iso = case["date"]
    sym_tdx = f"{code}.SH" if code.startswith(("6", "9")) else (
        f"{code}.SZ" if code.startswith(("0", "3")) else f"{code}.BJ")
    try:
        df = lake_df_lazy.filter(
            (pl.col("symbol") == sym_tdx)
            & (pl.col("event_time").dt.strftime("%Y-%m-%d") == iso)
        ).collect()
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    if df.height == 0:
        # 备用 symbol 形式 (000001 没后缀)
        try:
            df = lake_df_lazy.filter(
                (pl.col("symbol") == code)
                & (pl.col("event_time").dt.strftime("%Y-%m-%d") == iso)
            ).collect()
        except Exception:
            pass
    if df.height == 0:
        # lake 没数据 != 停牌. 检查 lake 是否覆盖该日期段.
        try:
            max_date_df = lake_df_lazy.select(pl.col("event_time").max().alias("m")).collect()
            lake_max = max_date_df.row(0, named=True)["m"]
            if lake_max is not None:
                lake_max_str = lake_max.strftime("%Y-%m-%d")
                target_dt = datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=UTC)
                if lake_max < target_dt:
                    out["na_reason"] = f"lake max={lake_max_str}, target {iso} 超出 lake 范围"
                    out["latency_s"] = round(time.time() - t0, 3)
                    return out
        except Exception:
            pass
        out["no_row"] = True
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    r = df.row(0, named=True)
    o = float(r["open"]); h = float(r["high"]); l = float(r["low"]); c = float(r["close"])
    vol = int(r["volume"])
    out.update({
        "open": o, "high": h, "low": l, "close": c, "volume": vol,
        "yizi": (o == h == l == c) and o > 0,
        "vol_zero": vol == 0,
        "tdx_symbol_resolved": r["symbol"],
        "latency_s": round(time.time() - t0, 3),
    })
    return out


def query_sse_official(case: dict[str, Any]) -> dict[str, Any]:
    """SSE yunhq dayk endpoint -- 只覆盖 sh.* (上交所).
    深交所 / 北交所 SSE 不提供, 标 N/A.
    yunhq 返回最近 N 天 [open, high, low, close, volume, suspend_flag?] 数组."""
    import requests
    t0 = time.time()
    out: dict[str, Any] = {"source": "sse-official"}
    code = case["symbol"]
    iso = case["date"]
    if not code.startswith(("6", "9")):
        out["na_reason"] = "SSE 不覆盖非上交所 symbol"
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    url = f"https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{code}"
    headers = {"User-Agent": USER_AGENT, "Referer": "https://www.sse.com.cn/"}
    # yunhq dayk: prev_close 字段总是 null; 改为拉 5 个交易日窗口, 取目标日 + 前一日 close 算 pct
    target_int = int(iso.replace("-", ""))
    # 从 target-15 拉到 target+1, 拿到目标日及前一交易日
    from datetime import datetime as _dt, timedelta as _td
    end_dt = _dt.strptime(iso, "%Y-%m-%d")
    start_dt = end_dt - _td(days=15)
    params = {"begin": start_dt.strftime("%Y%m%d"),
              "end": end_dt.strftime("%Y%m%d"),
              "select": "date,open,high,low,close,volume"}
    try:
        r = with_retry(
            lambda: requests.get(url, params=params, headers=headers, timeout=15),
            n=3, label="sse_dayk")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    if r.status_code != 200:
        out["error"] = f"HTTP {r.status_code}"
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    try:
        data = r.json()
    except Exception as e:
        out["error"] = f"json parse: {e}"; out["body_head"] = r.text[:300]
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    klines = data.get("kline", []) or []
    if not klines:
        out["no_row"] = True
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    # 字段顺序: date, open, high, low, close, volume
    target_row = None
    prev_row = None
    for i, row in enumerate(klines):
        if int(row[0]) == target_int:
            target_row = row
            if i > 0:
                prev_row = klines[i - 1]
            break
    if target_row is None:
        out["no_row"] = True
        out["window_dates"] = [r[0] for r in klines]
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    try:
        d_raw, o, h, l, c, vol = (target_row[0], float(target_row[1]),
                                   float(target_row[2]), float(target_row[3]),
                                   float(target_row[4]), int(target_row[5]))
    except Exception as e:
        out["error"] = f"row parse {e} row={target_row}"
        out["latency_s"] = round(time.time() - t0, 3)
        return out
    prev = float(prev_row[4]) if prev_row else None
    pct = ((c - prev) / prev * 100) if (prev and prev > 0) else None
    out.update({
        "yunhq_date": d_raw,
        "open": o, "high": h, "low": l, "close": c,
        "prev_close": prev, "volume": vol,
        "pct": round(pct, 4) if pct is not None else None,
        "yizi": (o == h == l == c) and o > 0,
        "vol_zero": vol == 0,
        "latency_s": round(time.time() - t0, 3),
    })
    return out


# ---------- predicate ----------

def derive_state(src_row: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """从原始 source 行派生 (suspended, limit_up, limit_down) 三态.

    Predicates:
      suspended = (no_row) OR (tradestatus==0) OR (volume==0)
      limit_up  = yizi AND pct ≈ +board_limit_pct (容差 1%)
      limit_down= yizi AND pct ≈ -board_limit_pct
    """
    src = src_row.get("source")
    if src_row.get("error"):
        return {"err": src_row["error"], "state": "ERROR"}
    if src_row.get("na_reason"):
        return {"na": src_row["na_reason"], "state": "N/A"}

    # baostock 直接信号
    if src == "baostock":
        if src_row.get("tradestatus") == "0":
            return {"state": "suspended", "via": "tradestatus=0"}
        if src_row.get("no_row"):
            return {"state": "suspended", "via": "no_row (baostock)"}

    # 通用派生
    if src_row.get("no_row") and src != "baostock":
        # 非 baostock 的 no_row: 当成 suspended (akshare 历史接口在停牌日通常无返回; SSE 也是)
        return {"state": "suspended", "via": "no_row"}
    if src_row.get("vol_zero"):
        return {"state": "suspended", "via": "volume=0"}

    pct = src_row.get("pct")
    yizi = src_row.get("yizi", False)
    is_st = src_row.get("isST", False) or case.get("name", "").upper().startswith(("ST", "*ST"))
    limit_pct = board_limit_pct(case["symbol"], is_st) * 100
    if pct is None:
        return {"state": "trading?", "via": "no pct"}

    if yizi and abs(pct - limit_pct) < 1.0:
        return {"state": "limit_up", "via": f"yizi & pct≈+{limit_pct}"}
    if yizi and abs(pct + limit_pct) < 1.0:
        return {"state": "limit_down", "via": f"yizi & pct≈-{limit_pct}"}
    return {"state": "trading", "via": f"o={src_row.get('open')} pct={pct}"}


# ---------- main ----------

def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for kind_key, expected in [
        ("cases_suspended", "suspended"),
        ("cases_yizi_up", "limit_up"),
        ("cases_yizi_down", "limit_down"),
    ]:
        for c in data.get(kind_key, []):
            cases.append({
                "symbol": c["symbol"],
                "symbol_bs": c.get("symbol_bs", sym_to_bs(c["symbol"])),
                "date": c["date"],
                "name": c["name"],
                "expected": expected,
            })
    return cases


def run(cases: list[dict[str, Any]], dry_run: bool = False) -> dict[str, Any]:
    import baostock as bs
    import polars as pl

    bs.login()
    if LAKE_KLINE.exists():
        lake_lazy = pl.scan_parquet(LAKE_KLINE)
    else:
        lake_lazy = None

    rows = []
    summary = {"akshare": {"ok": 0, "err": 0, "lat_sum": 0.0},
               "baostock": {"ok": 0, "err": 0, "lat_sum": 0.0},
               "tdx-lake": {"ok": 0, "err": 0, "lat_sum": 0.0, "no_data": 0},
               "sse-official": {"ok": 0, "err": 0, "lat_sum": 0.0, "na": 0}}

    try:
        for i, case in enumerate(cases):
            print(f"[{i+1}/{len(cases)}] {case['symbol']} {case['date']} expected={case['expected']} ({case['name']})", flush=True)

            ak_r = query_akshare(case)
            bs_r = query_baostock(case, bs)
            if lake_lazy is not None:
                tdx_r = query_tdx_lake(case, lake_lazy)
            else:
                tdx_r = {"source": "tdx-lake", "na_reason": "kline_daily_pit.parquet missing"}
            sse_r = query_sse_official(case)

            for r in (ak_r, bs_r, tdx_r, sse_r):
                bucket = summary[r["source"]]
                bucket["lat_sum"] += r.get("latency_s", 0.0)
                if r.get("error"):
                    bucket["err"] += 1
                elif r.get("na_reason"):
                    bucket.setdefault("na", 0)
                    bucket["na"] += 1
                elif r.get("no_row") and r["source"] in ("tdx-lake",):
                    bucket["no_data"] += 1
                else:
                    bucket["ok"] += 1

            derived = {
                "akshare": derive_state(ak_r, case),
                "baostock": derive_state(bs_r, case),
                "tdx-lake": derive_state(tdx_r, case),
                "sse-official": derive_state(sse_r, case),
            }
            rows.append({
                "case": case, "raw": {"akshare": ak_r, "baostock": bs_r,
                                       "tdx-lake": tdx_r, "sse-official": sse_r},
                "derived": derived,
            })

            if dry_run and i >= 0:  # 1-case dry-run
                print("  [dry-run] stopping after 1 case", flush=True)
                break
            time.sleep(0.3)
    finally:
        bs.logout()

    return {"rows": rows, "summary": summary}


def write_csv(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "name", "date", "expected", "source",
                    "derived_state", "derive_via", "open", "high", "low",
                    "close", "volume", "pct", "extra"])
        for row in result["rows"]:
            case = row["case"]
            for src, d in row["derived"].items():
                raw = row["raw"][src]
                extra_bits = []
                if raw.get("tradestatus"):
                    extra_bits.append(f"tradestatus={raw['tradestatus']}")
                if raw.get("isST"):
                    extra_bits.append("isST")
                if raw.get("error"):
                    extra_bits.append(f"err={raw['error'][:60]}")
                if raw.get("na_reason"):
                    extra_bits.append(f"na={raw['na_reason']}")
                if raw.get("no_row"):
                    extra_bits.append("no_row")
                w.writerow([
                    case["symbol"], case["name"], case["date"], case["expected"],
                    src, d.get("state"), d.get("via", ""),
                    raw.get("open"), raw.get("high"), raw.get("low"),
                    raw.get("close"), raw.get("volume"), raw.get("pct"),
                    "; ".join(extra_bits),
                ])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cases-json", default=str(CASES_JSON_DEFAULT))
    p.add_argument("--dry-run", action="store_true", help="只跑第 1 case")
    p.add_argument("--out-csv", default=str(OUT_CSV))
    p.add_argument("--out-json", default=str(OUT_JSON))
    args = p.parse_args(argv)

    cases_path = Path(args.cases_json)
    if not cases_path.exists():
        print(f"FATAL: {cases_path} missing. 跑 .tmp-port/a1-pick-cases.py 先生成 case 集.", file=sys.stderr)
        return 2

    cases = load_cases(cases_path)
    if args.dry_run:
        cases = cases[:1]
    print(f"loaded {len(cases)} cases", flush=True)

    result = run(cases, dry_run=False)
    out_csv = Path(args.out_csv); out_json = Path(args.out_json)
    write_csv(result, out_csv)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(f"-> {out_csv}", flush=True)
    print(f"-> {out_json}", flush=True)

    # 一致率 summary
    n_total = len(result["rows"])
    if n_total:
        agree_ct = 0
        for r in result["rows"]:
            states = {s: r["derived"][s].get("state") for s in
                      ("akshare", "baostock", "tdx-lake", "sse-official")}
            non_na = [v for v in states.values() if v not in ("N/A", "ERROR", None)]
            if len(set(non_na)) == 1 and len(non_na) >= 2:
                agree_ct += 1
        print(f"agreement: {agree_ct}/{n_total} = {agree_ct/n_total*100:.1f}% (排除 N/A & ERROR)", flush=True)

    avg_lat = {s: round(b["lat_sum"] / max(n_total, 1), 3)
               for s, b in result["summary"].items()}
    print(f"avg_latency_s: {avg_lat}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
