"""分层调用和冲突仲裁。规则只授权领域路由，不授权业务写操作。"""

import re

from app.ai.query_preprocessor import IntentName, QueryAnalysis


INTENT_LABELS = {
    IntentName.PRODUCT_INQUIRY: "商品咨询",
    IntentName.ORDER_INQUIRY: "订单或物流查询",
    IntentName.AFTER_SALE: "个人售后查询或申请",
    IntentName.KNOWLEDGE_INQUIRY: "通用规则或使用说明咨询",
    IntentName.HUMAN_SERVICE: "人工客服",
    IntentName.GENERAL_CONVERSATION: "普通对话",
}

COMPLEX = re.compile(r"[，,；;。]|以及|并且|同时|然后|再|不要|不用|不是|别|如果|否则|的话|之前|上次|这个|那个|它|刚才")
POLICY = re.compile(r"规则|政策|条件|规定|保修|使用说明|七天无理由|一般.*(?:多久|到账)")
APPLY = re.compile(r"申请(?:退货|换货|售后)|(?:我要|我想|帮我|给我)(?:办理)?(?:退货|换货)|我要退")
CUES = {
    IntentName.PRODUCT_INQUIRY: re.compile(r"推荐|耳机|商品|价格|库存|产品|手机|电脑"),
    IntentName.ORDER_INQUIRY: re.compile(r"订单|物流|配送|快递"),
    IntentName.AFTER_SALE: re.compile(r"退货|换货|退款|售后"),
    IntentName.KNOWLEDGE_INQUIRY: POLICY,
    IntentName.HUMAN_SERVICE: re.compile(r"人工|真人客服"),
}
SIMPLE = {
    IntentName.ORDER_INQUIRY: re.compile(r"(?:请|帮我)?(?:查|查询|查看)(?:一下)?(?:我的)?(?:订单|物流)[A-Za-z0-9-]*[？?]?"),
    IntentName.PRODUCT_INQUIRY: re.compile(r"(?:请|帮我)?(?:推荐|搜索|查找)(?:一款|一个|一下)?(?:耳机|手机|电脑|键盘|鼠标)[？?]?"),
    IntentName.HUMAN_SERVICE: re.compile(r"(?:请|帮我)?(?:转|找|联系)?(?:人工|人工客服|真人客服)[？?]?"),
}


class RoutingPolicy:
    def __init__(self, min_score: float = 0.85, min_margin: float = 0.15):
        self.min_score = min_score
        self.min_margin = min_margin

    def fast_path(self, analysis: QueryAnalysis, has_context: bool = False) -> bool:
        pattern = SIMPLE.get(analysis.primary_intent)
        return bool(
            not has_context and not analysis.secondary_intents and pattern
            and not COMPLEX.search(analysis.normalized_query)
            and pattern.fullmatch(analysis.normalized_query)
            and analysis.similarity_score >= self.min_score
            and analysis.route_margin >= self.min_margin
        )

    def arbitrate(self, embedding: QueryAnalysis, llm: QueryAnalysis) -> QueryAnalysis:
        original = [embedding.primary_intent, *embedding.secondary_intents]
        proposed = [llm.primary_intent, *llm.secondary_intents]
        old, new = set(original), set(proposed)
        query = embedding.normalized_query
        negative_parts = re.findall(
            r"(?:不要|不用|别)[^，,；;。]*?(?=只|但是|而是|同时|然后|再|[，,；;。]|$)", query,
        )
        negated_domains = {intent for intent, pattern in CUES.items()
                           if any(pattern.search(part) for part in negative_parts)}
        decision, reason = "agreement", "两种识别方式的意图集合一致。"
        conflict = False
        if old != new:
            # 原话片段并不保证分类正确；新增或替换领域还须有业务语义依据。
            grounded = all(
                intent in CUES and CUES[intent].search(llm.intent_evidence.get(intent.value, ""))
                for intent in new - old
            )
            negated = (old - new) & negated_domains
            policy_correction = (
                old == {IntentName.AFTER_SALE} and new == {IntentName.KNOWLEDGE_INQUIRY}
                and POLICY.search(query) and not APPLY.search(query)
                and not re.search(r"如果|的话", query)
            )
            reply = query.rsplit("用户补充：", 1)[-1] if "用户补充：" in query else ""
            explicit_selection = bool(reply and not re.search(r"不要|不用|别|如果|的话", reply) and all(
                intent in CUES and CUES[intent].search(reply) for intent in new
            ))
            if grounded and old < new:
                decision, reason = "supplement", "模型补充了有原话业务依据的独立意图。"
            elif grounded and (old - new) and (old - new).issubset(negated):
                decision, reason = "rule_correction", "用户明确否定了 embedding 命中的动作。"
            elif grounded and policy_correction:
                decision, reason = "rule_correction", "原话明确咨询规则，未表达办理售后诉求。"
            elif grounded and explicit_selection:
                decision, reason = "user_confirmation", "用户在追问回复中明确了业务领域。"
            elif grounded and old == {IntentName.GENERAL_CONVERSATION}:
                decision, reason = "low_confidence_recovery", "embedding 未命中业务领域，模型提供了明确业务依据。"
            else:
                conflict = True
                decision, reason = "clarify", "识别结果互相矛盾或缺少可仲裁的业务依据。"
        # 条件性写操作不能仅凭标签授权。
        if IntentName.AFTER_SALE in new and re.search(r"如果|的话|否则", query):
            conflict = True
            decision, reason = "clarify", "售后请求包含条件，需先确认具体诉求。"
        if new & negated_domains:
            conflict = True
            decision, reason = "clarify", "模型识别的意图包含用户明确否定的动作。"
        question = None
        if conflict:
            options = list(dict.fromkeys(INTENT_LABELS[item] for item in [*original, *proposed]))
            question = "请确认你希望处理的是" + "，还是".join(options) + "？如果有多个诉求，请分别说明；涉及申请时请说明是否现在办理。"
        return llm.model_copy(update={
            "routing_decision": decision, "routing_reason": reason,
            "embedding_intents": original, "llm_intents": proposed,
            "clarification_question": question,
        })
