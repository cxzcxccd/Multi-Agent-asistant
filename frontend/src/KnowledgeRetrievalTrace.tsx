type RecordValue = Record<string, unknown>;

function record(value: unknown): RecordValue | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as RecordValue)
    : undefined;
}
function text(value: unknown): string {
  return typeof value === 'string' && value ? value : '未记录';
}
function score(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(6) : '未记录';
}
const modes: Record<string, string> = {
  hybrid: 'BM25 + 向量混合检索',
  hyde: 'BM25 + 原问题向量 + HyDE 向量',
  hyde_fallback: 'HyDE 失败，回退到原混合检索',
  hybrid_rerank: '混合检索 + 重排',
  keyword: 'BM25 检索',
  vector: '向量检索',
  keyword_fallback: 'BM25 回退',
  unavailable: '检索服务不可用',
};
const scoreTypes: Record<string, string> = {
  bm25: 'BM25',
  cosine: '余弦相似度',
  rrf: 'RRF',
  rerank: '重排',
};

export function KnowledgeRetrievalTrace({ output }: { output: unknown }) {
  const result = record(output);
  if (!result) return <p>这条记录没有可读取的 RAG 召回结果。</p>;
  const sources = Array.isArray(result.sources)
    ? result.sources.flatMap((value) => {
        const item = record(value);
        return item ? [item] : [];
      })
    : [];
  return (
    <section className="rag-retrieval-trace" aria-label="RAG 召回详情">
      <h4>RAG 召回详情</h4>
      <p>检索问题：{text(result.query)}</p>
      <p>
        检索方式：{modes[text(result.retrieval_mode)] || text(result.retrieval_mode)} · 返回{' '}
        {sources.length} 个知识块
      </p>
      {!sources.length && (
        <p>
          {text(record(result.error)?.message) === '未记录'
            ? '未召回知识块。'
            : text(record(result.error)?.message)}
        </p>
      )}
      {typeof result.hyde_status === 'string' && result.hyde_status !== 'not_used' && (
        <p>
          HyDE：
          {result.hyde_status === 'used'
            ? '已使用'
            : result.hyde_status === 'fallback'
              ? '已回退'
              : '已跳过'}
          {' · '}
          {typeof result.hyde_latency_ms === 'number' ? result.hyde_latency_ms : 0} ms
          {typeof record(result.hyde_usage)?.total_tokens === 'number' &&
            ` · Token：${record(result.hyde_usage)?.total_tokens}`}
        </p>
      )}
      {sources.map((item, index) => (
        <article className="rag-hit" key={`${text(item.chunk_id)}-${index}`}>
          <h5>
            #{typeof item.rank === 'number' ? item.rank : index + 1} {text(item.document)}
          </h5>
          <dl>
            <div>
              <dt>文档标识</dt>
              <dd>{text(item.document_key)}</dd>
            </div>
            <div>
              <dt>知识块标识</dt>
              <dd>{text(item.chunk_id)}</dd>
            </div>
            <div>
              <dt>章节</dt>
              <dd>{text(item.section)}</dd>
            </div>
            <div>
              <dt>来源</dt>
              <dd>{text(item.source)}</dd>
            </div>
            <div>
              <dt>文档版本</dt>
              <dd>{text(item.document_version)}</dd>
            </div>
            <div>
              <dt>召回分数</dt>
              <dd>
                {score(item.score)}（{scoreTypes[text(item.score_type)] || text(item.score_type)}）
              </dd>
            </div>
            <div>
              <dt>BM25 分数 / 排名</dt>
              <dd>
                {score(item.keyword_score)} /{' '}
                {typeof item.keyword_rank === 'number' ? item.keyword_rank : '未记录'}
              </dd>
            </div>
            <div>
              <dt>向量分数 / 排名</dt>
              <dd>
                {score(item.vector_score)} /{' '}
                {typeof item.vector_rank === 'number' ? item.vector_rank : '未记录'}
              </dd>
            </div>
            {typeof item.rrf_score === 'number' && (
              <div>
                <dt>RRF 融合分数</dt>
                <dd>{score(item.rrf_score)}</dd>
              </div>
            )}
            {typeof item.hyde_rank === 'number' && (
              <div>
                <dt>HyDE 向量分数 / 排名</dt>
                <dd>
                  {score(item.hyde_score)} / {item.hyde_rank}
                </dd>
              </div>
            )}
          </dl>
          <details>
            <summary>查看知识块正文</summary>
            <p className="rag-chunk-content">{text(item.content)}</p>
          </details>
        </article>
      ))}
      <p className="tiny muted">
        分数随检索方式变化，并非回答置信度；旧记录缺失的字段显示为“未记录”。
      </p>
    </section>
  );
}
