# 极客优选后端

这是“极客优选智能数码电商客服”的第一阶段后端。当前使用 FastAPI、SQLAlchemy、
Alembic、LangChain、LangGraph、真实大模型和模拟业务数据，跑通可持久化的商品咨询流程。

## 当前进度

已经完成：

- FastAPI 应用入口、健康检查和商品查询接口。
- 商品数据格式校验、数据库读取、筛选、排序和分页；固定 JSON 只负责首次导入。
- SQLite、SQLAlchemy 和 Alembic 数据库基础，以及商品、会话、消息、订单和物流迁移。
- 订单、订单明细和物流节点表，以及按买家隔离的列表、详情和物流查询。
- `list_orders`、`get_order` 和 `get_logistics` 三个 AI 工具；买家身份由服务端会话配置注入。
- 售后草稿、买家确认、幂等提交、取消、待审队列、人工审核和完整操作记录。
- `list_after_sales`、`get_after_sale` 和 `prepare_after_sale_draft` 三个售后工具；草稿工具只返回待确认 artifact，不写入数据库。
- `search_products` 和 `get_product` 两个 AI 商品工具。
- Markdown 规则文档、章节切分、内容哈希增量索引、Milvus 向量召回、关键词混合检索和 `search_knowledge` RAG 工具。
- 默认使用本地 `BAAI/bge-small-zh-v1.5` 为知识文档和 Query 生成真实语义向量，同时保留特征哈希测试实现与 OpenAI Embedding Provider。
- 基于本地中文 FastEmbed 模型的 Query 语义路由，包含文本规范化、多轮上下文补全、意图识别、实体提取和业务 Query 整理。
- 索引重建仅为新增或内容变化的片段生成向量；未变化片段直接复用已有向量。
- OpenAI 与 OpenAI 兼容接口的模型客户端。
- LangGraph `preprocess → model → tools` 客服运行图、工具执行、错误隔离和循环次数限制。
- 面向数码商品售前咨询的中文系统提示词。
- 会话、消息、聊天请求、聊天响应和运行统计的数据格式。
- 数据库会话仓库和会话服务，可在服务重启后恢复会话并继续调用 LangGraph 客服运行时。
- `/api/chat`、`/api/chat/stream`、会话列表和会话详情接口，以及稳定的业务错误状态码。
- LangGraph 模型文本、业务工具进度和最终结果的 SSE 流式协议。
- 商品、模型、LangGraph 和会话模块的自动化测试。

当前边界：LangGraph 运行时已经可以完成真实模型与商品、订单、售后及知识检索工具的调用闭环，
业务数据和知识正文由 SQLite 持久保存，知识向量由 Milvus 持久保存；前端可以实时显示模型文本、检索来源和工具进度。
尚未实现 MCP；人工接管、回复与状态切换已持久化。

## 当前调用链

商品接口调用链：

```text
HTTP 请求
  → FastAPI 商品路由
  → ProductService 业务筛选
  → SqlProductRepository / SQLAlchemy
  → SQLite 商品表
  → Pydantic 校验响应
```

AI 商品咨询调用链：

```text
用户消息
  → CustomerServiceRuntime
  → LangGraph preprocess 节点规范化 Query 并识别意图
  → LangGraph model 节点
  → ModelClient 调用聊天模型
  → 模型请求业务工具时进入 tools 节点
  → ProductService / ProductRepository
  → 工具结果返回 model 节点
  → 模型生成最终客服回复
```

会话服务调用链：

```text
聊天请求
  → ConversationService 校验会话归属与状态
  → 转换已保存的历史消息
  → CustomerServiceRuntime 调用 LangGraph
  → 成功后在数据库事务中保存用户消息和 AI 回复
  → 返回会话编号、两条消息和运行统计
```

LangGraph 当前结构：

```text
START → preprocess → model ──无工具调用──→ END
                         │
                         └──有工具调用──→ tools ──→ model
```

订单查询调用链：

```text
用户消息 → 会话服务注入 buyer_id → LangGraph 订单工具
  → OrderService 按订单号和 buyer_id 同时查询
  → SQLAlchemy 读取订单、明细和物流节点
  → 模型根据工具结果组织回复
```

