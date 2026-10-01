"""多 Agent 运行记录的数据访问层。"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.multi_agent.schemas import AgentPlan, AgentRunRecord, AgentTaskResult
from app.modules.agent_runs.models import AgentRunModel, AgentTaskModel


class SqlAgentRunRepository:
    """原子保存运行摘要和全部领域任务结果。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def add(self, run: AgentRunRecord) -> AgentRunRecord:
        record = AgentRunModel(
            id=str(run.id),
            conversation_id=str(run.conversation_id),
            buyer_id=run.buyer_id,
            status=run.status,
            plan_json=run.plan.model_dump_json(),
            final_answer=run.final_answer,
            model_calls=run.model_calls,
            tool_calls=run.tool_calls,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
        for position, task in enumerate(run.results):
            record.tasks.append(self._task_to_model(task, position))
        with self._session_factory.begin() as session:
            session.add(record)
        return run.model_copy(deep=True)

    def list_by_conversation(self, conversation_id: UUID) -> list[AgentRunRecord]:
        with self._session_factory() as session:
            statement = (
                select(AgentRunModel)
                .options(selectinload(AgentRunModel.tasks))
                .where(AgentRunModel.conversation_id == str(conversation_id))
                .order_by(AgentRunModel.started_at.asc())
            )
            records = session.scalars(statement).all()
            return [self._to_schema(record) for record in records]

    @staticmethod
    def _task_to_model(task: AgentTaskResult, position: int) -> AgentTaskModel:
        return AgentTaskModel(
            position=position,
            task_id=task.task_id,
            agent_name=task.agent_name.value,
            status=task.status,
            summary=task.summary,
            result_json=json.dumps(task.tool_results, ensure_ascii=False),
            model_calls=task.model_calls,
            tool_calls=task.tool_calls,
            tool_rounds=task.tool_rounds,
            duration_ms=task.duration_ms,
            error=task.error,
        )

    @classmethod
    def _to_schema(cls, record: AgentRunModel) -> AgentRunRecord:
        results: list[AgentTaskResult] = []
        for task in record.tasks:
            results.append(
                AgentTaskResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status=task.status,
                    summary=task.summary,
                    tool_results=json.loads(task.result_json),
                    model_calls=task.model_calls,
                    tool_calls=task.tool_calls,
                    tool_rounds=task.tool_rounds,
                    duration_ms=task.duration_ms,
                    error=task.error,
                )
            )
        return AgentRunRecord(
            id=UUID(record.id),
            conversation_id=UUID(record.conversation_id),
            buyer_id=record.buyer_id,
            status=record.status,
            plan=AgentPlan.model_validate_json(record.plan_json),
            results=results,
            final_answer=record.final_answer,
            model_calls=record.model_calls,
            tool_calls=record.tool_calls,
            started_at=cls._with_timezone(record.started_at),
            completed_at=cls._with_timezone(record.completed_at),
        )

    @staticmethod
    def _with_timezone(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
