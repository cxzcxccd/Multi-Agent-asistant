# 极客优选后端

这是“极客优选智能数码电商客服”的第一阶段后端。当前目标是使用 FastAPI、
LangChain、LangGraph、真实大模型和本地模拟商品数据，跑通可信的商品咨询流程。

## 当前进度

已经完成：

- FastAPI 应用入口、健康检查和商品查询接口。
- 商品数据格式校验、本地 JSON 读取、缓存、筛选、排序和分页。
- `search_products` 和 `get_product` 两个 AI 商品工具。
- OpenAI 与 OpenAI 兼容接口的模型客户端。
- LangGraph 客服运行图、工具执行、错误隔离和循环次数限制。
- 面向数码商品售前咨询的中文系统提示词。
- 商品模块、模型客户端和 LangGraph 运行时的自动化测试。

当前边界：LangGraph 运行时已经可以在 Python 中完成真实模型与商品工具的调用
闭环，但还没有对外提供 `/api/chat` 接口。会话目录和流式协议文件目前是占位，
尚未实现数据库持久化、前端聊天接入、MCP、RAG、订单和售后能力。

## 当前调用链

商品接口调用链：

```text
HTTP 请求
  → FastAPI 商品路由
  → ProductService 业务筛选
  → ProductRepository 读取并缓存 JSON
  → Pydantic 校验响应
```

AI 商品咨询调用链：

```text
用户消息
  → CustomerServiceRuntime
  → LangGraph model 节点
  → ModelClient 调用聊天模型
  → 模型请求商品工具时进入 tools 节点
  → ProductService / ProductRepository
  → 工具结果返回 model 节点
  → 模型生成最终客服回复
```

LangGraph 当前结构：

```text
START → model ──无工具调用──→ END
          │
          └──有工具调用──→ tools ──→ model
```

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
│
├─ app/
│  ├─ __init__.py
│  │  标记 app 为 Python 包。
│  ├─ main.py
│  │  创建 FastAPI 应用、设置名称和版本，并挂载 /api 总路由。
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
│  │     为后续聊天流式输出预留的 SSE 公共协议文件，目前没有具体实现。
│  │
│  ├─ ai/
│  │  ├─ __init__.py
│  │  │  标记 ai 为 Python 包。
│  │  ├─ model_client.py
│  │  │  创建 ChatOpenAI 客户端并绑定商品工具；支持 OpenAI 和 OpenAI 兼容地址，
│  │  │  提供同步、异步、同步流式和异步流式调用，并统一包装模型服务异常。
│  │  ├─ runtime.py
│  │  │  使用 LangGraph 构建 model 与 tools 节点；保存消息和调用统计，根据 tool_calls
│  │  │  决定继续查询或结束，并限制连续工具轮数、隔离工具错误和校验对话输入。
│  │  ├─ prompts/
│  │  │  └─ customer_service.md
│  │  │     客服系统提示词，要求商品事实来自工具、信息不足时追问，并禁止编造。
│  │  └─ tools/
│  │     ├─ __init__.py
│  │     │  标记 AI 工具目录为 Python 包。
│  │     └─ catalog.py
│  │        定义商品搜索和详情工具的参数、结构化结果及错误格式，调用 ProductService
│  │        获取可信商品数据，并控制一次返回给模型的商品数量。
│  │
│  └─ modules/
│     ├─ __init__.py
│     │  标记业务模块目录为 Python 包。
│     ├─ catalog/
│     │  ├─ __init__.py
│     │  │  标记商品模块为 Python 包。
│     │  ├─ models.py
│     │  │  为后续数据库商品表预留的持久化模型文件，目前没有具体实现。
│     │  ├─ schemas.py
│     │  │  定义 Product、商品分类、搜索条件和列表响应等 Pydantic 数据格式，
│     │  │  校验商品编号、必填文本、价格、库存、分页和价格区间。
│     │  ├─ repository.py
│     │  │  读取并校验 products.json，拒绝无效 JSON、重复编号和错误商品结构，
│     │  │  缓存只读快照，并支持按编号读取与手动清除缓存。
│     │  ├─ service.py
│     │  │  实现关键词、分类、价格、库存筛选，价格排序、分页和商品不存在异常；
│     │  │  同时供 HTTP 路由与 AI 工具复用。
│     │  └─ router.py
│     │     提供 GET /api/products 和 GET /api/products/{product_id}，通过 FastAPI
│     │     依赖注入复用商品服务，并把不存在的商品转换为 404 响应。
│     └─ conversations/
│        ├─ __init__.py
│        │  标记会话模块为 Python 包。
│        ├─ models.py
│        │  为后续数据库会话和消息表预留，目前没有具体实现。
│        ├─ schemas.py
│        │  为后续聊天请求、消息和响应格式预留，目前没有具体实现。
│        ├─ repository.py
│        │  为后续会话数据访问和持久化预留，目前没有具体实现。
│        ├─ service.py
│        │  为后续会话生命周期、历史消息和 AI 调用业务规则预留，目前没有具体实现。
│        └─ router.py
│           为后续会话与聊天 FastAPI 接口预留，目前尚未挂载到总路由。
│
├─ data/
│  └─ seed/
│     └─ products.json
│        12 件模拟数码商品数据，编号为 p01 至 p12，覆盖耳机、充电器和扩展坞。
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
   ├─ test_model_client.py
   │  使用模型替身验证客户端配置、工具绑定、同步异步调用、流式调用和异常保护，
   │  测试过程不会请求真实模型。
   └─ test_runtime.py
      使用模型替身和真实商品工具验证 LangGraph 节点、路由、多工具调用、错误处理、
      历史统计、异步执行、输入校验和循环限制。
```

本地使用的 `backend/.env` 不会提交到 Git。它保存真实模型配置，职责与
`.env.example` 相同，但包含开发机器自己的值。

## 关键文件之间的分工

`model_client.py` 只负责“怎样连接和调用模型”：读取已经校验过的配置、创建模型
对象、绑定可用工具，并提供稳定的调用和错误边界。它不决定何时执行工具。

`runtime.py` 负责“模型和工具怎样协作”：把系统提示词和对话交给模型，检查模型的
`tool_calls`，通过 LangGraph `ToolNode` 执行商品工具，再把工具结果送回模型，直到
得到最终回复。

`catalog.py`、`service.py` 和 `repository.py` 依次负责“模型可调用的工具格式”、
“商品业务规则”和“商品数据读取”。这种分层让网页接口和 AI 客服使用同一套商品
规则，避免两边返回不同价格或库存。

## 已有接口

- `GET /api/health`：检查 FastAPI 服务是否运行。
- `GET /api/products`：按关键词、分类、价格、库存、排序和分页查询商品。
- `GET /api/products/{product_id}`：按商品编号查询详情。
- `GET /docs`：打开 FastAPI 交互式接口文档。

当前没有 `/api/chat` 接口，所以前端聊天页仍然使用浏览器模拟逻辑。

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

当前共有 75 项后端测试。自动化测试使用模型替身，不会消耗模型额度。真实模型闭环
需要本地 `.env` 配置，并应作为单独的手动集成测试运行。

## 下一步

下一阶段应依次完成：

1. 定义会话、用户消息和客服响应的数据格式。
2. 实现内存版会话仓库与会话服务。
3. 提供 `/api/chat` 接口并调用 `CustomerServiceRuntime`。
4. 根据前端需要增加 SSE 流式输出。
5. 把前端聊天页从模拟脚本切换到真实后端。

商品咨询闭环稳定后，再加入数据库、MCP、RAG、订单、售后和人工转接能力。