模型无法提供或覆盖 `buyer_id`。订单不存在和其他买家的订单都会返回同一个不可查询结果。

## 文件职责

```text
backend/
├─ .env.example
│  环境变量模板，列出应用名称、模型服务、密钥、超时和重试配置；不存放真实密钥。
├─ README.md
│  后端进度、架构、文件职责、启动方式和阶段边界说明。
├─ pyproject.toml
│  Python 项目元数据、运行依赖、开发依赖和 pytest 路径配置。
├─ uv.lock
│  uv 生成的依赖锁文件，用于在不同环境安装一致的依赖版本。
├─ alembic.ini
│  Alembic 迁移配置，可手动升级、回退或检查数据库结构。
├─ alembic/
│  数据库迁移环境和版本文件；依次创建商品、会话、订单和物流相关表。
│
├─ app/
│  ├─ __init__.py
│  │  标记 app 为 Python 包。
│  ├─ main.py
│  │  创建 FastAPI 应用、启动时升级数据库并导入演示商品，再挂载 /api 总路由。
│  ├─ db/
│  │  保存 SQLAlchemy 基类、数据库引擎、Session 工厂和迁移初始化流程。
│  │
│  ├─ core/
│  │  ├─ __init__.py
│  │  │  标记 core 为 Python 包。
│  │  └─ config.py
│  │     使用 pydantic-settings 从 .env 读取应用和模型配置；用 SecretStr 隐藏密钥，
│  │     并限制温度、超时和重试次数的有效范围。
│  │
│  ├─ api/
│  │  ├─ __init__.py
│  │  │  标记 api 为 Python 包。
│  │  ├─ router.py
│  │  │  汇总业务路由并提供 GET /api/health 健康检查。
│  │  └─ stream.py
│  │     把 Pydantic 事件数据编码为标准 SSE 文本块。
│  │
│  ├─ ai/
│  │  ├─ __init__.py
│  │  │  标记 ai 为 Python 包。
│  │  ├─ model_client.py
│  │  │  创建 ChatOpenAI 客户端并绑定商品、订单和物流工具；支持 OpenAI 和 OpenAI 兼容地址，
│  │  │  提供同步、异步、同步流式和异步流式调用，并统一包装模型服务异常。
│  │  ├─ query_preprocessor.py
│  │  │  规范化 Query，在需要时补充最近一轮上下文，使用本地中文 Embedding 匹配意图路由，
│  │  │  提取订单号、预算、商品类别和售后类型，并生成供模型参考的业务 Query。
│  │  ├─ runtime.py
│  │  │  使用 LangGraph 构建 preprocess、model 与 tools 节点；保存路由结果、消息和调用统计，根据 tool_calls
│  │  │  决定继续查询或结束；异步流式运行时转发模型片段与工具进度，并限制连续
│  │  │  工具轮数、隔离工具错误和校验对话输入。
│  │  ├─ prompts/
│  │  │  └─ customer_service.md
│  │  │     客服系统提示词，要求商品事实来自工具、信息不足时追问，并禁止编造。
│  │  └─ tools/
│  │     ├─ __init__.py
│  │     │  标记 AI 工具目录为 Python 包。
│  │     ├─ catalog.py
│  │        定义商品搜索和详情工具的参数、结构化结果及错误格式，调用 ProductService
│  │        获取可信商品数据，并控制一次返回给模型的商品数量。
│  │     └─ orders.py
│  │        定义订单列表、详情和物流工具，从 LangGraph 运行配置读取可信买家身份。
│  │
│  └─ modules/
│     ├─ __init__.py
│     │  标记业务模块目录为 Python 包。
│     ├─ catalog/
│     │  ├─ __init__.py
│     │  │  标记商品模块为 Python 包。
│     │  ├─ models.py
│     │  │  定义 products 数据表及字段、索引和 JSON 规格列。
│     │  ├─ schemas.py
│     │  │  定义 Product、商品分类、搜索条件和列表响应等 Pydantic 数据格式，
│     │  │  校验商品编号、必填文本、价格、库存、分页和价格区间。
│     │  ├─ repository.py
│     │  │  ProductRepository 校验 JSON 种子；SqlProductRepository 负责运行时数据库查询。
│     │  ├─ service.py
│     │  │  实现关键词、分类、价格、库存筛选，价格排序、分页和商品不存在异常；
│     │  │  同时供 HTTP 路由与 AI 工具复用。
│     │  └─ router.py
│     │     提供 GET /api/products 和 GET /api/products/{product_id}，通过 FastAPI
│     │     依赖注入复用商品服务，并把不存在的商品转换为 404 响应。
│     ├─ conversations/
│        ├─ __init__.py
│        │  标记会话模块为 Python 包。
│        ├─ models.py
│        │  定义 conversations 和 messages 数据表、外键、级联删除与查询索引。
│        ├─ schemas.py
│        │  定义会话模式、消息角色、会话快照、聊天请求、聊天响应和 LangGraph
│        │  调用统计，以及 SSE 开始、状态、文本片段和错误数据格式。
│        ├─ repository.py
│        │  保留隔离测试使用的内存仓库，并提供生产运行使用的 SQLAlchemy 会话仓库；
│        │  数据库更新在事务内完成，支持重启后恢复和跨仓库实例读取。
│        ├─ service.py
│        │  创建或继续会话，校验买家归属和 AI 服务状态，把历史消息转换为模型消息，
│        │  以普通或流式方式调用 LangGraph，并在成功后原子保存用户消息与 AI 回复；
│        │  同一会话串行处理，中断或失败时不保存不完整消息。
│        └─ router.py
│           提供普通聊天、SSE 聊天、会话列表和会话详情接口，并统一转换业务异常。
│     ├─ orders/
│        ├─ models.py / schemas.py
│        │  定义订单、订单明细、物流节点表和稳定的请求响应格式。
│        ├─ repository.py / service.py
│        │  按服务端买家身份读取订单并执行统一的不可查询规则。
│        └─ router.py
│           提供订单列表、详情和物流接口。
│     └─ after_sales/
│        定义售后状态和请求格式，实现带版本约束的数据库仓库、业务服务和 HTTP 接口。
│
├─ data/
│  ├─ app.db
│  │  本地 SQLite 数据库，首次启动自动创建，不提交到 Git。
│  ├─ intents/routes.json
│  │  商品、订单、售后、知识问答和人工服务的语义路由样本。
│  └─ seed/
│     ├─ products.json
│     │  12 件模拟数码商品数据，编号为 p01 至 p12，覆盖耳机、充电器和扩展坞。
│     └─ orders.json
│        5 笔模拟订单及物流节点，覆盖两位买家、越权查询和暂无物流场景。
│
└─ tests/
   ├─ __init__.py
   │  标记测试目录为 Python 包。
   ├─ test_health.py
   │  验证 FastAPI 健康检查接口。
   ├─ test_catalog_schemas.py
   │  验证模拟商品、搜索条件和商品列表响应的数据约束。
   ├─ test_catalog_repository.py
   │  验证 JSON 读取、缓存、副本隔离及各种损坏数据错误。
   ├─ test_catalog_service.py
   │  验证商品组合筛选、排序、分页和详情查询业务规则。
   ├─ test_catalog_api.py
   │  验证商品 HTTP 接口的成功响应、查询参数校验、404 和 422 响应。
   ├─ test_catalog_tools.py
   │  验证两个 AI 商品工具的名称、参数校验、结构化结果和 JSON 序列化。
   ├─ test_conversation_schemas.py
   │  验证会话、消息、聊天请求与响应的内容、角色、时间线和 JSON 输出约束。
   ├─ test_conversation_repository.py
   │  验证会话新增、更新、副本隔离、买家筛选、更新时间排序和清空行为。
   ├─ test_database_repositories.py
   │  验证 SQLAlchemy 商品读取、会话持久化、跨实例恢复和重复编号约束。
   ├─ test_order_api.py
   │  验证订单列表、详情、物流、输入校验以及不存在与越权的统一错误。
   ├─ test_order_tools.py
   │  验证订单工具使用服务端买家身份，且不会向模型开放 buyer_id 参数。
   ├─ test_after_sales_api.py
   │  验证草稿修改取消、幂等提交、防重复、版本冲突、买家隔离和人工审核。
   ├─ test_conversation_service.py
   │  验证会话创建与延续、历史转换、访问隔离、失败不落盘、延迟创建运行时和并发串行。
   ├─ test_conversation_api.py
   │  验证聊天和会话查询接口、连续对话、买家隔离、请求校验及业务错误状态码。
   ├─ test_model_client.py
   │  使用模型替身验证客户端配置、工具绑定、同步异步调用、流式调用和异常保护，
   │  测试过程不会请求真实模型。
   ├─ test_query_preprocessor.py
   │  验证文本规范化、上下文补全、向量路由、低分回退、实体提取和 Query 整理。
   └─ test_runtime.py
      使用模型替身和真实商品工具验证 LangGraph 节点、路由、多工具调用、错误处理、
      历史统计、异步执行、输入校验和循环限制。
```

