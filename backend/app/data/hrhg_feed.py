"""华融汇金 (Host.exe) 行情数据源 — 被动嗅探模式

通过抓取 Host.exe 与 8.223.13.2:8899 之间的 TCP 流量，
解压 zlib 数据并解析固定宽度的行情记录。

协议格式:
- 请求: 74字节固定包，序列号递增
- 响应: 37字节header + zlib压缩数据
- 数据: 8字节header + N条376字节记录
- 记录: code(12B) + reserved(4B) + name_gbk(16B偏移32) + doubles(价格/量/额)
"""

import struct
import subprocess
import tempfile
import threading
import time
import zlib
from pathlib import Path

from loguru import logger


RECORD_SIZE = 376
DATA_HEADER = 8

# Field offsets within a 376-byte record (all doubles unless noted)
FIELDS = {
    "code": (0, 12, "str"),       # char[12] null-terminated ASCII
    "name": (32, 32, "gbk"),      # GBK string at offset 32, ~16 bytes
    "pct_change": (64, 8, "f64"),  # 涨跌幅 %
    "price": (72, 8, "f64"),       # 当前价
    "change": (80, 8, "f64"),      # 涨跌额
    "last": (120, 8, "f64"),       # 最新价 (= price)
    "volume": (136, 8, "f64"),     # 成交量 (手)
    "amount": (144, 8, "f64"),     # 成交额
    "market_cap": (152, 8, "f64"), # 总市值?
    "turnover": (160, 8, "f64"),   # 换手率?
    "pe": (168, 8, "f64"),         # 市盈率?
    "open": (296, 8, "f64"),       # 开盘价
    "high": (304, 8, "f64"),       # 最高价
    "low": (312, 8, "f64"),        # 最低价
    "prev_close": (320, 8, "f64"), # 昨收价
    "vwap": (328, 8, "f64"),       # 均价/VWAP
}


def decode_record(rec: bytes) -> dict | None:
    """Decode a single 376-byte market data record."""
    if len(rec) < RECORD_SIZE:
        return None
    result = {}
    for name, (offset, size, dtype) in FIELDS.items():
        if dtype == "str":
            raw = rec[offset:offset + size]
            result[name] = raw.split(b'\x00')[0].decode('ascii', errors='replace')
        elif dtype == "gbk":
            raw = rec[offset:offset + size]
            end = raw.find(b'\x00\x00')
            if end > 0:
                raw = raw[:end]
            result[name] = raw.decode('gbk', errors='replace').rstrip('\x00').strip()
        elif dtype == "f64":
            result[name] = struct.unpack_from('<d', rec, offset)[0]
    return result


def decode_response_payload(decompressed: bytes) -> list[dict]:
    """Decode decompressed response data into list of quote dicts."""
    if len(decompressed) < DATA_HEADER + RECORD_SIZE:
        return []
    num_records = (len(decompressed) - DATA_HEADER) // RECORD_SIZE
    records = []
    for i in range(num_records):
        start = DATA_HEADER + i * RECORD_SIZE
        rec = decompressed[start:start + RECORD_SIZE]
        parsed = decode_record(rec)
        if parsed and parsed.get("code"):
            records.append(parsed)
    return records


