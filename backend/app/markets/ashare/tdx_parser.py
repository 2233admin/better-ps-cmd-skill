"""TDX 本地数据解析器 - 解析通达信本地缓存文件"""

import struct
from pathlib import Path

from loguru import logger


TDX_ROOT = Path("C:/zd_zsone")


def parse_base_dbf(path: Path | None = None) -> list[dict]:
    """解析 base.dbf 获取全市场代码表

    DBF 文件格式:
    - Header: 32 bytes + field descriptors
    - Records: fixed-length records
    """
    if path is None:
        path = TDX_ROOT / "T0002" / "hq_cache" / "base.dbf"

    if not path.exists():
        logger.warning(f"base.dbf not found: {path}")
        return []

    stocks = []
    with open(path, "rb") as f:
        # DBF Header
        version = struct.unpack("B", f.read(1))[0]
        f.read(3)  # last update date
        num_records = struct.unpack("<I", f.read(4))[0]
        header_size = struct.unpack("<H", f.read(2))[0]
        record_size = struct.unpack("<H", f.read(2))[0]
        f.read(20)  # reserved

        # Field descriptors
        num_fields = (header_size - 33) // 32
        fields = []
        for _ in range(num_fields):
            field_data = f.read(32)
            name = field_data[:11].split(b"\x00")[0].decode("ascii", errors="ignore")
            field_type = chr(field_data[11])
            field_len = field_data[16]
            fields.append({"name": name, "type": field_type, "length": field_len})

        f.read(1)  # header terminator

        # Records
        for _ in range(num_records):
            record = f.read(record_size)
            if record[0:1] == b"*":  # deleted record
                continue

            row = {}
            offset = 1  # skip deletion flag
            for field in fields:
                value = record[offset : offset + field["length"]]
                try:
                    value = value.decode("gbk").strip()
                except Exception:
                    value = value.decode("ascii", errors="ignore").strip()
                row[field["name"]] = value
                offset += field["length"]
            stocks.append(row)

    logger.info(f"Parsed {len(stocks)} records from base.dbf")
    return stocks


def parse_day_file(filepath: Path) -> list[dict]:
    """解析 .day 日线文件 (32 bytes per record)

    格式: date(4) open(4) high(4) low(4) close(4) amount(4) vol(4) reserved(4)
    """
    if not filepath.exists():
        return []

    records = []
    with open(filepath, "rb") as f:
        data = f.read()

    record_size = 32
    for i in range(0, len(data), record_size):
        if i + record_size > len(data):
            break
        rec = data[i : i + record_size]
        date_int = struct.unpack("<I", rec[0:4])[0]
        year = date_int // 10000
        month = (date_int % 10000) // 100
        day = date_int % 100

        records.append({
            "date": f"{year:04d}-{month:02d}-{day:02d}",
            "open": struct.unpack("<I", rec[4:8])[0] / 100,
            "high": struct.unpack("<I", rec[8:12])[0] / 100,
            "low": struct.unpack("<I", rec[12:16])[0] / 100,
            "close": struct.unpack("<I", rec[16:20])[0] / 100,
            "amount": struct.unpack("<f", rec[20:24])[0],
            "volume": struct.unpack("<I", rec[24:28])[0],
        })

    return records


def parse_minute_file(filepath: Path) -> list[dict]:
    """解析 .lc1/.lc5 分钟线文件 (32 bytes per record)

    格式: date(2)+time(2) open(4) high(4) low(4) close(4) amount(4) vol(4) reserved(4)
    """
    if not filepath.exists():
        return []

    records = []
    with open(filepath, "rb") as f:
        data = f.read()

    record_size = 32
    for i in range(0, len(data), record_size):
        if i + record_size > len(data):
            break
        rec = data[i : i + record_size]

        date_raw = struct.unpack("<H", rec[0:2])[0]
        time_raw = struct.unpack("<H", rec[2:4])[0]

        year = (date_raw >> 11) + 2004
        month = (date_raw >> 7) & 0x0F
        day = date_raw & 0x1F
        hour = time_raw // 60
        minute = time_raw % 60

        records.append({
            "datetime": f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}",
            "open": struct.unpack("<f", rec[4:8])[0],
            "high": struct.unpack("<f", rec[8:12])[0],
            "low": struct.unpack("<f", rec[12:16])[0],
            "close": struct.unpack("<f", rec[16:20])[0],
            "amount": struct.unpack("<f", rec[20:24])[0],
            "volume": struct.unpack("<I", rec[24:28])[0],
        })

    return records


def parse_gbbq(path: Path | None = None) -> list[dict]:
    """解析除权除息数据 gbbq 文件"""
    if path is None:
        path = TDX_ROOT / "T0002" / "hq_cache" / "gbbq"

    if not path.exists():
        logger.warning(f"gbbq not found: {path}")
        return []

    records = []
    with open(path, "rb") as f:
        # Header: first 4 bytes = record count
        count = struct.unpack("<I", f.read(4))[0]

        for _ in range(count):
            rec = f.read(32)
            if len(rec) < 32:
                break

            # market(1) code(6) ... date(4) category(1) ...
            market = rec[0]
            code = rec[1:7].decode("ascii", errors="ignore").strip("\x00")
            date_int = struct.unpack("<I", rec[8:12])[0]
            category = rec[12]

            # 除权因子等 (浮点数)
            fenhong = struct.unpack("<f", rec[16:20])[0]
            peigujia = struct.unpack("<f", rec[20:24])[0]
            songzhuangu = struct.unpack("<f", rec[24:28])[0]
            peigu = struct.unpack("<f", rec[28:32])[0]

            records.append({
                "market": market,
                "code": code,
                "date": date_int,
                "category": category,
                "fenhong": fenhong,
                "peigujia": peigujia,
                "songzhuangu": songzhuangu,
                "peigu": peigu,
            })

    logger.info(f"Parsed {len(records)} gbbq records")
    return records


def get_connect_servers(path: Path | None = None) -> list[dict]:
    """从 connect.cfg 解析行情服务器列表"""
    if path is None:
        path = TDX_ROOT / "connect.cfg"

    if not path.exists():
        return []

    servers = []
    with open(path, "r", encoding="gbk", errors="ignore") as f:
        lines = f.readlines()

    in_hq = False
    host_num = 0
    for line in lines:
        line = line.strip()
        if line == "[HQHOST]":
            in_hq = True
            continue
        if line.startswith("[") and in_hq:
            break
        if not in_hq:
            continue

        if line.startswith("HostNum="):
            host_num = int(line.split("=")[1])
        elif line.startswith("IPAddress"):
            idx = line.split("=")[0].replace("IPAddress", "")
            ip = line.split("=")[1]
            servers.append({"ip": ip, "idx": idx})
        elif line.startswith("Port"):
            idx = line.split("=")[0].replace("Port", "")
            port = int(line.split("=")[1])
            for s in servers:
                if s["idx"] == idx:
                    s["port"] = port

    result = [{"host": s["ip"], "port": s.get("port", 7709)} for s in servers]
    logger.info(f"Found {len(result)} HQ servers from connect.cfg")
    return result
