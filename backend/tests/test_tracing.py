"""Tracing must survive concurrency, streaming and handled tool failures."""

import asyncio
import json
import logging
from uuid import uuid4

import pytest
from langchain_core.messages import ToolMessage

from app.core.tracing import TraceMiddleware, ToolTraceCallback, context, emit, logger, span


@pytest.fixture
def events():
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(json.loads(record.getMessage()))

    handler = Capture()
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(old_level)


def test_streaming_requests_and_worker_threads_are_isolated(events):
    responses = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(0)
        await asyncio.to_thread(emit, "retrieval.result")
        await send({"type": "http.response.body", "body": b"done"})

    async def send(message):
        if message["type"] == "http.response.start":
            responses.append(dict(message["headers"]))

    async def run():
        middleware = TraceMiddleware(app)
        await asyncio.gather(*(middleware({"type": "http", "method": "POST", "path": "/api/chat/stream"}, None, send)
                               for _ in range(2)))
        assert context.get() == {}

    asyncio.run(run())
    assert len({r[b"x-trace-id"] for r in responses}) == 2
    for response in responses:
        group = [event for event in events if event["trace_id"] == response[b"x-trace-id"].decode()]
        assert [e["event"] for e in group] == ["request.start", "retrieval.result", "request.result", "request.end"]
        assert all(e["run_id"] == response[b"x-run-id"].decode() for e in group)


def test_failure_cancellation_and_context_cleanup(events):
    with pytest.raises(ValueError):
        with span("router", trace_id="trace", run_id="run"):
            raise ValueError("private user content")
    with pytest.raises(asyncio.CancelledError):
        with span("agent", trace_id="trace", run_id="run"):
            raise asyncio.CancelledError()
    assert [e["event"] for e in events] == ["router.start", "router.failed", "agent.start", "agent.cancelled"]
    assert "private user content" not in json.dumps(events)
    assert context.get() == {}


def test_handled_tool_error_keeps_agent_identity(events):
    callback = ToolTraceCallback()
    call_id = uuid4()
    with span("agent", trace_id="trace", run_id="run", task_id="task-1", agent="knowledge_agent"):
        callback.on_tool_start({"name": "search_knowledge"}, "private query", run_id=call_id)
        callback.on_tool_end(ToolMessage(content='{"success":false}', tool_call_id="model-call"), run_id=call_id)
    event = next(e for e in events if e["event"] == "tool.failed")
    assert event["tool"] == "search_knowledge"
    assert event["task_id"] == "task-1"
    assert event["duration_ms"] >= 0
    assert "private query" not in json.dumps(events)


def test_complete_graph_traces_router_agents_tools_and_retrieval(events):
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import tool
    from app.ai.multi_agent.runtime import MultiAgentRuntime
    from app.ai.multi_agent.schemas import AgentName
    from app.ai.query_preprocessor import IntentName
    from tests.test_multi_agent_runtime import FixedPreprocessor, SynthesisClient

    @tool
    def search_knowledge(query: str) -> dict:
        """Search test knowledge."""
        with span("retrieval"):
            emit("retrieval.result", total=1)
        return {"success": True}

    class Client:
        async def ainvoke(self, messages, config=None):
            if any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage(content="done")
            return AIMessage(content="", tool_calls=[{"name": "search_knowledge", "args": {"query": "test"}, "id": "call-1"}])

    class Runtime(MultiAgentRuntime):
        def _tools_for_task(self, task):
            return [search_knowledge]

    async def run():
        client = Client()
        runtime = Runtime(model_client=client, query_preprocessor=FixedPreprocessor(IntentName.KNOWLEDGE_INQUIRY),
                          agent_clients={AgentName.KNOWLEDGE: client}, synthesis_client=SynthesisClient("answer"))
        with span("request", trace_id="trace", run_id="run"):
            await runtime.ainvoke([HumanMessage(content="test")], config={
                "callbacks": [ToolTraceCallback()], "configurable": {"thread_id": "conversation"}})

    asyncio.run(run())
    names = [event["event"] for event in events]
    for name in ["router.start", "supervisor.end", "agent.end", "tool.start", "tool.end", "retrieval.end", "synthesis.end"]:
        assert name in names
    assert all(event["trace_id"] == "trace" and event["run_id"] == "run" for event in events)
    retrieval = next(event for event in events if event["event"] == "retrieval.result")
    assert retrieval["agent"] == "knowledge_agent"
    assert retrieval["task_id"]
    assert retrieval["conversation_id"] == "conversation"
