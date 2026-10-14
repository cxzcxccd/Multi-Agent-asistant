# 上下文压缩固定实验

合成压力案例；本地BPE估算，不是服务端Token或真实任务成功率。

| 案例 | 输入消息数 | 压缩前估算 | 压缩后估算 | 结果 |
| --- | ---: | ---: | ---: | --- |
| duplicate | 102 | 178155 | 1196 | bounded_request |
| separate_orders | 83 | 177782 | 1195 | bounded_request |
| correction | 83 | 177886 | 1142 | bounded_request |
| attempt_and_switch | 84 | 177906 | 1289 | bounded_request |
| archive | 84 | 177916 | 1182 | bounded_request |
| tool_evidence | 83 | 188240 | 2644 | bounded_request |
| required_overflow | 1 | 66427 | 未发送 | required_overflow_rejected |

检查通过：7/7。包含必要信息超限拒绝案例，不代表模型回答成功。

所有计数、预算、输入摘要哈希、代码哈希、完整场景和压缩后请求见report.json；输入由脚本可重建。
