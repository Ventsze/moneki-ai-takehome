"""KB-001《指标口径手册 v3》清洗规则的回归测试。

§2 规范化、§3 剔除（按顺序）、§4 指标定义都以 KB-001 为准；
这些测试用手工构造的脏数据逐条对照手册，跑真实数据前先把规则钉死。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kbqa.cleaning import build_clean_db, clean_rows, normalize_date

STORES = {"S01", "S02"}
PRODUCTS = {"P01", "P02", "P03"}


def _row(**kw) -> dict:
    base = {
        "order_id": "ORD1",
        "date": "2026-06-01",
        "store_id": "S01",
        "product_id": "P01",
        "qty": "1",
        "amount": "10.00",
        "payment": "现金",
    }
    base.update(kw)
    return base


class TestNormalizeDate:
    """KB-001 §2.2：三种日期格式，DD-MM-YYYY 日在前。"""

    @pytest.mark.parametrize(
        "raw,expect",
        [
            ("2026-07-25", "2026-07-25"),
            ("2026-7-5", "2026-07-05"),
            ("2026/5/1", "2026-05-01"),
            ("25-07-2026", "2026-07-25"),
            ("07-06-2026", "2026-06-07"),
            ("13-06-2026", "2026-06-13"),
            (" 2026-07-25 ", "2026-07-25"),
        ],
    )
    def test_parseable(self, raw, expect):
        assert normalize_date(raw) == expect

    @pytest.mark.parametrize("raw", ["", None, "2026-13-01", "31-02-2026", "知道"])
    def test_unparseable(self, raw):
        assert normalize_date(raw) is None


class TestCleanRows:
    def test_kb001_removes_in_order(self):
        """六类剔除各命中一次，台账计数正确。"""
        rows = [
            _row(order_id="R1", date="不是日期"),                     # 1 坏日期
            _row(order_id="R2", amount=""),                          # 2 空金额
            _row(order_id="R3", qty="0"),                            # 3 qty<=0
            _row(order_id="R4", store_id="S99"),                     # 4 脏门店
            _row(order_id="R5", product_id="P99"),                   # 5 脏商品
            _row(order_id="R6"),                                     # 6 重复行（两条完全一致）
            _row(order_id="R6"),
            _row(order_id="R7"),                                     # 保留
        ]
        kept, report = clean_rows(rows, store_ids=STORES, product_ids=PRODUCTS)
        assert report.raw_rows == 8
        assert report.removed == {
            "1_unparseable_date": 1,
            "2_empty_amount": 1,
            "3_qty_le_zero": 1,
            "4_store_not_in_stores": 1,
            "5_product_not_in_products": 1,
            "6_duplicate_row": 1,
        }
        assert report.kept_rows == 2  # R6 首条保留 + R7
        assert [r[0] for r in kept] == ["R6", "R7"]

    def test_kb001_recoverable_values_kept(self):
        """§7：¥ 金额、大小写/空格编号、负金额都是可恢复脏值，不许扔。"""
        rows = [
            _row(order_id="A1", amount="¥38.00"),
            _row(order_id="A2", store_id=" s02 ", product_id="p03"),
            _row(order_id="A3", amount="-5.00", qty="1"),  # 退款行
        ]
        kept, report = clean_rows(rows, store_ids=STORES, product_ids=PRODUCTS)
        assert report.kept_rows == 3
        assert report.removed["4_store_not_in_stores"] == 0
        by_id = {r[0]: r for r in kept}
        assert by_id["A1"][5] == 3800
        assert by_id["A2"][2] == "S02" and by_id["A2"][3] == "P03"
        assert by_id["A3"][5] == -500 and by_id["A3"][7] == 1  # is_refund

    def test_kb001_multiline_order_not_duplicate(self):
        """§3.6：同订单号不同商品是合法多行订单，全部保留、只算 1 单。"""
        rows = [
            _row(order_id="M1", product_id="P01", amount="10.00"),
            _row(order_id="M1", product_id="P02", amount="20.00"),
            _row(order_id="M1", product_id="P02", amount="20.00"),  # 这条才是重复
        ]
        kept, report = clean_rows(rows, store_ids=STORES, product_ids=PRODUCTS)
        assert report.removed["6_duplicate_row"] == 1
        assert report.kept_rows == 2

    def test_dates_normalized_to_iso(self):
        rows = [
            _row(order_id="D1", date="2026/5/1"),
            _row(order_id="D2", date="25-07-2026"),
        ]
        kept, _ = clean_rows(rows, store_ids=STORES, product_ids=PRODUCTS)
        assert [r[1] for r in kept] == ["2026-05-01", "2026-07-25"]


class TestRealData:
    """对真实作业数据的锚点测试：数字与公开评测题 M01 的期望一致。"""

    def test_june_metrics_anchor(self, tmp_path):
        root = Path(__file__).resolve().parents[2]
        report = build_clean_db(root / "data" / "pos.db", tmp_path / "clean.db")
        # 剔除台账：坏日期 8（3×'2026-13-45'、3×'N/A'、2×空）、空金额 150、
        # qty≤0 30、脏门店 10、脏商品 40、完全重复 100
        assert report.removed["1_unparseable_date"] == 8
        assert report.kept_rows == 18290
        conn = sqlite3.connect(tmp_path / "clean.db")
        try:
            net_cents, refunds, orders, qty = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents), 0),
                       COALESCE(SUM(CASE WHEN amount_cents < 0 THEN -amount_cents ELSE 0 END), 0),
                       COUNT(DISTINCT CASE WHEN amount_cents > 0 THEN order_id END),
                       COALESCE(SUM(CASE WHEN amount_cents > 0 THEN qty ELSE -qty END), 0)
                FROM sales_clean WHERE date >= '2026-06-01' AND date <= '2026-06-30'
                """
            ).fetchone()
        finally:
            conn.close()
        assert net_cents == 15675700  # 净营业额 156757.00 元
        assert refunds == 95300       # 退款 953.00 元
        assert orders == 4311         # 有效订单数
        assert qty == 6496            # 销量