本地使用的 `backend/.env` 不会提交到 Git。它保存真实模型配置，职责与
`.env.example` 相同，但包含开发机器自己的值。

## 关键文件之间的分工

`model_client.py` 只负责“怎样连接和调用模型”：读取已经校验过的配置、创建模型
对象、绑定可用工具，并提供稳定的调用和错误边界。它不决定何时执行工具。

`query_preprocessor.py` 负责“用户 Query 进入模型前怎样理解”：保留原文，完成轻量清洗、
上下文补全、语义意图路由、实体提取和业务 Query 整理。路由结果只辅助模型，不会屏蔽其他工具。

知识模块中的 `repository.py` 保存文档正文、来源、内容哈希和 Embedding 模型名；
`vector_store.py` 通过 PyMilvus 创建集合、写入向量并执行 COSINE Top-K 搜索；`indexer.py`
只为新增、修改或向量缺失的片段重新生成向量；`service.py` 合并 Milvus 向量分数与关键词分数。

`runtime.py` 负责“预处理、模型和工具怎样协作”：把系统提示词、Query 分析和对话交给模型，检查模型的
`tool_calls`，通过 LangGraph `ToolNode` 执行商品、订单或物流工具，再把工具结果送回模型，直到
得到最终回复。

`catalog.py`、`service.py` 和 `repository.py` 依次负责“模型可调用的工具格式”、
“商品业务规则”和“商品数据读取”。这种分层让网页接口和 AI 客服使用同一套商品
规则，避免两边返回不同价格或库存。

