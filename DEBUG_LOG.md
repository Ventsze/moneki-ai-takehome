# DEBUG_LOG —— starter 缺陷定位与修复台账

按修复顺序排列。每条缺陷都按"现象 → 假设 → 验证 → 根因 → 修复 → 回归测试"记录；
回归测试一律先写、先跑红、先 commit，再修（提交历史可对照）。
公开题库得分轨迹见 `EVAL_REPORT.md`。

---

## D1 清洗规则形同虚设：六类剔除一条都不执行

- **现象**：`make rebuild` 输出 `removed` 七项全 0、18628 行全部保留；评测 M01（6 月净营业额 156757）对不上，而 README 明说数据里有重复、缺失、脏外键。
- **假设**：清洗 SQL 条件太严？还是剔除逻辑压根没写？
- **验证**：读 `starter/kbqa/cleaning.py` 的 `clean_rows`，docstring 自己写着"把 sales 原样搬过来……日期照抄"。逐行核对 §3 六类剔除——一行实现都没有。用 Python 按 KB-001 手写一遍清洗模拟：剔除 335 行后 M01 五项指标与期望**逐位一致**，证实口径理解无误、错在 starter。
- **根因**：`cleaning.py` `clean_rows()`（原 77–102 行）只解析金额和 qty，没有任何剔除分支；`parse_amount` 失败时甚至把金额按 0 入库。
- **修复**：按 KB-001 §2/§3 重写：`normalize_date()` 支持三种格式（`DD-MM-YYYY` 日在前，带日历校验，`2026-13-45` 判无效）；编号先规范化（去空白+转大写）再判脏外键；六类剔除按序执行；重复行按规范化后七字段全等判定（多行订单不误杀）。真实数据剔除台账：坏日期 8、空金额 150、qty≤0 30、脏门店 10、脏商品 40、完全重复 100，保留 18290 行。
- **回归测试**：`tests/test_cleaning.py`（修复前跑：`ImportError: cannot import name 'normalize_date'`，全红）。

## D2 指标口径三连错：退款排除、订单数按行数、区间少一天

- **现象**：清洗修好后 M01 仍 0 分。API 返回 `net_revenue=153131`（期望 156757）、`refund_amount=0`、`orders=4243`（期望 4311）。
- **假设**：查询 WHERE 条件或聚合字段写错。
- **验证**：`curl /api/metrics/summary` 对比期望值；refund_amount 恰为 0、净营业额差值 ≈ 6 月退款总额，锁定退款被整段排除。
- **根因**：`tools.py` `query_metrics()`（原 93–120 行），三处都写着 v2 口径的注释：
  1. `WHERE ... AND is_refund = 0` 排除退款行，且 `refund_amount` 直接**硬编码 0**——KB-001 §4 明确"退款行计入净营业额"（v3 相对 v2 的变更点 1）；
  2. `COUNT(*)` 数明细行——§4 要求有效订单数 = 销售行中不同 `order_id` 的个数（变更点 3）；
  3. `_where()` 用 `date < end`——API 契约 §2/§3 要求闭区间，6 月 30 日整天被丢掉。
- **修复**：三个查询（summary/daily/top/payment）统一改为：净营业额 = 全部金额之和（负数自然相减）、退款金额取负金额绝对值、订单数按 `COUNT(DISTINCT CASE WHEN amount_cents > 0 THEN order_id END)`、销量 = 销售 qty − 退款 qty；`_where` 改 `date <= end`。
- **回归测试**：`tests/test_metrics.py`（修复前 6 红 2 绿：M01–M04 全错、闭区间单日查不到数）。

## D3 `SUPPORTED_SUFFIXES` 缺 txt/html：三篇文档进不了索引

- **现象**：知识库 35 份文档，重建索引后只有 32 篇；丢的恰好是 KB-022（.txt 供应商邮件）、KB-062（.txt 旧 OA 导出）、KB-061（.html FAQ）。
- **假设**：HANDOVER.md 声称"md、txt、html 三种格式都支持，当时专门试过"——README 提醒过不要信交接文档，先核对。
- **验证**：`grep SUPPORTED_SUFFIXES kbqa/loader.py` → `{".md", ".markdown"}`。而 `load_document()` 里 txt/html 的解析代码（fmt 分支、HTML title 提取）都写好了，只是入口被扩展名白名单挡死。
- **根因**：`loader.py:12`。契约 §0 明确"doc_id 与文件格式无关"。
- **修复**：白名单补 `.txt`、`.html`、`.htm`。
- **回归测试**：`tests/test_index_kb.py::TestLoaderFormats`（修复前 2 红）。

