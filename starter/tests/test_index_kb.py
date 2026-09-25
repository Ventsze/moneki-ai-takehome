"""知识库加载 / 索引缓存 / 切块的回归测试。

契约 §0：doc_id 与文件格式无关；README 评审流程第 3 步会换一整套知识库
执行重建命令，所以缓存键必须感知知识库内容。
"""

from __future__ import annotations

from pathlib import Path

from kbqa.chunker import CHUNK_SIZE, chunk_document
from kbqa.index import content_key, load_index
from kbqa.loader import load_knowledge_base


def _make_kb(root: Path) -> Path:
    """最小知识库：md + txt + html 各一篇，都有 KB 编号。"""
    kb = root / "kb"
    kb.mkdir(parents=True)
    (kb / "KB-101_政策.md").write_text(
        "---\ndoc_id: KB-101\ntitle: 政策甲\nstatus: 现行\n---\n外卖订单在送达后 24 小时内可以申请退款。",
        encoding="utf-8",
    )
    (kb / "KB-102_供应商邮件.txt").write_text(
        "From: supplier@example.com\nSubject: Salmon incident\n\n三文鱼暂停供货两天。",
        encoding="utf-8",
    )
    (kb / "KB-103_FAQ.html").write_text(
        "<html><head><title>常见问题</title></head><body><p>会员储值满 500 送 50。</p></body></html>",
        encoding="utf-8",
    )
    (kb / "README.md").write_text("说明文件，没有 KB 编号。", encoding="utf-8")
    return kb


class TestLoaderFormats:
    def test_txt_and_html_are_documents(self, tmp_path):
        """契约 §0：doc_id 与格式无关。txt/html 也是知识库文档，不能丢。"""
        docs, warnings = load_knowledge_base(_make_kb(tmp_path))
        ids = {d.doc_id for d in docs}
        assert ids == {"KB-101", "KB-102", "KB-103"}
        assert not any("KB 编号" in w and "README" not in w for w in warnings)
        # README 没编号，应作为噪音被跳过并告警
        assert any("README.md" in w for w in warnings)

    def test_html_title_extracted(self, tmp_path):
        docs, _ = load_knowledge_base(_make_kb(tmp_path))
        faq = next(d for d in docs if d.doc_id == "KB-103")
        assert faq.title == "常见问题"
        assert faq.fmt == "html"


class TestCacheKey:
    def test_key_changes_when_kb_content_changes(self, tmp_path):
        """换知识库后缓存必须失效：内容变 → 键变。"""
        kb = _make_kb(tmp_path)
        key_before = content_key(kb)
        (kb / "KB-101_政策.md").write_text("内容改了。", encoding="utf-8")
        assert content_key(kb) != key_before

    def test_key_changes_when_doc_added(self, tmp_path):
        kb = _make_kb(tmp_path)
        key_before = content_key(kb)
        (kb / "KB-104_新增.md").write_text("---\ndoc_id: KB-104\n---\n新文档。", encoding="utf-8")
        assert content_key(kb) != key_before


class TestLoadIndex:
    def test_stale_cache_is_rebuilt(self, tmp_path):
        """知识库变了，旧缓存不能再用（评测流程：换库 → 重建 → 隐藏题库）。"""
        kb = _make_kb(tmp_path)
        index_path = tmp_path / "index.json"
        first = load_index(kb, index_path)
        assert len(first.docs_meta) == 3
        (kb / "KB-101_政策.md").write_text(
            "---\ndoc_id: KB-101\ntitle: 政策甲\n---\n退款时限改为 48 小时。", encoding="utf-8"
        )
        second = load_index(kb, index_path)  # 不传 rebuild，模拟服务启动路径
        assert "退款时限改为 48 小时" in second.texts["KB-101"]


class TestChunker:
    def test_tail_of_long_document_is_indexed(self):
        """切块不能丢文档尾部：700 字 → 300+300+100 三块。"""
        from kbqa.loader import Document

        doc = Document(
            doc_id="KB-201",
            title="长文档",
            text="甲" * (2 * CHUNK_SIZE) + "尾",
            path=Path("KB-201.md"),
            fmt="md",
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 3
        assert chunks[-1].text.endswith("尾")
