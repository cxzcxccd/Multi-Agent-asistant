"""明确的售后政策短语及补充原因的对话上下文。"""

import re
import unicodedata
from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def is_short_return_policy(query: str) -> bool:
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", query))
    return bool(re.fullmatch(
        r"(?:什么是|介绍一下|解释一下|关于)?(?:七|7)(?:天|日)无理由"
        r"(?:退货)?(?:的)?(?:政策|规则|条件|适用条件|是什么意思|是什么|怎么规定)?[?？。!！]*",
        text,
    ))


def is_bare_return_reason(query: str) -> bool:
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", query))
    return bool(re.fullmatch(r"(?:七|7)(?:天|日)无理由(?:退货)?[。!！]*", text))


def return_reason_context(messages: Sequence[BaseMessage]) -> HumanMessage | None:
    """只延续最近一轮明确要求补充原因的退货请求，不采用助手举例的订单。"""
    if not messages or not is_bare_return_reason(str(messages[-1].content)):
        return None
    recent = [item for item in messages[:-1] if isinstance(item, (HumanMessage, AIMessage))]
    if len(recent) < 2:
        return None
    user, assistant = recent[-2:]
    if not isinstance(user, HumanMessage) or not isinstance(assistant, AIMessage):
        return None
    if "退货" not in str(user.content):
        return None
    answer = str(assistant.content)
    if re.search(r"(?:已取消|未保留|取消了|申请已提交|已转交人工)", answer):
        return None
    if re.search(r"请[^。\n]*?(?:补充|填写|回复|提供)[^。\n]*?(?:原因|问题描述)", answer):
        return user
    return None
