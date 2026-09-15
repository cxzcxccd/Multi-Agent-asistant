import { useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  ArrowUp,
  CheckCheck,
  Headphones,
  MessageSquarePlus,
  PackageSearch,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Square,
  Truck,
  UserRound,
  X,
} from 'lucide-react';
import { buyers, findProduct, orders, quickQuestions } from './data';
import {
  cancelRun,
  currentConversation,
  handoff,
  newConversation,
  sendMessage,
  useDemo,
} from './store';
import {
  Badge,
  DraftCard,
  LatestRun,
  OrderCard,
  ProductCard,
  RequestCard,
  SourceList,
  timeLabel,
} from './components';
import type { Conversation, Message } from './types';

export function MessageFeed({
  conversation,
  staffView = false,
}: {
  conversation: Conversation;
  staffView?: boolean;
}) {
  const s = useDemo(),
    scroll = useRef<HTMLDivElement>(null);
  const last = conversation.messages.at(-1);
  useEffect(() => {
    if (scroll.current)
      scroll.current.scrollTop =
        conversation.messages.length === 1 ? 0 : scroll.current.scrollHeight;
  }, [
    conversation.id,
    conversation.messages.length,
    last?.text,
    last?.streaming,
    conversation.draft,
    s.requests,
  ]);
  const send = (text: string) => {
    if (!staffView) void sendMessage(conversation.id, text);
  };
  const activeRun = s.runs.find(
    (r) => r.conversationId === conversation.id && r.status === 'running',
  );
  return (
    <div
      className="message-feed"
      ref={scroll}
      role="log"
      aria-label="聊天记录"
      aria-relevant="additions text"
    >
      <div className="day-divider">
        <span>今天的咨询</span>
      </div>
      {conversation.messages.map((m) => (
        <MessageBubble
          key={m.id}
          message={m}
          conversation={conversation}
          staffView={staffView}
          send={send}
        />
      ))}
      {conversation.messages.length === 1 && !staffView && (
        <div className="welcome-content">
          <div className="welcome-label">
            <span />
            从这里开始
          </div>
          <div className="intent-grid">
            <button onClick={() => send(quickQuestions[0])}>
              <span className="icon-tile blue">
                <ShoppingBag size={22} />
              </span>
              <strong>发现适合你的数码</strong>
              <span>选购建议、参数与兼容性</span>
              <ArrowRight size={17} />
            </button>
            <button onClick={() => send('查一下我的订单')}>
              <span className="icon-tile mint">
                <Truck size={22} />
              </span>
              <strong>看看订单到哪里了</strong>
              <span>订单详情与物流进度</span>
              <ArrowRight size={17} />
            </button>
          </div>
          <div className="row-between welcome-products-title">
            <span>店内人气好物</span>
            <span className="tiny muted">示例商品</span>
          </div>
          <div className="product-grid">
            {['p01', 'p05', 'p09'].map((id) => (
              <ProductCard
                id={id}
                key={id}
                onSelect={(id) => send(`了解 ${findProduct(id).name}`)}
              />
            ))}
          </div>
        </div>
      )}
      {activeRun && !last?.streaming && (
        <div className="typing-row" role="status">
          <span className="assistant-avatar">
            <Sparkles size={17} />
          </span>
          <span className="typing-dots">
            <i />
            <i />
            <i />
          </span>
          <span>{activeRun.events.at(-1)?.label || '正在处理'}</span>
        </div>
      )}
    </div>
  );
}
function MessageBubble({
  message: m,
  conversation: c,
  staffView,
  send,
}: {
  message: Message;
  conversation: Conversation;
  staffView: boolean;
  send: (text: string) => void;
}) {
  const s = useDemo();
  if (m.role === 'system')
    return (
      <div className="system-message">
        <ShieldCheck size={13} />
        <span>{m.text}</span>
        {m.retryText && !staffView && (
          <button className="text-button" onClick={() => send(m.retryText!)}>
            重新查询
          </button>
        )}
      </div>
    );
  const user = m.role === 'user';
  const request = m.requestId
    ? s.requests.find((r) => r.id === m.requestId && r.buyer === c.buyer)
    : undefined;
  return (
    <article className={`message ${user ? 'from-user' : 'from-assistant'}`}>
      <span
        className={user ? 'user-avatar' : m.role === 'staff' ? 'staff-avatar' : 'assistant-avatar'}
      >
        {user ? (
          <UserRound size={18} />
        ) : m.role === 'staff' ? (
          <Headphones size={18} />
        ) : (
          <Sparkles size={18} />
        )}
      </span>
      <div className="message-body">
        <div className="message-meta">
          <strong>
            {user ? buyers[c.buyer] : m.role === 'staff' ? '小周 · 人工客服' : '小极 · 购物助手'}
          </strong>
          {!user && <span className="tiny">{m.role === 'staff' ? '人工服务' : '模拟 AI'}</span>}
          <time>{timeLabel(m.time)}</time>
        </div>
        <div className={`message-text ${m.streaming ? 'streaming' : ''}`}>{m.text}</div>
        {m.products?.length ? (
          <div className="product-grid message-products">
            {m.products.map((id) => (
              <ProductCard
                key={id}
                id={id}
                onSelect={!staffView ? (id) => send(`了解 ${findProduct(id).name}`) : undefined}
              />
            ))}
          </div>
        ) : null}
        {m.orders?.map((id) => (
          <OrderCard
            key={id}
            id={id}
            buyer={c.buyer}
            full={m.orders?.length === 1}
            onQuery={!staffView ? (id) => send(`查询订单 ${id}`) : undefined}
            onAfter={!staffView ? (id) => send(`订单 ${id} 申请退货`) : undefined}
          />
        ))}
        {m.sources?.length ? <SourceList sources={m.sources} /> : null}
        {m.draftId &&
          (c.draft?.id === m.draftId ? (
            staffView ? (
              <p className="draft-readonly">买家正在确认申请草稿，尚未提交。</p>
            ) : (
              <DraftCard conversation={c} />
            )
          ) : (
            <p className="tiny muted">此草稿已提交、取消或被更新。</p>
          ))}
        {request && <RequestCard request={request} />}
        {m.retryText && !staffView && (
          <button className="btn btn-small" onClick={() => send(m.retryText!)}>
            重试查询
          </button>
        )}
        {user && (
          <span className="message-delivered">
            <CheckCheck size={13} />
            已发送
          </span>
        )}
      </div>
    </article>
  );
}
export default function Chat() {
  const s = useDemo(),
    c = currentConversation(s),
    [input, setInput] = useState(''),
    [contextOpen, setContextOpen] = useState(false);
  const running = s.runs.some((r) => r.conversationId === c.id && r.status === 'running');
  useEffect(() => setInput(''), [c.id]);
  const submit = () => {
    if (!input.trim() || running) return;
    void sendMessage(c.id, input);
    setInput('');
  };
  return (
    <div className="chat-layout">
      <section className="chat-panel">
        <div className="panel-heading">
          <div className="assistant-avatar large">
            <Sparkles size={23} />
          </div>
          <div>
            <h2>小极，随时为你解答</h2>
            <p>
              <span className={`status-dot ${c.mode === 'ai' ? 'online' : ''}`} />
              {c.mode === 'ai'
                ? '购物助手在线'
                : c.mode === 'waiting'
                  ? '等待人工接入'
                  : c.mode === 'human'
                    ? '人工客服小周服务中'
                    : '会话已结束'}
              <span className="heading-separator">/</span>所有业务数据均为模拟
            </p>
          </div>
          <button
            className="btn btn-small handoff-button"
            disabled={c.mode !== 'ai'}
            onClick={() => handoff(c.id)}
          >
            <Headphones size={15} />
            转人工
          </button>
          <button
            className="icon-button mobile-context-button"
            aria-label="查看订单和处理进度"
            onClick={() => setContextOpen(true)}
          >
            <PackageSearch size={21} />
          </button>
        </div>
        <MessageFeed conversation={c} />
        {c.mode === 'waiting' || c.mode === 'human' ? (
          <div className="service-banner">
            <Headphones size={16} />
            {c.mode === 'waiting'
              ? '正在等待人工接入，你可以继续补充问题。'
              : '小周正在为你服务，AI 自动回复已暂停。'}
          </div>
        ) : null}
        <div className="composer-area">
          <div className="quick-prompts">
            <button
              onClick={() => void sendMessage(c.id, '查一下我的订单')}
              disabled={running || c.mode === 'closed'}
            >
              <Truck size={14} />
              我的订单
            </button>
            <button
              onClick={() => void sendMessage(c.id, '我想申请售后')}
              disabled={running || c.mode === 'closed'}
            >
              <ShieldCheck size={14} />
              申请售后
            </button>
            <button
              onClick={() => void sendMessage(c.id, '店铺有哪些规则')}
              disabled={running || c.mode === 'closed'}
            >
              <ShoppingBag size={14} />
              店铺规则
            </button>
          </div>
          {c.mode === 'closed' ? (
            <button className="btn btn-primary" onClick={newConversation}>
              <MessageSquarePlus size={17} />
              开始新的咨询
            </button>
          ) : (
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
            >
              <textarea
                aria-label="输入咨询内容"
                placeholder="想了解什么？告诉我你的问题…"
                maxLength={1000}
                value={input}
                rows={2}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    submit();
                  }
                }}
              />
              <div className="composer-bottom">
                <span>
                  {input.length ? `${input.length}/1000` : 'Enter 发送，Shift + Enter 换行'}
                </span>
                {running ? (
                  <button
                    type="button"
                    className="send-button stop"
                    aria-label="停止输出"
                    onClick={() => cancelRun(c.id)}
                  >
                    <Square size={16} fill="currentColor" />
                  </button>
                ) : (
                  <button
                    type="submit"
                    className="send-button"
                    aria-label="发送消息"
                    disabled={!input.trim()}
                  >
                    <ArrowUp size={21} />
                  </button>
                )}
              </div>
            </form>
          )}
          <p className="composer-disclaimer">
            <ShieldCheck size={12} />
            交互演示 · 回答由预设脚本生成 · 未连接真实大模型
          </p>
        </div>
      </section>
      <aside className={`context-panel ${contextOpen ? 'is-open' : ''}`}>
        <div className="context-heading">
          <h2>服务速览</h2>
          <button
            className="icon-button mobile-context-button"
            aria-label="关闭服务速览"
            onClick={() => setContextOpen(false)}
          >
            <X size={18} />
          </button>
          <span className="tiny muted desktop-only">当前会话</span>
        </div>
        <div className="customer-card">
          <span className="customer-avatar">{buyers[s.buyer][0]}</span>
          <div>
            <strong>{buyers[s.buyer]}</strong>
            <p>模拟买家 {s.buyer}</p>
          </div>
          <Badge tone="blue">演示账户</Badge>
        </div>
        <div className="context-section">
          <div className="section-label">
            <h3>我的订单</h3>
            <span>{orders.filter((o) => o.buyer === s.buyer).length} 笔</span>
          </div>
          <div className="mini-orders">
            {orders
              .filter((o) => o.buyer === s.buyer)
              .map((o) => (
                <button
                  key={o.id}
                  disabled={running || c.mode === 'closed'}
                  onClick={() => {
                    void sendMessage(c.id, `查询订单 ${o.id}`);
                    setContextOpen(false);
                  }}
                >
                  <span className="mini-product-icon">
                    <PackageSearch size={19} />
                  </span>
                  <div>
                    <strong>订单 {o.id}</strong>
                    <span>{findProduct(o.productId).name}</span>
                  </div>
                  <span className={`mini-order-status ${o.status === '运输中' ? 'blue-text' : ''}`}>
                    {o.status}
                  </span>
                </button>
              ))}
          </div>
        </div>
        <div className="context-section execution-section">
          <div className="section-label">
            <h3>处理进度</h3>
            <Badge>模拟执行</Badge>
          </div>
          <LatestRun conversationId={c.id} />
        </div>
        <div className="context-tip">
          <ShieldCheck size={19} />
          <div>
            <strong>涉及操作，先由你确认</strong>
            <p>售后申请需确认后提交，再由人工审核。</p>
          </div>
        </div>
      </aside>
    </div>
  );
}