## 已有接口

- `GET /api/health`：检查 FastAPI 服务是否运行。
- `GET /api/products`：按关键词、分类、价格、库存、排序和分页查询商品。
- `GET /api/products/{product_id}`：按商品编号查询详情。
- `GET /api/orders?buyer_id=A`：查询当前买家的订单列表。
- `GET /api/orders/{order_id}?buyer_id=A`：查询当前买家的订单详情。
- `GET /api/orders/{order_id}/logistics?buyer_id=A`：查询当前买家的物流状态和节点。
- `POST /api/after-sales/drafts`：校验订单并创建尚未提交的售后草稿。
- `PUT /api/after-sales/drafts/{request_id}`：按版本修改草稿。
- `POST /api/after-sales/drafts/{request_id}/submit`：携带 `Idempotency-Key` 确认提交。
- `POST /api/after-sales/drafts/{request_id}/cancel`：取消草稿，不进入审核。
- `GET /api/after-sales?buyer_id=A`：查询买家的申请及操作记录。
- `GET /api/staff/after-sales/pending`：查询客服待审队列。
- `POST /api/staff/after-sales/{request_id}/review`：按版本批准或拒绝申请。
- `POST /api/chat`：创建新会话或在已有会话中发送消息并获得 AI 回复。
- `POST /api/chat/stream`：通过 SSE 返回开始、运行状态、文本片段、完成或错误事件。
- `POST /api/auth/login`：校验数据库中的 Argon2 密码哈希，签发访问令牌和刷新令牌。
- `POST /api/auth/refresh`：轮换一次性刷新令牌并签发新的令牌对。
- `POST /api/auth/logout`：撤销刷新令牌。
- `GET /api/auth/me`：读取当前登录身份。
- `GET /api/conversations`：按令牌中的买家身份查询会话列表。
- `GET /api/conversations/{conversation_id}`：按令牌身份查询完整会话和消息历史。
- `GET /api/conversation-events?buyer_id=A&access_token=...`：通过 SSE 主动推送会话快照。
- `GET /api/staff/conversation-events?access_token=...`：验证客服身份后推送全部工作台会话。
- `GET /api/knowledge/search?query=...&category=...`：登录后调试知识召回结果。
- `GET /api/staff/knowledge/status`：客服查看索引数量、向量数量和模型。
- `POST /api/staff/knowledge/reindex`：客服按内容哈希增量重建知识索引。
- `POST /api/staff/knowledge/evaluate`：运行固定检索评测；增加 `?include_answers=true` 后还会调用聊天模型评估回答质量。
- `POST /api/staff/knowledge/evaluate/comparison`：在同一批案例上比较关键词、向量、混合和混合重排检索。
- `GET /docs`：打开 FastAPI 交互式接口文档。

