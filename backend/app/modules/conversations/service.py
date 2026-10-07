"""会话生命周期、消息保存和 LangGraph 客服调用规则。"""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.ai.model_client import ModelClientError
from app.ai.multi_agent.schemas import AgentRunRecord
from app.ai.runtime import (
    RuntimeErrorBase,
    RuntimePause,
    RuntimeResult,
    RuntimeStreamEvent,
    create_customer_service_runtime,
)
from app.modules.conversations.repository import ConversationRepository
from app.modules.conversations.memory import MemoryService
from app.modules.conversations.schemas import (
    AfterSaleConfirmationDraft,
    BuyerId,
    ChatRequest,
    ChatConfirmationRequired,
    ChatResponse,
    ChatRunStats,
    ChatStreamDelta,
    ChatStreamStart,
    ChatStreamStatus,
    Conversation,
    ConversationMessage,
    ConversationMode,
    MessageRole,
    ResumeConfirmationRequest,
    HandoffRequest,
    StaffModeRequest,
    StaffReplyRequest,
)


class RuntimeProtocol(Protocol):
    """会话服务实际依赖的最小客服运行时接口。"""

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: dict[str, object] | None = None,
    ) -> RuntimeResult | RuntimePause: ...

    def astream(
        self,
        messages: Sequence[BaseMessage],
        config: dict[str, object] | None = None,
    ) -> AsyncIterator[RuntimeStreamEvent]: ...

    async def aresume(
        self,
        decision: dict[str, object],
        config: dict[str, object],
    ) -> RuntimeResult: ...


class ConversationStore(Protocol):
    """会话服务依赖的最小仓库接口。"""

    def add(self, conversation: Conversation) -> Conversation: ...

    def update(self, conversation: Conversation) -> Conversation: ...

    def get(self, conversation_id: UUID) -> Conversation | None: ...

    def list_by_buyer(self, buyer_id: BuyerId) -> list[Conversation]: ...

    def list_all(self) -> list[Conversation]: ...


class AgentRunStore(Protocol):
    """会话服务保存和读取 Agent 运行记录所需的最小接口。"""

    def add(self, run: AgentRunRecord) -> AgentRunRecord: ...

    def list_by_conversation(self, conversation_id: UUID) -> list[AgentRunRecord]: ...


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    """完成模型调用前需要保留的一次会话处理上下文。"""

    existing: Conversation | None
    conversation: Conversation
    user_message: ConversationMessage
    model_messages: list[BaseMessage]


@dataclass(frozen=True, slots=True)
class ConversationStreamEvent:
    """会话服务交给 SSE 路由的一个命名事件。"""

    name: Literal[
        "start", "status", "delta", "confirmation_required", "complete"
    ]
    data: (
        ChatStreamStart
        | ChatStreamStatus
        | ChatStreamDelta
        | ChatConfirmationRequired
        | ChatResponse
    )


class ConversationServiceError(RuntimeError):
    """会话业务错误的基类。"""


class ConversationNotFoundError(ConversationServiceError):
    """请求的会话不存在。"""


class ConversationAccessError(ConversationServiceError):
    """会话不属于当前买家。"""


class ConversationUnavailableError(ConversationServiceError):
    """会话当前不能继续由 AI 回复。"""


class AssistantReplyError(ConversationServiceError):
    """模型运行失败或没有产生有效文本回复。"""


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""

    return datetime.now(UTC)