## D4 缓存键不含知识库内容 + rebuild 不强制重建：预置旧索引永远命中

- **现象**：`make rebuild` 后 `.cache/index.json` 的文件时间戳不变；索引始终 25 篇/53 片段（仓库里预置的旧缓存），删掉缓存文件重建变成 32 篇/72 片段。
- **假设**：两层嫌疑——`content_key()` 的键不够"内容敏感"；`rebuild.py` 调 `load_index` 时没传 `rebuild=True`。
- **验证**：读 `index.py` `content_key()`：注释说"改了切块或分词，键就变"，实现只哈希 `INDEX_VERSION|CHUNKER_VERSION|TOKENIZER_VERSION` 三个字符串，**`kb_dir` 参数根本没用**。`rebuild.py:22` 确实没传 `rebuild=True`。两者叠加：只要版本号不变，任何数据/知识库变更都命中旧缓存——而评审流程第 3 步恰恰是"换一份数据和知识库执行重建命令"。
- **根因**：`index.py content_key()` + `rebuild.py` 的调用方式。
- **修复**：`content_key` 纳入知识库全部文件（相对路径 + 内容 SHA256）；`make rebuild` 改为强制重建；启动路径（`only_if_missing=True`）保留缓存判断。另把 `.cache/` 移出版本库（HANDOVER 提交的病灶缓存本身就是雷）。
- **回归测试**：`tests/test_index_kb.py::TestCacheKey`（键随内容变化）、`TestLoadIndex::test_stale_cache_is_rebuilt`（修复前 3 红）。

## D5 切块丢文档尾部：`range(0, len-300, 300)`

- **现象**：重读 `chunker.py` 时发现止点是 `len(text) - CHUNK_SIZE`：每篇文档最后不足 300 字的尾巴进不了索引。政策文档的"变更记录、生效日期"恰在结尾。
- **假设 / 验证**：构造 700 字文档跑 `chunk_document`，只出 2 块，第三块（含尾部"尾"字）消失。
- **根因**：`chunker.py chunk_document()` 的 range 止点。
- **修复**：改为 `range(0, len(text), CHUNK_SIZE)`。
- **回归测试**：`tests/test_index_kb.py::TestChunker::test_tail_of_long_document_is_indexed`。

## D6 health 的 kb_docs 数的是目录文件数

- **现象**：`/api/health` 报 `kb_docs: 36`，评测报告载入 35 份——多出的是没有 KB 编号的 `knowledge_base/README.md`。
- **验证**：`service.health()` 里 `sum(1 for path in kb_dir.rglob("*") if path.is_file())`——数的是磁盘文件。契约 §1：kb_docs 是"实际进入索引的文档数"。
- **根因**：`service.py health()`。
- **修复**：改为 `len(self.index.docs_meta)`。
- **回归测试**：`tests/test_index_kb.py` 补充断言（35 篇）。

## D7 中文分词按空白切：检索与闸门的共同总根因

- **现象**：文档全部进索引后，检索仍只有 8/15，纯文档题 0/16；C02（问过敏原，KB-040 明明在）的拒答理由是"词表覆盖率 0.00"——"牛肉poke""过敏原"这种实词居然覆盖率为零。
- **假设**：`vocab_coverage` 的词表统计有 bug？还是 token 根本对不上？
- **验证**：手工调用 `tokenize("外卖订单多久内可以申请退款？")` → 返回**整句一个 token**（含问号）。中文没有空格，`normalise(text).split()` 按空白切，整句变巨型 token，在索引里永远查不到——BM25、`coverage()`、`vocab_coverage` 全部失效。关键旁证：`docfacts.vocab_coverage` 的注释写着"只看二元组与英文词：单个汉字在二元组索引里本来就不会出现"——**设计意图就是汉字二元组索引，实现却完全不是**。注释说 A、代码做 B，是典型的埋缺陷手法。
- **根因**：`tokenizer.py tokenize()`。
- **修复**：重写为"拉丁/数字串（含 `kb-013` 这类编号）整词 + 汉字二元组"切分，文档侧与查询侧走同一函数天然一致；`TOKENIZER_VERSION` 升级触发缓存失效。改完即重建，检索 8→14、混合 3→15、总分 44.5→66.5。
- **回归测试**：`tests/test_tokenizer_planner.py::TestTokenize`（修复前 5 红），其中 `test_query_and_document_share_tokens` 钉死"问句与文档必须有公共 token"这一检索前提。

