import { useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  Check,
  CircleCheck,
  Headphones,
  Inbox,
  MessageSquare,
  RotateCcw,
  ShieldCheck,
  UserRound,
  X,
} from 'lucide-react';
import { buyers, findProduct, findOrder } from './data';
import { refreshAfterSales, reviewRequest, setServiceMode, staffReply, useDemo } from './store';
import { Badge, Modal, OrderCard, RequestCard, timeLabel } from './components';
import { MessageFeed } from './Chat';
import type { AfterSale } from './types';

const modes = { ai: 'AI 服务中', waiting: '待人工接管', human: '人工服务中', closed: '已结束' };
export default function Workbench({
  active = true,
  productQuote,
}: {
  active?: boolean;
  productQuote?: { id: string; text: string };
}) {
  const s = useDemo(),
    [tab, setTab] = useState<'conversations' | 'approvals'>('conversations');
  const [selected, setSelected] = useState(s.active[s.buyer]);
  const [filter, setFilter] = useState('all'),
    [reply, setReply] = useState('');
  const c = s.conversations.find((c) => c.id === selected) || s.conversations[0];
  const lastQuote = useRef<string>(undefined);
  useEffect(() => {
    if (!productQuote || lastQuote.current === productQuote.id) return;
    lastQuote.current = productQuote.id;
    setTab('conversations');
    setReply((previous) => `${previous}${previous ? '\n' : ''}${productQuote.text}`);
  }, [productQuote]);
  const waiting = s.conversations.filter((c) => c.mode === 'waiting').length;
  const pending = s.requests.filter((r) => r.status === 'pending').length;
  const filtered = s.conversations.filter((c) => filter === 'all' || c.mode === filter);
  const doReply = () => {
    staffReply(c.id, reply);
    setReply('');
  };
  return (
    <div className="workbench-page">
      <div className="page-intro">
        <div>
          <span className="eyebrow">SERVICE DESK</span>
          <h1>每一次接手，都有上下文。</h1>
          <p>处理会话与售后申请，让客户的问题得到回应。</p>
        </div>
        <Badge tone="blue">
          <Headphones size={14} />
          客服小周 · 模拟身份
        </Badge>
      </div>
      <div className="work-stats">
        <div>
          <span className="icon-tile blue">
            <MessageSquare size={21} />
          </span>
          <div>
            <span>全部会话</span>
            <strong>{s.conversations.length}</strong>
          </div>
        </div>
        <div>
          <span className="icon-tile orange">
            <Headphones size={21} />
          </span>
          <div>
            <span>待接管</span>
            <strong>{waiting}</strong>
          </div>
        </div>
        <div>
          <span className="icon-tile purple">
            <ShieldCheck size={21} />
          </span>
          <div>
            <span>待审核申请</span>
            <strong>{pending}</strong>
          </div>
        </div>
        <div>
          <span className="icon-tile mint">
            <CircleCheck size={21} />
          </span>
          <div>
            <span>已处理申请</span>
            <strong>{s.requests.filter((r) => r.status !== 'pending').length}</strong>
          </div>
        </div>
      </div>
      <div className="tab-bar" role="tablist" aria-label="工作台功能">
        <button
          role="tab"
          aria-selected={tab === 'conversations'}
          onClick={() => setTab('conversations')}
        >
          <MessageSquare size={16} />
          会话处理
        </button>
        <button role="tab" aria-selected={tab === 'approvals'} onClick={() => setTab('approvals')}>
          <ShieldCheck size={16} />
          售后审核{pending > 0 && <span className="count-badge">{pending}</span>}
        </button>
      </div>
      {tab === 'conversations' ? (
        <div className="service-workspace">
          <aside className="conversation-list">
            <div className="list-heading">
              <h2>客户会话</h2>
              <select
                aria-label="筛选会话"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              >
                <option value="all">全部</option>
                <option value="waiting">待接管</option>
                <option value="human">人工服务中</option>
                <option value="closed">已结束</option>
              </select>
            </div>
            {filtered.length ? (
              filtered.map((conv) => (
                <button
                  className={`conversation-item ${conv.id === c.id ? 'selected' : ''}`}
                  key={conv.id}
                  onClick={() => {
                    setSelected(conv.id);
                    setReply('');
                  }}
                >
                  <span className="small-avatar">{buyers[conv.buyer][0]}</span>
                  <div>
                    <div className="row-between">
                      <strong>{buyers[conv.buyer]}</strong>
                      <span className="tiny muted">{timeLabel(conv.messages.at(-1)!.time)}</span>
                    </div>
                    <p>{conv.title}</p>
                    <Badge
                      tone={
                        conv.mode === 'waiting'
                          ? 'amber'
                          : conv.mode === 'human'
                            ? 'blue'
                            : 'neutral'
                      }
                    >
                      {modes[conv.mode]}
                    </Badge>
                  </div>
                </button>
              ))
            ) : (
              <div className="quiet-state">
                <Inbox size={28} />
                <p>暂无此类会话</p>
              </div>
            )}
          </aside>
          <section className="staff-conversation">
            <div className="staff-heading">
              <span className="user-avatar">
                <UserRound size={21} />
              </span>
              <div>
                <h2>{buyers[c.buyer]}的咨询</h2>
                <p>
                  {modes[c.mode]} · 模拟买家 {c.buyer}
                </p>
              </div>
              <div className="staff-actions">
                {c.mode !== 'human' && c.mode !== 'closed' ? (
                  <button
                    className="btn btn-primary btn-small"
                    onClick={() => setServiceMode(c.id, 'human')}
                  >
                    <Headphones size={15} />
                    接管会话
                  </button>
                ) : null}
                {c.mode === 'human' && (
                  <>
                    <button className="btn btn-small" onClick={() => setServiceMode(c.id, 'ai')}>
                      <RotateCcw size={14} />
                      恢复 AI
                    </button>
                    <button
                      className="btn btn-small"
                      onClick={() => setServiceMode(c.id, 'closed')}
                    >
                      结束服务
                    </button>
                  </>
                )}
              </div>
            </div>
            <div className="handoff-summary">
              <ShieldCheck size={16} />
              <span>
                关联订单：{c.context.orderId || '尚未选择'} ·{' '}
                {c.draft
                  ? '买家正在确认售后草稿'
                  : s.requests.some((r) => r.conversationId === c.id && r.status === 'pending')
                    ? '存在待审核申请'
                    : '会话与已完成查询记录可在下方查看'}
              </span>
            </div>
            <MessageFeed conversation={c} staffView active={active} />
            <form
              className="staff-composer"
              onSubmit={(e) => {
                e.preventDefault();
                doReply();
              }}
            >
              <textarea
                aria-label="人工回复内容"
                placeholder={c.mode === 'human' ? '以客服小周的身份回复…' : '接管会话后即可回复'}
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                disabled={c.mode !== 'human'}
                maxLength={1000}
                rows={2}
              />
              <button
                className="send-button"
                aria-label="发送人工回复"
                disabled={c.mode !== 'human' || !reply.trim()}
              >
                <ArrowUp size={21} />
              </button>
            </form>
          </section>
        </div>
      ) : (
        <ApprovalList />
      )}
    </div>
  );
}
function ApprovalList() {
  const s = useDemo(),
    [filter, setFilter] = useState('all');
  const [reviewing, setReviewing] = useState<{ id: string; decision: 'approved' | 'rejected' }>();
  const [reason, setReason] = useState(''),
    [notice, setNotice] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    void refreshAfterSales().catch((refreshError: unknown) => {
      const message = refreshError instanceof Error ? refreshError.message : '售后申请同步失败。';
      setError(message);
    });
  }, []);
  const requests = s.requests.filter((r) => filter === 'all' || r.status === filter);
  const request = s.requests.find((r) => r.id === reviewing?.id);
  const open = (r: AfterSale, decision: 'approved' | 'rejected') => {
    setReviewing({ id: r.id, decision });
    setReason('');
  };
  return (
    <section className="approvals">
      <div className="row-between approvals-heading">
        <div>
          <h2>售后申请</h2>
          <p>批准申请不会执行资金退款。</p>
        </div>
        <select
          aria-label="筛选售后状态"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="pending">待人工审核</option>
          <option value="approved">已批准</option>
          <option value="rejected">已拒绝</option>
        </select>
      </div>
      {notice && (
        <div className="notice" role="status">
          <Check size={16} />
          {notice}
        </div>
      )}
      {error && (
        <div className="notice" role="alert">
          {error}
        </div>
      )}
      {requests.length ? (
        <div className="approval-grid">
          {requests.map((r) => (
            <article key={r.id} className="approval-item">
              <div className="row-between">
                <strong>
                  {buyers[r.buyer]} · 模拟买家 {r.buyer}
                </strong>
                <span className="tiny muted">{timeLabel(r.createdAt)} 提交</span>
              </div>
              <RequestCard request={r} />
              <div className="policy-reference">
                <ShieldCheck size={16} />
                <p>
                  依据 POL-02：结合订单、商品状态与问题描述人工审核。批准后需继续指引客户处理退件。
                </p>
              </div>
              {r.status === 'pending' && (
                <div className="approval-actions">
                  <button className="btn" onClick={() => open(r, 'rejected')}>
                    <X size={15} />
                    拒绝申请
                  </button>
                  <button className="btn btn-primary" onClick={() => open(r, 'approved')}>
                    <Check size={15} />
                    批准申请
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
      ) : (
        <div className="large-empty">
          <span className="icon-tile blue">
            <ShieldCheck size={30} />
          </span>
          <h3>暂时没有售后申请</h3>
          <p>在买家客服页为订单 10002 提交申请后，会出现在这里。</p>
        </div>
      )}
      {reviewing && request && (
        <Modal
          title={reviewing.decision === 'approved' ? '审核通过此申请' : '拒绝此申请'}
          onClose={() => {
            if (!submitting) setReviewing(undefined);
          }}
        >
          <p className="muted">
            {buyers[request.buyer]} · {request.id} · {request.kind}
          </p>
          <OrderCard id={request.orderId} buyer={request.buyer} />
          <p className="review-question">申请问题：{request.reason}</p>
          <label className="field">
            审核意见（必填）
            <textarea
              aria-label="审核意见"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              maxLength={500}
              placeholder={
                reviewing.decision === 'approved'
                  ? '说明批准理由和下一步处理指引…'
                  : '请说明拒绝原因，客户将看到这段说明…'
              }
            />
          </label>
          <p className="tiny muted">
            {findProduct(findOrder(request.orderId, request.buyer)!.productId).name} ·
            审核人：客服小周
          </p>
          <div className="modal-actions">
            <button className="btn" disabled={submitting} onClick={() => setReviewing(undefined)}>
              取消
            </button>
            <button
              className={`btn ${reviewing.decision === 'approved' ? 'btn-primary' : 'btn-danger'}`}
              disabled={!reason.trim() || request.status !== 'pending' || submitting}
              onClick={async () => {
                setSubmitting(true);
                setError('');
                try {
                  await reviewRequest(request.id, reviewing.decision, reason);
                  setReviewing(undefined);
                  setNotice('审核结果已记录，并发送到买家会话。');
                } catch (reviewError) {
                  const message =
                    reviewError instanceof Error ? reviewError.message : '审核提交失败。';
                  setError(message);
                } finally {
                  setSubmitting(false);
                }
              }}
            >
              {submitting
                ? '正在提交…'
                : reviewing.decision === 'approved'
                  ? '确认批准'
                  : '确认拒绝'}
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
