# LLM 接入说明

按 `docs/API_CONTRACT.md` §7.4 的骨架编写。评审方照本说明把服务切到 DeepSeek
`deepseek-flash`，**不需要改任何代码，只改环境变量**。

## 1. 用了什么

- 协议：OpenAI 兼容 Chat Completions，只调 `POST {LLM_BASE_URL}/chat/completions`，
  地址**原样拼接**（不补 `/v1`、不截路径），带路径前缀的代理可直接使用。
- HTTP 客户端：`httpx`（`starter/requirements.txt` 已含），无任何模型厂商 SDK。
- 开发阶段未配置真实 Key：所有功能在本地降级模式下开发与自测；
  live 链路由 `eval/llm_gateway.py` 的假模型 + 预检覆盖（见第 7 节，14/14 通过）。
- 思考模式：**保持开启**（不发送 `thinking: disabled`）。理由：规划正确性优先于速度与费用；
  `reasoning_content` 按 §7.3 整条回传、绝不进回答（预检 P10 验证）。数字一律由代码从
  工具结果渲染，不依赖 `temperature=0`。

## 2. 配置从哪里读

| 变量 | 含义 | 默认值 | 读取位置 |
|---|---|---|---|
| `LLM_BASE_URL` | 模型服务地址 | 空（→ mock 模式） | 环境变量，`kbqa/config.py load_settings()` |
| `LLM_API_KEY` | 模型 Key | 空 | 环境变量 |
| `LLM_MODEL` | 模型名 | 空 | 环境变量 |
| `LLM_TIMEOUT` | 单次模型调用超时秒数 | 120 | 环境变量（可选，`kbqa/config.py`） |

- 三个变量**全部非空**即进入 `live` 模式，任一为空即 `mock`；`/api/health` 的
  `llm_mode` 字段实时反映。
- 不做启动时 Key 格式校验，不调用查余额/列模型等接口（§7.2 要求）。
- 单次调用超时 120 秒；`/api/chat` 总预算 180 秒，每次调用取"120 秒与剩余预算较小者"
  （`kbqa/llm.py LLMClient.chat_with_retry`）。
- Key 只存在于进程环境变量，不落盘、不入库（仓库内无任何 Key，`.gitignore` 排除
  `.env` 类文件）。

## 3. 怎么换成你们的

```bash
# 1) 设置三个环境变量（地址原样，含可能的路径前缀）
export LLM_BASE_URL=https://api.deepseek.com
export LLM_API_KEY=<你们的 Key>
export LLM_MODEL=deepseek-flash

# 2) 重启服务（不需要重建索引/清洗表，缓存键与 LLM 配置无关）
cd starter && make run        # 即 .venv/bin/python -m uvicorn kbqa.server:app --port 8000

# 3) 验证
curl -s http://localhost:8000/api/health | grep llm_mode   # 期望 "live"
```

就这三步：改三个值、重启服务。没有别的开关，没有配置文件要改。
若走带前缀的代理（§7.5），把 `LLM_BASE_URL` 设为 `https://host/prefix` 即可，
代码按 `{LLM_BASE_URL}/chat/completions` 原样拼接（预检 P1/P6 用带前缀地址验证过）。

## 4. 怎么看到发给模型的请求

两条路都可用，推荐第一条：

1. **代理（零配置）**：把服务指到仓库自带的网关代理上，完整请求（提示词、工具定义、
   每一轮消息）会打印到终端：

   ```bash
   python3 eval/llm_gateway.py proxy --upstream https://api.deepseek.com --port 9000
   export LLM_BASE_URL=http://127.0.0.1:9000/ds-gw
   # 以 proxy 启动后打印的完整 Base URL 为准；重启服务后，请求原文写入 llm_traffic.jsonl
   ```

2. **trace**：每次 `/api/chat` 的 `trace_id` 对应 `/api/trace/{trace_id}`，其中记录了
   逐轮工具调用与结果、完整 LLM 请求和原始响应（`llm` 步骤）。前端看板的
   AI 助手面板会直接展示这条时间线。

样例（proxy 输出，脱敏截断）：

```json
{"model": "deepseek-flash", "max_tokens": 4096,
 "messages": [{"role": "system", "content": "…经营问答…今天=2026-09-01…"},
              {"role": "user", "content": "618 当天 S02 的牛肉poke 卖了多少份…"}],
 "tools": [{"type": "function", "function": {"name": "query_metrics", …}}]}
```

## 5. 没有 Key 时会怎样

- 服务正常启动，`/api/health` 返回 `llm_mode: "mock"`。
- `/api/metrics/*`、`/api/retrieve`、`/api/data_quality`、看板前端：完全正常
  （纯本地实现，与大模型无关）。