## D8 规划器词面路由覆盖已判定的意图

- **现象**：C01"外卖订单**多久**内可以申请退款？"被规划成 `data`（查出全公司净营业额）；H 类混合题"卖了多少份，达到目标了吗"丢掉目标对比。
- **假设**：`_choose_kind` 的 `asks_policy` 分支没问题（"多久"在 POLICY_WORDS 里），嫌疑落在后面的"路由"段。
- **验证**：`Planner.plan` 打断点式打印：`asks_policy` 分支正确产出 `doc/doc`，随后被末段 `if E.has_any(text, ("多少", "多久", "几")): plan.intent = "data"` 强行覆盖。注释还写着"两边都走一遍太慢，没必要"。
- **根因**：`planner.py _choose_kind()` 末尾的路由块；同段 `elif ("为什么"...)` 分支还会把"营业额为什么这么低"这类本该 hybrid 的异常分析掰成纯文档题。
- **修复**：整段删除。`_choose_kind` 前面的分支已正确处理：政策问句走 `doc`、目标/价格/异常走 `hybrid`、数字问句走 `data`。
- **回归测试**：`tests/test_tokenizer_planner.py::TestPlannerIntent`（修复前 2 红：C01 被判 data、target 题被判 data）。

## D9 会话两处断：历史没传给规划器 + 全局串线

- **现象**：V03 第二轮"那 6 月的时候呢？"回答"这句像是追问，但这个会话里没有上文"——同一 session 第一轮明明问过。T 类多轮也大面积丢分。
- **假设**：`SessionStore` 没存上？还是存了没读？
- **验证**：读 `service.py _answer()`：`history = self.sessions.history(session_id)` 取到了，但下一行 `self.planner.plan(question)` **没把 history 传进去**（`plan()` 第三参数默认 None）——追问链路从未见过历史。再看 `sessions.py`：`_turns` 是一个全局列表，`history(session_id)` 直接返回全部——**不同 session_id 互相串线**（契约 §5 明令禁止），且多会话下 6 条上限互相挤占。
- **根因**：`service.py _answer()` 丢参数 + `sessions.py` 全局列表。
- **修复**：`plan(question, history)` 补传；`SessionStore` 改为 `dict[session_id, list]` 按 id 隔离，无 id 的对话不存不读。
- **回归测试**：`tests/test_doc_pipeline.py::TestSessions`（隔离）、`TestFollowupHistory::test_second_turn_sees_first_turn`（修复前 3 红）。

## D10 文档候选句排序方向反了：取最低分句当答案

- **现象**：V01"618 做活动的是哪个商品，活动价多少？"引用了"目标销量 120 份"，而 `facts.rank(query, 'KB-023')` 的第一名明明是"**牛肉poke 活动价 ¥29**"（0.617 vs 0.187）。V02/V03 引"余额不足""档位叠加"而非"赠送 60/50 元"，同款症状。
- **假设**：rank 排序错？逐层排查后发现 rank 是对的，问题在 `_doc_block` 的**跨文档合排序**。
- **验证**：`answerer.py _doc_block()` 里 `candidates.sort(key=lambda item: (round(item["score"], 2), item["effective_from"]))`——**升序**。注释写的意图是"分数接近时以生效日期更新的为准"，实现把整个排序方向写反，`candidates[0]`（当"最佳"用）实际是最低分候选。
- **根因**：`answerer.py _doc_block()`。
- **修复**：改为得分降序为主、分数打平时生效日期新的在前。
- **回归测试**：`tests/test_doc_pipeline.py::TestDocBlockOrdering`（修复前 2 红，用真实知识库跑）。

