"""Request-scoped JSON tracing, including async tasks and worker threads."""

import asyncio
import inspect
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from time import perf_counter
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler


context: ContextVar[dict] = ContextVar("trace_context", default={})
logger = logging.getLogger("app.trace")


def configure_tracing():
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def emit(event, **fields):
    logger.info(json.dumps({"timestamp": datetime.now(UTC).isoformat(),
                            **context.get(), "event": event, **fields}, ensure_ascii=False, default=str))


@contextmanager
def span(name, **fields):
    token = context.set({**context.get(), **fields})
    started = perf_counter()
    outcome = {}
    emit(f"{name}.start")
    try:
        yield outcome
    except BaseException as error:
        emit(f"{name}.cancelled" if isinstance(error, (asyncio.CancelledError, GeneratorExit))
             else f"{name}.failed", error_type=type(error).__name__,
             duration_ms=round((perf_counter() - started) * 1000, 2))
        raise
    else:
        emit(f"{name}.failed" if outcome.get("failed") else f"{name}.end",
             duration_ms=round((perf_counter() - started) * 1000, 2))
    finally:
        context.reset(token)


def traced(name):
    def decorate(function):
        def fields(args, kwargs):
            bound = inspect.signature(function).bind(*args, **kwargs).arguments
            task = bound.get("task")
            values = {"task_id": task.id, "agent": task.agent_name.value} if task is not None else {}
            if name == "synthesis":
                values["agent"] = "synthesis_agent"
            if name.startswith("workflow"):
                if not context.get():
                    values.update(trace_id=str(uuid4()), run_id=str(uuid4()))
                config = bound.get("config") or {}
                conversation_id = config.get("configurable", {}).get("thread_id")
                if conversation_id:
                    values["conversation_id"] = conversation_id
            return values

        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with span(name, **fields(args, kwargs)) as outcome:
                    result = await function(*args, **kwargs)
                    if getattr(result, "status", None) == "failed":
                        outcome["failed"] = True
                    return result
            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            with span(name, **fields(args, kwargs)):
                return function(*args, **kwargs)
        return synchronous
    return decorate


class ToolTraceCallback(BaseCallbackHandler):
    """Track individual tool calls, including errors converted to ToolMessages."""

    def __init__(self):
        self.calls = {}

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = (serialized or {}).get("name")
        self.calls[run_id] = (perf_counter(), name, dict(context.get()))
        emit("tool.start", tool=name, tool_call_id=str(run_id))

    def finish(self, event, run_id, **fields):
        started, name, identifiers = self.calls.pop(run_id, (perf_counter(), None, context.get()))
        emit(event, **identifiers, tool=name, tool_call_id=str(run_id),
             duration_ms=round((perf_counter() - started) * 1000, 2), **fields)

    def on_tool_end(self, output, *, run_id, **kwargs):
        if run_id not in self.calls:
            return
        failed = getattr(output, "status", None) == "error"
        content = getattr(output, "content", output)
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (ValueError, TypeError):
                pass
        if isinstance(content, dict) and content.get("success") is False:
            failed = True
        self.finish("tool.failed" if failed else "tool.end", run_id)

    def on_tool_error(self, error, *, run_id, **kwargs):
        self.finish("tool.failed", run_id, error_type=type(error).__name__)


class TraceMiddleware:
    """Pure ASGI middleware keeps context alive for the entire streaming body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = context.set({"trace_id": str(uuid4()), "run_id": str(uuid4())})
        status = None
        disconnected = False

        async def traced_receive():
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect" and not disconnected:
                disconnected = True
                emit("request.disconnected")
            return message

        async def traced_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []),
                    (b"x-trace-id", context.get()["trace_id"].encode()),
                    (b"x-run-id", context.get()["run_id"].encode())]}
            await send(message)

        try:
            with span("request", method=scope["method"], path=scope["path"]) as outcome:
                await self.app(scope, traced_receive, traced_send)
                emit("request.result", status=status)
                if status is not None and status >= 400:
                    outcome["failed"] = True
        finally:
            context.reset(token)
