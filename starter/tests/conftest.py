"""测试夹具。

`client` 把检索整个换成固定返回：接口层测试就不用跟着知识库一起改，
跑起来也快。patch 的是类方法，测试结束必须恢复，否则会污染
同一个进程里要用真实检索的测试（见 test_doc_pipeline 的排序用例）。
要看真实检索效果，用 `real_client` 或直接实例化 Service。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAKE_TEXT = "退款政策 v2 > 三、时限：外卖订单在订单送达后 24 小时内可以申请退款。"


@pytest.fixture(scope="session")
def real_client(tmp_path_factory):
    os.environ["VAR_DIR"] = str(tmp_path_factory.mktemp("var"))
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)

    from fastapi.testclient import TestClient

    from kbqa import server

    return TestClient(server.app)


@pytest.fixture()
def client(real_client):
    from fastapi.testclient import TestClient  # noqa: F401

    from kbqa import retriever as retriever_module

    original_search = retriever_module.Retriever.search

    def fake_search(self, query, top_k=5, **kwargs):
        hit = retriever_module.Hit(
            doc_id="KB-013",
            chunk_id="KB-013#1",
            score=42.0,
            text=FAKE_TEXT,
            source_text=FAKE_TEXT,
            meta={"title": "退款政策 v2", "status": "现行"},
        )
        return retriever_module.SearchResult(
            hits=[hit][:top_k],
            query=query,
            terms=[],
            expansions=[],
            filtered=[],
            coverage=1.0,
        )

    retriever_module.Retriever.search = fake_search
    yield real_client
    retriever_module.Retriever.search = original_search
