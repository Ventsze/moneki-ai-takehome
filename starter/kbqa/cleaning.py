"""把原始 sales 导进 var/clean.db，指标都查这张表。

清洗规则以 KB-001《指标口径手册 v3》为准：
§2 规范化（编号大小写/空白、三种日期格式、¥ 前缀、qty 整数），
§3 六类剔除按顺序执行，§4 退款行按负金额、日期归属退款行自己。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

#: 金额里的 `¥` 去掉再按数字解析。
_CURRENCY = str.maketrans("", "", "¥￥ \t　")

#: KB-001 §2.2 的三种日期格式。DD-MM-YYYY 是旧 POS 导出，日在前、月在后。
_DATE_PATTERNS = (
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), (1, 2, 3)),
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$"), (1, 2, 3)),
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), (3, 2, 1)),
)

REMOVAL_REASONS = (
    "1_unparseable_date",
    "2_empty_amount",
    "3_qty_le_zero",
    "4_store_not_in_stores",
    "5_product_not_in_products",
    "6_duplicate_row",
)


def normalize_date(value: Optional[str]) -> Optional[str]:
    """把三种合法格式统一成 `YYYY-MM-DD`；解析不了返回 None。

    `25-07-2026` 是 2026 年 7 月 25 日（日在前），`2026/5/1` 是 2026 年 5 月 1 日。
    """
    text = (value or "").strip()
    for pattern, (y, m, d) in _DATE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        year, month, day = int(match.group(y)), int(match.group(m)), int(match.group(d))
        if not 1 <= month <= 12 or not 1 <= day <= 31:
            return None
        try:
            _date(year, month, day)
        except ValueError:
            return None
        return "%04d-%02d-%02d" % (year, month, day)
    return None


def normalize_id(value: Optional[str]) -> str:
    """KB-001 §2.1：编号去首尾空白并转大写。"""
    return (value or "").strip().upper()


def parse_amount(value: Optional[str]) -> tuple[Optional[int], str]:
    """返回 (分, 状态)。状态取值：`ok`、`empty`、`bad`。

    KB-001 §2.3 与 §3.2：`¥38.00` 与 `38.00` 是同一个金额；空金额直接剔除，**不回填**。
    """
    text = (value or "").translate(_CURRENCY)
    if not text:
        return None, "empty"
    try:
        cents = int((Decimal(text) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None, "bad"
    return cents, "ok"


def parse_qty(value: Optional[str]) -> Optional[int]:
    """KB-001 §2.4：按整数解析。解析不了的按 0 处理，会被 §3.3 剔除。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class CleaningReport:
    raw_rows: int = 0
    kept_rows: int = 0
    kept_sales_rows: int = 0
    kept_refund_rows: int = 0
    removed: dict[str, int] = field(default_factory=lambda: {k: 0 for k in REMOVAL_REASONS})
    note_unparseable_amount: int = 0

    def as_dict(self) -> dict:
        return {
            "raw_rows": self.raw_rows,
            "removed": dict(self.removed, note_unparseable_amount=self.note_unparseable_amount),
            "kept_rows": self.kept_rows,
            "kept_sales_rows": self.kept_sales_rows,
            "kept_refund_rows": self.kept_refund_rows,
        }


