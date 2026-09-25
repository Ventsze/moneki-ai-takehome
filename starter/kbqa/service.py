"""把各个部件接起来：规划、取数、检索、作答。"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any, Optional

from .answerer import Answerer
from .schemas import Answer
from .cleaning import build_clean_db
from .docfacts import DocFacts
from .config import Settings, load_settings
from .entities import Catalog
from .index import load_index
from .live import LiveEngine
from .llm import LLMClient, LLMError
from .planner import Planner
from .retriever import Retriever
from .sessions import SessionStore
from .toolspec import TOOL_NAMES, TOOLS
from .tools import DataTools
from .trace import Trace, TraceStore

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INT_PARAMS = {"top_k", "limit"}

#: 安全闸门：文档内容只当资料用，不当指令执行；数据库只读。
#: 删改数据、注入式指令、索要系统内部信息，一律拒绝。
_HARMFUL_PATTERNS = (
    re.compile(r"删(除|掉|了|掉它)|清空|清掉|抹掉|清除"),
    re.compile(r"(修改|更新|覆盖|写入|导入|插入|撤销|作废).{0,10}(记录|数据|订单|表|库存|排班)"),
    re.compile(r"\b(drop\s+table|delete\s+from|truncate\s+table|update\s+\w+\s+set|insert\s+into)\b", re.I),
    re.compile(r"(系统提示词|system\s*prompt|初始指令|预设指令)", re.I),
    re.compile(r"(表结构|库表|schema|数据库.{0,6}(结构|定义))", re.I),
    re.compile(r"忽略.{0,8}(之前|以上|上面|先前|所有).{0,8}(规则|指令|提示|设定)"),
    re.compile(r"(你是|假装你是|现在你是).{0,12}(管理员|root|开发者| unrestricted)", re.I),
)


class Service:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or load_settings()
        self.sessions = SessionStore()
        self.traces = TraceStore()
        self.rebuild(only_if_missing=True)

    # -- 启动与重建 -------------------------------------------------------------

    def rebuild(self, only_if_missing: bool = False) -> None:
        settings = self.settings
        if not only_if_missing or not settings.clean_db.exists():
            build_clean_db(settings.source_db, settings.clean_db)
        self.tools = DataTools(settings.clean_db)
        self.index = load_index(settings.kb_dir, settings.index_path, rebuild=not only_if_missing)
        self.retriever = Retriever(self.index, settings.today)
        self.catalog = Catalog(
            stores=self.tools.stores(), products=self.tools.products(), aliases=self.index.aliases
        )
        self.data_period = self.tools.data_period()
        self.facts = DocFacts(self.index)
        self.answerer = Answerer(
            self.tools, self.retriever, self.catalog, settings.today, self.data_period, self.facts
        )
        self.planner = Planner(self.catalog, settings.today, self.data_period, self._scout)

    def _scout(self, text: str) -> tuple[float, float]:
        """给一句话探底：它的词在知识库里有多少、检索最高分多少。

        越界判断只看这两个数，不看话题词表：知识库真讲这件事就一定照答。
        """
        result = self.retriever.search(text, top_k=1)
        return self.facts.vocab_coverage(text), (result.hits[0].score if result.hits else 0.0)

    # -- 只读接口 ---------------------------------------------------------------

    def health(self) -> dict:
        report = self.tools.cleaning_report()
        return {
            "status": "ok",
            "llm_mode": self.settings.llm_mode,
            # 契约 §1：实际进入索引的文档数，不是目录里的文件数
            # （README.md 之类的非文档文件不算）。
            "kb_docs": len(self.index.docs_meta),
            "kb_chunks": len(self.index.chunks),
            "valid_sales_rows": self.tools.valid_sales_rows(),
            "today": self.settings.today.isoformat(),
            "data_period": self.data_period,
            "cleaning_report": report,
            "index_key": self.index.key[:12],
            "kb_warnings": self.index.warnings,
        }

    def metrics_summary(self, start: str, end: str, store_id=None, product_id=None) -> dict:
        return self.tools.query_metrics(start, end, store_id, product_id)

    def metrics_daily(self, start: str, end: str, store_id=None, product_id=None) -> dict:
        return self.tools.daily_metrics(start, end, store_id, product_id)

    def metrics_top(self, start: str, end: str, store_id=None, limit: int = 10) -> dict:
        return self.tools.top_products(start, end, store_id, limit)

    def metrics_compare(self, start: str, end: str, store_id=None) -> dict:
        """所选区间 vs 之前等长区间的环比。"""
        from datetime import timedelta

        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end)
        length = (end_d - start_d).days + 1
        prev_end = start_d - timedelta(days=1)
        prev_start = prev_end - timedelta(days=length - 1)
        result = self.tools.compare_periods(
            prev_start.isoformat(), prev_end.isoformat(), start, end, store_id
        )
        result["current_window"] = [start, end]
        result["previous_window"] = [prev_start.isoformat(), prev_end.isoformat()]
        return result

    def anomalies(self) -> dict:
        """运营预警：营业中断、月度环比骤跌、退款集中，每条附可直接追问 AI 的问题。"""
        from datetime import timedelta

        period_start = date.fromisoformat(self.data_period["start"])
        period_end = date.fromisoformat(self.data_period["end"])
        stores = [s["store_id"] for s in self.tools.stores()]
        per_store_day = {}
        for row in self.tools.day_store_revenue(period_start.isoformat(), period_end.isoformat()):
            per_store_day[(row["store_id"], row["date"])] = row["net_cents"]
        all_days = sorted({day for _, day in per_store_day})

        anomalies: list[dict] = []

        # 营业中断：某店某日无明细，但该店前后 7 天内都在正常营业。
        # 窗口取 7 天是为了覆盖连续多日的停业（如 S03 六月连停 4 天）：
        # 中段的日子两侧紧邻日都没有销售，只有看远一点才知道店还开着。
        GAP_WINDOW = 7
        open_days: dict[str, set[str]] = {store: set() for store in stores}
        for store, day in per_store_day:
            open_days[store].add(day)
        for store in stores:
            days = open_days[store]
            if not days:
                continue
            cursor = date.fromisoformat(min(days))
            last = date.fromisoformat(max(days))
            while cursor <= last:
                day = cursor.isoformat()
                if day not in days:
                    before = any(
                        (cursor - timedelta(days=back)).isoformat() in days
                        for back in range(1, GAP_WINDOW + 1)
                    )
                    after = any(
                        (cursor + timedelta(days=fwd)).isoformat() in days
                        for fwd in range(1, GAP_WINDOW + 1)
                    )
                    if before and after:
                        anomalies.append(
                            {
                                "kind": "closed_day",
                                "store_id": store,
                                "date": day,
                                "detail": {"note": "前后 7 天内均有销售，当日没有任何明细"},
                                "question": "%s 这家门店 %s 为什么没有任何营业额？"
                                % (store, day),
                            }
                        )
                cursor += timedelta(days=1)

        # 月度环比骤跌：同一门店相邻自然月净营业额跌幅 ≥ 15%。
        monthly: dict[tuple[str, str], float] = {}
        for (store, day), cents in per_store_day.items():
            monthly[(store, day[:7])] = monthly.get((store, day[:7]), 0.0) + cents / 100.0
        months = sorted({ym for _, ym in monthly})
        for (store, ym), net in sorted(monthly.items()):
            prev_ym_index = months.index(ym) - 1
            if prev_ym_index < 0:
                continue
            prev = monthly.get((store, months[prev_ym_index]), 0.0)
            if prev <= 0:
                continue
            pct = round((net - prev) / prev * 100, 1)
            if pct <= -15:
                anomalies.append(
                    {
                        "kind": "month_drop",
                        "store_id": store,
                        "date": ym,
                        "detail": {"pct": pct, "current": net, "previous": prev},
                        "question": "%s %s 的营业额为什么比 %s 低这么多？"
                        % (store, ym, months[prev_ym_index]),
                    }
                )

        # 退款集中：单店单日退款 ≥ 75 元（数据期内的显著离群值）。
        for row in self.tools.refund_spikes(period_start.isoformat(), period_end.isoformat(), 7500):
            anomalies.append(
                {
                    "kind": "refund_spike",
                    "store_id": row["store_id"],
                    "date": row["date"],
                    "detail": {"refund": row["refund_cents"] / 100.0},
                    "question": "%s %s 为什么退了 %.0f 元？"
                    % (row["store_id"], row["date"], row["refund_cents"] / 100.0),
                }
            )

        severity = {"closed_day": 0, "month_drop": 1, "refund_spike": 2}
        anomalies.sort(key=lambda a: (severity[a["kind"]], a["date"]))
        return {"anomalies": anomalies, "generated_for": self.data_period}

    def meta(self) -> dict:
        """看板首屏要用的静态元信息：筛选下拉、默认区间。"""
        return {
            "today": self.settings.today.isoformat(),
            "data_period": self.data_period,
            "stores": self.tools.stores(),
            "products": self.tools.products(),
        }

    def retrieve(self, query: str, top_k: int = 5) -> dict:
        """契约 §4：片段够就恰好给 top_k 条，不够才少给。

        `top_k` 大于索引里的片段总数时按总数封顶——这正是契约允许少给的那种情况。
        """
        wanted = max(1, min(int(top_k or 5), len(self.index.chunks) or 1))
        result = self.retriever.search(query or "", top_k=wanted)
        return {"results": [hit.as_result() for hit in result.hits]}

    # -- 工具执行（live 模式下由模型驱动） ---------------------------------------

    def run_tool(self, name: str, params: dict) -> dict:
        if name not in TOOL_NAMES:
            return {"error": "没有这个工具：%s，可用工具：%s" % (name, "、".join(TOOL_NAMES))}
        schema = next(
            tool["function"]["parameters"] for tool in TOOLS if tool["function"]["name"] == name
        )
        cleaned: dict[str, Any] = {}
        for key, value in (params or {}).items():
            if key not in schema["properties"]:
                continue
            if key in _INT_PARAMS:
                try:
                    cleaned[key] = int(value)
                except (TypeError, ValueError):
                    return {"error": "参数 %s 应该是整数，收到 %r" % (key, value)}
                continue
            if value is None:
                continue
            text = str(value).strip()
            if key.startswith(("start", "end")) or key == "date":
                if not _ISO_DATE.match(text):
                    return {"error": "参数 %s 必须是 YYYY-MM-DD，收到 %r" % (key, value)}
            cleaned[key] = text
        for key in schema.get("required", []):
            if key not in cleaned:
                return {"error": "缺少必填参数 %s" % key}
        try:
            if name == "search_kb":
                return self.retrieve(cleaned["query"], cleaned.get("top_k", 5))
            return getattr(self.tools, name)(**cleaned)
        except (TypeError, ValueError) as exc:
            return {"error": "工具 %s 执行失败：%s" % (name, exc)}

    # -- 问答 -------------------------------------------------------------------

    def chat(self, session_id: Optional[str], question: str) -> dict:
        trace = Trace(
            trace_id=self.traces.new_id(self.settings.today.isoformat()),
            question=question or "",
            session_id=session_id,
        )
        answer = self._answer(trace, session_id, question or "")
        payload = {
            "answer": answer.answer,
            "answer_type": answer.answer_type,
            "citations": answer.citations,
            "data_evidence": answer.data_evidence,
            "trace_id": trace.trace_id,
        }
        trace.step("response", {"answer_type": answer.answer_type, "notes": answer.notes})
        self.traces.save(trace)
        return payload

    def _answer(self, trace: Trace, session_id: Optional[str], question: str) -> Answer:
        try:
            if not question.strip():
                return Answer(answer="没有收到问题内容，请再说一次。", answer_type="clarify")
            harm = _harmful_reason(question)
            if harm:
                trace.step("safety_gate", {"reason": harm})
                return Answer(
                    answer="这个要求我不能执行：%s。本系统只能查询数据与检索文档，"
                    "数据库不会做任何改动，内部配置也不对外提供。" % harm,
                    answer_type="refusal",
                    notes=["安全闸门：%s" % harm],
                )
            history = self.sessions.history(session_id)
            started = time.perf_counter()
            plan = self.planner.plan(question, history)
            trace.step("plan", plan.as_trace(), started=started)
            answer = self._run_engine(plan, trace, history)
            self.sessions.append(
                session_id,
                {
                    "question": question,
                    "standalone": plan.standalone,
                    "slots": plan.slots,
                    "answer": answer.answer,
                    "answer_type": answer.answer_type,
                },
            )
            return answer
        except Exception:  # noqa: BLE001 - 不管里面出什么事，接口都得给个像样的回答
            return Answer(
                answer="抱歉，我暂时无法回答。",
                answer_type="refusal",
            )

    def _run_engine(self, plan, trace: Trace, history: list[dict]) -> Answer:
        if not self.settings.live or plan.intent == "refusal":
            started = time.perf_counter()
            answer = self.answerer.answer(plan, trace)
            trace.step("answer_mock", {"answer_type": answer.answer_type}, started=started)
            return answer
        client = LLMClient(
            self.settings.llm_base_url,
            self.settings.llm_api_key,
            self.settings.llm_model,
            timeout=self.settings.llm_timeout,
        )
        engine = LiveEngine(
            client,
            self.answerer,
            self.run_tool,
            self.settings.today.isoformat(),
            self.data_period,
            budget=self.settings.chat_budget,
        )
        started = time.perf_counter()
        try:
            answer = engine.answer(plan, trace, history)
            trace.step("answer_live", {"answer_type": answer.answer_type}, started=started)
            return answer
        except LLMError as exc:
            trace.error("llm", exc)
            trace.step("answer_live_failed", {"kind": exc.kind, "detail": exc.detail}, started=started)
            return Answer(
                answer="模型服务这次没有正常返回（%s），为了不给出没有依据的数字，这个问题先不回答。"
                "可以稍后重试；失败的真实原因记在 trace 里。" % _reason_cn(exc),
                answer_type="refusal",
                notes=["live 模式失败：%s" % exc.detail],
            )

    # -- trace ------------------------------------------------------------------

    def get_trace(self, trace_id: str) -> Optional[dict]:
        return self.traces.get(trace_id)


def _harmful_reason(question: str) -> Optional[str]:
    """命中安全闸门时返回人话原因，否则 None。"""
    for pattern in _HARMFUL_PATTERNS:
        match = pattern.search(question)
        if match:
            return "检测到删改数据或索取内部信息的指令（%s…）" % match.group(0)[:20]
    return None


def _reason_cn(exc: LLMError) -> str:
    mapping = {
        "timeout": "调用超时",
        "http_error": "接口返回错误码 %s" % (exc.status or ""),
        "empty_content": "返回了空回答",
        "length": "输出额度被思考耗尽",
        "content_filter": "被内容过滤拦截",
        "insufficient_system_resource": "服务端资源不足",
        "aborted": "请求被中止",
        "bad_tool_args": "工具参数无法解析",
        "bad_json": "返回的不是合法 JSON",
        "budget": "整体耗时接近时限",
        "transport": "网络异常",
        "tool_loop": "工具调用没有收敛",
    }
    return mapping.get(exc.kind, exc.kind)
