"""记录模型提供的真实用量和实际执行的 MCP 工具。"""

from typing import Any
from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ModelUsage(BaseModel):
    stage: str
    model_name: str | None = None
    usage: TokenUsage | None = None


class McpUsage(BaseModel):
    server: str
    tool: str
    calls: int = 0
    successes: int = 0


class RunTelemetry(BaseModel):
    model_name: str | None = None
    model_names: list[str] = Field(default_factory=list)
    token_usage: TokenUsage | None = None
    usage_calls: int = 0
    model_calls: int = 0
    mcp_enabled: list[str] = Field(default_factory=list)
    mcp_calls: list[McpUsage] = Field(default_factory=list)


def measure_model(response: Any, stage: str, model_name: str | None = None) -> ModelUsage:
    metadata = getattr(response, "response_metadata", {}) or {}
    model_name = metadata.get("model_name") or metadata.get("model") or model_name
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        raw = metadata.get("token_usage") or {}
        usage = {"input_tokens": raw.get("prompt_tokens"), "output_tokens": raw.get("completion_tokens"), "total_tokens": raw.get("total_tokens")}
    keys = ("input_tokens", "output_tokens", "total_tokens")
    valid = all(isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and usage[key] >= 0 for key in keys)
    return ModelUsage(stage=stage, model_name=model_name, usage=TokenUsage(**{key: usage[key] for key in keys}) if valid else None)


def summarize(model_name: str | None, measurements: list[ModelUsage], results: list[Any], enabled: list[str], calls: int) -> RunTelemetry:
    known = [item.usage for item in measurements if item.usage is not None]
    totals = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0) if known or calls == 0 else None
    if totals is not None:
        for usage in known:
            totals.input_tokens += usage.input_tokens
            totals.output_tokens += usage.output_tokens
            totals.total_tokens += usage.total_tokens
    tools: dict[tuple[str, str], McpUsage] = {}
    for task in results:
        for result in task.tool_results:
            if result.get("transport") != "mcp":
                continue
            server, name = result["server"], result["name"]
            key = (server, name)
            item = tools.setdefault(key, McpUsage(server=server, tool=name))
            item.calls += 1
            output = result.get("output")
            if isinstance(output, dict) and output.get("success") is not False:
                item.successes += 1
    names = list(dict.fromkeys(item.model_name for item in measurements if item.model_name))
    return RunTelemetry(model_name=model_name or (names[0] if names else None), model_names=names,
                        token_usage=totals, usage_calls=len(known), model_calls=calls,
                        mcp_enabled=enabled, mcp_calls=list(tools.values()))
