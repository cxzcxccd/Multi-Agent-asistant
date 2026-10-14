import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  ArrowUpRight,
  Check,
  ChevronDown,
  Clock3,
  FileText,
  Headphones,
  Package,
  PlugZap,
  ShieldCheck,
  Truck,
  Usb,
  X,
} from 'lucide-react';
import { KnowledgeRetrievalTrace } from './KnowledgeRetrievalTrace';
import { productImageUrl } from './api';
import { productHref } from './productLinks';
import { findOrder, findProduct, money, policies, products } from './data';
import { cancelDraft, editDraft, submitDraft, useDemo } from './store';
import type { AfterSale, BuyerId, Conversation, Product, Run } from './types';

export const timeLabel = (value: string) =>
  new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

function formatToolOutput(output: unknown): string {
  if (typeof output === 'string') return output;
  return JSON.stringify(output, null, 2);
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function ProductIcon({ product, size = 32 }: { product: Product; size?: number }) {
  if (product.image_url)
    return (
      <img
        className="product-icon-image"
        src={productImageUrl(product.image_url)}
        alt={product.name}
        style={{ width: size, height: size, objectFit: 'contain' }}
      />
    );
  const Icon =
    product.category === '耳机' ? Headphones : product.category === '充电器' ? PlugZap : Usb;
  return (
    <span className={`product-icon ${product.color}`}>
      <Icon size={size} strokeWidth={1.5} />
    </span>
  );
}
export function ProductCard({
  id,
  onSelect,
  compact = false,
}: {
  id: string;
  onSelect?: (id: string) => void;
  compact?: boolean;
}) {
  const p = findProduct(id);
  return (
    <article className={`product-card ${compact ? 'compact' : ''}`} data-testid={`product-${id}`}>
      <div className={`product-visual ${p.color}`}>
        <span className="product-series">{p.series}</span>
        <ProductIcon product={p} size={compact ? 26 : 50} />
        <span className="product-category">{p.category} · 示例商品</span>
      </div>
      <div className="product-info">
        <h3>{p.name}</h3>
        <p>{p.specs.slice(0, 2).join(' · ')}</p>
        <div className="product-bottom">
          <strong>{money(p.price)}</strong>
          <span className={p.stock ? 'stock' : 'out-stock'}>
            {p.stock ? `有货 · ${p.stock} 件` : '暂时缺货'}
          </span>
        </div>
        {onSelect && (
          <button className="product-link" onClick={() => onSelect(id)}>
            了解商品 <ArrowUpRight size={15} />
          </button>
        )}
        <a className="product-link" href={productHref(id)} aria-label={`查看 ${p.name} 商品详情`}>
          查看商品详情 <ArrowUpRight size={15} />
        </a>
      </div>
    </article>
  );
}
export function OrderCard({
  id,
  buyer,
  onQuery,
  onAfter,
  full = false,
}: {
  id: string;
  buyer: BuyerId;
  onQuery?: (id: string) => void;
  onAfter?: (id: string) => void;
  full?: boolean;
}) {
  const o = findOrder(id, buyer);
  if (!o) return null;
  const p = findProduct(o.productId);
  return (
    <article className="order-card" data-testid={`order-${id}`}>
      <div className="order-top">
        <span>
          <Package size={16} />
          订单 {o.id}
        </span>
        <Badge tone={o.status === '运输中' ? 'blue' : o.status === '待发货' ? 'amber' : 'green'}>
          {o.status}
        </Badge>
      </div>
      <div className="order-product">
        <ProductIcon product={p} />
        <div>
          <strong>{p.name}</strong>
          <p>
            {o.date} 下单 · 数量 {o.quantity}
          </p>
        </div>
        <strong className="order-price">{money(p.price * o.quantity)}</strong>
      </div>
      {full && (
        <details className="logistics" open>
          <summary>
            <Truck size={15} />
            物流记录 <ChevronDown size={14} />
          </summary>
          {o.logistics.length ? (
            <ol className="timeline">
              {o.logistics.map((event, i) => (
                <li key={event.time} className={i === 0 ? 'latest' : ''}>
                  <strong>{event.title}</strong>
                  <time>{event.time}</time>
                  <p>{event.detail}</p>
                </li>
              ))}
            </ol>
          ) : (
            <div className="empty-logistics">尚无物流信息，商家正在准备发货。</div>
          )}
          <span className="tiny muted">演示速运 · 模拟物流记录</span>
        </details>
      )}
      {(onQuery || onAfter) && (
        <div className="order-actions">
          {onQuery && (
            <button
              className="btn btn-small"
              aria-label={`查看订单 ${id}`}
              onClick={() => onQuery(id)}
            >
              查看订单与物流
            </button>
          )}
          {onAfter && (
            <button
              className="text-button"
              aria-label={`订单 ${id} 申请售后`}
              onClick={() => onAfter(id)}
            >
              申请售后 <ArrowUpRight size={14} />
            </button>
          )}
        </div>
      )}
    </article>
  );
}
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      className="modal"
      ref={ref}
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      aria-label={title}
    >
      <div className="modal-heading">
        <h2>{title}</h2>
        <button className="icon-button" aria-label="关闭弹窗" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function DraftCard({ conversation }: { conversation: Conversation }) {
  const d = conversation.draft!;
  const [confirming, setConfirming] = useState(false);
  const [checked, setChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const order = findOrder(d.orderId, conversation.buyer)!;
  const reasonReady = d.reason.trim().length >= 5;
  const busy = submitting || cancelling;
  return (
    <section className="draft-card" aria-busy={busy}>
      <div className="draft-heading">
        <span className="icon-tile blue">
          <FileText size={20} />
        </span>
        <div>
          <h3>请核对售后申请</h3>
          <p>先查看草稿，补充原因，再确认转交人工</p>
        </div>
        <Badge tone={reasonReady ? 'blue' : 'amber'}>
          {reasonReady ? '待你确认' : '待补充原因'}
        </Badge>
      </div>
      <ol className="draft-steps" aria-label="售后申请步骤">
        <li className="is-complete">1 查看草稿</li>
        <li className={reasonReady ? 'is-complete' : 'is-current'}>2 补充原因</li>
        <li className={reasonReady ? 'is-current' : ''}>3 确认转交人工</li>
      </ol>
      <div className="draft-module draft-module-info">
        <h4>已确认的申请信息</h4>
        <div className="draft-product">
          <span>订单 {d.orderId}</span>
          <strong>{findProduct(order.productId).name}</strong>
          <span>当前订单状态：{order.status}</span>
        </div>
        <label className="field">
          申请类型
          <select
            disabled={busy}
            value={d.kind}
            onChange={(e) => editDraft(conversation.id, { kind: e.target.value, reason: d.reason })}
          >
            <option>退货申请</option>
            <option>换货申请</option>
          </select>
        </label>
      </div>
      <div className="draft-module draft-module-reason">
        <h4>补充申请原因</h4>
        <label className="field">
          {d.kind.includes('换货') ? '换货原因 / 问题描述' : '退货原因 / 问题描述'}
          <textarea
            disabled={busy}
            aria-label="问题描述"
            aria-describedby={`draft-reason-${d.id}`}
            required
            minLength={5}
            placeholder="请说明退货原因或具体问题，例如：左耳无声音，已更换设备重试。"
            value={d.reason}
            maxLength={500}
            rows={3}
            onChange={(e) => editDraft(conversation.id, { kind: d.kind, reason: e.target.value })}
          />
        </label>
        <p className="draft-hint" id={`draft-reason-${d.id}`}>
          {reasonReady
            ? '原因已填写，可继续核对并确认。'
            : '请填写至少 5 个字的具体原因，补充后才能确认。'}
        </p>
      </div>
      <div className="draft-module draft-module-next">
        <h4>确认后转交人工</h4>
        <p>你确认提交后，客服才能收到申请并审核退货事项。当前草稿尚未提交。</p>
        {order.status === '待发货' ? (
          <p>订单尚未发货，人工客服将核实取消发货等处理方式。</p>
        ) : order.status === '运输中' ? (
          <p>商品尚在运输途中，人工客服将核实物流拦截或退回等处理方式。</p>
        ) : null}
      </div>
      <div className="draft-actions">
        <button
          className="btn"
          disabled={busy}
          onClick={async () => {
            setCancelling(true);
            try {
              await cancelDraft(conversation.id);
            } finally {
              setCancelling(false);
            }
          }}
        >
          {cancelling ? '正在取消…' : '取消草稿'}
        </button>
        <button
          className="btn btn-primary"
          disabled={!reasonReady || busy}
          onClick={() => {
            setChecked(false);
            setConfirming(true);
          }}
        >
          确认申请内容
        </button>
      </div>
      {confirming && (
        <Modal title="确认提交售后申请" onClose={() => setConfirming(false)}>
          <p className="draft-confirm-notice">
            请核对下面的完整草稿。点击“提交申请”即确认转交人工客服审核。
          </p>
          <dl className="confirmation-detail">
            <dt>订单</dt>
            <dd>{d.orderId}</dd>
            <dt>商品</dt>
            <dd>{findProduct(order.productId).name}</dd>
            <dt>类型</dt>
            <dd>{d.kind}</dd>
            <dt>问题</dt>
            <dd>{d.reason}</dd>
          </dl>
          <label className="check-label">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
            />
            我已核对订单与申请内容
          </label>
          {submitError && (
            <p className="notice" role="alert">
              {submitError}
            </p>
          )}
          <div className="modal-actions">
            <button className="btn" disabled={submitting} onClick={() => setConfirming(false)}>
              返回修改
            </button>
            <button
              className="btn btn-primary"
              disabled={!checked || !reasonReady || submitting}
              onClick={async () => {
                setSubmitting(true);
                setSubmitError('');
                try {
                  await submitDraft(conversation.id, d.id, d.revision);
                  setConfirming(false);
                } catch (error) {
                  const message = error instanceof Error ? error.message : '提交失败，请稍后重试。';
                  setSubmitError(message);
                } finally {
                  setSubmitting(false);
                }
              }}
            >
              {submitting ? '正在提交…' : '提交申请'}
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
export function RequestCard({ request }: { request: AfterSale }) {
  return (
    <section className="request-card" data-testid={`request-${request.id}`}>
      <div className="row-between">
        <strong>
          <ShieldCheck size={17} />
          {request.kind}
        </strong>
        <Badge
          tone={
            request.status === 'pending' ? 'amber' : request.status === 'approved' ? 'green' : 'red'
          }
        >
          {request.status === 'pending'
            ? '待人工审核'
            : request.status === 'approved'
              ? '已批准'
              : '已拒绝'}
        </Badge>
      </div>
      <p className="tiny muted">
        {request.id} · 订单 {request.orderId}
      </p>
      <p>{request.reason}</p>
      {request.reviewReason && (
        <div className="review-note">
          <strong>{request.reviewer}的审核意见</strong>
          <p>{request.reviewReason}</p>
        </div>
      )}
      <div className="request-footer">
        <Clock3 size={13} />
        {request.status === 'pending'
          ? '等待客服审核，请勿重复提交'
          : request.status === 'approved'
            ? '申请审核通过 · 未执行资金退款'
            : '如需补充材料，可联系人工客服'}
      </div>
    </section>
  );
}
export function SourceList({ sources }: { sources: string[] }) {
  return (
    <details className="sources">
      <summary>
        <FileText size={14} />
        参考了 {sources.length} 份资料 <ChevronDown size={14} />
      </summary>
      <ul>
        {sources.map((s) => {
          const [sourceLabel, ...sourceContentParts] = s.split('\n');
          const sourceContent = sourceContentParts.join('\n');
          const p = products.find((p) => p.source === s),
            policy = policies.find((p) => s.startsWith(p.id));
          return (
            <li key={s}>
              <strong>{sourceLabel}</strong>
              <p>
                {sourceContent || (p ? `${p.specs.join('；')}。${p.description}` : policy?.content)}
              </p>
            </li>
          );
        })}
      </ul>
      <span className="tiny muted">演示店铺资料 · 不是模型思维过程</span>
    </details>
  );
}
export function RunTrace({ run, expanded = false }: { run: Run; expanded?: boolean }) {
  const typeLabels: Record<string, string> = {
    Router: '分流 Router',
    Tool: '工具',
    Summary: '汇总',
    Agent: 'Agent',
    Retrieval: '检索',
  };
  return (
    <div className="run-trace">
      {run.events.map((e) => (
        <div key={e.id} className={`trace-event trace-${e.type.toLowerCase()} ${e.status}`}>
          <span className="trace-mark">
            {e.status === 'success' ? (
              <Check size={13} />
            ) : e.status === 'error' ? (
              <X size={13} />
            ) : (
              <span />
            )}
          </span>
          <div>
            <div className="row-between">
              <strong className="trace-kind">{typeLabels[e.type] || e.type}</strong>
              <span className="tiny muted">
                {e.status === 'running'
                  ? '进行中'
                  : e.status === 'success'
                    ? '完成'
                    : e.status === 'error'
                      ? '失败'
                      : '已停止'}
              </span>
            </div>
            {expanded && (
              <div className="trace-details">
                {e.output && (
                  <div>
                    <span>输出</span>
                    <code>{e.output}</code>
                  </div>
                )}
                {e.description && (
                  <div>
                    <span>描述</span>
                    <p>{e.description}</p>
                  </div>
                )}
                {!e.output && (
                  <code>
                    {e.type} · {e.name}
                  </code>
                )}
                {e.result && e.result !== e.description && (
                  <div>
                    <span>结果</span>
                    <p>{e.result}</p>
                  </div>
                )}
                {e.toolResults?.map((toolResult, index) => (
                  <div className="trace-tool-result" key={`${toolResult.name}-${index}`}>
                    <span>工具返回 · {toolResult.name}</span>
                    {toolResult.name === 'search_knowledge' ? (
                      <>
                        <KnowledgeRetrievalTrace output={toolResult.output} />
                        <details>
                          <summary>查看原始工具返回</summary>
                          <pre>{formatToolOutput(toolResult.output)}</pre>
                        </details>
                      </>
                    ) : (
                      <pre>{formatToolOutput(toolResult.output)}</pre>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
export function LatestRun({ conversationId }: { conversationId: string }) {
  const s = useDemo(),
    run = s.runs.find((r) => r.conversationId === conversationId);
  if (!run)
    return (
      <div className="quiet-state">
        <ShieldCheck size={24} />
        <p>每次处理都有迹可循</p>
        <span>发起咨询后，在这里查看查询与处理状态。</span>
      </div>
    );
  return (
    <>
      <div className="row-between">
        <strong>{run.module}</strong>
        <Badge
          tone={run.status === 'error' ? 'red' : run.status === 'running' ? 'blue' : 'neutral'}
        >
          {run.status === 'running'
            ? '处理中'
            : run.status === 'error'
              ? '查询失败'
              : run.status === 'stopped'
                ? '已停止'
                : '处理完成'}
        </Badge>
      </div>
      <RunTrace run={run} expanded />
      <p className="tiny muted">
        {run.origin === 'backend' ? '后端 LangGraph 真实执行' : '脚本模拟执行 · 非真实 Agent 调用'}
      </p>
    </>
  );
}