## D11 `status` 字段名错位：已废止判断永远为假

- **现象**：C01 修复排序后仍偶发引用 KB-012（退款政策 v1，7 天）而非现行 KB-013（24 小时），且 `cite_none` 扣分。检索层本该把"已废止"文档整个排除。
- **假设**：`_eligible` 的条件写错？打印 meta 发现异常。
- **验证**：`docs_meta["KB-012"]` 里**没有 `status` 键**，只有 `state`。`loader.Document.meta()` 序列化时写 `"state": self.status`，而 `retriever._eligible()` 与 `docfacts.version_note()` 都读 `meta.get("status")`——永远拿到 None。顺带发现 `version_note`（"现行版"标注）也因此一直失灵。
- **根因**：`loader.py meta()` 键名。
- **修复**：统一为 `status`；同时把 `LOADER_VERSION` 纳入 `content_key`——loader 代码变更（键名、编码、去标签）都影响索引产物，必须能触发缓存失效。
- **回归测试**：`tests/test_doc_pipeline.py::TestVersionStatus`（修复前 3 红）。

## D12 300 字切块把跨边界行劈成两半

- **现象**：H03"冷萃乌龙茶上市第一个月的销量达标了吗？"——检索第一名就是 KB-028，但 `_find_target` 的正则"目标…900 杯"匹配不到任何句子；打印 units 发现目标句被切成 `…全门店合计目标销量` 和 `900 杯。` 两段。C07 的"毛利率低于 35%"句同雷。
- **假设**：`split_sentences` 切的？不——原文一行完整存在，是 **chunk 边界**正落在行中间。
- **验证**：`units()` 逐 chunk 调 `_lines_of(chunk.source_text)`：一行跨 300 字边界时，前一块拿行头、后一块拿行尾，各自成 unit，永远拼不回。KB-028 的目标行起点恰在块边界前 3 字。
- **根因**：`units.py units()` 按 chunk 切行。
- **修复**：正文行改在**整篇文档**上切（chunk 的 source_text 拼接即原文，无改写，位置定位不受影响）；表格行保留按 chunk 处理（需要各 chunk 的表头）。
- **回归测试**：`tests/test_doc_pipeline.py::TestCrossChunkLine`（构造 297 甲 + 目标行，行恰跨块边界；修复前红）。

## D13 零词重叠的焦点句被跳过：英文邮件永远进不了候选

- **现象**：C04"三文鱼那次断供，供应商最后赔了我们多少钱？"——期望引用 KB-022（英文供应商邮件，含 CNY 8,600），实际引的是 KB-021/KB-041。`rank(query, 'KB-022')` 的前 4 名全是邮件开头行，结算句（含 8,600）根本不在。
- **假设**：结算句不满足 money 焦点？不——句里有 `CNY 8,600`，正则命中。嫌疑在 `rank` 的词重叠门槛。
- **验证**：`rank()` 里 `if hit <= 0: continue`——中文问句与英文句的 token 重叠为 0（别名归一只补了"三文鱼poke"），结算句在"词命中"环节就被丢掉，轮不到焦点判断。
- **根因**：`docfacts.py rank()` 的零命中跳过。
- **修复**：主题相关性已由外层 BM25 保证（这篇文档本来就是检索命中的），句内零词重叠但**满足问句焦点**的句子给保底分参与排序。
- **回归测试**：`tests/test_doc_pipeline.py::TestRealQuestions::test_c04_english_email_compensation`（部分生效，见 EVAL_REPORT 的止损说明）。

## D14 检索"先取满 top_k 再过滤"：契约 §4 点名禁止的反模式

- **现象**：`status` 修复（D11）让已废止文档真正被排除后，R01/R06/R08 突然只回 3/5 条：`results_count` 失败。契约原文："先取前 top_k 再做过滤、结果只剩两三条的实现，不符合这一条"。
- **假设**：`retriever.search` 里有补齐逻辑（padded hits），为什么没生效？
- **验证**：读到 `search()` 末尾：补满 top_k **之后**还有一行 `hits = [hit for hit in hits if hit.doc_id not in excluded]`——把补进来的已废止文档块又剔掉了。另外 `allowed` 候选池本身没剔除 excluded 文档，排除只发生在最后一行过滤。
- **根因**：`retriever.py search()` 的过滤时机。
- **修复**：被 `_eligible` 淘汰文档的片段**根本不进 allowed 候选池**；缺的格子在池内用零相关片段垫底补齐；删除末尾那行过滤。
- **回归测试**：`tests/test_doc_pipeline.py::TestRetrieveTopK`（修复前红）。

