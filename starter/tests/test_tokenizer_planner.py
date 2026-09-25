"""分词与意图规划的回归测试。

分词是检索的地基：中文必须切成二元组（设计意图见 docfacts.vocab_coverage
的注释"只看二元组与英文词"），拉丁/数字串保持整词，两侧（文档与查询）一致。
规划器不得用"多少/多久/几"的词面匹配覆盖已经判定的政策/混合意图。
"""

from __future__ import annotations

import pytest

from kbqa.tokenizer import content_tokens, tokenize


class TestTokenize:
    def test_chinese_becomes_bigrams(self):
        tokens = tokenize("申请退款")
        assert tokens == ["申请", "请退", "退款"]

    def test_latin_and_digits_stay_whole(self):
        assert tokenize("牛肉poke P06 42.5") == ["牛肉", "poke", "p06", "42.5"]

    def test_mixed_script(self):
        tokens = tokenize("KB-013 三文鱼poke")
        assert "poke" in tokens
        assert "三文" in tokens and "文鱼" in tokens
        assert "kb-013" in tokens  # 编号整词，便于引用命中

    def test_query_and_document_share_tokens(self):
        """问句与文档原文必须有公共 token，这是 BM25 能命中的前提。"""
        question = "外卖订单多久内可以申请退款？"
        document = "三、时限：外卖订单在订单送达后 24 小时内可以申请退款。"
        overlap = set(tokenize(question)) & set(tokenize(document))
        assert {"外卖", "退款", "申请"} <= overlap

    def test_single_char_run(self):
        assert tokenize("好") == ["好"]

    def test_content_tokens_drops_stopwords(self):
        tokens = content_tokens("请问 7 月的净营业额是多少？")
        assert "净营" in tokens and "营业" in tokens


@pytest.fixture(scope="module")
def planner():
    from kbqa.service import Service

    service = Service()
    return service.planner


class TestPlannerIntent:
    def test_refund_time_limit_is_doc(self, planner):
        """「多久内可以退款」是问规定，不是问数字（C01）。"""
        plan = planner.plan("外卖订单多久内可以申请退款？")
        assert plan.intent == "doc"

    def test_amount_question_stays_data(self, planner):
        plan = planner.plan("牛肉poke 六月卖了多少钱？")
        assert plan.intent == "data"

    def test_target_question_stays_hybrid(self, planner):
        """「卖了多少份，达到目标了吗」需要数据库 + 活动方案（H 类）。"""
        plan = planner.plan("618 当天 S02 的牛肉poke 卖了多少份，达到目标了吗？")
        assert plan.intent == "hybrid"

    def test_allergen_question_is_doc_not_refused_by_planner(self, planner):
        plan = planner.plan("有顾客问牛肉poke里有哪些过敏原，怎么答？")
        assert plan.intent != "refusal"
