"""运营预警与环比接口的回归测试（加分项：异常预警、图表联动）。"""

from __future__ import annotations


class TestCompare:
    def test_compare_june_july(self, real_client):
        body = real_client.get(
            "/api/metrics/compare", params={"start": "2026-06-01", "end": "2026-06-30"}
        ).json()
        assert body["current"]["net_revenue"] == 156757.0
        assert body["previous"]["net_revenue"] == 111795.0
        # 环比涨跌幅方向正确：6 月比 5 月（前 30 天 5/2–5/31）涨
        assert body["delta"]["net_revenue"]["direction"] in ("涨", "跌", "持平")
        assert body["current_window"] == ["2026-06-01", "2026-06-30"]
        assert body["previous_window"] == ["2026-05-02", "2026-05-31"]


class TestAnomalies:
    def test_closed_days_detected(self, real_client):
        """S03 六月停业一周（6/8–6/11）必须被自动预警。"""
        body = real_client.get("/api/anomalies").json()
        closed = [
            a
            for a in body["anomalies"]
            if a["kind"] == "closed_day" and a["store_id"] == "S03"
        ]
        dates = {a["date"] for a in closed}
        assert {"2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"} <= dates
        for a in closed:
            assert a["question"], "每条预警都要带可直接追问 AI 的问题"

    def test_month_drop_detected(self, real_client):
        """S03 六月环比下跌约 20%（停业所致）触发月度预警。"""
        body = real_client.get("/api/anomalies").json()
        drops = [
            a
            for a in body["anomalies"]
            if a["kind"] == "month_drop" and a["store_id"] == "S03"
        ]
        assert drops and drops[0]["detail"]["pct"] <= -15

    def test_refund_spike_detected(self, real_client):
        """单日单店退款峰值（6/4 S02 126 元）触发退款预警。"""
        body = real_client.get("/api/anomalies").json()
        spikes = [
            a
            for a in body["anomalies"]
            if a["kind"] == "refund_spike" and a["store_id"] == "S02"
        ]
        assert any(a["date"] == "2026-06-04" for a in spikes)

    def test_anomalies_sorted_by_severity(self, real_client):
        body = real_client.get("/api/anomalies").json()
        kinds = [a["kind"] for a in body["anomalies"]]
        # 营业中断最严重，排最前
        assert "closed_day" not in kinds or kinds.index("closed_day") == 0