## D15 Markdown 表行没有表头：过敏原渲染不出字段名

- **现象**：C02 回答里是原始表行 `| P06 | 牛肉poke | ✓ | ✓ | — |…`，评测要求回答里出现"麸质、大豆、芝麻"。`docfacts.render_row()` 本来就会把表头渲染成"P06 牛肉poke：含有 麸质、大豆、芝麻"。
- **假设**：`render()` 依赖 `unit.kind == "table"` 且 `unit.header` 非空，而 chunker 从不产生 table 块——表行全是 `kind="text"`、`header=[]`，`render_row` 收到空表头原样返回。
- **验证**：打印 KB-040 的 units，P06 行 `kind='text'`、`header=[]`。
- **根因**：`units.py` 没有 Markdown 表格结构识别。
- **修复**：全文切行时识别分隔行（`|---|`），其上一行为表头，后续连续 `|` 行标 `kind="table"` 并共享表头。首版实现把"表头行号"映射错了一行（键应为分隔行自身行号），测试抓出后修正。
- **回归测试**：`tests/test_doc_pipeline.py::TestTableRowRender`（修复前 2 红）。

## D16 数据库可写：`open_readonly` 名不副实 + `run_sql` 会 commit

- **现象**：代码审读发现 `tools.run_sql()`（供模型自由写 SQL 的工具）执行后会 `self.conn.commit()`；而 `open_readonly` 只是名字叫 readonly，`sqlite3.connect` 默认可写。第三关硬要求"数据库不能有任何改动"被整个绕过。
- **假设 / 验证**：`tools.conn.execute("DELETE FROM sales_clean")` 在测试里真的删光——连接确实可写。
- **根因**：`cleaning.open_readonly()` + `tools.run_sql()`。
- **修复**：连接改 SQLite URI `mode=ro`（物理只读，写操作直接 `OperationalError`）；`run_sql` 拒绝 INSERT/UPDATE/DELETE/DROP/ATTACH 等并只放行 SELECT，删掉 commit。双层防护：即使将来有人绕过工具层，连接层也写不进去。
- **回归测试**：`tests/test_security.py`（修复前 4 红）。

## D17 文档回答把整篇文档倒给用户

- **现象**：S01/C02/C05/C06/C07 的回答 1500~5800 字、数字 20~36 个——`_answer_doc` 返回 `self._context(result) + body`，把命中文档的**全文**拼进回答。评测的 `answer_length`（≤1200 字）与 `number_flood`（≤20 个数字）双双爆表；KB-061 的 HTML 甚至把源码整篇倒出。
- **根因**：`answerer.py _answer_doc()` + `_context()`；loader 对 html 未去标签放大了问题。
- **修复**：回答只保留引用句组装的正文（上限 600 字），原文事实在 `citations` 里逐字可查。
- **回归测试**：`tests/test_doc_pipeline.py::TestDocAnswerShape`。

## D18 文档读取与 quote 核对规则不一致：GBK、HTML、归一

- **现象**：C03 的 quote 报"不是原文里的连续文字"且含怪字符 ζ；C05 的 quote 带 `<p>` 标签；verbatim 行为与评测核对规则不齐。
- **假设 / 验证**：读评测脚本的核对实现：`decode_bytes` 是 utf-8→**gb18030**（KB-062 是 GBK 旧 OA 导出，我们按 utf-8/ignore 读出怪字符）；html 要**去标签取可见正文**；quote 归一是 NFKC + 去 Markdown 噪声 + 去全部空白 + casefold。三样我们都做了简化处理。
- **根因**：`loader.decode_bytes()`、html 分支、`docfacts.verbatim()`。
- **修复**：三处与核对规则对齐；KB-061 源码入库问题随 D17/D18 一并解决。
- **回归测试**：`tests/test_doc_pipeline.py::TestDecodeBytes`、`TestHtmlDocument`、`TestVerbatim`。

