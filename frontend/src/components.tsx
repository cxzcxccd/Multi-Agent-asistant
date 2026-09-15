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
import { findOrder, findProduct, money, policies, products } from './data';
import { cancelDraft, editDraft, submitDraft, useDemo } from './store';
import type { AfterSale, BuyerId, Conversation, Product, Run } from './types';

export const timeLabel = (value: string) =>
  new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function ProductIcon({ product, size = 32 }: { product: Product; size?: number }) {
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
  const order = findOrder(d.orderId, conversation.buyer)!;
  return (
    <section className="draft-card">
      <div className="draft-heading">
        <span className="icon-tile blue">
          <FileText size={20} />
        </span>
        <div>
          <h3>请核对售后申请</h3>
          <p>草稿尚未提交，确认后进入人工审核</p>
        </div>
        <Badge tone="amber">待你确认</Badge>
      </div>
      <div className="draft-product">
        <span>订单 {d.orderId}</span>
        <strong>{findProduct(order.productId).name}</strong>
      </div>
      <label className="field">
        申请类型
        <select
          value={d.kind}
          onChange={(e) => editDraft(conversation.id, { kind: e.target.value, reason: d.reason })}
        >
          <option>退货申请</option>
          <option>换货申请</option>
        </select>
      </label>
      <label className="field">
        问题描述
        <textarea
          value={d.reason}
          maxLength={500}
          rows={3}
          onChange={(e) => editDraft(conversation.id, { kind: d.kind, reason: e.target.value })}
        />
      </label>
      <div className="draft-actions">
        <button className="btn" onClick={() => cancelDraft(conversation.id)}>
          取消草稿
        </button>
        <button
          className="btn btn-primary"
          disabled={!d.reason.trim()}
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
          <p className="muted">提交后由客服审核，当前不会执行退款。</p>
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
          <div className="modal-actions">
            <button className="btn" onClick={() => setConfirming(false)}>
              返回修改
            </button>
            <button
              className="btn btn-primary"
              disabled={!checked}
              onClick={() => submitDraft(conversation.id, d.id, d.revision)}
            >
              提交申请
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
          const p = products.find((p) => p.source === s),
            policy = policies.find((p) => s.startsWith(p.id));
          return (
            <li key={s}>
              <strong>{s}</strong>
              <p>{p ? `${p.specs.join('；')}。${p.description}` : policy?.content}</p>
            </li>
          );
        })}
      </ul>
      <span className="tiny muted">演示店铺资料 · 不是模型思维过程</span>
    </details>
  );
}
export function RunTrace({ run, expanded = false }: { run: Run; expanded?: boolean }) {
  return (
    <div className="run-trace">
      {run.events.map((e) => (
        <div key={e.id} className={`trace-event ${e.status}`}>
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
              <strong>{e.label}</strong>
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
              <>
                <code>
                  {e.type} · {e.name}
                </code>
                {e.result && <p>{e.result}</p>}
              </>
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
      <RunTrace run={run} />
      <p className="tiny muted">脚本模拟执行 · 非真实 Agent 调用</p>
    </>
  );
}
