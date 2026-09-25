# 评测报告

- 服务地址：`http://127.0.0.1:18765`
- 题库：`/Users/wenze/Developer/moneki-ai-takehome-main/eval/my_questions.jsonl`
- 生成时间：2026-09-25 21:44:44
- 知识库：载入 35 份文档（用于 quote 逐字校验）

## 总分

**18.00 / 18.00（100.0%）**，10 题全绿 / 共 10 题。

每题耗时：中位数 0.00 秒，最大 0.03 秒，合计 0.1 秒。

## 分类别

| 类别 | 得分 | 满分 | 比例 | 全绿题数 |
|---|---|---|---|---|
| 指标接口（`metrics`） | 2.00 | 2.00 | 100.0% | 2 / 2 |
| 检索质量（`retrieval`） | 2.00 | 2.00 | 100.0% | 2 / 2 |
| 纯文档问题（`doc`） | 4.00 | 4.00 | 100.0% | 2 / 2 |
| 数据 + 文档（`hybrid`） | 3.00 | 3.00 | 100.0% | 1 / 1 |
| 多轮追问（`multi_turn`） | 3.00 | 3.00 | 100.0% | 1 / 1 |
| 拒答（`refusal`） | 4.00 | 4.00 | 100.0% | 2 / 2 |

## `/api/health` 快照

```json
{
  "status": "ok",
  "llm_mode": "mock",
  "kb_docs": 35,
  "kb_chunks": 124,
  "valid_sales_rows": 18290,
  "today": "2026-09-01",
  "data_period": {
    "start": "2026-05-01",
    "end": "2026-08-31"
  },
  "cleaning_report": {
    "raw_rows": 18628,
    "removed": {
      "1_unparseable_date": 8,
      "2_empty_amount": 150,
      "3_qty_le_zero": 30,
      "4_store_not_in_stores": 10,
      "5_product_not_in_products": 40,
      "6_duplicate_row": 100,
      "note_unparseable_amount": 0
    },
    "kept_rows": 18290,
    "kept_sales_rows": 18196,
    "kept_refund_rows": 94
  },
  "index_key": "e7d3b9001a08",
  "kb_warnings": [
    "跳过没有 KB 编号的文件：README.md"
  ]
}
```

## 没通过的题（0 道）

没有。

## 全部题目

| 题号 | 类别 | 得分 | 满分 | 耗时（秒） |
|---|---|---|---|---|
| X-M1 | metrics | 1.00 | 1.00 | 0.00 |
| X-M2 | metrics | 1.00 | 1.00 | 0.00 |
| X-R1 | retrieval | 1.00 | 1.00 | 0.00 |
| X-R2 | retrieval | 1.00 | 1.00 | 0.00 |
| X-C1 | doc | 2.00 | 2.00 | 0.01 |
| X-C2 | doc | 2.00 | 2.00 | 0.02 |
| X-F1 | refusal | 2.00 | 2.00 | 0.00 |
| X-F2 | refusal | 2.00 | 2.00 | 0.00 |
| X-H1 | hybrid | 3.00 | 3.00 | 0.01 |
| X-T1 | multi_turn | 3.00 | 3.00 | 0.03 |