## D19 规划器把"追问"当"无上文的追问"拒掉（与 D9 合并修复）

- **现象**：与 D9 同源，单列是因为它还有一个独立触发点：`E.looks_like_follow_up` 的判定依赖"会话里有历史"，而 `plan()` 拿不到历史时把合法追问全部拒成 clarify。
- **根因 / 修复**：同 D9，随 `plan(question, history)` 补传一并解决。
- **回归测试**：同 D9。

---

## D20 完整疑问句被误判为追问（由自命题题库抓到）

- **现象**：自命题题 `X-F1"这个月猪肉进价多少钱？"`（10 字、指示词开头）期望 refusal，实际 `clarify`（"这句像是追问，但这个会话里没有上文"）。
- **假设**：`looks_like_follow_up` 的 `^(它|他们|这家|那家|这个|那个|同期|同比)` 分支只看开头指示词 + 长度 ≤12，"这个月"开头的完整疑问句被当成省略上文的指代。
- **验证**：`looks_like_follow_up("这个月猪肉进价多少钱？")` 返回 True；"那 7 月呢？"等真追问也 True——判定过宽。
- **根因**：`entities.py looks_like_follow_up()` 缺少"完整疑问结构"的排除；顺带发现 `CANNOT_KNOW` 词表缺外部价格类词（"进价/成本价/批发价"），即便不误判追问，该问题也会走 price 意图拿数据库数字硬答。
- **修复**：含"多少/几/怎么/为什么/多久/哪些"等完整疑问结构的句子不判追问；`CANNOT_KNOW` 补外部价格词，使其正确落入越界拒答。
- **回归测试**：`tests/test_tokenizer_planner.py::TestFollowUpMisclassification`（先红后绿，含"那 7 月呢？"不回归锚点）。

## D21 检索结果的 doc_id 与 chunk_id 来自不同文档

- **现象 / 验证**：构造两篇得分不同的文档后，返回项的 `doc_id` 会被候选顺序覆盖，但 `chunk_id`、正文和分数仍来自原命中，引用身份不一致。
- **根因**：`retriever.py` 在组装 hit 时按 `ordered[len(hits)]` 二次写入 `doc_id`。
- **修复**：保留 BM25 命中的原始文档身份；新增断言逐项核对 `doc_id == chunk_id` 前缀。
- **回归测试**：`tests/test_audit_regressions.py::test_retrieval_keeps_doc_and_chunk_identity`。

## D22 top_k 补位把零相关结果标成可回答

- **现象 / 验证**：无关查询或 `top_k` 大于合格候选数时，接口为凑数量返回零分文档，后续链路会把它们当证据。
- **根因**：检索结果没有区分“契约补位”和“可回答命中”，`ranked` 也包含补位项。
- **修复**：结果增加 `answerable`；零分、过期或元数据不合格项仅在 API 契约要求补足数量时置尾返回，不进入作答候选。
- **回归测试**：`test_irrelevant_padding_is_not_answerable`、`test_large_top_k_keeps_padding_out_of_ranked`。

## D23 实体编号中的数字污染月份解析

- **现象 / 验证**：`S02 6月净营业额` 被正则读成 26 月，最终扩成全数据期。
- **根因**：月份正则跨过实体编号末尾数字匹配 `02 6月`。
- **修复**：解析日期前先移除规范实体编号，月份只从剩余自然语言中提取。
- **回归测试**：`test_entity_id_does_not_pollute_month_parsing`。

## D24 mock 工具调用和异常没有形成可审计 trace

- **现象 / 验证**：mock 模式只记录计划，不记录工具参数、结果和耗时；工具抛异常时 `/api/chat` 可能直接 500。
- **根因**：trace 只在 live 编排器的局部路径写入，工具层没有请求级上下文。
- **修复**：用 `ContextVar` 绑定当前请求 trace；mock/live 工具统一记录名称、完整参数、结果、耗时；普通异常转结构化 refusal 并在 trace 留下错误。
- **回归测试**：`test_mock_trace_contains_tool_inputs_and_results`、`test_chat_fails_closed_on_unexpected_tool_error`。

## D25 每日序列和零营业日证据体积无上限

