"""结构化需求理解、槽位检查与工具调用前置条件。

模型只提取操作和用户明确提供的事实；工具权限和缺失字段由代码决定。
本模块不读取评测标签，不把模型生成的编号当作可信业务对象。
"""

import re
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from app.ai.context import read_context, context_message
from app.ai.context import CONTEXT_KEY
from app.ai.context_budget import prepare_context
from pydantic import BaseModel, ConfigDict, Field, model_validator, PrivateAttr
from app.ai.after_sale_queries import (
    is_short_return_policy, is_bare_return_reason, return_reason_context,
)


Operation = Literal[
    "policy", "usage_help", "service_info", "order_detail", "order_list", "logistics", "refund_detail",
    "refund_list", "after_sale_apply", "product_search", "product_detail",
    "general", "unclear", "unsupported",
]


class RequestSlots(BaseModel):
    """槽位值必须能在当前原话或经过授权解析的业务对象中找到依据。"""

    model_config = ConfigDict(extra="forbid")
    order_id: str | None = None
    request_id: str | None = None
    product_id: str | None = None
    product_type: str | None = None
    request_type: Literal["退货", "换货"] | None = None
    reason: str | None = None
    reference_position: int | None = Field(default=None, ge=1, le=100)


class UnderstoodTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation
    evidence: str = Field(min_length=1, max_length=1000)
    slots: RequestSlots = Field(default_factory=RequestSlots)
    needs_context: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_slot_placement(cls, value: Any) -> Any:
        """兼容模型把已知槽位放在任务顶层的情况，仍拒绝未知字段和冲突值。"""
        if not isinstance(value, dict):
            return value
        result = dict(value)
        misplaced = set(result).intersection(RequestSlots.model_fields)
        if not misplaced:
            return result
        slots = result.get("slots", {})
        if not isinstance(slots, dict):
            return result
        slots = dict(slots)
        for key in misplaced:
            item = result.pop(key)
            if key in slots and slots[key] != item:
                raise ValueError("同一槽位包含冲突值：" + key)
            slots[key] = item
        result["slots"] = slots
        return result


class QueryUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[UnderstoodTask] = Field(min_length=1, max_length=8)
    _model_response: Any = PrivateAttr(default=None)


class ClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_query: str
    tasks: list[UnderstoodTask]
    missing_fields: list[str]
    question: str
    attempts: int = Field(default=1, ge=1)


