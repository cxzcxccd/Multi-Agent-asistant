# 极客优选 · 智能数码电商客服

一个用于产品验证、学习和展示的电商客服项目。当前包含独立运行的 Frontend
Demo，以及使用 FastAPI、LangChain、LangGraph、真实模型和模拟商品数据开发的
第一阶段后端。

前端数码旗舰店通过 `/api/products` 读取商品，客服对话通过 `/api/chat/stream` 连接
LangGraph。用户 Query 会先经过文本规范化、本地中文 Embedding 语义路由、实体提取和业务 Query
整理，再进入模型与工具循环；售后、人工接管、知识检索与评测均已接入后端。

## 本地运行

本机已使用 Node.js 22.14.0 和 npm 验证。建议使用 Node.js 22.12 或更高版本。

在项目根目录打开终端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 [买家客服](http://127.0.0.1:5173/#chat)。浏览数码旗舰店和使用商品咨询都需要
同时启动后端；商品咨询还需要在 `backend/.env` 中配置模型，密钥不会发送给浏览器。

后端在另一个终端运行：

```powershell
cd backend
uv sync --dev --no-cache
cd infrastructure/milvus
docker compose up -d
cd ../..
uv run --no-cache uvicorn app.main:app --reload
```

后端默认地址为 `http://127.0.0.1:8000`，接口文档位于
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。模型配置方法和全部
后端文件职责见 [后端 README](backend/README.md)。

四个页面分别是：

- [买家客服](http://127.0.0.1:5173/#chat)：商品咨询、订单与物流、售后草稿、申请进度、转人工。
- [客服工作台](http://127.0.0.1:5173/#workbench)：接管与回复会话、恢复 AI、结束服务、批准或拒绝售后。
- [开发者与评测](http://127.0.0.1:5173/#lab)：模拟执行事件、评测界面预览、故障开关、只读资料和重置。
- [数码旗舰店](http://127.0.0.1:5173/#shop)：点击侧栏店铺入口浏览完整商品陈列，搜索、分类、按价格排序及查看详情。客服可把商品引用到回复草稿，买家可回原会话继续咨询。

## 建议体验顺序

1. 在买家客服页输入 `推荐 300 元以内的耳机`，查看真实 LangGraph 回复和执行记录。
2. 输入 `查询订单 10001 的物流`，查看运输节点。
3. 输入 `订单 10002 耳机有一边没声音，想退货`，修改并核对申请内容，勾选确认后提交。
4. 切换到客服工作台 → 售后审核，填写意见并批准或拒绝，再回到买家页查看结果。
5. 在买家页点击转人工，进入客服工作台接管并回复，确认买家页收到人工回复。
6. 在开发者页查看执行记录；在演示控制中开启工具失败或资料不足，再回买家页查询。
7. 点击左侧数码旗舰店，浏览商品和规格。返回时会保留原会话、人工回复草稿和工作台标签；引用商品只填入草稿，不自动发送。

更多用例、边界和验证记录见 [Frontend Demo 体验与验收](docs/frontend-demo.md)。

## 构建与验证

在 `frontend` 目录运行：

```powershell
npm run build
npm test
npm run format:check
```

- `build`：TypeScript 检查和生产构建，输出到 `frontend/dist`。
- `test`：Playwright 浏览器交互测试，使用已安装的 Google Chrome。脚本演示和后端聊天分别在 5174、5175 端口启动测试服务，避免干扰 5173 端口的开发页面。后端聊天测试拦截 HTTP 响应，不会消耗模型额度。
- `format:check`：检查前端源码和配置格式。
- `npm run test:report`：打开最近的测试报告。
- `npm run preview`：预览构建产物，默认地址为 `http://127.0.0.1:4173`。

浏览器测试需要 Google Chrome；它只用于测试，不是网页用户的使用要求。测试输出、截图、构建结果和依赖目录均不提交到 Git。

## 当前实现

前端：

- React + TypeScript + Vite，使用 Tailwind CSS 构建集成及自定义工作台样式。
- 12 个虚构商品、5 笔订单、2 位模拟买家，固定业务日期为 2026-09-15。
- 数码旗舰店的列表、搜索、分类、价格、库存、排序和详情均调用后端商品接口，包含加载、空结果、失败及重试状态。
- 普通对话、商品咨询、订单和物流查询通过 SSE 接收真实 LangGraph 文本片段与工具进度。
- Agent 处理过程按“语义分流 Router → 工具 → 汇总”展示；Router 和工具卡均使用后端 SSE 返回的真实执行结果。
- 售后提交与审核、转人工、客服接管、人工回复、恢复 AI 和结束服务均调用后端接口。
- 前端通过 SSE 订阅会话快照；会话变化由服务端主动推送，客服工作台无需定时轮询或手动刷新。
- 买家只订阅自己的会话；客服使用客服令牌订阅全部工作台会话和待审售后队列。
- 买家 A、买家 B 和客服使用数据库账号、Argon2 密码哈希、短期 Bearer 令牌和可轮换刷新令牌；后端按令牌角色和买家编号校验接口权限。
- 独立登录页按身份开放工作空间：买家进入聊天与商城，客服进入人工服务和审核工作台；刷新页面可恢复当前会话登录态。
- 先确认申请再提交，客服审批与买家确认分别呈现。批准不表示退款到账。
- 人工接管、停止输出和切换买家会中断相应自动处理，防止迟到的自动回复。

后端：

- FastAPI 健康检查、商品列表和商品详情接口。
- Pydantic 商品格式、SQLAlchemy 商品仓库、商品筛选、排序和分页服务；JSON 仅作为首次初始化种子。
- 可供模型调用的商品搜索与详情工具。
- 支持 OpenAI 和 OpenAI 兼容服务的模型客户端。
- 使用 LangGraph 编排 Query 预处理、模型、商品／订单／售后／知识工具和最终客服回复。
- 使用本地 `BAAI/bge-small-zh-v1.5` ONNX Embedding 完成语义意图路由，并为 RAG 文档和 Query 生成 512 维真实语义向量。
- 会话、消息、聊天请求与响应的数据格式。
- SQLAlchemy 会话仓库和服务，支持重启恢复、买家隔离、历史消息转换、失败回滚和同会话串行处理。
- 聊天、会话列表和会话详情接口。
- 后端允许本地前端跨端口访问，模型密钥只保存在后端。
- SQLite、SQLAlchemy 和 Alembic 数据库基础，包含商品、会话、消息、订单和物流表。
- 订单列表、详情和物流接口，订单归属校验，以及绑定当前会话买家身份的 LangGraph 订单工具。
- 售后草稿、修改、取消、幂等提交、待审队列、人工批准／拒绝和操作记录接口。
- Markdown 知识库、Milvus 增量向量索引和 `search_knowledge` 混合检索工具已接入 LangGraph，回复会展示真实文档与章节来源。
- RAG 默认使用本地 BGE 中文语义向量，同时保留离线特征哈希测试实现和 OpenAI Embedding Provider；评测覆盖 Recall@K、MRR、拒答率、检索延迟、回答正确性、忠实度、完整性和引用准确率。
- 客服可在“开发者与评测”页面运行真实检索评测并查看每条案例的召回来源与通过状态。
- Milvus 使用 COSINE 相似度执行向量召回，SQLite 只保存知识正文和版本信息；MCP 尚未实现。
- 165 项后端测试。

## 代码导览

```text
docs/
  product-design.md       已确认的产品方案及阶段边界
  frontend-demo.md        Demo 体验步骤、测试与限制
frontend/
  src/
    App.tsx              工作空间布局、导航、模拟身份切换
    Chat.tsx             买家聊天、消息呈现、服务速览
    Workbench.tsx        人工会话处理和售后审批
    Lab.tsx              执行记录、评测预览、演示控制
    Shop.tsx             全店陈列、搜索筛选、商品详情及咨询／回复引用
    shop.css             店铺陈列与移动端布局
    components.tsx       商品／订单／申请卡片、确认弹窗、资料与轨迹
    api.ts              商品、聊天、售后接口请求、响应类型和错误处理
    data.ts              尚未迁移的订单、店铺规则及前端演示商品快照
    types.ts             前端业务状态类型
    store.ts             前后端分流、浏览器状态、售后同步和审批流转
    styles.css           工作台主题与响应式布局
  tests/demo.spec.ts      浏览器交互回归测试
  tests/shop.spec.ts      商品接口、陈列、详情与客服往返流程测试
  tests/backend-chat.spec.ts 对话、错误恢复、停止请求及真实售后接口联调测试
backend/
  README.md               后端进度、架构、全部文件职责和运行方式
  app/main.py             FastAPI 应用入口
  app/db/                 数据库连接、迁移初始化和演示数据导入
  app/ai/model_client.py  模型连接、工具绑定和调用边界
  app/ai/query_preprocessor.py Query 清洗、语义意图路由、上下文补全和实体提取
  app/ai/runtime.py       LangGraph 客服运行图
  app/ai/tools/catalog.py AI 商品搜索与详情工具
  app/ai/tools/orders.py  AI 订单与物流查询工具
  app/ai/tools/after_sales.py AI 售后查询与待确认草稿工具
  app/modules/catalog/    商品格式、仓库、服务和接口
  app/modules/conversations/ 会话格式、数据库仓库和 AI 聊天服务
  app/modules/orders/     订单格式、数据库仓库、归属规则和接口
  app/modules/after_sales/ 售后状态、幂等提交、审核和操作记录
  app/modules/auth/       用户与刷新令牌表、Argon2 密码、登录服务和角色权限依赖
  app/modules/knowledge/  Markdown 加载、Milvus 向量存储、混合检索和调试接口
  infrastructure/milvus/ Milvus Standalone 官方 Docker Compose 配置
  alembic/                数据库结构迁移及版本记录
  data/seed/products.json 首次初始化使用的模拟商品种子
  data/seed/orders.json   订单归属与物流场景种子
  data/knowledge/         退换货、保修、物流、支付和使用说明知识文档
  data/evaluation/        RAG 问题、正确来源、参考答案和外部评测数据
  scripts/evaluate_rag.py 运行真实 BGE、Milvus 与聊天模型的 RAG 评测
  data/intents/routes.json 商品、订单、售后、知识与人工服务的语义路由样本
  data/app.db             本地 SQLite 数据库，不提交到 Git
  tests/                  后端自动化测试
```

商品咨询数据流是：**用户消息 → `/api/chat/stream` → 会话服务 → Query 规范化与语义路由
→ LangGraph 模型节点 → SSE 文本片段 → 商品工具节点 → 商品数据 → 模型最终回复 → 保存会话**。

商城数据流是：**商城搜索或筛选 → `/api/products` → 商品服务 → 后端商品数据 → 页面陈列；
点击商品 → `/api/products/{product_id}` → 商品详情**。商城和 AI 商品工具由同一个后端
商品模块提供数据。`frontend/src/data.ts` 中暂时保留商品与订单快照，供脚本演示模式和
售后卡片展示使用；订单卡片完成后端数据适配后再移除。

订单数据流是：**用户消息 → `/api/chat/stream` → LangGraph → 订单工具读取服务端买家身份
→ 订单服务校验归属 → 数据库订单与物流 → 模型回复**。

售后数据流是：**页面生成待确认内容 → `/api/after-sales/drafts` 创建后端草稿
→ 携带幂等键确认提交 → 客服工作台同步待审申请 → 按版本批准／拒绝
→ 保存操作记录并更新买家页面**。页面会显示提交中与失败信息，重复点击由前端请求锁和
后端幂等提交共同保护。

商品图片使用内置 imagegen 生成，仅作外观示意；素材路径和完整提示词见 [商品素材记录](docs/catalog-assets.md)。

当前登录页会使用三个数据库演示账号获取短期签名令牌，密码使用 Argon2 保存，刷新令牌
只以 SHA-256 摘要保存并在每次刷新后轮换。演示密码仍由前端配置提供，只适合本地演示；
正式部署应将令牌改用 HttpOnly Cookie 或成熟身份服务。页面会话关联和评测状态仍保存在当前浏览器。项目尚未建立真实 Agent
评测集，也没有完整的模型效果指标。

SQLite、SQLAlchemy 和 Alembic 数据库基础已经完成，商品、会话、消息、订单和售后申请
已迁入数据库。当前会话同步使用 SSE 服务端推送；浏览器断线时由 EventSource 自动重连。
部署时可通过 `DATABASE_URL` 切换为 PostgreSQL。

知识正文和内容哈希保存在关系数据库中，知识向量保存在 Milvus。Windows 不支持原生
Milvus Lite，本项目使用 Docker Desktop 启动 Milvus Standalone；生产环境可以只修改
`MILVUS_URI` 和 `MILVUS_TOKEN`，连接 Milvus 集群或 Zilliz Cloud。