def open_readonly(path: Path) -> sqlite3.Connection:
    """以 SQLite URI 只读模式打开：连接层面保证不可能写入。"""
    from urllib.parse import quote

    uri = "file:%s?mode=ro" % quote(path.as_posix())
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def clean_rows(
    rows: Iterable,
    store_ids: Optional[set[str]] = None,
    product_ids: Optional[set[str]] = None,
) -> tuple[list[tuple], CleaningReport]:
    """按 KB-001 §2/§3 清洗：先规范化，再按顺序剔除六类行，最后去重。

    `store_ids`、`product_ids` 是规范化后的合法编号集合（来自维表），
    判断脏外键之前必须先做 §2.1 的规范化，顺序反了会误删真实订单（§7.2）。
    """
    store_ids = store_ids or set()
    product_ids = product_ids or set()
    report = CleaningReport()
    kept: list[tuple] = []
    seen: set[tuple] = set()
    for row in rows:
        report.raw_rows += 1
        # §3.1 日期无法解析的行
        day = normalize_date(row["date"])
        if day is None:
            report.removed["1_unparseable_date"] += 1
            continue
        # §3.2 amount 为空的行，不回填
        cents, status = parse_amount(row["amount"])
        if status == "empty":
            report.removed["2_empty_amount"] += 1
            continue
        if status == "bad":
            # 手册只规定了空金额；解析不了的金额同样无法参与统计，剔除并单记一笔
            report.note_unparseable_amount += 1
            continue
        # §3.3 qty ≤ 0 的行（解析不了的按 0 处理）
        qty = parse_qty(row["qty"]) or 0
        if qty <= 0:
            report.removed["3_qty_le_zero"] += 1
            continue
        # §2.1 编号规范化后再判脏外键（§3.4/§3.5）
        store_id = normalize_id(row["store_id"])
        if store_id not in store_ids:
            report.removed["4_store_not_in_stores"] += 1
            continue
        product_id = normalize_id(row["product_id"])
        if product_id not in product_ids:
            report.removed["5_product_not_in_products"] += 1
            continue
        record = (
            (row["order_id"] or "").strip(),
            day,
            store_id,
            product_id,
            qty,
            cents,
            (row["payment"] or "").strip(),
            1 if cents < 0 else 0,
        )
        # §3.6 规范化后七字段完全相同的重复行只留一条；
        # 共用订单号但商品不同的多行订单在这里天然不会被当成重复。
        if record in seen:
            report.removed["6_duplicate_row"] += 1
            continue
        seen.add(record)
        kept.append(record)
    report.kept_rows = len(kept)
    report.kept_refund_rows = sum(1 for row in kept if row[-1])
    report.kept_sales_rows = report.kept_rows - report.kept_refund_rows
    return kept, report


_SCHEMA = """
CREATE TABLE stores (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
CREATE TABLE products (product_id TEXT PRIMARY KEY, product_name TEXT,
                       product_category TEXT, unit_price REAL);
CREATE TABLE sales_clean (
    order_id TEXT, date TEXT, store_id TEXT, product_id TEXT,
    qty INTEGER, amount_cents INTEGER, payment TEXT, is_refund INTEGER
);
CREATE INDEX idx_clean_date ON sales_clean(date);
CREATE INDEX idx_clean_store ON sales_clean(store_id);
CREATE INDEX idx_clean_product ON sales_clean(product_id);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def build_clean_db(source: Path, target: Path) -> CleaningReport:
    """从只读的源库重建清洗表。返回清洗台账，供 `/api/health` 与数据质量面板使用。"""
    if not source.exists():
        raise FileNotFoundError("找不到源数据库：%s" % source)
    src = open_readonly(source)
    try:
        stores = [tuple(r) for r in src.execute("SELECT store_id, store_name, category, district FROM stores")]
        products = [
            tuple(r)
            for r in src.execute(
                "SELECT product_id, product_name, product_category, unit_price FROM products"
            )
        ]
        rows, report = clean_rows(
            src.execute("SELECT order_id, date, store_id, product_id, qty, amount, payment FROM sales"),
            store_ids={r[0] for r in src.execute("SELECT store_id FROM stores")},
            product_ids={r[0] for r in src.execute("SELECT product_id FROM products")},
        )
    finally:
        src.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    out = sqlite3.connect(target)
    try:
        out.executescript(_SCHEMA)
        out.executemany("INSERT INTO stores VALUES (?,?,?,?)", stores)
        out.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
        out.executemany("INSERT INTO sales_clean VALUES (?,?,?,?,?,?,?,?)", rows)
        out.execute(
            "INSERT INTO meta VALUES ('cleaning_report', ?)",
            (json.dumps(report.as_dict(), ensure_ascii=False),),
        )
        out.execute("INSERT INTO meta VALUES ('source_db', ?)", (source.name,))
        out.commit()
    finally:
        out.close()
    return report