class HRHGFeed:
    """华融汇金行情数据源 — 被动嗅探 Host.exe 网络流量

    工作原理:
    1. 定期用 pktmon 抓包 (2秒窗口)
    2. 提取 8899 端口 TCP payload
    3. 找到 zlib 块并解压
    4. 解析固定宽度记录

    要求: Host.exe 必须正在运行且已登录
    """

    name = "hrhg"

    def __init__(self, capture_seconds: float = 3.0):
        self._quotes: dict[str, dict] = {}
        self._last_update: float = 0
        self._capture_seconds = capture_seconds
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        """Check if Host.exe is running and connected to 8899."""
        try:
            result = subprocess.run(
                ["netstat", "-an"],
                capture_output=True, text=True, timeout=5
            )
            return "8.223.13.2:8899" in result.stdout and "ESTABLISHED" in result.stdout
        except Exception:
            return False

    def _capture_and_decode(self) -> list[dict]:
        """Capture packets and decode market data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            etl_path = Path(tmpdir) / "cap.etl"
            pcap_path = Path(tmpdir) / "cap.pcap"

            # Capture with full packet size
            try:
                subprocess.run(
                    ["pktmon", "start", "--capture", "--comp", "nics",
                     "--pkt-size", "0", "--file-name", str(etl_path)],
                    capture_output=True, timeout=5
                )
                time.sleep(self._capture_seconds)
                subprocess.run(["pktmon", "stop"], capture_output=True, timeout=5)

                # Convert to pcap
                subprocess.run(
                    ["pktmon", "etl2pcap", str(etl_path), "--out", str(pcap_path)],
                    capture_output=True, timeout=10
                )
            except Exception as e:
                logger.error(f"HRHG capture failed: {e}")
                return []

            if not pcap_path.exists():
                return []

            # Parse pcap with dpkt
            try:
                import dpkt
                with open(pcap_path, 'rb') as f:
                    try:
                        pcap = dpkt.pcapng.Reader(f)
                    except Exception:
                        f.seek(0)
                        pcap = dpkt.pcap.Reader(f)

                    segments = {}
                    for ts, buf in pcap:
                        try:
                            eth = dpkt.ethernet.Ethernet(buf)
                            if not isinstance(eth.data, dpkt.ip.IP):
                                continue
                            ip = eth.data
                            if not isinstance(ip.data, dpkt.tcp.TCP):
                                continue
                            tcp = ip.data
                            if tcp.sport == 8899 and len(tcp.data) > 0:
                                if tcp.seq not in segments:
                                    segments[tcp.seq] = tcp.data
                        except Exception:
                            continue

                if not segments:
                    return []

                # Reassemble stream
                stream = bytearray()
                for seq in sorted(segments):
                    stream.extend(segments[seq])

                # Find and decompress first zlib block
                for i in range(len(stream) - 1):
                    if stream[i] == 0x78 and stream[i + 1] == 0x9c:
                        try:
                            d = zlib.decompressobj()
                            result = d.decompress(bytes(stream[i:]))
                            return decode_response_payload(result)
                        except zlib.error:
                            continue

            except ImportError:
                logger.error("dpkt not installed: pip install dpkt")
            except Exception as e:
                logger.error(f"HRHG pcap parse error: {e}")

        return []

    def _refresh(self):
        """Refresh quotes if stale (>2 seconds old)."""
        now = time.time()
        if now - self._last_update < 2.0:
            return
        with self._lock:
            if now - self._last_update < 2.0:
                return
            records = self._capture_and_decode()
            if records:
                for r in records:
                    self._quotes[r["code"]] = r
                self._last_update = time.time()
                logger.debug(f"HRHG refreshed: {len(records)} stocks")

    def get_quotes(self, codes: list[str]) -> list[dict]:
        """Get quotes for specified stock codes."""
        self._refresh()
        result = []
        for code in codes:
            # Strip market prefix if present (e.g., "sh600537" -> "600537")
            clean = code[-6:] if len(code) > 6 else code
            if clean in self._quotes:
                q = self._quotes[clean]
                result.append({
                    "code": q["code"],
                    "name": q["name"],
                    "price": q["price"],
                    "change": q["change"],
                    "pct_change": q["pct_change"],
                    "open": q["open"],
                    "high": q["high"],
                    "low": q["low"],
                    "close": q["price"],
                    "prev_close": q["prev_close"],
                    "volume": q["volume"],
                    "amount": q["amount"],
                })
        return result

    def get_kline(self, code: str, klt: int = 101, count: int = 500) -> list[dict]:
        """HRHG only provides real-time snapshots, no kline history."""
        return []

    def get_all_quotes(self) -> list[dict]:
        """Get all available quotes from the last snapshot."""
        self._refresh()
        return [
            {
                "code": q["code"],
                "name": q["name"],
                "price": q["price"],
                "change": q["change"],
                "pct_change": q["pct_change"],
                "open": q["open"],
                "high": q["high"],
                "low": q["low"],
                "close": q["price"],
                "prev_close": q["prev_close"],
                "volume": q["volume"],
                "amount": q["amount"],
            }
            for q in self._quotes.values()
        ]
