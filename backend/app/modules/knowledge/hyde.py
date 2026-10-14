"""Generate retrieval-only hypothetical documents; never use them as evidence."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings

HYDE_PROMPT_PATH = Path(__file__).resolve().parents[2] / "ai" / "prompts" / "hyde.md"


def build_hyde_prompt(max_chars: int) -> str:
    """Render the versioned prompt with the same limit enforced by the generator."""
    return HYDE_PROMPT_PATH.read_text(encoding="utf-8").replace("{{max_chars}}", str(max_chars))


@dataclass(frozen=True)
class HypotheticalDocument:
    text: str
    usage: dict[str, int] | None = None


class HyDEGenerator(Protocol):
    def generate(self, query: str) -> HypotheticalDocument: ...


class ModelHyDEGenerator:
    def __init__(self, model_client=None) -> None:
        self.model_client = model_client

    def generate(self, query: str) -> HypotheticalDocument:
        # Lazy import avoids the model_client -> tools -> knowledge service cycle.
        from app.ai.model_client import create_model_client

        client = self.model_client
        if client is None:
            config = settings.model_copy(update={
                "model_temperature": 0,
                "model_timeout_seconds": settings.knowledge_hyde_timeout_seconds,
                "model_max_retries": 0,
            })
            client = create_model_client(config, tools=[])
        response = client.invoke([
            SystemMessage(content=build_hyde_prompt(settings.knowledge_hyde_max_chars)),
            HumanMessage(content=query[:2000]),
        ])
        if response.tool_calls or not isinstance(response.content, str):
            raise ValueError("HyDE requires plain text without tool calls")
        text = response.content.strip()
        if not text:
            raise ValueError("HyDE returned an empty document")
        usage = response.usage_metadata
        return HypotheticalDocument(
            text=text[:settings.knowledge_hyde_max_chars],
            usage=dict(usage) if usage else None,
        )
