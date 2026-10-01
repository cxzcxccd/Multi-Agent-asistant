"""多 Agent 运行记录持久化测试。"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai.multi_agent.schemas import (
    AgentName,
    AgentPlan,
    AgentRunRecord,
    AgentTask,
    AgentTaskResult,
)
from app.db.base import Base
from app.modules.agent_runs.repository import SqlAgentRunRepository
from app.modules.conversations.models import ConversationRecord


def test_agent_run_repository_saves_plan_and_task_results(tmp_path: Path) -> None:
    database_path = tmp_path / "agent-runs.db"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    conversation_id = uuid4()
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        session.add(
            ConversationRecord(
                id=str(conversation_id),
                buyer_id="A",
                title="测试",
                mode="ai",
                created_at=now,
                updated_at=now,
            )
        )

    task = AgentTask(
        id="task-1-product_agent",
        agent_name=AgentName.PRODUCT,
        description="搜索商品",
        allowed_tools=["search_products"],
    )
    plan = AgentPlan(
        agents=[AgentName.PRODUCT],
        primary_agent=AgentName.PRODUCT,
        reason="商品咨询",
        allowed_tools=["search_products"],
        tasks=[task],
    )
    run = AgentRunRecord(
        id=uuid4(),
        conversation_id=conversation_id,
        buyer_id="A",
        status="completed",
        plan=plan,
        results=[
            AgentTaskResult(
                task_id=task.id,
                agent_name=AgentName.PRODUCT,
                status="completed",
                summary="找到一款耳机",
                tool_results=[{"name": "search_products", "output": {"total": 1}}],
                model_calls=2,
                tool_calls=1,
                tool_rounds=1,
                duration_ms=20,
            )
        ],
        final_answer="推荐这款耳机。",
        model_calls=3,
        tool_calls=1,
        started_at=now,
        completed_at=now,
    )
    repository = SqlAgentRunRepository(session_factory)

    repository.add(run)
    loaded = repository.list_by_conversation(conversation_id)

    assert loaded == [run]
