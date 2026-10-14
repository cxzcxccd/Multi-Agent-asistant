"""Load stable rules and exactly one domain's workflow instructions."""

from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"
DOMAINS = {"product_agent", "order_agent", "after_sales_agent", "knowledge_agent", "general_agent"}


def resident_prompt() -> str:
    return (PROMPTS / "resident.md").read_text(encoding="utf-8")


def domain_prompt(agent: str) -> str:
    if agent not in DOMAINS:
        raise ValueError("Unknown context domain")
    return (PROMPTS / "skills" / f"{agent}.md").read_text(encoding="utf-8")
