# 极客优选后端

这是智能电商客服的第一阶段后端骨架，目标是先用 FastAPI、真实大模型和模拟商品数据跑通商品咨询。

当前只包含：

- FastAPI 应用入口、健康检查和商品查询接口。
- 商品数据格式、数据读取、筛选、排序和分页规则。
- 会话模块的分层目录。
- AI 模型、运行时、商品工具和提示词目录。
- 与前端商品编号一致的模拟商品数据。
- 后续测试目录。

真实模型调用、会话持久化和前端接入将在后续开发阶段实现。

## 计划中的请求链路

```text
React 前端 → FastAPI → AI Runtime → 商品工具 → 模拟商品数据
```

MCP、LangGraph、RAG、订单和售后不属于当前骨架，将在商品咨询闭环验证后逐步加入。

## 本地启动

安装依赖后运行：

```powershell
cd backend
uv sync --dev --no-cache
uv run --no-cache uvicorn app.main:app --reload
```

这里使用 `--no-cache`，以兼容 Python 和 `uv` 缓存位于不同磁盘的 Windows 开发环境。

健康检查地址：`http://127.0.0.1:8000/api/health`

商品接口：

- `GET /api/products`：按照关键词、分类、价格、库存、排序和分页查询商品。
- `GET /api/products/{product_id}`：按照商品编号查询详情。
- `GET /docs`：使用 FastAPI 交互式接口文档测试请求。
