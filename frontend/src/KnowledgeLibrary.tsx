import { useCallback, useEffect, useRef, useState } from 'react';
import { BookOpen, RefreshCw, Search } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import { Badge } from './components';
import { getKnowledgeLibrary } from './api';
import type { KnowledgeLibraryResponse } from './api';
import { backendChatEnabled } from './store';

const categoryNames: Record<string, string> = {
  return_policy: '退换货',
  warranty_policy: '保修',
  shipping_policy: '物流与发货',
  payment_policy: '支付与退款',
  product_guide: '产品使用',
};
const dateLabel = (value: string) =>
  new Date(value).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
  });

export default function KnowledgeLibrary({ active = true }: { active?: boolean }) {
  const [library, setLibrary] = useState<KnowledgeLibraryResponse>();
  const [selected, setSelected] = useState('');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [checkedAt, setCheckedAt] = useState('');
  const etag = useRef<string | undefined>(undefined);
  const pending = useRef<AbortController | null>(null);
  const mounted = useRef(false);

  const refresh = useCallback(async () => {
    if (!backendChatEnabled || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    try {
      const result = await getKnowledgeLibrary(etag.current, controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      if (result.library) setLibrary(result.library);
      etag.current = result.etag || undefined;
      setError('');
      setCheckedAt(new Date().toISOString());
    } catch (caught) {
      if (!controller.signal.aborted && mounted.current) {
        setError(caught instanceof Error ? caught.message : '知识库加载失败');
      }
    } finally {
      if (pending.current === controller) pending.current = null;
      if (mounted.current && !controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = active;
    if (!active || !backendChatEnabled)
      return () => {
        mounted.current = false;
      };
    const check = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    check();
    const timer = window.setInterval(check, 5000);
    document.addEventListener('visibilitychange', check);
    window.addEventListener('focus', check);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', check);
      window.removeEventListener('focus', check);
      pending.current?.abort();
      pending.current = null;
    };
  }, [active, refresh]);

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const documents =
    library?.documents.filter(
      (item) =>
        (category === 'all' || item.category === category) &&
        `${item.title} ${item.document_key} ${item.chunks.map((chunk) => `${chunk.section} ${chunk.content}`).join(' ')}`
          .toLocaleLowerCase()
          .includes(normalizedQuery),
    ) || [];
  const selectedDocument = documents.find((item) => item.document_key === selected) || documents[0];
  const matchingChunks =
    selectedDocument?.chunks.filter(
      (chunk) =>
        !normalizedQuery ||
        `${selectedDocument.title} ${selectedDocument.document_key} ${chunk.section} ${chunk.content}`
          .toLocaleLowerCase()
          .includes(normalizedQuery),
    ) || [];

  return (
    <section className="panel knowledge-library" aria-label="实时知识库">
      <div className="row-between knowledge-heading">
        <div>
          <h2>
            <BookOpen size={19} />
            知识库
          </h2>
          <p>当前 RAG 使用的已入库文档与知识块，每 5 秒自动检查更新。</p>
        </div>
        <button
          className="btn"
          disabled={loading || !backendChatEnabled}
          onClick={() => void refresh()}
        >
          <RefreshCw size={15} className={loading ? 'knowledge-refreshing' : ''} />
          {loading ? '同步中…' : '刷新知识库'}
        </button>
      </div>
      {!backendChatEnabled && <p className="notice">请连接后端后查看实际知识库。</p>}
      {error && (
        <p className="notice error" role="alert">
          {error}
          {library ? ' 当前展示上次成功同步的内容。' : ''}
        </p>
      )}
      {library && (
        <div className="knowledge-summary">
          <Badge>{library.total_documents} 份文档</Badge>
          <Badge>{library.total_chunks} 个知识块</Badge>
          <span className="tiny muted">{checkedAt ? `最近检查：${dateLabel(checkedAt)}` : ''}</span>
        </div>
      )}
      <div className="knowledge-filters">
        <label>
          <Search size={16} />
          <input
            aria-label="搜索知识库"
            placeholder="搜索文档、章节或正文"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select
          aria-label="知识分类"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        >
          <option value="all">全部分类</option>
          {Array.from(new Set(library?.documents.map((item) => item.category))).map((item) => (
            <option key={item} value={item}>
              {categoryNames[item] || item}
            </option>
          ))}
        </select>
      </div>
      {loading && !library && <p role="status">正在加载知识库…</p>}
      {library && !library.documents.length && (
        <p className="quiet-state">知识库暂无已入库内容。</p>
      )}
      {library && library.documents.length > 0 && !documents.length && (
        <p className="quiet-state">没有匹配的文档，请调整关键词或分类。</p>
      )}
      {selectedDocument && (
        <div className="knowledge-workspace">
          <nav className="knowledge-document-list" aria-label="知识文档">
            {documents.map((item) => (
              <button
                key={item.document_key}
                aria-pressed={item.document_key === selectedDocument.document_key}
                onClick={() => setSelected(item.document_key)}
              >
                <strong>{item.title}</strong>
                <span>
                  {categoryNames[item.category] || item.category} · {item.chunks.length} 个知识块
                </span>
              </button>
            ))}
          </nav>
          <article className="knowledge-document" aria-label={selectedDocument.title}>
            <h3>{selectedDocument.title}</h3>
            <p className="knowledge-document-meta">
              {selectedDocument.document_key} ·{' '}
              {categoryNames[selectedDocument.category] || selectedDocument.category} · 版本{' '}
              {selectedDocument.document_versions.join('、')}
            </p>
            {selectedDocument.updated_at && (
              <p className="tiny muted">入库更新时间：{dateLabel(selectedDocument.updated_at)}</p>
            )}
            <p className="tiny muted">
              显示 {matchingChunks.length} / {selectedDocument.chunks.length} 个知识块
            </p>
            {matchingChunks.map((chunk) => (
              <details className="knowledge-chunk" key={`${chunk.document_key}:${chunk.position}`}>
                <summary>
                  <strong>{chunk.section}</strong>
                  <code>
                    {chunk.document_key}:{chunk.position}
                  </code>
                </summary>
                <p className="knowledge-chunk-source">来源：{chunk.source}</p>
                <div className="knowledge-markdown">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, remarkBreaks]}
                    components={{
                      a: ({ children, ...props }) => (
                        <a {...props} target="_blank" rel="noopener noreferrer">
                          {children}
                        </a>
                      ),
                    }}
                  >
                    {chunk.content}
                  </ReactMarkdown>
                </div>
              </details>
            ))}
          </article>
        </div>
      )}
    </section>
  );
}