class BusinessReference(BaseModel):
    """保留工具实际返回的对象顺序，编号仍须在执行时重新核验归属。"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["order", "refund", "product"]
    id: str


TOOL_OPERATIONS: dict[str, set[Operation]] = {
    "search_knowledge": {"policy", "usage_help", "service_info"},
    "list_orders": {"order_detail", "order_list", "logistics", "after_sale_apply"},
    "get_order": {"order_detail", "logistics", "after_sale_apply"},
    "get_logistics": {"logistics"},
    "list_after_sales": {"refund_detail", "refund_list", "after_sale_apply"},
    "get_after_sale": {"refund_detail"},
    "prepare_after_sale_draft": {"after_sale_apply"},
    "search_products": {"product_search"},
    "get_product": {"product_detail", "product_search"},
}


UNDERSTANDING_PROMPT = """提取电商用户的明确诉求，返回给定结构，不执行工具。
operation定义：policy是一般规则、条件、时效或使用方法；个人订单/到账/金额/库存不能由规则确定。
usage_help是设备、软件、支付方式的使用或故障排查，包括用户直接陈述的故障。
service_info是公开的品牌售后电话、维修网点地址、维修收费、检测流程或时效咨询。
这两类即使知识库没有答案，也先检索，再根据证据决定回答或拒答，不能预判为unsupported。
order_detail查询某笔订单；order_list列出自己的订单；logistics查某笔物流；
refund_detail查询具体售后/退款；refund_list列出售后；after_sale_apply明确申请退货或换货；
product_search搜索或推荐商品；product_detail查询指定商品；general普通聊天；
unclear语句不完整、只有名词、指代不明或不清楚具体诉求；unsupported当前工具无法完成的操作。
unsupported只用于请求执行当前不支持的业务操作，不用于一般信息查询。
联系当前订单的具体配送人员属于logistics；查询公共配送客服电话属于service_info。
一般丢件处理、赔付规则属于policy；查询我那笔丢件的处理进度属于logistics。
一般退款时效、原路退回规则属于policy；“我退的款到哪里了”属于refund_detail。
只有“退款金额”或“但是只收到一个”应为unclear，不自行补成规则问题。
明确问政策时即使提到自己的经历也可用policy，复合问题可提取多个任务。
evidence必须引用用户原话中的连续片段；槽位只提取明确事实，不编造编号、商品或原因。
申请取消订单不是退货草稿，使用unsupported。预算不是商品推荐的必填字段。
退货或换货申请的原因可以暂时缺失，先生成草稿，再由用户在草稿中补充；不要编造原因。
补充回复需要与待处理任务一起理解，但不得把追问中举的示例当成用户事实。
needs_context表示缺少前文就无法确定含义，例如对象不明确的价格比较、
省略谁收到什么的“到了就检测吗”以及依赖前文的拒收决定；不能自行假设条件。
明确的通用规则或故障现象不因句子短就判不完整。带清楚对象的知识问题无需订单号。
“七天无理由”“7天无理由退货”等明确政策术语属于policy，不属于general或unclear；
若正在补充某笔退货申请的原因，则延续after_sale_apply，把该短语作为reason。
业务字段必须放在tasks[].slots里，不要放在tasks[]顶层。
"""


class QueryUnderstandingService:
    def __init__(self, model: Any) -> None:
        # 工具调用型结构化输出有Pydantic校验，不依赖自由文本JSON解析。
        # 当前兼容服务的思考模式不接受强制tool_choice，使用auto并在后端严格校验。
        self._extractor = model.bind_tools([QueryUnderstanding], tool_choice="auto")
        self.last_usage: dict[str, int] | None = None

    async def analyze(
        self,
        messages: Sequence[BaseMessage],
        pending: ClarificationRequest | None = None,
    ) -> QueryUnderstanding:
        current = str(messages[-1].content)
        self.last_usage = None
        if is_short_return_policy(current):
            if is_bare_return_reason(current) and pending is not None:
                apply_tasks = [task for task in pending.tasks if task.operation == "after_sale_apply"]
                if len(apply_tasks) == 1:
                    task = apply_tasks[0].model_copy(deep=True)
                    task.evidence = current.strip()
                    task.needs_context = False
                    task.slots.reason = current.strip()
                    task.slots.request_type = "退货"
                    return QueryUnderstanding(tasks=[
                        task if item is apply_tasks[0] else item.model_copy(deep=True)
                        for item in pending.tasks
                    ])
            previous = return_reason_context(messages)
            if previous is not None:
                # 只从用户上一条明确退货请求读取订单，不从助手示例猜测。
                ids = set(re.findall(r"(?<!\d)\d{5}(?!\d)", str(previous.content)))
                order_id = next(iter(ids)) if len(ids) == 1 else None
                return QueryUnderstanding(tasks=[UnderstoodTask(
                    operation="after_sale_apply", evidence=current.strip(),
                    slots=RequestSlots(order_id=order_id, request_type="退货", reason=current.strip()),
                )])
            return QueryUnderstanding(tasks=[UnderstoodTask(operation="policy", evidence=current.strip())])
        context = list(messages[-12:])
        memory = next((message for message in messages if CONTEXT_KEY in message.additional_kwargs),
                      context_message(read_context(messages)))
        context.insert(0, memory)
        if pending is not None:
            context.insert(0, HumanMessage(content="待补充的原始请求：" + pending.original_query,
                                          additional_kwargs={"context_required": True}))
        instruction = UNDERSTANDING_PROMPT + "\n请调用QueryUnderstanding函数提交结果，不生成普通文本回复。"
        prepared, _report = prepare_context([SystemMessage(content=instruction), *context], [QueryUnderstanding])
        response = await self._extractor.ainvoke(prepared)
        self.last_usage = getattr(response, "usage_metadata", None)
        if response.invalid_tool_calls or len(response.tool_calls) != 1:
            raise ValueError("结构化需求理解返回格式不正确")
        call = response.tool_calls[0]
        if call["name"] != "QueryUnderstanding":
            raise ValueError("需求理解返回了未授权的工具")
        result = QueryUnderstanding.model_validate(call["args"])
        result._model_response = response
        # 拒绝没有原话依据的提取结果。历史对象只能由后面的对象解析器补充。
        source = current
        if pending is not None:
            source = pending.original_query + "\n" + current
        for task in result.tasks:
            if task.evidence not in source:
                raise ValueError("需求提取的证据不属于用户原话")
            for field in ("order_id", "request_id", "product_id", "product_type", "reason"):
                value = getattr(task.slots, field)
                if value is not None and value not in source:
                    raise ValueError("需求提取的槽位缺少原话依据：" + field)
        return result


def apply_conversation_slots(tasks: list[UnderstoodTask], messages: Sequence[BaseMessage]) -> None:
    """User-selected objects are query hints; every tool must still verify ownership."""
    state = read_context(messages)
    current = str(messages[-1].content)
    correction = bool(re.search(r"改成|改为|换成|更正为|应该是|不是.+?是", current))
    current_id = str(messages[-1].id) if messages[-1].id else None
    for task in tasks:
        if task.operation in {"order_detail", "logistics", "after_sale_apply", "refund_detail"}:
            entity = state.order
            if entity is None or entity.value is None:
                continue
            refers = bool(re.search(r"这单|这笔|这个订单|那笔|该订单|刚才", task.evidence))
            if correction and entity.source_message_id == current_id and entity.evidence == current[:200]:
                task.slots.order_id = entity.value
            elif task.slots.order_id is None and refers:
                task.slots.order_id = entity.value
        if task.operation == "product_detail" and task.slots.product_id is None:
            entity = state.sku
            if entity and entity.value and re.search(r"这款|这个|它|刚才", task.evidence):
                task.slots.product_id = entity.value


def resolve_references(tasks: list[UnderstoodTask], references: list[BusinessReference]) -> None:
    """只在引用表达明确且对象唯一或序号有效时补全，不猜测多笔订单。"""

    kinds = {
        "order_detail": ("order", "order_id"), "logistics": ("order", "order_id"),
        "after_sale_apply": ("order", "order_id"), "refund_detail": ("refund", "request_id"),
        "product_detail": ("product", "product_id"),
    }
    for task in tasks:
        target = kinds.get(task.operation)
        if target is None:
            continue
        kind, field = target
        if getattr(task.slots, field) is not None:
            continue
        candidates: list[BusinessReference] = []
        for reference in references:
            if reference.kind == kind:
                candidates.append(reference)
        position = task.slots.reference_position
        if position is not None and position <= len(candidates):
            setattr(task.slots, field, candidates[position - 1].id)
        elif len(candidates) == 1 and re.search(r"这个|这笔|刚才|上面|那笔", task.evidence):
            setattr(task.slots, field, candidates[0].id)


def find_missing_fields(tasks: list[UnderstoodTask]) -> list[str]:
    """规则决定业务必填项；产品推荐不强制预算，列表查询不强制编号。"""

    missing: list[str] = []
    for task in tasks:
        fields: list[str] = []
        if task.needs_context:
            fields.append("明确诉求")
        if task.operation in {"order_detail", "logistics", "after_sale_apply"}:
            fields.append("order_id")
        if task.operation == "refund_detail" and not task.slots.order_id:
            fields.append("request_id")
        if task.operation == "product_detail":
            fields.append("product_id")
        if task.operation == "product_search":
            fields.append("product_type")
        if task.operation == "after_sale_apply":
            fields.append("request_type")
        if task.operation == "unclear":
            fields.append("明确诉求")
        for field in fields:
            if field == "明确诉求" or getattr(task.slots, field) is None:
                if field not in missing:
                    missing.append(field)
    return missing


def clarification_question(missing: list[str], tasks: list[UnderstoodTask]) -> str:
    labels = {
        "order_id": "要处理的订单编号", "request_id": "要查询的售后申请编号或订单编号",
        "product_id": "要查询的商品", "product_type": "想购买的商品类型",
        "request_type": "申请退货还是换货", "reason": "申请售后的具体原因",
        "明确诉求": "你想查询或处理的具体问题",
    }
    if missing == ["明确诉求"]:
        for task in tasks:
            if "退款" in task.evidence:
                return "你想查询退款进度、到账去向，还是退款金额不完整？请补充具体情况。"
    parts: list[str] = []
    for field in missing:
        parts.append(labels[field])
    return "请补充" + "、".join(parts) + "，我再继续处理。"


def tool_precondition(
    name: str,
    arguments: dict[str, Any],
    tasks: list[UnderstoodTask],
    references: list[BusinessReference] | None = None,
) -> str | None:
    """执行门禁：操作权限与编号/原因必须来自已解析槽位，而非模型临时编造。"""

    allowed = TOOL_OPERATIONS.get(name, set())
    applicable: list[UnderstoodTask] = []
    for task in tasks:
        if task.operation in allowed:
            applicable.append(task)
    if not applicable:
        return "当前诉求不允许调用此工具，请澄清具体业务。"
    required_fields = {
        "get_order": ["order_id"], "get_logistics": ["order_id"],
        "get_after_sale": ["request_id"], "get_product": ["product_id"],
        "prepare_after_sale_draft": ["order_id", "request_type"],
        "search_knowledge": ["query"],
    }
    for field in required_fields.get(name, []):
        if arguments.get(field) is None or arguments.get(field) == "":
            return "工具缺少必要参数，请补充：" + field
    for field in ("order_id", "request_id", "product_id", "request_type", "reason"):
        value = arguments.get(field)
        if value is None or (field == "reason" and value == ""):
            continue
        matched = False
        for task in applicable:
            if str(getattr(task.slots, field)) == str(value):
                matched = True
                break
        kinds = {"order_id": "order", "request_id": "refund", "product_id": "product"}
        if field in kinds:
            for reference in references or []:
                if reference.kind == kinds[field] and reference.id == str(value):
                    matched = True
                    break
        if not matched:
            return "工具参数缺少已确认的用户或业务对象依据：" + field
    return None


def operation_allows_tool(name: str, tasks: list[UnderstoodTask]) -> bool:
    """计划阶段只检查操作权限，实际参数在执行门禁中单独检查。"""

    for task in tasks:
        if task.operation in TOOL_OPERATIONS.get(name, set()):
            return True
    return False


def collect_references(results: list[dict[str, Any]]) -> list[BusinessReference]:
    """仅从成功的真实工具返回中提取对象，保留列表顺序。"""

    references_by_kind: dict[str, list[BusinessReference]] = {}
    list_kinds: set[str] = set()
    mapping = {
        "list_orders": ("order", "orders"), "get_order": ("order", "order"),
        "list_after_sales": ("refund", "requests"), "get_after_sale": ("refund", "request"),
        "search_products": ("product", "items"), "get_product": ("product", "product"),
    }
    for result in results:
        target = mapping.get(result.get("name"))
        output = result.get("output")
        if target is None or not isinstance(output, dict):
            continue
        if result.get("name") == "search_products":
            successful = isinstance(output.get("items"), list)
        else:
            successful = output.get("success") is True
        if not successful:
            continue
        kind, field = target
        objects = output.get(field, [])
        is_list = isinstance(objects, list)
        if isinstance(objects, dict):
            objects = [objects]
        if not isinstance(objects, list):
            continue
        batch = []
        for item in objects:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            reference = BusinessReference(kind=kind, id=str(item["id"]))
            if reference not in batch:
                batch.append(reference)
        if is_list:
            # 多次搜索后“第二个”指向最新列表，详情查询不重排已展示列表。
            references_by_kind[kind] = batch
            list_kinds.add(kind)
        elif kind not in list_kinds:
            references_by_kind[kind] = batch
    references = []
    for batch in references_by_kind.values():
        references.extend(batch)
    return references
