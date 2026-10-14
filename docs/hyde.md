# HyDE 知识检索

结构化指令与五个数码电商示范已保存在 `backend/app/ai/prompts/hyde.md`；资料来源、设计理由与离线评价标准见 [提示词研究记录](hyde-prompt-research.md)。

新增 `hyde` 策略：原问题 BM25 + 原问题向量 + 假设文档向量，三路候选独立过滤后通过 RRF 融合。假设文档通过 `embed_documents` 编码，原问题仍通过 `embed_query` 编码。分类、候选数量和向量相似度阈值沿用已有配置，不需要重建知识索引。

## 启用

在 `backend/.env` 设置并重启后端：

```dotenv
KNOWLEDGE_RETRIEVAL_STRATEGY=hyde
KNOWLEDGE_HYDE_TIMEOUT_SECONDS=10
KNOWLEDGE_HYDE_MAX_CHARS=600
```

默认值为 `hybrid`，方便先保留基线。HyDE 复用 `MODEL_*` 聊天模型配置，使用独立的无工具客户端，温度为 0、禁用重试，并设置单次模型请求超时。超时并非整个检索流程的硬截止时间。输出提示要求简短正文，返回文本另有字符上限；字符上限不是生成 Token 上限。

登录后可单独测试 `/api/knowledge/search?query=耳机拆封还能退吗&strategy=hyde`。聊天的 `search_knowledge` 工具采用环境变量默认策略。接口查询在工作线程执行，避免模型同步调用阻塞事件循环。

生成、假设文档编码或额外召回失败时，保留已完成的原问题混合检索，响应标记 `hyde_fallback`；原问题召回本身的 Milvus 故障仍按已有不可用行为处理。空问题、空知识库跳过生成。

假设文档不保存到知识库，不出现在 `answer_context`、`sources` 或引用中。仅真实知识块成为答案证据。日志只记录状态、耗时、实际 Token 和错误类型，不记录假设正文或异常详情。提示约束不能保证生成文档没有幻觉，因此不可以据此判断售后资格。

## 评测

在 `backend` 目录运行：

```powershell
uv run python -m scripts.evaluate_rag --retrieval-only --strategy hybrid
uv run python -m scripts.evaluate_rag --retrieval-only --strategy hyde
uv run python -m scripts.evaluate_rag --retrieval-only --compare --include-hyde
```

`--retrieval-only` 仅跳过最终回答和评审，HyDE 仍调用模型并产生费用。默认 `--compare` 保持原四方案，需要 `--include-hyde` 才加入第五方案。单方案和比较报告使用显式策略，不受聊天默认策略影响。

客服评测接口支持 `/api/staff/knowledge/evaluate?strategy=hyde` 和 `/api/staff/knowledge/evaluate/comparison?include_hyde=true`。案例结果单独记录 `hyde_status`、`hyde_latency_ms`、`hyde_usage`、`hyde_error_type`；召回延迟包含 HyDE 调用。HyDE Token 独立于最终回答及评审 Token，当前回答费用字段不包含 HyDE 费用。对照应检查回退数量，避免把回退样本当作成功使用 HyDE。

开发者执行记录显示检索策略、HyDE 状态、耗时、Token，以及真实知识块的 HyDE 分数与排名。检索候选不等于足以回答的证据，尤其需要检查不可回答问题的错误回答率。

尚未运行真实模型的全量效果对照，不宣称召回率或回答质量提升。当前 100 条数据仅含 4 条可回答问题，标签仍是候选，不足以可靠判断收益。

原始方法：[Precise Zero-Shot Dense Retrieval without Relevance Labels](https://aclanthology.org/2023.acl-long.99/)。本项目保留原问题双路召回，是 HyDE 与现有混合检索的组合。
