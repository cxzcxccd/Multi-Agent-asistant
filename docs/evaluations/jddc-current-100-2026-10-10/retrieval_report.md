# RAG 检索与回答评测报告

- 案例数：100
- 可回答 / 不可回答：4 / 96
- 标注来源：{'ai_verified': 100}
- 检索方案：hybrid
- 向量存储后端：MilvusKnowledgeVectorStore
- 回答评测案例数：0
- Recall@1 / @3 / @5：0.3750 / 0.5982 / 0.6339
- MRR：0.7083
- 拒答准确率：0.0000
- 无答案错误召回率：1.0000
- 回答拒答准确率：0.0000
- 检索延迟 P50 / P95：381.45 / 924.33 ms
- 回答正确性：0.0000
- 回答忠实度：0.0000
- 回答完整性：0.0000
- 引用准确率：0.0000
- 引用支持度：0.0000

## RAG 运行指标

- 模型配置：None
- 回答任务通过率（正确性、忠实度、完整性均至少0.7）：None
- 检索到答案完成 P95（不含评审）：None ms
- Token 用量覆盖：0/0
- 单次回答平均估算费用：None None
- 未提供价格或未返回用量时，费用为 null，不按零费用处理。
- 此流程不测工具选择、工具参数或完整 Agent 会话成本。

## 失败案例

共检测到 96 条失败，下面最多展示20条。
- `JDDC-PROJECT-0336` retrieval_error：你好，我已经付款为何还未确认
- `JDDC-PROJECT-0354` retrieval_error：请帮我查一查我刚退的款显示到账是哪里支付的
- `JDDC-PROJECT-0361` retrieval_error：昨晚商品点错了是白条分期加刷卡
- `JDDC-PROJECT-0399` retrieval_error：但是只收到一个的退款
- `JDDC-PROJECT-0318` retrieval_error：这个订单取消了，但是白条额度没有回来
- `JDDC-PROJECT-0345` retrieval_error：售后退款金额
- `JDDC-PROJECT-0353` retrieval_error：您好。我想开通货到付款相关服务
- `JDDC-PROJECT-0315` retrieval_error：不过手机一直缺货，我就取消了订单
- `JDDC-PROJECT-0339` retrieval_error：我在客户服务里申请退款了，你帮忙看下吧
- `JDDC-PROJECT-0335` retrieval_error：我申请的退货订单，面包机是按多少钱退款
- `JDDC-PROJECT-0338` retrieval_error：取消就不用申请退款什么的吗?直接自己就退了?
- `JDDC-PROJECT-0370` retrieval_error：你好，我想取消申请退款
- `JDDC-PROJECT-0385` retrieval_error：请问京东支持话费支付吗
- `JDDC-PROJECT-0379` retrieval_error：这个订单帮忙查一下是否退款成功
- `JDDC-PROJECT-0392` retrieval_error：不按原价退?
- `JDDC-PROJECT-0390` retrieval_error：你好，返退详情在哪里可以看到?
- `JDDC-PROJECT-0301` retrieval_error：无法使用苹果支付
- `JDDC-PROJECT-0360` retrieval_error：是借记卡还是信用卡?
- `JDDC-PROJECT-0420` retrieval_error：说是配备四片驱蚊片，为啥只有三片?
- `JDDC-PROJECT-0406` retrieval_error：有什么更好的办法储存录像么