- `/api/chat`：规划、检索、取数照常运行，由本地作答引擎渲染答案
  （`llm_mode=mock` 时预检 P8/P9/P11 验证：32 次问答全部 200、字段完整、从无空串）。
  模型不可用的场景一律返回结构化 `refusal`（带 `trace_id`），绝不 HTTP 500、绝不编数字。
- 评测分数：公开题库 100.0 就是无 Key 模式跑的（见 `EVAL_REPORT.md`）。

## 6. 依赖与安装

- `pip install -r starter/requirements.txt`（fastapi、uvicorn、httpx、pytest），
  无模型 SDK、无模型文件下载、无额外系统依赖。
- 首次启动：`make rebuild` 需 <1 秒（清洗 1.9 万行 + 35 篇文档索引）；服务冷启动 <3 秒。

## 7. 自测结果（eval/llm_gateway.py preflight）

环境：macOS（arm64）、Python 3.12.11、服务以预检注入的三个环境变量启动。
**14/14 全部通过**，逐项结论：

```text
P1    服务确实把请求发到了注入的 LLM_BASE_URL（含路径前缀）             通过  共观察到 60 次 POST /ds-gw/chat/completions。
P2    请求里的 model 等于注入的 LLM_MODEL                               通过  全部请求都用了 preflight-model-7f3a。
P3    注入的 Key 以 Authorization: Bearer 发送                          通过  全部请求都带了正确的 Bearer Key。
P4    只用了 DeepSeek 文档列出的顶层参数                                通过  只出现了 DeepSeek 文档列出的顶层参数。
P5    max_tokens 不设，或不小于 2048                                    通过  max_tokens 都不小于 2048。
P6    没有访问 {prefix}/chat/completions 之外的任何路径                 通过  只访问了 POST /ds-gw/chat/completions。
P7    工具定义规范，且每一个工具调用都以 role=tool + tool_call_id 回传  通过  工具定义规范，44 个工具调用的结果都正确回传了。
P8    每个场景下 /api/chat 都返回 HTTP 200 与字段完整的合法 JSON        通过  32 次问答全部返回 200 和字段完整的 JSON。
P9    模型不可用时给出结构化 refusal，answer 从不是空串                 通过  结构化 refusal 或有据可查的回答，answer 从不是空串。
P10   思考内容没有漏进 answer / citations / data_evidence               通过  32 次回答里，思考标记都没有出现在任何对外字段里。
P11   /api/chat 在时限内返回（含长时间无响应的场景）                    通过  最慢 120.03 秒，都在 180 秒以内。
P12   注入环境变量后 /api/health 报告 llm_mode = live                   通过  llm_mode = live。
P13   多轮工具调用之间 reasoning_content 原样回传（没有触发 400）       通过  18 次多轮请求都原样回传了 reasoning_content。
P14   保持连接的空行与 SSE 注释没有把服务弄坏                           通过  空行与 `: keep-alive` 注释被正确跳过，slow 场景照常回答。

预检通过：在 OpenAI 兼容这条路线上，我们能原样接上你的服务。
```

完整原始输出见 `docs/validation/final-preflight/preflight_report.md`。

## 8. 契约 §7.3 行为对照表

| 规则 | 本服务的处理 |
|---|---|
| `reasoning_content` 与 `content` 分离 | assistant 消息**整条**原样回传（不自选字段重组）；思考内容只进 trace，绝不进 answer/citations/data_evidence（P10/P13） |
| `max_tokens` | 固定 4096（≥2048，P5） |
| 异常 `finish_reason`（length/content_filter/insufficient_system_resource/aborted） | 一律抛 `LLMError`，`/api/chat` 返回结构化 refusal，真实原因写 trace（P9） |
| 空正文 / 无 tool_calls 且 content 为空 | 按 `empty_content` 错误处理（带 tool_calls 的空 content 正常放行） |
| 工具调用参数是 JSON 字符串 | 自行 `json.loads`，解析失败按 `bad_tool_args` 处理；结果以 `role:"tool"` + `tool_call_id` 回传，支持一次多个调用（P7） |
| 不用文档外参数 | 未使用 `seed`/`n`/`parallel_tool_calls` 等（P4） |
| 繁忙时空行 / `: keep-alive` 注释 | httpx 标准解析天然跳过（P14 显式验证） |
| 错误码 400/401/402/422/429/500/503 | 全部映射为结构化 refusal + trace 记录，可重试的做有限重试（P8/P9 覆盖 402/422/429/500/503 场景） |
| 180 秒总预算 | 每次调用超时 = min(120s, 剩余预算)；预算耗尽返回 refusal（P11：最慢 120.03s < 180s） |