- **现象 / 验证**：趋势与异常问题会把整段每日明细写入 `data_evidence`，长区间可轻易越过响应上限。
- **根因**：`daily_metrics` 没有行数限制，零营业日分析还复用了完整每日序列。
- **修复**：趋势默认只返回 7 行并附 `days_total`；零营业日使用只返回日期的紧凑工具。
- **回归测试**：`test_daily_answer_uses_compact_evidence`、`test_zero_revenue_evidence_is_compact`。

## D26 响应契约只写在评测器里，服务端不 fail closed

- **现象 / 验证**：超长 answer、数字洪泛、过量引用和超大 evidence 在服务端都可直接发布。
- **根因**：`service.py` 在保存会话前没有统一输出闸门。
- **修复**：新增 `contracts.enforce_limits()`，统一限制回答长度、互异数字数、引用数、单条 quote、证据项数量和序列化字节数；越界返回结构化 refusal。
- **回归测试**：`test_response_limits_fail_closed`。

## D27 LLM trace 截断原始请求/响应，重试预算按估算扣减

- **现象 / 验证**：trace 只保留提示词预览和响应片段，无法复盘；慢失败的真实耗时与固定扣减不同，可能超预算。
- **根因**：`llm.py` 主动切片日志，并按常量估算重试耗时。
- **修复**：trace 保存完整请求与原始响应；每轮用单调时钟的实际耗时更新剩余预算。
- **回归测试**：`test_llm_trace_keeps_full_request_and_response`。

## D28 live 模式直接发布模型文字，可能交换指标或伪造引用

- **现象 / 验证**：假模型故意把营业额和订单数对调、复述用户输入数字、返回矛盾 citation 时，旧实现会原样发布。
- **根因**：工具虽提供了真实结果，最终 answer/citations/data_evidence 仍由模型自由生成。
- **修复**：模型负责选工具和表达意图，最终对外字段由请求级本地 Answerer 根据已验证工具结果、检索身份与可回答标记渲染，再过统一响应闸门。
- **回归测试**：`test_live_answer_is_grounded_in_tool_results`、`test_live_citations_cannot_be_fabricated`。

## D29 前端“近 7 天”实际选择 8 天且受本地时区影响

- **现象 / 验证**：数据截止 8 月 31 日时按钮给出 8 月 24–31 日，共 8 天；日期字符串经本地时区换算可能偏一天。
- **根因**：起点减了 7 天，且日期辅助函数混用本地时间。
- **修复**：闭区间起点减 6 天，所有预设用 UTC 计算和格式化。
- **回归测试**：浏览器点击验收确认 `2026-08-25` 至 `2026-08-31`。

## D30 跨文档线索无法追到被引用资料

- **现象 / 验证**：中文问题能命中供应商事件汇总，但英文结算邮件因词面不同进不了 top5，导致 C04/R04 丢 3 分。
- **根因**：索引把文档间明确的 `KB-xxx` 引用当普通文本，没有利用知识库已有链接。
- **修复**：对直接命中文档中出现的 KB 编号做一次低权重关联扩展；不增加公开题关键词、文档号白名单或答案数字。自定义供应商题保持满分。
- **回归测试**：`test_cross_document_reference_expansion_is_generic`；公开题库 C04/R04 转绿。

## D31 trace 面板隐藏了关键诊断信息

- **现象 / 验证**：页面只显示固定耗时和摘要，无法查看模型请求、原始响应、工具参数与错误。
- **根因**：前端把后端 trace 压成单行文本。
- **修复**：改为可展开步骤，显示真实耗时及完整结构化详情；错误单独标注。
- **回归测试**：浏览器验收确认 mock 工具查询的参数、结果与 4.2ms 总耗时均可展开查看。

---

## 修复后的整链路验证

- 单元与接口回归：`make test`，109 个用例全部通过。
- 公开题库：100 / 100（55/55 全绿）；自命题题库：18 / 18（10/10 全绿）。
- 接入预检：`eval/llm_gateway.py preflight` 14/14 通过，32 次问答覆盖空响应、坏参数、内容过滤、鉴权、限流、5xx、慢响应和 120 秒无响应。
- 浏览器验收：日期预设、实体月份查询和 trace 展开均通过。
- 红测试提交：`5b89464`；修复提交：`2f91dbf`。
