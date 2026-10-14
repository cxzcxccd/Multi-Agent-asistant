import { useEffect, useState } from 'react';
import { Play, RefreshCw } from 'lucide-react';
import { Badge } from './components';
import { KnowledgeRetrievalTrace } from './KnowledgeRetrievalTrace';
import { getRagEvaluationRecord, getRagEvaluationRecords, runRagEvaluation } from './api';
import type { RagEvaluationRecord, RagEvaluationSummary } from './api';
import { backendChatEnabled } from './store';

const percent = (value: number | null | undefined) =>
  value == null ? '未测量' : `${(value * 100).toFixed(2)}%`;
const time = (value: number | null | undefined) =>
  value == null ? '未测量' : `${value.toFixed(2)} ms`;
const decision = (value: boolean | null | undefined) =>
  value == null ? '未评审' : value ? '通过' : '失败';
const date = (value: string) =>
  new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });

export default function RagEvaluationPreview() {
  const [records, setRecords] = useState<RagEvaluationSummary[]>([]);
  const [selected, setSelected] = useState('');
  const [record, setRecord] = useState<RagEvaluationRecord>();
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [version, setVersion] = useState(0);
  const [filter, setFilter] = useState<'all' | 'failed'>('all');
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (!backendChatEnabled) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void getRagEvaluationRecords(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        setRecords(items);
        setSelected((current) =>
          items.some((item) => item.id === current) ? current : items[0]?.id || '',
        );
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted)
          setError(caught instanceof Error ? caught.message : '评测记录加载失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [version]);

  useEffect(() => {
    if (!selected) {
      setRecord(undefined);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setRecord(undefined);
    void getRagEvaluationRecord(selected, controller.signal)
      .then((item) => {
        if (!controller.signal.aborted) setRecord(item);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted)
          setError(caught instanceof Error ? caught.message : '评测详情加载失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [selected, version]);

  const run = async () => {
    setPlaying(true);
    setError('');
    setNotice('');
    try {
      const report = await runRagEvaluation();
      setSelected(report.evaluation_id || '');
      setVersion((value) => value + 1);
      setNotice('新版 100 条检索评测完成，报告已保存。');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '评测失败');
    } finally {
      setPlaying(false);
    }
  };
  const retrieval = record?.retrieval;
  const runtime = record?.runtime;
  const runtimeById = new Map(record?.runtime_results.map((item) => [item.id, item]));
  const cases =
    retrieval?.results ||
    record?.runtime_results.map((item) => ({
      id: item.id,
      question: item.query,
      expected_sources: item.expected_sources || [],
      retrieved_sources: item.sources,
      should_answer: Boolean(item.expected_sources?.length),
      reference_answer: item.reference_answer,
      retrieved_chunks: item.retrieved_chunks,
      retrieval_mode: undefined,
      retrieval_latency_ms: undefined,
      passed: undefined,
    })) ||
    [];
  const visibleCases = cases.filter((item) => {
    const online = runtimeById.get(item.id);
    return (
      (filter === 'all' || item.passed === false || online?.task_passed === false) &&
      `${item.id} ${item.question}`.includes(query.trim())
    );
  });
  const metrics = retrieval
    ? [
        ['Recall@1', percent(retrieval.recall_at_1)],
        ['Recall@3', percent(retrieval.recall_at_3)],
        ['Recall@5', percent(retrieval.recall_at_5)],
        ['MRR', retrieval.mrr.toFixed(4)],
        ['无答案错误召回率', percent(retrieval.false_retrieval_rate)],
        ['检索 P95', time(retrieval.retrieval_latency_p95_ms)],
      ]
    : [];

  return (
    <section className="evaluation">
      <div className="row-between evaluation-title">
        <div>
          <h2>新版 JDDC 100 条评测</h2>
          <p>固定使用当前知识库标注的 100 条问题，每轮结果保存在后端。</p>
        </div>
        <div className="rag-eval-actions">
          <button
            className="btn"
            disabled={loading || playing}
            onClick={() => setVersion((value) => value + 1)}
          >
            <RefreshCw size={15} />
            刷新记录
          </button>
          <button
            className="btn btn-primary"
            disabled={playing || !backendChatEnabled}
            onClick={() => void run()}
          >
            <Play size={15} />
            {playing ? '评测中…' : '运行 RAG 评测'}
          </button>
        </div>
      </div>
      <p className="tiny muted">
        “运行 RAG 评测”重新运行检索；模型工具与回答结果显示在对应的已保存轮次中。
      </p>
      {!backendChatEnabled && <p className="notice">请连接后端后查看真实评测记录。</p>}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {playing && <p role="status">正在对新版 100 条运行检索评测，完成后保存为新轮次。</p>}
      <label className="field">
        评测轮次
        <select
          aria-label="评测轮次"
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
          disabled={!records.length || playing}
        >
          {!records.length && <option value="">暂无已保存记录</option>}
          {records.map((item) => (
            <option key={item.id} value={item.id}>
              {date(item.created_at)} · {item.cases} 条 · {item.has_runtime ? '工具与回答' : ''}
              {item.has_retrieval ? ' 检索' : ''}
            </option>
          ))}
        </select>
      </label>
      {loading && <p role="status">正在加载评测记录…</p>}
      {!loading && !record && !error && (
        <p className="quiet-state">尚无新版 100 条评测记录，可点击“运行 RAG 评测”。</p>
      )}
      {record && (
        <>
          <p className="tiny muted">
            {record.dataset_name} · {date(record.created_at)} · {record.annotation_status}
          </p>
          {retrieval && (
            <>
              <h3 className="section-title">检索指标</h3>
              <p className="tiny muted">
                {retrieval.answerable_cases} 条可回答 / {retrieval.unanswerable_cases}{' '}
                条不可回答；Recall 与 MRR 按可回答样本统计。候选召回不等于可以回答。
              </p>
              <div className="metric-grid">
                {metrics.map(([label, value]) => (
                  <article className="metric-card" key={label}>
                    <div className="row-between">
                      <span>{label}</span>
                      <Badge>实测</Badge>
                    </div>
                    <strong>{value}</strong>
                  </article>
                ))}
              </div>
            </>
          )}
          {runtime && (
            <>
              <h3 className="section-title">知识工具与回答评测</h3>
              <div className="metric-grid">
                {[
                  [
                    '工具选择准确率',
                    percent(runtime.tool_selection_accuracy),
                    `${runtime.tool_selection_correct}/${runtime.tool_selection_cases}`,
                  ],
                  [
                    '参数通过率',
                    percent(runtime.parameter_accuracy),
                    `${runtime.parameter_valid_cases}/${runtime.parameter_cases}`,
                  ],
                  [
                    '严格任务通过率',
                    percent(runtime.task_completion_rate),
                    `${runtime.task_successes}/${runtime.cases}`,
                  ],
                  ['端到端 P95', time(runtime.end_to_end_p95_ms), '不含评审耗时'],
                  [
                    '回答正确性均分',
                    runtime.quality_averages.correctness?.toFixed(4) || '未评审',
                    `${runtime.quality_cases} 条`,
                  ],
                  [
                    '回答忠实度均分',
                    runtime.quality_averages.faithfulness?.toFixed(4) || '未评审',
                    `${runtime.quality_cases} 条`,
                  ],
                  [
                    '回答完整性均分',
                    runtime.quality_averages.completeness?.toFixed(4) || '未评审',
                    `${runtime.quality_cases} 条`,
                  ],
                  [
                    '引用支持均分',
                    runtime.quality_averages.citation_correctness?.toFixed(4) || '未评审',
                    `${runtime.quality_cases} 条`,
                  ],
                ].map(([label, value, count]) => (
                  <article className="metric-card" key={label}>
                    <div className="row-between">
                      <span>{label}</span>
                      <Badge>实测</Badge>
                    </div>
                    <strong>{value}</strong>
                    <span className="tiny muted">{count}</span>
                  </article>
                ))}
              </div>
              <p className="tiny muted">
                仅评测 search_knowledge
                的调用、参数与回答。生成和评审使用同配置模型；评分为模型评审均分。
              </p>
            </>
          )}
          <article className="panel cost-panel rag-eval-conditions">
            <h3>运行条件</h3>
            {[
              ['检索方案', retrieval?.strategy || 'hybrid'],
              ['向量后端', retrieval?.retrieval_backend || runtime?.retrieval_backend || '未记录'],
              [
                '可回答 / 不可回答',
                `${retrieval?.answerable_cases ?? runtime?.answerable_cases ?? '未记录'} / ${retrieval?.unanswerable_cases ?? runtime?.unanswerable_cases ?? '未记录'}`,
              ],
              ['数据指纹', record.cases_sha256.slice(0, 16)],
              ['模型', runtime?.model || '本轮未运行回答评测'],
              ['平均检索耗时', retrieval ? time(retrieval.average_latency_ms) : '未测量'],
              [
                '复用已完成样本',
                runtime
                  ? `${runtime.reused_cases || 0}/${runtime.cases}（续跑仅重试异常项）`
                  : '不适用',
              ],
              ['并发样本', runtime ? String(runtime.workers) : '检索顺序运行'],
              [
                '检索 P50 / P95',
                retrieval
                  ? `${time(retrieval.retrieval_latency_p50_ms)} / ${time(retrieval.retrieval_latency_p95_ms)}`
                  : '未测量',
              ],
              [
                '运行 / 评审错误',
                runtime
                  ? `${runtime.runtime_error_cases} / ${runtime.evaluation_failure_cases}`
                  : '未运行回答评测',
              ],
              [
                '在线 Token',
                runtime
                  ? `${runtime.generation_usage.total_tokens.toLocaleString()}（${runtime.usage_cases}/${runtime.cases} 条有用量）`
                  : '未运行回答评测',
              ],
              [
                '平均模型费用',
                runtime?.average_request_cost != null
                  ? `${runtime.average_request_cost} ${runtime.cost_currency}`
                  : '未提供单价，未计算',
              ],
            ].map(([label, value]) => (
              <div className="row-between" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </article>
          <article className="panel cases-panel">
            <div className="row-between">
              <h3>逐条评测记录</h3>
              <Badge>
                {visibleCases.length} / {cases.length} 条
              </Badge>
            </div>
            <div className="rag-eval-actions">
              <input
                aria-label="搜索评测案例"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索问题或案例编号"
              />
              <select
                aria-label="筛选评测结果"
                value={filter}
                onChange={(event) => setFilter(event.target.value as 'all' | 'failed')}
              >
                <option value="all">全部案例</option>
                <option value="failed">失败案例</option>
              </select>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>案例与问题</th>
                    <th>预期来源</th>
                    <th>实际召回来源</th>
                    <th>检索 / 任务</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleCases.map((item) => {
                    const online = runtimeById.get(item.id);
                    return (
                      <tr key={item.id}>
                        <td>
                          <code>{item.id}</code>
                          <p>{item.question}</p>
                        </td>
                        <td>{item.expected_sources.join('、') || '知识库不足以回答'}</td>
                        <td>{item.retrieved_sources.join('、') || '未召回资料'}</td>
                        <td>
                          <Badge
                            tone={item.passed == null ? 'neutral' : item.passed ? 'green' : 'red'}
                          >
                            {decision(item.passed)}
                          </Badge>
                          {online && (
                            <p>
                              <Badge tone={online.task_passed ? 'green' : 'red'}>
                                任务{decision(online.task_passed)}
                              </Badge>
                            </p>
                          )}
                        </td>
                        <td>
                          <details className="rag-case-detail">
                            <summary>查看详情</summary>
                            <p>
                              参考答案：
                              {item.reference_answer || online?.reference_answer || '未记录'}
                            </p>
                            <p>检索耗时：{time(item.retrieval_latency_ms)}</p>
                            <KnowledgeRetrievalTrace
                              output={{
                                query: item.question,
                                retrieval_mode: item.retrieval_mode,
                                sources: item.retrieved_chunks || [],
                              }}
                            />
                            {online && (
                              <div className="rag-online-detail">
                                <h4>模型调用与回答</h4>
                                <p>
                                  预期调用：
                                  {online.expected_tool === undefined
                                    ? '未记录'
                                    : online.expected_tool || '无需知识工具'}
                                </p>
                                <p>
                                  工具选择：{decision(online.tool_selection_correct)} · 参数：
                                  {decision(online.parameters_correct)}
                                </p>
                                <p>实际答案：{online.answer || '未生成答案'}</p>
                                <p>评审理由：{online.quality?.reason || '未完成评审'}</p>
                                {online.quality && (
                                  <p>
                                    正确性 {online.quality.correctness} · 忠实度{' '}
                                    {online.quality.faithfulness} · 完整性{' '}
                                    {online.quality.completeness} · 引用支持{' '}
                                    {online.quality.citation_correctness}
                                  </p>
                                )}
                                {online.error && <p role="alert">运行错误：{online.error}</p>}
                                {online.evaluation_error && (
                                  <p role="alert">评审错误：{online.evaluation_error}</p>
                                )}
                                <details>
                                  <summary>工具参数</summary>
                                  <pre>{JSON.stringify(online.calls, null, 2)}</pre>
                                </details>
                                <KnowledgeRetrievalTrace
                                  output={{
                                    query: item.question,
                                    retrieval_mode: undefined,
                                    sources: online.retrieved_chunks || [],
                                  }}
                                />
                              </div>
                            )}
                          </details>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </article>
        </>
      )}
    </section>
  );
}