class ConversationService:
    """创建和继续会话，并在成功回复后原子保存两条消息。"""

    def __init__(
        self,
        repository: ConversationStore | None = None,
        runtime: RuntimeProtocol | None = None,
        runtime_factory: Callable[
            [], RuntimeProtocol
        ] = create_customer_service_runtime,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = utc_now,
        agent_run_repository: AgentRunStore | None = None,
        memory_service: MemoryService | None = None,
        confirmation_checker: Callable[
            [BuyerId, AfterSaleConfirmationDraft], bool
        ]
        | None = None,
    ) -> None:
        self.repository = repository or ConversationRepository()
        self._runtime = runtime
        self._runtime_factory = runtime_factory
        self._id_factory = id_factory
        self._clock = clock
        self._agent_run_repository = agent_run_repository
        self._memory_service = memory_service
        self._confirmation_checker = (
            confirmation_checker or self._has_submitted_after_sale
        )
        self._conversation_locks: dict[UUID, asyncio.Lock] = {}

    async def send_message(
        self,
        request: ChatRequest,
    ) -> ChatResponse | ChatConfirmationRequired:
        """创建或继续会话，调用客服图，并在成功后保存消息。"""

        conversation_id = request.conversation_id or self._id_factory()
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())

        async with lock:
            turn = self._prepare_turn(request, conversation_id)
            turn = await self._with_memory(turn, request.message)

            try:
                output = await self._get_runtime().ainvoke(
                    turn.model_messages,
                    config=self._runtime_config(conversation_id, request.buyer_id),
                )
            except (ModelClientError, RuntimeErrorBase) as exc:
                raise AssistantReplyError("AI 客服暂时无法生成回复") from exc

            if isinstance(output, RuntimePause):
                return self._save_pending_turn(turn, output, self._id_factory())
            return self._save_completed_turn(turn, output)

    async def stream_message(
        self,
        request: ChatRequest,
    ) -> AsyncIterator[ConversationStreamEvent]:
        """创建或继续会话，并实时返回模型和商品工具事件。"""

        conversation_id = request.conversation_id or self._id_factory()
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())

        async with lock:
            turn = self._prepare_turn(request, conversation_id)
            turn = await self._with_memory(turn, request.message)
            assistant_message_id = self._id_factory()
            start_data = ChatStreamStart(
                conversation_id=conversation_id,
                mode=turn.conversation.mode,
                user_message=turn.user_message,
                assistant_message_id=assistant_message_id,
            )
            yield ConversationStreamEvent(name="start", data=start_data)

            try:
                runtime_events = self._get_runtime().astream(
                    turn.model_messages,
                    config=self._runtime_config(conversation_id, request.buyer_id),
                )
                async for runtime_event in runtime_events:
                    if runtime_event.type == "confirmation_required":
                        pause = runtime_event.pause
                        if pause is None:
                            raise RuntimeErrorBase("确认事件缺少暂停状态")
                        response = self._save_pending_turn(
                            turn,
                            pause,
                            assistant_message_id,
                        )
                        yield ConversationStreamEvent(
                            name="confirmation_required",
                            data=response,
                        )
                        continue

                    if runtime_event.type == "complete":
                        result = runtime_event.result
                        if result is None:
                            raise RuntimeErrorBase("流式运行结束时缺少完整结果")
                        response = self._save_completed_turn(
                            turn,
                            result,
                            assistant_message_id,
                        )
                        yield ConversationStreamEvent(
                            name="complete",
                            data=response,
                        )
                        continue

                    stream_event = self._convert_runtime_event(runtime_event)
                    if stream_event is not None:
                        yield stream_event
            except (ModelClientError, RuntimeErrorBase) as exc:
                raise AssistantReplyError("AI 客服暂时无法生成回复") from exc

    async def resume_confirmation(
        self,
        conversation_id: UUID,
        buyer_id: BuyerId,
        request: ResumeConfirmationRequest,
    ) -> ChatResponse:
        """验证会话归属，并从持久化中断点继续生成最终回复。"""

        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            conversation = self.get_conversation(conversation_id, buyer_id)
            if conversation.mode is not ConversationMode.AWAITING_CONFIRMATION:
                raise ConversationUnavailableError("当前会话没有等待确认的工作流")
            if not conversation.messages:
                raise AssistantReplyError("等待确认的会话缺少用户消息")

            user_message = conversation.messages[-1]
            if user_message.role is not MessageRole.USER:
                raise AssistantReplyError("等待确认的会话状态不完整")

            if request.action == "confirm":
                draft = request.draft
                if draft is None or not self._confirmation_checker(buyer_id, draft):
                    raise ConversationUnavailableError(
                        "未找到与确认内容一致的已提交售后申请"
                    )

            try:
                result = await self._get_runtime().aresume(
                    request.model_dump(mode="json"),
                    config=self._runtime_config(conversation_id, buyer_id),
                )
            except (ModelClientError, RuntimeErrorBase) as exc:
                raise AssistantReplyError("AI 客服暂时无法恢复确认流程") from exc

            return self._save_resumed_turn(conversation, user_message, result)

    @staticmethod
    def _has_submitted_after_sale(
        buyer_id: BuyerId,
        draft: AfterSaleConfirmationDraft,
    ) -> bool:
        """确认业务数据库中已经存在与最终草稿一致的待审申请。"""

        from app.ai.tools.after_sales import get_after_sale_tool_service
        from app.modules.after_sales.schemas import AfterSaleStatus

        service = get_after_sale_tool_service()
        requests = service.list_for_buyer(buyer_id)
        for request in requests:
            if request.status is not AfterSaleStatus.PENDING:
                continue
            if request.order_id != draft.order_id:
                continue
            if request.request_type.value != draft.request_type:
                continue
            if request.reason != draft.reason:
                continue
            return True
        return False

    def _prepare_turn(
        self,
        request: ChatRequest,
        conversation_id: UUID,
    ) -> PreparedTurn:
        """校验会话并准备用户消息和 LangChain 历史。"""

        existing = self.repository.get(conversation_id)
        conversation = self._resolve_conversation(
            existing=existing,
            conversation_id=conversation_id,
            buyer_id=request.buyer_id,
            first_message=request.message,
            allow_create=request.conversation_id is None,
        )
        user_message = ConversationMessage(
            id=self._id_factory(),
            role=MessageRole.USER,
            content=request.message,
            created_at=max(self._clock(), conversation.updated_at),
        )
        model_messages = self._to_model_messages(conversation.messages)
        human_message = HumanMessage(
            content=user_message.content,
            id=str(user_message.id),
        )
        model_messages.append(human_message)

        return PreparedTurn(
            existing=existing,
            conversation=conversation,
            user_message=user_message,
            model_messages=model_messages,
        )

    async def _with_memory(self, turn: PreparedTurn, query: str) -> PreparedTurn:
        """在线程中运行本地向量计算，避免阻塞其他会话的SSE。"""

        if self._memory_service is None:
            return turn
        messages = await asyncio.to_thread(
            self._memory_service.build_context,
            turn.conversation,
            query,
            turn.model_messages,
        )
        return replace(turn, model_messages=messages)

    def _save_completed_turn(
        self,
        turn: PreparedTurn,
        result: RuntimeResult,
        assistant_message_id: UUID | None = None,
    ) -> ChatResponse:
        """校验最终回复，并一次保存用户消息和 AI 消息。"""

        reply_text = result.reply.text.strip()
        if not reply_text:
            raise AssistantReplyError("AI 客服没有返回有效的文本回复")

        if assistant_message_id is None:
            assistant_message_id = self._id_factory()

        assistant_created_at = max(self._clock(), turn.user_message.created_at)
        assistant_message = ConversationMessage(
            id=assistant_message_id,
            role=MessageRole.ASSISTANT,
            content=reply_text,
            created_at=assistant_created_at,
        )
        conversation = turn.conversation
        updated = Conversation(
            id=conversation.id,
            buyer_id=conversation.buyer_id,
            title=conversation.title,
            mode=conversation.mode,
            messages=[
                *conversation.messages,
                turn.user_message,
                assistant_message,
            ],
            created_at=conversation.created_at,
            updated_at=assistant_message.created_at,
        )

        if turn.existing is None:
            self.repository.add(updated)
        else:
            self.repository.update(updated)

        self._save_agent_run(updated, turn, result, reply_text)

        return ChatResponse(
            conversation_id=updated.id,
            mode=updated.mode,
            user_message=turn.user_message,
            assistant_message=assistant_message,
            run=ChatRunStats(
                model_calls=result.model_calls,
                tool_rounds=result.tool_rounds,
                tool_calls=result.tool_calls,
                query_analysis=result.query_analysis,
                agent_plan=result.agent_plan,
                task_results=list(result.task_results),
            ),
        )

    def _save_pending_turn(
        self,
        turn: PreparedTurn,
        pause: RuntimePause,
        assistant_message_id: UUID,
    ) -> ChatConfirmationRequired:
        """保存已经触发中断的用户消息，并将会话标记为等待确认。"""

        conversation = turn.conversation
        updated = Conversation(
            id=conversation.id,
            buyer_id=conversation.buyer_id,
            title=conversation.title,
            mode=ConversationMode.AWAITING_CONFIRMATION,
            messages=[*conversation.messages, turn.user_message],
            created_at=conversation.created_at,
            updated_at=turn.user_message.created_at,
        )
        if turn.existing is None:
            self.repository.add(updated)
        else:
            self.repository.update(updated)

        return ChatConfirmationRequired(
            conversation_id=updated.id,
            mode=ConversationMode.AWAITING_CONFIRMATION,
            user_message=turn.user_message,
            assistant_message_id=assistant_message_id,
            confirmation=pause.confirmation,
            run=self._pause_stats(pause),
        )

    def _save_resumed_turn(
        self,
        conversation: Conversation,
        user_message: ConversationMessage,
        result: RuntimeResult,
    ) -> ChatResponse:
        """恢复完成后只追加AI消息，避免重复保存已经落库的用户消息。"""

        reply_text = result.reply.text.strip()
        if not reply_text:
            raise AssistantReplyError("AI 客服没有返回有效的文本回复")

        assistant_message = ConversationMessage(
            id=self._id_factory(),
            role=MessageRole.ASSISTANT,
            content=reply_text,
            created_at=max(self._clock(), user_message.created_at),
        )
        updated = conversation.model_copy(
            update={
                "mode": ConversationMode.AI,
                "messages": [*conversation.messages, assistant_message],
                "updated_at": assistant_message.created_at,
            }
        )
        self.repository.update(updated)

        turn = PreparedTurn(
            existing=conversation,
            conversation=conversation,
            user_message=user_message,
            model_messages=[],
        )
        self._save_agent_run(updated, turn, result, reply_text)
        return ChatResponse(
            conversation_id=updated.id,
            mode=updated.mode,
            user_message=user_message,
            assistant_message=assistant_message,
            run=ChatRunStats(
                model_calls=result.model_calls,
                tool_rounds=result.tool_rounds,
                tool_calls=result.tool_calls,
                query_analysis=result.query_analysis,
                agent_plan=result.agent_plan,
                task_results=list(result.task_results),
            ),
        )

    @staticmethod
    def _pause_stats(pause: RuntimePause) -> ChatRunStats:
        """把运行时暂停信息转换成稳定的接口统计格式。"""

        return ChatRunStats(
            model_calls=pause.model_calls,
            tool_rounds=pause.tool_rounds,
            tool_calls=pause.tool_calls,
            query_analysis=pause.query_analysis,
            agent_plan=pause.agent_plan,
            task_results=list(pause.task_results),
        )

    def _save_agent_run(
        self,
        conversation: Conversation,
        turn: PreparedTurn,
        result: RuntimeResult,
        reply_text: str,
    ) -> None:
        """在启用运行仓库时保存调度计划和全部领域任务结果。"""

        if self._agent_run_repository is None or result.agent_plan is None:
            return

        failed_tasks = [
            task for task in result.task_results if task.status != "completed"
        ]
        status: Literal["completed", "partial", "failed"] = "completed"
        if failed_tasks and len(failed_tasks) == len(result.task_results):
            status = "failed"
        elif failed_tasks:
            status = "partial"

        run = AgentRunRecord(
            id=self._id_factory(),
            conversation_id=conversation.id,
            buyer_id=conversation.buyer_id,
            status=status,
            plan=result.agent_plan,
            results=list(result.task_results),
            final_answer=reply_text,
            model_calls=result.model_calls,
            tool_calls=result.tool_calls,
            started_at=turn.user_message.created_at,
            completed_at=conversation.updated_at,
        )
        self._agent_run_repository.add(run)

    def list_agent_runs(
        self,
        conversation_id: UUID,
        buyer_id: BuyerId,
    ) -> list[AgentRunRecord]:
        """读取当前买家指定会话的多 Agent 执行记录。"""

        self.get_conversation(conversation_id, buyer_id)
        if self._agent_run_repository is None:
            return []
        return self._agent_run_repository.list_by_conversation(conversation_id)

    def get_conversation(
        self,
        conversation_id: UUID,
        buyer_id: BuyerId,
    ) -> Conversation:
        """读取属于指定买家的会话。"""

        conversation = self.repository.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(f"未找到会话：{conversation_id}")
        self._ensure_buyer_access(conversation, buyer_id)
        return conversation

    def list_conversations(self, buyer_id: BuyerId) -> list[Conversation]:
        """返回指定买家的全部会话，最近更新的排在前面。"""

        return self.repository.list_by_buyer(buyer_id)

    def list_all_conversations(self) -> list[Conversation]:
        """为已认证客服返回全部会话。"""

        return self.repository.list_all()

    def request_handoff(self, request: HandoffRequest) -> Conversation:
        """创建或更新一段会话，使其进入等待人工接管状态。"""

        if request.conversation_id is None:
            current_time = self._clock()
            conversation = Conversation(
                id=self._id_factory(),
                buyer_id=request.buyer_id,
                title=request.title,
                mode=ConversationMode.WAITING,
                messages=[],
                created_at=current_time,
                updated_at=current_time,
            )
            conversation = self._append_persisted_message(
                conversation,
                MessageRole.SYSTEM,
                "已进入人工服务队列。咨询记录会一并交给客服，自动回复已暂停。",
            )
            return self.repository.add(conversation)

        conversation = self.get_conversation(
            request.conversation_id,
            request.buyer_id,
        )
        if conversation.mode is ConversationMode.CLOSED:
            raise ConversationUnavailableError("已结束的会话不能申请人工接管")
        if conversation.mode in {ConversationMode.WAITING, ConversationMode.HUMAN}:
            return conversation

        updated = conversation.model_copy(update={"mode": ConversationMode.WAITING})
        updated = self._append_persisted_message(
            updated,
            MessageRole.SYSTEM,
            "已进入人工服务队列。咨询记录会一并交给客服，自动回复已暂停。",
        )
        return self.repository.update(updated)

    def change_service_mode(
        self,
        conversation_id: UUID,
        request: StaffModeRequest,
    ) -> Conversation:
        """由客服接管、恢复 AI 或结束一段会话。"""

        conversation = self.repository.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(f"未找到会话：{conversation_id}")
        if conversation.mode is ConversationMode.CLOSED:
            raise ConversationUnavailableError("已结束的会话不能再次修改状态")

        target_mode = ConversationMode(request.mode)
        allowed_modes = {
            ConversationMode.WAITING: {ConversationMode.HUMAN},
            ConversationMode.HUMAN: {ConversationMode.AI, ConversationMode.CLOSED},
            ConversationMode.AI: {ConversationMode.HUMAN},
        }
        if target_mode not in allowed_modes.get(conversation.mode, set()):
            raise ConversationUnavailableError(
                f"会话不能从 {conversation.mode.value} 切换到 {target_mode.value}"
            )

        messages = {
            ConversationMode.HUMAN: f"{request.staff_id} 已接入，会继续为你处理。",
            ConversationMode.AI: "客服已恢复 AI 服务，智能客服继续为你解答。",
            ConversationMode.CLOSED: "本次人工服务已结束。你可以开始新会话。",
        }
        updated = conversation.model_copy(update={"mode": target_mode})
        updated = self._append_persisted_message(
            updated,
            MessageRole.SYSTEM,
            messages[target_mode],
        )
        return self.repository.update(updated)

    def add_staff_reply(
        self,
        conversation_id: UUID,
        request: StaffReplyRequest,
    ) -> Conversation:
        """在人工服务状态下保存一条客服回复。"""

        conversation = self.repository.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(f"未找到会话：{conversation_id}")
        if conversation.mode is not ConversationMode.HUMAN:
            raise ConversationUnavailableError("只有人工服务中的会话可以发送客服回复")

        updated = self._append_persisted_message(
            conversation,
            MessageRole.STAFF,
            request.message,
        )
        return self.repository.update(updated)

    def _append_persisted_message(
        self,
        conversation: Conversation,
        role: MessageRole,
        content: str,
    ) -> Conversation:
        """追加一条带稳定时间和编号的消息，并更新会话时间。"""

        created_at = max(self._clock(), conversation.updated_at)
        message = ConversationMessage(
            id=self._id_factory(),
            role=role,
            content=content,
            created_at=created_at,
        )
        return conversation.model_copy(
            update={
                "messages": [*conversation.messages, message],
                "updated_at": created_at,
            }
        )

    @staticmethod
    def _runtime_config(
        conversation_id: UUID,
        buyer_id: BuyerId,
    ) -> dict[str, object]:
        """创建一次客服运行使用的 LangGraph 配置。"""

        return {
            "configurable": {
                "thread_id": str(conversation_id),
                "buyer_id": buyer_id,
            },
            "tags": ["customer-service", str(buyer_id)],
        }

    @staticmethod
    def _convert_runtime_event(
        event: RuntimeStreamEvent,
    ) -> ConversationStreamEvent | None:
        """把底层运行事件转换为稳定的会话 SSE 事件。"""

        if event.type == "delta" and event.text:
            delta = ChatStreamDelta(content=event.text)
            return ConversationStreamEvent(name="delta", data=delta)

        if event.type == "router_start":
            status_data = ChatStreamStatus(
                phase="router",
                state="started",
            )
            return ConversationStreamEvent(name="status", data=status_data)

        if event.type == "router_end" and event.query_analysis is not None:
            status_data = ChatStreamStatus(
                phase="router",
                state="completed",
                query_analysis=event.query_analysis,
            )
            return ConversationStreamEvent(name="status", data=status_data)

        if event.type == "supervisor_start":
            status_data = ChatStreamStatus(
                phase="supervisor",
                state="started",
            )
            return ConversationStreamEvent(name="status", data=status_data)

        if event.type == "supervisor_end" and event.agent_plan is not None:
            status_data = ChatStreamStatus(
                phase="supervisor",
                state="completed",
                agent_plan=event.agent_plan,
            )
            return ConversationStreamEvent(name="status", data=status_data)

        if event.type in {"agent_start", "agent_end"} and event.agent_name:
            status_data = ChatStreamStatus(
                phase="agent",
                state="started" if event.type == "agent_start" else "completed",
                agent_name=event.agent_name,
                agent_plan=event.agent_plan,
                task_id=event.task_id or None,
                task_result=event.task_result,
            )
            return ConversationStreamEvent(name="status", data=status_data)

        if event.type in {"synthesis_start", "synthesis_end"}:
            status_data = ChatStreamStatus(
                phase="synthesis",
                state=(
                    "started" if event.type == "synthesis_start" else "completed"
                ),
                agent_name=event.agent_name or None,
            )
            return ConversationStreamEvent(name="status", data=status_data)

        if event.type == "model_start":
            status_data = ChatStreamStatus(
                phase="model",
                state="started",
            )
            return ConversationStreamEvent(name="status", data=status_data)

        if event.type == "tool_start":
            status_data = ChatStreamStatus(
                phase="tool",
                state="started",
                tool_calls=event.tool_calls,
                tool_names=list(event.tool_names),
                agent_name=event.agent_name or None,
                task_id=event.task_id or None,
            )
            return ConversationStreamEvent(name="status", data=status_data)

        if event.type == "tool_end":
            status_data = ChatStreamStatus(
                phase="tool",
                state="completed",
                tool_calls=event.tool_calls,
                tool_names=list(event.tool_names),
                tool_results=list(event.tool_results),
                agent_name=event.agent_name or None,
                task_id=event.task_id or None,
            )
            return ConversationStreamEvent(name="status", data=status_data)

        return None

    def _get_runtime(self) -> RuntimeProtocol:
        """首次发送消息时才创建真实模型运行时。"""

        if self._runtime is None:
            self._runtime = self._runtime_factory()
        return self._runtime

    def _resolve_conversation(
        self,
        existing: Conversation | None,
        conversation_id: UUID,
        buyer_id: BuyerId,
        first_message: str,
        allow_create: bool,
    ) -> Conversation:
        """读取已有会话，或为第一条消息创建尚未保存的会话。"""

        if existing is None:
            if not allow_create:
                raise ConversationNotFoundError(f"未找到会话：{conversation_id}")
            created_at = self._clock()
            return Conversation(
                id=conversation_id,
                buyer_id=buyer_id,
                title=self._make_title(first_message),
                created_at=created_at,
                updated_at=created_at,
            )

        self._ensure_buyer_access(existing, buyer_id)
        if existing.mode is not ConversationMode.AI:
            raise ConversationUnavailableError(
                f"当前会话状态不能由 AI 继续回复：{existing.mode.value}"
            )
        return existing

    @staticmethod
    def _ensure_buyer_access(
        conversation: Conversation,
        buyer_id: BuyerId,
    ) -> None:
        """阻止买家读取或继续其他买家的会话。"""

        if conversation.buyer_id != buyer_id:
            raise ConversationAccessError("无权访问其他买家的会话")

    @staticmethod
    def _to_model_messages(
        messages: Sequence[ConversationMessage],
    ) -> list[BaseMessage]:
        """把已保存的业务消息转换为 LangChain 对话历史。"""

        result: list[BaseMessage] = []
        for message in messages:
            if message.role is MessageRole.USER:
                result.append(
                    HumanMessage(content=message.content, id=str(message.id))
                )
            elif message.role in {MessageRole.ASSISTANT, MessageRole.STAFF}:
                result.append(AIMessage(content=message.content, id=str(message.id)))
        return result

    @staticmethod
    def _make_title(message: str) -> str:
        """从第一条用户消息生成简短会话标题。"""

        return message if len(message) <= 24 else f"{message[:23]}…"