前端普通对话、商品咨询、订单、物流、售后和人工服务均调用后端接口。执行时间线中的
Router 结果来自后端 SSE，不再由浏览器关键词规则生成。

## 模型配置

在 `backend` 目录把 `.env.example` 复制为 `.env`，再填写自己的模型配置：

```dotenv
MODEL_PROVIDER=openai
MODEL_NAME=你的模型名称
MODEL_API_KEY=你的服务端密钥
MODEL_BASE_URL=
```

使用 OpenAI 官方接口时可以留空 `MODEL_BASE_URL`。使用 OpenAI 兼容服务时，建议
把 `MODEL_PROVIDER` 设置为 `openai-compatible`，并填写服务商提供的 API 根地址。
真实密钥只能保存在后端 `.env` 或正式环境的密钥管理服务中。

模型客户端只在显式创建时检查模型名称和密钥。因此即使尚未配置模型，健康检查和
商品接口也可以正常启动。

意图识别默认使用 `BAAI/bge-small-zh-v1.5` 本地 ONNX Embedding。首次聊天会下载约
90 MB 模型到 `.local/fastembed`，后续直接复用缓存，不调用聊天模型；下载或加载失败时会
降级到离线特征哈希路由。阈值可通过 `INTENT_ROUTE_MIN_SCORE`、
`INTENT_ROUTE_MIN_MARGIN` 调整。

RAG 知识索引也默认使用同一个 `BAAI/bge-small-zh-v1.5`，文档通过
`passage_embed` 编码，查询通过 `query_embed` 编码，生成的 512 维向量写入 Milvus。
模型或向量维度变化时，索引器会重建 Milvus Collection，并根据 `embedding_model`
重新生成全部知识向量。特征哈希仅供自动化测试或显式降级使用。
当前 BGE 混合检索最低分默认为 `0.32`，可通过 `KNOWLEDGE_MIN_SCORE` 调整；该阈值需要随
知识规模和独立评测集继续校准。

## RAG 检索与回答评测

`data/evaluation/rag_cases.json` 中的每条案例包含用户问题、正确知识来源和人工参考答案。
评测直接使用当前环境的 BGE 和 Milvus，不另外模拟检索。完整模式会使用当前聊天模型生成
回答，再以结构化评审提示词计算正确性、忠实度和完整性。模型评分适合发现回归和比较版本，
重要结论仍需抽样人工复核。

先启动 Milvus，并确认 `.env` 中已经配置聊天模型，然后在 `backend` 目录运行：

```powershell
# 只运行真实检索评测，不消耗聊天模型额度
.\.venv\Scripts\python.exe -m scripts.evaluate_rag --retrieval-only

# 运行检索与回答完整评测
.\.venv\Scripts\python.exe -m scripts.evaluate_rag

# 运行四种检索方案对比；首次运行会下载约1GB的BGE重排模型
.\.venv\Scripts\python.exe -m scripts.evaluate_rag --compare --retrieval-only
```

结果写入 `data/evaluation/results/evaluation_results.jsonl` 和
`data/evaluation/results/evaluation_report.md`。前者保存每条问题的召回来源、生成答案、引用、
评分和错误归因；后者汇总 Recall@K、MRR、拒答准确率、P50/P95 延迟、回答质量和引用准确率。
生成结果属于本地运行产物，不提交到 Git。

