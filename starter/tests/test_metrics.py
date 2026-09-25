"""指标接口的口径回归测试。

期望值全部来自公开评测题库（M01–M06），等价于公司参考实现的答案；
KB-001 §4 是唯一口径：退款行计入净营业额、有效订单数按 DISTINCT order_id、
销量 = 销售 qty − 退款 qty、客单价 = 净营业额 ÷ 有效订单数。
"""

from __future__ import annotations

import pytest

# (题目, 参数, 期望) —— 与 eval/public_questions.jsonl 逐字段对齐
SUMMARY_CASES = [
    (
        "M01_六月全量",
        {"start": "2026-06-01", "end": "2026-06-30"},
        {"net_revenue": 156757.0, "refund_amount": 953.0, "orders": 4311, "qty": 6496, "aov": 36.36},
    ),
    (
        "M02_七月S02",
        {"start": "2026-07-01", "end": "2026-07-31", "store_id": "S02"},
        {"net_revenue": 41740.0, "refund_amount": 107.0, "orders": 875, "qty": 1395, "aov": 47.7},
    ),
    (
        "M03_八月P21",
        {"start": "2026-08-01", "end": "2026-08-31", "product_id": "P21"},
        {"net_revenue": 11024.0, "refund_amount": 16.0, "orders": 461, "qty": 689, "aov": 23.91},
    ),
    (
        "M04_618当天S02P06",
        {"start": "2026-06-18", "end": "2026-06-18", "store_id": "S02", "product_id": "P06"},
        {"net_revenue": 3625.0, "refund_amount": 0.0, "orders": 53, "qty": 125, "aov": 68.4},
    ),
]


@pytest.mark.parametrize("name,params,expect", SUMMARY_CASES, ids=[c[0] for c in SUMMARY_CASES])
def test_summary_matches_eval(client, name, params, expect):
    body = client.get("/api/metrics/summary", params=params).json()
    for field, value in expect.items():
        assert body[field] == pytest.approx(value, abs=0.01), "%s 的 %s" % (name, field)


def test_summary_closed_interval_single_day(client):
    """契约 §2/§3：闭区间。start == end 的一天必须查得到数，不能被 date < end 排空。"""
    body = client.get(
        "/api/metrics/summary", params={"start": "2026-06-18", "end": "2026-06-18", "store_id": "S02"}
    ).json()
    assert body["orders"] > 0


def test_summary_empty_range_returns_zero(client):
    """契约 §2：区间没数据时数值为 0、aov 为 null，不报错。"""
    body = client.get(
        "/api/metrics/summary", params={"start": "2026-09-01", "end": "2026-09-30"}
    ).json()
    assert body["net_revenue"] == 0 and body["orders"] == 0
    assert body["aov"] is None and body["refund_amount"] == 0


def test_daily_zero_fill_and_recovery(client):
    """M06：S03 六月停业（6/8–6/11 零填充），6/12 恢复。"""
    body = client.get(
        "/api/metrics/daily",
        params={"start": "2026-06-08", "end": "2026-06-12", "store_id": "S03"},
    ).json()
    days = {d["date"]: d for d in body["days"]}
    assert set(days) == {"2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"}
    for d in ("2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"):
        assert days[d]["net_revenue"] == 0.0 and days[d]["orders"] == 0
        assert days[d]["aov"] is None
    assert days["2026-06-12"]["net_revenue"] == pytest.approx(998.0, abs=0.01)
    assert days["2026-06-12"]["orders"] == 27
    assert days["2026-06-12"]["aov"] == pytest.approx(36.96, abs=0.01)


def test_daily_every_day_present(client):
    """契约 §3：区间内每一天都要有一条，含没营业额的日期。"""
    body = client.get(
        "/api/metrics/daily", params={"start": "2026-05-01", "end": "2026-05-10"}
    ).json()
    assert [d["date"] for d in body["days"]] == [
        "2026-05-%02d" % i for i in range(1, 11)
    ]
