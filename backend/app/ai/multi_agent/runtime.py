"""基于 LangGraph 的任务级多 Agent 客服运行时。"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from time import perf_counter
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    RemoveMessage,
)
from langchain_core.messages.utils import message_chunk_to_message
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from app.ai.model_client import ModelClient, create_model_client
from app.ai.multi_agent.schemas import AgentName, AgentPlan, AgentTask, AgentTaskResult
from app.ai.multi_agent.supervisor import Supervisor, tools_for_plan
from app.ai.query_preprocessor import (
    QueryAnalysis,
    QueryPreprocessor,
    create_query_preprocessor,
)
from app.ai.runtime import (
    AgentPermissionError,
    InvalidToolCallError,
    RuntimeErrorBase,
    RuntimeInputError,
    RuntimeLoopLimitError,
    RuntimePause,
    RuntimeResult,
    RuntimeStreamEvent,
    load_customer_service_prompt,
)


class MultiAgentState(TypedDict):
    """顶层工作流在节点之间传递的共享状态。"""

    messages: Annotated[list[AnyMessage], add_messages]
    query_analysis: QueryAnalysis | None
    agent_plan: AgentPlan | None
    task_results: list[AgentTaskResult]
    final_reply: AIMessage | None
    model_calls: int
    tool_rounds: int
    tool_calls: int
    confirmation: dict[str, Any] | None


class AgentTaskCheckpointState(TypedDict):
    """单个领域任务子图保存的最小可恢复状态。"""

    result: AgentTaskResult | None


StreamCallback = Callable[[RuntimeStreamEvent], Awaitable[None]]
STREAM_CALLBACK_KEY = "multi_agent_stream_callback"


class MultiAgentRuntime:
    """拆解领域任务、按依赖执行并由汇总 Agent 生成最终回答。"""

    def __init__(
        self,
        model_client: ModelClient,
        query_preprocessor: QueryPreprocessor | None = None,
        supervisor: Supervisor | None = None,
        agent_clients: dict[AgentName, Any] | None = None,
        synthesis_client: Any | None = None,
        system_prompt: str | None = None,
        max_tool_rounds: int = 4,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        catalog_tools: list[BaseTool] | None = None,
        order_tools: list[BaseTool] | None = None,
    ) -> None:
        self._model_client = model_client
        self._query_preprocessor = query_preprocessor or create_query_preprocessor()
        self._supervisor = supervisor or Supervisor()
        self._provided_agent_clients = dict(agent_clients or {})
        self._provided_synthesis_client = synthesis_client
        self._system_prompt = system_prompt or load_customer_service_prompt()
        self._max_tool_rounds = max_tool_rounds
        self._checkpointer = checkpointer
        self._catalog_tools = catalog_tools
        self._order_tools = order_tools
        self._client_cache: dict[AgentName, Any] = {}
        self._graph = self._build_graph()

    @property
    def graph(self) -> Any:
        return self._graph

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> RuntimeResult | RuntimePause:
        prepared = self._validate_messages(messages)
        state = await self._graph.ainvoke(
            self._initial_state(prepared),
            config=dict(config or {}),
        )
        return self._build_output(state)

    async def aresume(
        self,
        decision: dict[str, Any],
        config: RunnableConfig,
    ) -> RuntimeResult:
        """从当前会话的持久化中断点继续执行。"""

        if self._checkpointer is None:
            raise RuntimeErrorBase("当前运行时没有配置Checkpointer，无法恢复")
        state = await self._graph.ainvoke(Command(resume=decision), config=config)
        output = self._build_output(state)
        if isinstance(output, RuntimePause):
            raise RuntimeErrorBase("工作流恢复后仍然等待确认")
        return output

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> RuntimeResult:
        """为脚本和测试提供同步入口。"""

        return asyncio.run(self.ainvoke(messages, config=config))

    async def astream(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """并发执行领域任务，并按产生顺序返回任务、工具和回答事件。"""

        event_queue: asyncio.Queue[RuntimeStreamEvent | BaseException | None]
        event_queue = asyncio.Queue()

        async def publish(event: RuntimeStreamEvent) -> None:
            await event_queue.put(event)

        stream_config = self._with_callback(config, publish)

        async def run_graph() -> None:
            try:
                output = await self.ainvoke(messages, config=stream_config)
                if isinstance(output, RuntimePause):
                    event = RuntimeStreamEvent(
                        type="confirmation_required",
                        pause=output,
                    )
                else:
                    event = RuntimeStreamEvent(type="complete", result=output)
                await event_queue.put(event)
            except BaseException as error:
                await event_queue.put(error)
            finally:
                await event_queue.put(None)

        graph_task = asyncio.create_task(run_graph())
        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    break
                if isinstance(event, BaseException):
                    raise event
                yield event
        finally:
            if not graph_task.done():
                graph_task.cancel()
            with suppress(asyncio.CancelledError):
                await graph_task

    def _build_graph(self) -> Any:
        builder = StateGraph(MultiAgentState)
        builder.add_node("preprocess", self._preprocess)
        builder.add_node("supervisor", self._supervise)
        builder.add_node("execute_tasks", self._execute_tasks)
        builder.add_node("await_confirmation", self._await_confirmation)
        builder.add_node("synthesis", self._synthesize)
        builder.add_edge(START, "preprocess")
        builder.add_edge("preprocess", "supervisor")
        builder.add_edge("supervisor", "execute_tasks")
        builder.add_conditional_edges(
            "execute_tasks",
            self._route_after_tasks,
            {
                "await_confirmation": "await_confirmation",
                "synthesis": "synthesis",
            },
        )
        builder.add_edge("await_confirmation", "synthesis")
        builder.add_edge("synthesis", END)
        return builder.compile(checkpointer=self._checkpointer)

    async def _preprocess(
        self,
        state: MultiAgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        callback = self._callback(config)
        if callback is not None:
            await callback(RuntimeStreamEvent(type="router_start"))
        analysis = await asyncio.to_thread(
            self._query_preprocessor.analyze,
            state["messages"],
        )
        if callback is not None:
            await callback(
                RuntimeStreamEvent(type="router_end", query_analysis=analysis)
            )
        return {"query_analysis": analysis}

    async def _supervise(
        self,
        state: MultiAgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        callback = self._callback(config)
        if callback is not None:
            await callback(RuntimeStreamEvent(type="supervisor_start"))
        analysis = self._require_analysis(state)
        plan = self._supervisor.plan(analysis)
        if callback is not None:
            await callback(RuntimeStreamEvent(type="supervisor_end", agent_plan=plan))
        return {"agent_plan": plan}

    async def _execute_tasks(
        self,
        state: MultiAgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        plan = self._require_plan(state)
        pending = list(plan.tasks)
        results: list[AgentTaskResult] = []
        completed_ids: set[str] = set()

        while pending:
            ready: list[AgentTask] = []
            for task in pending:
                if set(task.depends_on).issubset(completed_ids):
                    ready.append(task)

            if not ready:
                for task in pending:
                    results.append(
                        AgentTaskResult(
                            task_id=task.id,
                            agent_name=task.agent_name,
                            status="skipped",
                            summary="任务依赖未满足，未执行。",
                        )
                    )
                break

            dependency_results = list(results)
            if plan.execution_mode == "parallel" and len(ready) > 1:
                batch_results = await asyncio.gather(
                    *[
                        self._run_task(task, state, dependency_results, config)
                        for task in ready
                    ]
                )
            else:
                batch_results = []
                for task in ready:
                    result = await self._run_task(
                        task,
                        state,
                        [*dependency_results, *batch_results],
                        config,
                    )
                    batch_results.append(result)

            results.extend(batch_results)
            for task in ready:
                completed_ids.add(task.id)
                pending.remove(task)

        model_calls = sum(result.model_calls for result in results)
        tool_calls = sum(result.tool_calls for result in results)
        tool_rounds = sum(result.tool_rounds for result in results)
        return {
            "task_results": results,
            "model_calls": model_calls,
            "tool_calls": tool_calls,
            "tool_rounds": tool_rounds,
        }

    def _route_after_tasks(self, state: MultiAgentState) -> str:
        """只有生产运行时发现待确认售后草稿时才暂停。"""

        if self._checkpointer is None:
            return "synthesis"
        task_results = self._validated_task_results(state)
        confirmation = self._find_confirmation(task_results)
        return "await_confirmation" if confirmation is not None else "synthesis"

    async def _await_confirmation(
        self,
        state: MultiAgentState,
    ) -> dict[str, dict[str, Any]]:
        """暂停工作流，直到买家明确确认或取消售后草稿。"""

        task_results = self._validated_task_results(state)
        confirmation = self._find_confirmation(task_results)
        if confirmation is None:
            raise RuntimeErrorBase("未找到需要确认的售后草稿")

        decision = interrupt(confirmation)
        if not isinstance(decision, dict):
            raise RuntimeInputError("确认结果格式不正确")
        action = decision.get("action")
        if action not in {"confirm", "cancel"}:
            raise RuntimeInputError("确认操作只能是confirm或cancel")
        if action == "confirm":
            confirmed_draft = decision.get("draft")
            original_draft = confirmation.get("draft")
            if not isinstance(confirmed_draft, dict):
                raise RuntimeInputError("确认售后申请时缺少最终草稿")
            if not isinstance(original_draft, dict):
                raise RuntimeErrorBase("中断状态中的售后草稿不完整")
            if confirmed_draft.get("order_id") != original_draft.get("order_id"):
                raise RuntimeInputError("恢复时不能更换售后申请对应的订单")
        return {"confirmation": dict(decision)}

    @staticmethod
    def _find_confirmation(
        results: list[AgentTaskResult],
    ) -> dict[str, Any] | None:
        """从结构化工具结果中提取需要买家确认的售后草稿。"""

        for result in results:
            for tool_result in result.tool_results:
                if tool_result.get("name") != "prepare_after_sale_draft":
                    continue
                output = tool_result.get("output")
                if not isinstance(output, dict):
                    continue
                artifact = output.get("artifact")
                if not isinstance(artifact, dict):
                    continue
                if artifact.get("requires_confirmation") is not True:
                    continue
                return artifact
        return None

    @staticmethod
    def _validated_task_results(state: MultiAgentState) -> list[AgentTaskResult]:
        """把Checkpoint恢复出的字典统一还原成任务结果模型。"""

        results: list[AgentTaskResult] = []
        for value in state.get("task_results", []):
            if isinstance(value, AgentTaskResult):
                results.append(value)
                continue
            results.append(AgentTaskResult.model_validate(value))
        return results

    async def _run_task(
        self,
        task: AgentTask,
        state: MultiAgentState,
        previous_results: list[AgentTaskResult],
        config: RunnableConfig,
    ) -> AgentTaskResult:
        """执行任务子图；恢复时直接复用已经完成的任务结果。"""

        if self._checkpointer is None:
            return await self._execute_task_body(
                task,
                state,
                previous_results,
                config,
            )

        async def execute_agent(
            _task_state: AgentTaskCheckpointState,
            config: RunnableConfig,
        ) -> dict[str, AgentTaskResult]:
            result = await self._execute_task_body(
                task,
                state,
                previous_results,
                config,
            )
            return {"result": result}

        builder = StateGraph(AgentTaskCheckpointState)
        builder.add_node("execute_agent", execute_agent)
        builder.add_edge(START, "execute_agent")
        builder.add_edge("execute_agent", END)
        task_graph = builder.compile(checkpointer=self._checkpointer)
        task_config = self._task_checkpoint_config(config, state, task)

        snapshot = await task_graph.aget_state(task_config)
        saved_result = snapshot.values.get("result") if snapshot.values else None
        if isinstance(saved_result, AgentTaskResult):
            return saved_result
        if isinstance(saved_result, dict):
            return AgentTaskResult.model_validate(saved_result)

        output = await task_graph.ainvoke(
            {"result": None},
            config=task_config,
        )
        result = output.get("result")
        if result is None:
            raise RuntimeErrorBase(f"任务子图没有返回结果：{task.id}")
        if isinstance(result, AgentTaskResult):
            return result
        return AgentTaskResult.model_validate(result)

    async def _execute_task_body(
        self,
        task: AgentTask,
        state: MultiAgentState,
        previous_results: list[AgentTaskResult],
        config: RunnableConfig,
    ) -> AgentTaskResult:
        """执行一个领域Agent的模型和工具循环。"""

        callback = self._callback(config)
        started = perf_counter()
        if callback is not None:
            await callback(
                RuntimeStreamEvent(
                    type="agent_start",
                    agent_name=task.agent_name.value,
                    agent_plan=self._require_plan(state),
                    task_id=task.id,
                )
            )

        client = self._client_for_task(task)
        tools = self._tools_for_task(task)
        tool_node = ToolNode(tools, handle_tool_errors=self._format_tool_error)
        agent_messages = self._task_messages(task, state, previous_results)
        collected_tool_results: list[dict[str, Any]] = []
        model_calls = 0
        tool_calls = 0
        tool_rounds = 0

        try:
            while True:
                response = await client.ainvoke(agent_messages, config=config)
                model_calls += 1
                self._validate_response(response, task)
                agent_messages.append(response)
                if not response.tool_calls:
                    result = AgentTaskResult(
                        task_id=task.id,
                        agent_name=task.agent_name,
                        status="completed",
                        summary=response.text.strip() or "任务已完成。",
                        tool_results=collected_tool_results,
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                        tool_rounds=tool_rounds,
                        duration_ms=self._duration_ms(started),
                    )
                    break

                if tool_rounds >= self._max_tool_rounds:
                    raise RuntimeLoopLimitError(
                        f"{task.agent_name.value} 连续调用工具超过限制"
                    )

                names = self._tool_names(response)
                if callback is not None:
                    await callback(
                        RuntimeStreamEvent(
                            type="tool_start",
                            agent_name=task.agent_name.value,
                            task_id=task.id,
                            tool_calls=len(response.tool_calls),
                            tool_names=names,
                        )
                    )

                tool_output = await tool_node.ainvoke(
                    {"messages": agent_messages},
                    config=config,
                )
                tool_messages = list(tool_output["messages"])
                agent_messages.extend(tool_messages)
                current_results = self._serialize_tool_results(response, tool_messages)
                collected_tool_results.extend(current_results)
                tool_rounds += 1
                tool_calls += len(response.tool_calls)

                if callback is not None:
                    await callback(
                        RuntimeStreamEvent(
                            type="tool_end",
                            agent_name=task.agent_name.value,
                            task_id=task.id,
                            tool_calls=len(response.tool_calls),
                            tool_names=names,
                            tool_results=tuple(current_results),
                        )
                    )
        except Exception as error:
            result = AgentTaskResult(
                task_id=task.id,
                agent_name=task.agent_name,
                status="failed",
                summary="领域任务执行失败。",
                tool_results=collected_tool_results,
                model_calls=model_calls,
                tool_calls=tool_calls,
                tool_rounds=tool_rounds,
                duration_ms=self._duration_ms(started),
                error=str(error),
            )

        if callback is not None:
            await callback(
                RuntimeStreamEvent(
                    type="agent_end",
                    agent_name=task.agent_name.value,
                    agent_plan=self._require_plan(state),
                    task_id=task.id,
                    task_result=result,
                )
            )
        return result

    @staticmethod
    def _task_checkpoint_config(
        config: RunnableConfig,
        state: MultiAgentState,
        task: AgentTask,
    ) -> RunnableConfig:
        """为每轮对话的每个Agent任务创建独立Checkpoint命名空间。"""

        result: RunnableConfig = dict(config)
        configurable = dict(result.get("configurable") or {})
        current_human_message: HumanMessage | None = None
        for message in reversed(state["messages"]):
            if isinstance(message, HumanMessage):
                current_human_message = message
                break

        message_id = "current-turn"
        if current_human_message is not None and current_human_message.id:
            message_id = str(current_human_message.id)
        configurable["checkpoint_ns"] = f"agent-task:{message_id}:{task.id}"
        result["configurable"] = configurable
        return result

    async def _synthesize(
        self,
        state: MultiAgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        callback = self._callback(config)
        if callback is not None:
            await callback(
                RuntimeStreamEvent(
                    type="synthesis_start",
                    agent_name=AgentName.SYNTHESIS.value,
                )
            )

        messages = self._synthesis_messages(state)
        client = self._synthesis_model_client()
        if callback is None:
            response = await client.ainvoke(messages, config=config)
        else:
            response = await self._stream_synthesis(client, messages, config, callback)

        if response.tool_calls:
            raise AgentPermissionError("汇总 Agent 不允许调用业务工具")
        if callback is not None:
            await callback(
                RuntimeStreamEvent(
                    type="synthesis_end",
                    agent_name=AgentName.SYNTHESIS.value,
                )
            )
        return {
            "final_reply": response,
            "messages": [response],
            "model_calls": state.get("model_calls", 0) + 1,
        }

    async def _stream_synthesis(
        self,
        client: Any,
        messages: list[BaseMessage],
        config: RunnableConfig,
        callback: StreamCallback,
    ) -> AIMessage:
        await callback(RuntimeStreamEvent(type="model_start"))
        combined: AIMessageChunk | None = None
        async for chunk in client.astream(messages, config=config):
            combined = chunk if combined is None else combined + chunk
            if chunk.text:
                await callback(RuntimeStreamEvent(type="delta", text=chunk.text))
        if combined is None:
            raise RuntimeErrorBase("汇总 Agent 没有返回内容")
        response = message_chunk_to_message(combined)
        if not isinstance(response, AIMessage):
            raise RuntimeErrorBase("汇总 Agent 没有生成有效消息")
        return response

    def _task_messages(
        self,
        task: AgentTask,
        state: MultiAgentState,
        previous_results: list[AgentTaskResult],
    ) -> list[BaseMessage]:
        analysis = self._require_analysis(state)
        dependency_payload = [
            result.model_dump(mode="json")
            for result in previous_results
            if result.task_id in task.depends_on
        ]
        task_prompt = (
            f"你是 {task.agent_name.value}。{task.description}\n"
            f"当前独立任务：{analysis.optimized_query}\n"
            f"只允许调用：{', '.join(task.allowed_tools) or '无工具'}。\n"
            "完成后请返回简洁、可供汇总 Agent 使用的事实结果。"
        )
        if dependency_payload:
            task_prompt += "\n依赖任务结果：" + json.dumps(
                dependency_payload,
                ensure_ascii=False,
            )
        return [
            SystemMessage(content=self._system_prompt),
            SystemMessage(content=task_prompt),
            *list(state["messages"]),
        ]

    def _synthesis_messages(self, state: MultiAgentState) -> list[BaseMessage]:
        task_results = self._validated_task_results(state)
        payload = []
        for result in task_results:
            payload.append(result.model_dump(mode="json"))
        prompt = (
            "你是电商客服的汇总 Agent。请只根据各领域 Agent 的结构化结果回答用户，"
            "合并重复信息，保留失败说明和知识来源，不得调用工具或编造事实。\n"
            f"领域结果：{json.dumps(payload, ensure_ascii=False)}"
        )
        confirmation = state.get("confirmation")
        if confirmation is not None:
            prompt += "\n买家确认结果：" + json.dumps(
                confirmation,
                ensure_ascii=False,
            )
        return [
            SystemMessage(content=prompt),
            *list(state["messages"]),
        ]

    def _client_for_task(self, task: AgentTask) -> Any:
        provided = self._provided_agent_clients.get(task.agent_name)
        if provided is not None:
            return provided
        cached = self._client_cache.get(task.agent_name)
        if cached is not None:
            return cached
        raw_model = getattr(self._model_client, "model", None)
        if raw_model is None:
            return self._model_client
        client = ModelClient(model=raw_model, tools=self._tools_for_task(task))
        self._client_cache[task.agent_name] = client
        return client

    def _synthesis_model_client(self) -> Any:
        if self._provided_synthesis_client is not None:
            return self._provided_synthesis_client
        cached = self._client_cache.get(AgentName.SYNTHESIS)
        if cached is not None:
            return cached
        raw_model = getattr(self._model_client, "model", None)
        if raw_model is None:
            return self._model_client
        client = ModelClient(model=raw_model, tools=[])
        self._client_cache[AgentName.SYNTHESIS] = client
        return client

    def _tools_for_task(self, task: AgentTask) -> list[BaseTool]:
        if task.agent_name is AgentName.ORDER and self._order_tools is not None:
            selected: list[BaseTool] = []
            for tool in self._order_tools:
                if tool.name in task.allowed_tools:
                    selected.append(tool)
            return selected
        if task.agent_name is AgentName.PRODUCT and self._catalog_tools is not None:
            selected: list[BaseTool] = []
            for tool in self._catalog_tools:
                if tool.name in task.allowed_tools:
                    selected.append(tool)
            return selected
        plan = AgentPlan(
            agents=[task.agent_name],
            primary_agent=task.agent_name,
            reason=task.description,
            allowed_tools=task.allowed_tools,
        )
        return tools_for_plan(plan)

    @staticmethod
    def _validate_response(response: AIMessage, task: AgentTask) -> None:
        if response.invalid_tool_calls:
            raise InvalidToolCallError("领域 Agent 生成了无法解析的工具参数")
        allowed = set(task.allowed_tools)
        for tool_call in response.tool_calls:
            name = str(tool_call.get("name", ""))
            if name not in allowed:
                raise AgentPermissionError(
                    f"{task.agent_name.value} 无权调用工具：{name or 'unknown'}"
                )

    @staticmethod
    def _tool_names(response: AIMessage) -> tuple[str, ...]:
        return tuple(str(call.get("name", "")) for call in response.tool_calls)

    @staticmethod
    def _serialize_tool_results(
        request: AIMessage,
        messages: Sequence[AnyMessage],
    ) -> list[dict[str, Any]]:
        names = {
            str(call.get("id", "")): str(call.get("name", "tool"))
            for call in request.tool_calls
        }
        results: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            output: Any = message.content
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except json.JSONDecodeError:
                    pass
            results.append(
                {
                    "name": names.get(message.tool_call_id, message.name or "tool"),
                    "output": output,
                }
            )
        return results

    @staticmethod
    def _format_tool_error(_error: Exception) -> str:
        return (
            '{"success":false,"error":{"code":"TOOL_EXECUTION_ERROR",'
            '"message":"业务工具执行失败，请检查参数后重试"}}'
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1000))

    @staticmethod
    def _validate_messages(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        prepared = list(messages)
        if not prepared or not isinstance(prepared[-1], HumanMessage):
            raise RuntimeInputError("对话必须以最新的用户消息结尾")
        if any(isinstance(message, SystemMessage) for message in prepared):
            raise RuntimeInputError("系统提示词只能由客服运行时设置")
        return prepared

    @staticmethod
    def _initial_state(messages: list[BaseMessage]) -> MultiAgentState:
        return {
            # 新一轮以会话服务组装的上下文为准，避免检查点把裁剪的历史重新追加。
            # Command(resume=...) 不经过这里，因此确认恢复仍使用原检查点。
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages],
            "query_analysis": None,
            "agent_plan": None,
            "task_results": [],
            "final_reply": None,
            "model_calls": 0,
            "tool_rounds": 0,
            "tool_calls": 0,
            "confirmation": None,
        }

    @staticmethod
    def _require_analysis(state: MultiAgentState) -> QueryAnalysis:
        analysis = state.get("query_analysis")
        if analysis is None:
            raise RuntimeErrorBase("缺少 Query 分析结果")
        return analysis

    @staticmethod
    def _require_plan(state: MultiAgentState) -> AgentPlan:
        plan = state.get("agent_plan")
        if plan is None:
            raise RuntimeErrorBase("缺少 Supervisor 调度计划")
        return plan

    @staticmethod
    def _build_output(state: MultiAgentState) -> RuntimeResult | RuntimePause:
        interrupts = state.get("__interrupt__", [])  # type: ignore[typeddict-item]
        if interrupts:
            confirmation = interrupts[0].value
            if not isinstance(confirmation, dict):
                raise RuntimeErrorBase("工作流中断数据格式不正确")
            return RuntimePause(
                confirmation=confirmation,
                model_calls=state.get("model_calls", 0),
                tool_rounds=state.get("tool_rounds", 0),
                tool_calls=state.get("tool_calls", 0),
                query_analysis=state.get("query_analysis"),
                agent_plan=state.get("agent_plan"),
                task_results=tuple(
                    MultiAgentRuntime._validated_task_results(state)
                ),
            )

        reply = state.get("final_reply")
        if not isinstance(reply, AIMessage):
            raise RuntimeErrorBase("汇总 Agent 没有生成最终回复")
        return RuntimeResult(
            reply=reply,
            messages=tuple(state["messages"]),
            model_calls=state.get("model_calls", 0),
            tool_rounds=state.get("tool_rounds", 0),
            tool_calls=state.get("tool_calls", 0),
            query_analysis=state.get("query_analysis"),
            agent_plan=state.get("agent_plan"),
            task_results=tuple(MultiAgentRuntime._validated_task_results(state)),
        )

    @staticmethod
    def _with_callback(
        config: RunnableConfig | None,
        callback: StreamCallback,
    ) -> RunnableConfig:
        result: RunnableConfig = dict(config or {})
        configurable = dict(result.get("configurable") or {})
        configurable[STREAM_CALLBACK_KEY] = callback
        result["configurable"] = configurable
        return result

    @staticmethod
    def _callback(config: RunnableConfig) -> StreamCallback | None:
        configurable = config.get("configurable") or {}
        callback = configurable.get(STREAM_CALLBACK_KEY)
        return callback if callable(callback) else None


def create_multi_agent_runtime(
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> MultiAgentRuntime:
    """使用当前模型和语义路由配置创建生产多 Agent 运行时。"""

    from app.core.config import settings
    from app.mcp.client import CatalogMcpClient, OrderMcpClient

    catalog_tools = None
    if settings.catalog_mcp_enabled:
        client = CatalogMcpClient(
            settings.catalog_mcp_url,
            settings.catalog_mcp_timeout_seconds,
        )
        catalog_tools = client.tools()

    order_tools = None
    if settings.order_mcp_enabled:
        order_client = OrderMcpClient(
            settings.order_mcp_url,
            settings.catalog_mcp_timeout_seconds,
        )
        order_tools = order_client.tools()

    return MultiAgentRuntime(
        model_client=create_model_client(),
        query_preprocessor=create_query_preprocessor(),
        checkpointer=checkpointer,
        catalog_tools=catalog_tools,
        order_tools=order_tools,
    )