完整评测会计算 `Recall@1/3/5`、MRR、无答案错误召回率、P50/P95延迟、回答正确性、
忠实度、完整性、拒答准确率和引用支持度。检索失败时，评测器会再把人工标注的正确知识块
直接交给模型：正确知识块下能够回答则归为检索错误，仍不能回答则归为知识库或标注错误。

JDDC 500条数据必须人工对应当前知识库后才能用于项目RAG指标。标注与划分命令如下：

```powershell
# 已生成一次标注模板；仅在文件不存在时执行
.\.venv\Scripts\python.exe -m scripts.prepare_rag_evaluation initialize

# 人工填写并把 review_status 改成 reviewed 后，生成300/100/100分层数据
.\.venv\Scripts\python.exe -m scripts.prepare_rag_evaluation build
```

标注模板位于 `data/evaluation/rag_annotations_500.jsonl`。构建脚本会拒绝未复核、可回答但
缺少来源、不可回答却填写来源或缺少参考答案的数据，避免自动主题映射污染最终指标。

## 数据库与迁移

本地默认使用 `backend/data/app.db`。首次启动后端时会自动执行全部 Alembic 迁移；
当对应表为空时，再从 `data/seed/products.json` 和 `data/seed/orders.json` 导入演示商品、
订单及物流节点。后续启动不会覆盖数据库中已有的数据。

也可以手动管理数据库版本：

```powershell
cd backend
uv run --no-cache alembic upgrade head
uv run --no-cache alembic check
uv run --no-cache alembic downgrade -1
```

`upgrade head` 升级到最新版，`check` 检查 SQLAlchemy 模型是否遗漏迁移，
`downgrade -1` 回退一个版本。回退会删除对应版本的数据表，只应用于明确需要重建的开发数据库。

部署到 PostgreSQL 时，在 `.env` 中修改 `DATABASE_URL`，业务 Service 和 Router 无需重写。

Alembic `20260921_07` 会删除 SQLite 中旧的 `embedding_json` 列。知识向量迁移到 Milvus 后，
SQLite 只保存正文和索引元数据。应用启动或客服调用重建接口时，会根据内容哈希重新生成并
写入缺失的 Milvus 向量。

## Milvus 向量数据库

Windows 原生环境不能运行 Milvus Lite，本项目使用官方 Milvus 2.6 Standalone Docker
Compose。安装并启动 Docker Desktop 后运行：

```powershell
cd backend\infrastructure\milvus
docker compose up -d
docker compose ps
```

Milvus 默认监听 `http://127.0.0.1:19530`，WebUI 位于
`http://127.0.0.1:9091/webui/`。连接配置放在 `backend/.env`：

```dotenv
MILVUS_URI=http://127.0.0.1:19530
MILVUS_TOKEN=
MILVUS_DATABASE=default
MILVUS_COLLECTION=knowledge_chunks
MILVUS_TIMEOUT_SECONDS=10
```

启动后端时会自动建立集合并同步知识索引。也可以登录客服账号后调用
`POST /api/staff/knowledge/reindex` 手动重建。连接 Milvus 集群或 Zilliz Cloud 时只需替换
URI、Token 和数据库名，不需要修改检索代码。停止本地服务使用 `docker compose down`；
向量数据保存在 `infrastructure/milvus/volumes`，不会提交到 Git。

## 本地启动

```powershell
cd backend
uv sync --dev --no-cache
uv run --no-cache uvicorn app.main:app --reload
```

这里使用 `--no-cache`，以兼容 Python、项目和 uv 缓存位于不同磁盘的 Windows
开发环境。服务默认运行在 `http://127.0.0.1:8000`。

## 测试

```powershell
cd backend
uv run --no-cache pytest -q
```

当前共有 165 项后端测试。自动化测试使用模型替身、内存向量库和隔离数据库，不会连接 Milvus，
也不会消耗模型额度。真实模型与 Milvus 闭环需要本地 `.env` 配置，并应作为单独的手动集成测试运行。

## 下一步

下一阶段应依次完成：

1. 把浏览器会话令牌迁移到 HttpOnly Cookie。
2. 为 SSE 增加刷新后的自动重连、断线恢复游标和生产环境代理配置。
3. 增加登录限流、认证审计和签名密钥轮换机制。

Milvus 混合检索形成评测基线后，再按产品设计接入 MCP 和 Multi-Agent。
