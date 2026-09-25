# JDDC 项目相关500条评测子集

本目录从 DialogueCSE 发布的 JDDC 处理数据中确定性筛选500条中文电商客服 Query，用于
本项目的检索、语义路由和错误分析实验。JDDC 原始语料来自京东电商客服场景；DialogueCSE
说明其 JDDC train/dev/test 数据继续采用 Apache License 2.0。

- JDDC论文：https://aclanthology.org/2020.lrec-1.58/
- 数据分发来源：https://github.com/wangruicn/DialogueCSE
- 许可文件：`LICENSE-2.0.txt`
- 可复现脚本：`backend/scripts/build_jddc_project_subset.py`

## 数据规模

共500条不重复Query，每类100条：

| 项目分类 | 数量 |
| --- | ---: |
| `return_policy` | 100 |
| `warranty_policy` | 100 |
| `shipping_policy` | 100 |
| `payment_policy` | 100 |
| `product_guide` | 100 |

`project_category` 根据JDDC原始意图自动映射，`project_category_reviewed=false` 表示尚未经过
人工逐条复核。自动映射不能作为最终的RAG正确答案。

## 文件说明

### `jddc_project_eval_500.jsonl`

保存500条完整筛选记录，包括：

- JDDC用户Query和原始意图；
- 自动映射的项目分类；
- 开发集或测试集、源文件行号和原始编号；
- JDDC原始正样本与困难负样本候选编号；
- 空的 `expected_sources` 和 `rag_label_status=unlabeled`。

`expected_sources` 故意保持为空。JDDC没有使用本项目的15个知识切块，因此不能把原始正样本
候选编号伪装成 `warranty_policy.md#保修范围` 等项目知识来源。

### `corpus.jsonl`

包含JDDC开发集和测试集的9064条候选Query，字段为：

```json
{"id":"jddc-dev-0","text":"有什么更好的办法储存录像么","jddc_intent":"使用咨询"}
```

### `queries.jsonl`

包含筛选出的500条评测Query。它与 `corpus.jsonl`、`qrels.tsv` 共同组成一个可直接转换为
标准检索评测的数据结构。

### `qrels.tsv`

包含500条Query与JDDC原始语义相关候选之间的正相关标注：

```text
query_id    corpus_id    relevance
```

共有14629条正相关关系，引用的Query和Corpus ID均已完成完整性校验。

### `hard_negatives.tsv`

保留JDDC原始检索选择任务中的困难负样本，用于检查BGE是否把表达相近但语义不同的问题排在
正确结果之前。

### `manifest.json`

记录总数、五类数量、JDDC原始意图分布、许可和RAG标注状态。

## 两种正确用法

### 1. 评测BGE与Milvus检索能力

1. 将 `corpus.jsonl` 写入独立的Milvus Collection，例如 `jddc_benchmark_chunks`；
2. 使用项目的 `BAAI/bge-small-zh-v1.5` 为Corpus和Query生成向量；
3. 对 `queries.jsonl` 中500条Query执行Top-K检索；
4. 使用 `qrels.tsv` 计算Recall@K、MRR和NDCG；
5. 使用 `hard_negatives.tsv` 分析错误排序。

这一方式评测的是JDDC语义相关检索能力，不代表模型回答符合本项目退货、保修和物流政策。

### 2. 扩展项目自己的RAG评测集

1. 阅读 `jddc_project_eval_500.jsonl` 中的Query；
2. 对照项目当前15个知识块进行人工标注；
3. 能回答时填写真实 `expected_sources`；
4. 需要订单或物流工具时标记为 `tool_required`；
5. 当前知识库没有依据时标记为 `unanswerable`；
6. 只有人工复核后的记录才进入项目正式RAG指标。

这种方式可用JDDC的真实口语表达增强项目评测，同时保持正确答案来自项目自己的知识库。

## 数据边界

- 本子集是对JDDC处理版的筛选和结构转换，不是新采集数据；
- 原数据中的订单号、姓名、电话、地址等使用占位符脱敏，本子集保留这些占位符；
- 自动主题映射只用于初筛，不能替代人工RAG来源标注；
- 不应把500条Query或标准答案写入正式业务知识库；
- 使用和再分发时应保留来源、论文引用和Apache 2.0许可文件。
