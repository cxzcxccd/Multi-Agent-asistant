import { useSyncExternalStore } from 'react';
import {
  AfterSalesApiError,
  ChatApiError,
  createAfterSaleDraft,
  getAuthSession,
  listAfterSales,
  listPendingAfterSales,
  reviewAfterSale,
  requestHumanHandoff,
  sendStaffMessage,
  streamChatMessage,
  submitAfterSaleDraft,
  subscribeToConversations,
  subscribeToStaffConversations,
  updateConversationMode,
} from './api';
import type { ApiAfterSaleRequest, ApiConversation, ChatStreamCallbacks } from './api';
import { buyers, findOrder, findProduct, orders, policies, products } from './data';
import type {
  AfterSale,
  BuyerId,
  Conversation,
  DemoState,
  Draft,
  Message,
  RunEvent,
} from './types';

const KEY = 'geek-select-demo-v1';
const uid = () => crypto.randomUUID();
const tabId = (() => {
  try {
    const saved = sessionStorage.getItem('geek-select-tab-id');
    if (saved) return saved;
    const id = uid();
    sessionStorage.setItem('geek-select-tab-id', id);
    return id;
  } catch {
    return uid();
  }
})();
const now = () => new Date().toISOString();
const tokens = new Map<string, string>();
const backendRequests = new Map<string, AbortController>();
const afterSaleSubmissions = new Map<string, Promise<void>>();
const useBackendChat = import.meta.env.VITE_CHAT_MODE !== 'script';
export const backendChatEnabled = useBackendChat;
const listeners = new Set<() => void>();
const welcome = (): Message => ({
  id: uid(),
  role: 'assistant',
  time: now(),
  text: '你好，我是小极。想了解商品，还是查询订单？',
});
function createConversation(buyer: BuyerId): Conversation {
  return {
    id: uid(),
    buyer,
    title: '新的咨询',
    createdAt: now(),
    mode: 'ai',
    messages: [welcome()],
    context: {},
  };
}
function initial(): DemoState {
  const a = createConversation('A'),
    b = createConversation('B');
  return {
    version: 1,
    buyer: 'A',
    active: { A: a.id, B: b.id },
    conversations: [a, b],
    requests: [],
    runs: [],
    fault: 'none',
  };
}
function recover(raw: string, interruptRunning = true): DemoState {
  const saved = JSON.parse(raw) as DemoState;
  if (
    saved.version !== 1 ||
    !['A', 'B'].includes(saved.buyer) ||
    !Array.isArray(saved.conversations) ||
    !saved.active ||
    !Array.isArray(saved.runs) ||
    !Array.isArray(saved.requests)
  )
    throw new Error('Invalid snapshot');
  if (
    !saved.conversations.every(
      (c) => Array.isArray(c.messages) && c.context && ['A', 'B'].includes(c.buyer),
    ) ||
    !(['A', 'B'] as BuyerId[]).every((b) =>
      saved.conversations.some((c) => c.id === saved.active[b] && c.buyer === b),
    )
  )
    throw new Error('Invalid conversations');
  for (const run of saved.runs)
    if (interruptRunning && run.status === 'running' && (!run.ownerTab || run.ownerTab === tabId)) {
      run.status = 'stopped';
      run.events.forEach((e) => {
        if (e.status === 'running') e.status = 'stopped';
      });
      const c = saved.conversations.find((c) => c.id === run.conversationId);
      c?.messages.forEach((m) => {
        m.streaming = false;
      });
      c?.messages.push({
        id: uid(),
        role: 'system',
        time: now(),
        text: '上次处理已中断。已提交的申请仍然保留，你可以重新查询。',
        retryText: run.query,
      });
    }
  return saved;
}
let state: DemoState;
try {
  const saved = localStorage.getItem(KEY);
  state = saved ? recover(saved) : initial();
  if (saved && JSON.stringify(state) !== saved) {
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch {
      state.storageWarning = '浏览器存储不可用，恢复状态未能保存。';
    }
  }
} catch {
  state = initial();
  state.storageWarning = '未能读取浏览器记录，已载入初始演示数据。';
}
function latestState(): DemoState {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? recover(raw, false) : state;
  } catch {
    return state;
  }
}
function update(fn: (draft: DemoState) => void) {
  const next = structuredClone(latestState());
  fn(next);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    next.storageWarning = '浏览器存储不可用，刷新后可能丢失本次演示记录。';
  }
  state = next;
  listeners.forEach((l) => l());
}
window.addEventListener('storage', (e) => {
  if (e.key !== KEY || !e.newValue) return;
  try {
    // A queued storage event may describe an older write than our own last action.
    state = latestState();
    for (const id of tokens.keys()) {
      if (
        getConversation(id)?.mode !== 'ai' ||
        !state.runs.some((r) => r.conversationId === id && r.status === 'running')
      )
        tokens.delete(id);
    }
    listeners.forEach((l) => l());
  } catch {
    /* Keep the current valid snapshot. */
  }
});
export const useDemo = () =>
  useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => state,
  );
export const currentConversation = (s: DemoState) =>
  s.conversations.find((c) => c.id === s.active[s.buyer])!;
const getConversation = (id: string) => state.conversations.find((c) => c.id === id);
const addMessage = (
  c: Conversation,
  role: Message['role'],
  text: string,
  extra: Partial<Message> = {},
) => c.messages.push({ id: uid(), role, text, time: now(), ...extra });

export function cancelRun(conversationId: string) {
  backendRequests.get(conversationId)?.abort();
  backendRequests.delete(conversationId);
  tokens.delete(conversationId);
  update((s) => {
    s.runs
      .filter((r) => r.conversationId === conversationId && r.status === 'running')
      .forEach((r) => {
        r.status = 'stopped';
        r.events.forEach((e) => {
          if (e.status === 'running') e.status = 'stopped';
        });
      });
    const c = s.conversations.find((c) => c.id === conversationId);
    c?.messages.forEach((m) => {
      m.streaming = false;
    });
  });
}
export function switchBuyer(buyer: BuyerId) {
  if (buyer === state.buyer) return;
  cancelRun(state.active[state.buyer]);
  update((s) => {
    s.buyer = buyer;
  });
}
export function newConversation() {
  cancelRun(state.active[state.buyer]);
  const c = createConversation(state.buyer);
  update((s) => {
    s.conversations.unshift(c);
    s.active[s.buyer] = c.id;
  });
  return c.id;
}
export function selectConversation(id: string) {
  if (getConversation(id)?.buyer === state.buyer)
    update((s) => {
      s.active[s.buyer] = id;
    });
}
export function resetDemo() {
  backendRequests.forEach((request) => request.abort());
  backendRequests.clear();
  tokens.clear();
  const fresh = initial();
  update((s) => Object.assign(s, fresh, { storageWarning: undefined }));
}
export function setFault(fault: DemoState['fault']) {
  update((s) => {
    s.fault = fault;
  });
}
function mergeRemoteConversation(local: Conversation, remote: ApiConversation) {
  local.remoteId = remote.id;
  local.title = remote.title;
  local.mode = remote.mode;
  for (const remoteMessage of remote.messages) {
    const exists = local.messages.some((message) => message.id === remoteMessage.id);
    if (exists) continue;
    local.messages.push({
      id: remoteMessage.id,
      role: remoteMessage.role,
      text: remoteMessage.content,
      time: remoteMessage.created_at,
      origin: 'backend',
    });
  }
}

function conversationFromRemote(remote: ApiConversation): Conversation {
  const conversation: Conversation = {
    id: remote.id,
    remoteId: remote.id,
    buyer: remote.buyer_id,
    title: remote.title,
    createdAt: remote.created_at,
    mode: remote.mode,
    messages: [],
    context: {},
  };
  mergeRemoteConversation(conversation, remote);
  return conversation;
}

function mergeConversationSnapshots(remoteConversations: ApiConversation[]) {
  update((s) => {
    for (const remote of remoteConversations) {
      const local = s.conversations.find(
        (conversation) => conversation.remoteId === remote.id || conversation.id === remote.id,
      );
      if (local) {
        mergeRemoteConversation(local, remote);
        continue;
      }
      s.conversations.unshift(conversationFromRemote(remote));
    }
  });
}

export function subscribeToConversationUpdates(): () => void {
  if (!useBackendChat) return () => {};
  const session = getAuthSession();
  if (session?.principal.role === 'staff') {
    return subscribeToStaffConversations(mergeConversationSnapshots);
  }
  if (session?.principal.buyer_id) {
    return subscribeToConversations(session.principal.buyer_id, mergeConversationSnapshots);
  }
  return () => {};
}

export async function handoff(id: string): Promise<void> {
  cancelRun(id);
  const conversation = getConversation(id);
  if (!conversation || conversation.mode !== 'ai') return;
  if (useBackendChat) {
    try {
      const remote = await requestHumanHandoff({
        buyerId: conversation.buyer,
        conversationId: conversation.remoteId,
        title: conversation.title,
      });
      update((s) => {
        const current = s.conversations.find((item) => item.id === id);
        if (current) mergeRemoteConversation(current, remote);
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '转人工请求失败。';
      update((s) => {
        const current = s.conversations.find((item) => item.id === id);
        if (current) addMessage(current, 'system', message);
      });
    }
    return;
  }
  update((s) => {
    const c = s.conversations.find((c) => c.id === id);
    if (!c || c.mode !== 'ai') return;
    c.mode = 'waiting';
    addMessage(c, 'system', '已进入人工服务队列。咨询记录会一并交给客服，自动回复已暂停。');
  });
}
export async function setServiceMode(id: string, mode: 'human' | 'ai' | 'closed'): Promise<void> {
  cancelRun(id);
  const conversation = getConversation(id);
  if (!conversation || conversation.mode === mode) return;
  if (useBackendChat && conversation.remoteId) {
    try {
      const remote = await updateConversationMode({
        conversationId: conversation.remoteId,
        mode,
      });
      update((s) => {
        const current = s.conversations.find((item) => item.id === id);
        if (current) mergeRemoteConversation(current, remote);
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '会话状态更新失败。';
      update((s) => {
        const current = s.conversations.find((item) => item.id === id);
        if (current) addMessage(current, 'system', message);
      });
    }
    return;
  }
  update((s) => {
    const c = s.conversations.find((c) => c.id === id);
    if (!c || c.mode === mode) return;
    c.mode = mode;
    addMessage(
      c,
      'system',
      mode === 'human'
        ? '客服小周已接入，会继续为你处理。'
        : mode === 'ai'
          ? '客服已恢复 AI 服务，小极继续为你解答。'
          : '本次人工服务已结束。你可以开始新会话。',
    );
  });
}
export async function staffReply(id: string, text: string): Promise<void> {
  if (!text.trim()) return;
  const conversation = getConversation(id);
  if (useBackendChat && conversation?.remoteId) {
    try {
      const remote = await sendStaffMessage({
        conversationId: conversation.remoteId,
        message: text.trim(),
      });
      update((s) => {
        const current = s.conversations.find((item) => item.id === id);
        if (current) mergeRemoteConversation(current, remote);
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '人工回复发送失败。';
      update((s) => {
        const current = s.conversations.find((item) => item.id === id);
        if (current) addMessage(current, 'system', message);
      });
    }
    return;
  }
  update((s) => {
    const c = s.conversations.find((c) => c.id === id);
    if (c?.mode === 'human') addMessage(c, 'staff', text.trim());
  });
}
export function editDraft(conversationId: string, patch: Pick<Draft, 'kind' | 'reason'>) {
  update((s) => {
    const c = s.conversations.find((c) => c.id === conversationId && c.buyer === s.buyer);
    if (c?.draft) c.draft = { ...c.draft, ...patch, revision: c.draft.revision + 1 };
  });
}
export function cancelDraft(conversationId: string) {
  update((s) => {
    const c = s.conversations.find((c) => c.id === conversationId && c.buyer === s.buyer);
    if (!c?.draft) return;
    delete c.draft;
    addMessage(c, 'system', '申请草稿已取消，没有提交售后申请。');
  });
}
function submitDraftInScriptMode(conversationId: string, draftId: string, revision: number) {
  update((s) => {
    const c = s.conversations.find((c) => c.id === conversationId && c.buyer === s.buyer);
    if (
      !c?.draft ||
      c.draft.id !== draftId ||
      c.draft.revision !== revision ||
      !c.draft.reason.trim() ||
      !findOrder(c.draft.orderId, c.buyer)
    )
      return;
    if (s.requests.some((r) => r.sourceDraftId === draftId)) return;
    const duplicate = s.requests.find(
      (r) => r.orderId === c.draft!.orderId && r.buyer === c.buyer && r.status === 'pending',
    );
    if (duplicate) {
      addMessage(c, 'system', '这笔订单已有待审核申请，请等待客服处理。', {
        requestId: duplicate.id,
      });
      delete c.draft;
      return;
    }
    const id = `AS-${String(s.requests.length + 1).padStart(4, '0')}`;
    s.requests.unshift({
      id,
      conversationId,
      buyer: c.buyer,
      orderId: c.draft.orderId,
      kind: c.draft.kind,
      reason: c.draft.reason.trim(),
      status: 'pending',
      createdAt: now(),
      sourceDraftId: draftId,
      origin: 'script',
    });
    delete c.draft;
    addMessage(c, 'assistant', '申请已提交，正在等待客服审核。你可以在这里查看进度。', {
      requestId: id,
    });
  });
}

function requestTypeFromDraft(kind: string): '退货' | '换货' {
  return kind.includes('换货') ? '换货' : '退货';
}

function mapAfterSaleRequest(
  remote: ApiAfterSaleRequest,
  conversationId: string,
  sourceDraftId: string,
): AfterSale {
  const status = remote.status as AfterSale['status'];
  return {
    id: remote.id,
    conversationId,
    buyer: remote.buyer_id,
    orderId: remote.order_id,
    kind: `${remote.request_type}申请`,
    reason: remote.reason,
    status,
    createdAt: remote.submitted_at || remote.created_at,
    reviewedAt: remote.reviewed_at || undefined,
    reviewer: remote.reviewer || undefined,
    reviewReason: remote.review_reason || undefined,
    sourceDraftId,
    version: remote.version,
    origin: 'backend',
  };
}

async function createOrRecoverAfterSaleDraft(
  buyerId: BuyerId,
  draft: Draft,
): Promise<ApiAfterSaleRequest> {
  try {
    return await createAfterSaleDraft({
      buyerId,
      orderId: draft.orderId,
      requestType: requestTypeFromDraft(draft.kind),
      reason: draft.reason.trim(),
    });
  } catch (error) {
    if (!(error instanceof AfterSalesApiError) || error.status !== 409) throw error;

    // 创建响应丢失时，后端可能已经保存草稿。先恢复它，再安全地重试提交。
    const requests = await listAfterSales(buyerId);
    const matchingDraft = requests.find((item) => {
      const sameOrder = item.order_id === draft.orderId;
      const sameReason = item.reason === draft.reason.trim();
      const active = item.status === 'draft' || item.status === 'pending';
      return active && sameOrder && sameReason;
    });
    if (!matchingDraft) throw error;
    return matchingDraft;
  }
}

async function submitDraftToBackend(
  conversationId: string,
  draftId: string,
  revision: number,
): Promise<void> {
  const conversation = getConversation(conversationId);
  const draft = conversation?.draft;
  if (
    !conversation ||
    !draft ||
    draft.id !== draftId ||
    draft.revision !== revision ||
    !draft.reason.trim()
  ) {
    throw new Error('草稿已变更，请重新核对后提交。');
  }

  const created = await createOrRecoverAfterSaleDraft(conversation.buyer, draft);
  let submitted = created;
  if (created.status === 'draft') {
    submitted = await submitAfterSaleDraft({
      requestId: created.id,
      buyerId: conversation.buyer,
      expectedVersion: created.version,
      idempotencyKey: `submit-${draft.id}-${revision}`,
    });
  }

  update((s) => {
    const current = s.conversations.find((item) => item.id === conversationId);
    if (!current?.draft || current.draft.id !== draftId) return;

    const request = mapAfterSaleRequest(submitted, conversationId, draftId);
    s.requests = s.requests.filter((item) => item.id !== request.id);
    s.requests.unshift(request);
    delete current.draft;
    addMessage(current, 'assistant', '申请已提交，正在等待客服审核。你可以在这里查看进度。', {
      requestId: request.id,
      origin: 'backend',
    });
  });
}

export function submitDraft(
  conversationId: string,
  draftId: string,
  revision: number,
): Promise<void> {
  if (!useBackendChat) {
    submitDraftInScriptMode(conversationId, draftId, revision);
    return Promise.resolve();
  }

  const currentSubmission = afterSaleSubmissions.get(draftId);
  if (currentSubmission) return currentSubmission;

  const submission = submitDraftToBackend(conversationId, draftId, revision).finally(() => {
    afterSaleSubmissions.delete(draftId);
  });
  afterSaleSubmissions.set(draftId, submission);
  return submission;
}

function reviewRequestInScriptMode(id: string, decision: 'approved' | 'rejected', reason: string) {
  if (!reason.trim()) return;
  update((s) => {
    const r = s.requests.find((r) => r.id === id);
    if (!r || r.status !== 'pending' || !findOrder(r.orderId, r.buyer)) return;
    r.status = decision;
    r.reviewReason = reason.trim();
    r.reviewedAt = now();
    r.reviewer = '客服小周';
    const c = s.conversations.find((c) => c.id === r.conversationId);
    if (c)
      addMessage(
        c,
        'staff',
        decision === 'approved'
          ? '你的售后申请已审核通过。请根据客服指引确认后续处理；当前没有执行退款。'
          : `售后申请未获通过：${reason.trim()}。如需补充说明，请联系人工客服。`,
        { requestId: id },
      );
  });
}

export async function reviewRequest(
  id: string,
  decision: 'approved' | 'rejected',
  reason: string,
): Promise<void> {
  const request = state.requests.find((item) => item.id === id);
  if (!request || !reason.trim()) return;
  if (!useBackendChat || request.origin !== 'backend' || request.version === undefined) {
    reviewRequestInScriptMode(id, decision, reason);
    return;
  }

  const reviewed = await reviewAfterSale({
    requestId: request.id,
    reviewer: '客服小周',
    decision,
    reason: reason.trim(),
    expectedVersion: request.version,
  });
  update((s) => {
    const current = s.requests.find((item) => item.id === id);
    if (!current) return;

    const mapped = mapAfterSaleRequest(reviewed, current.conversationId, current.sourceDraftId);
    Object.assign(current, mapped);
    const conversation = s.conversations.find((item) => item.id === current.conversationId);
    if (!conversation) return;
    const reply =
      decision === 'approved'
        ? '你的售后申请已审核通过。请根据客服指引确认后续处理；当前没有执行退款。'
        : `售后申请未获通过：${reason.trim()}。如需补充说明，请联系人工客服。`;
    addMessage(conversation, 'staff', reply, { requestId: id, origin: 'backend' });
  });
}

export async function refreshAfterSales(): Promise<void> {
  if (!useBackendChat) return;

  const session = getAuthSession();
  let remoteRequests: ApiAfterSaleRequest[] = [];
  if (session?.principal.role === 'staff') {
    remoteRequests = await listPendingAfterSales();
  } else if (session?.principal.buyer_id) {
    const buyerRequests = await listAfterSales(session.principal.buyer_id);
    remoteRequests = buyerRequests.filter((item) => item.status !== 'draft');
  }
  update((s) => {
    for (const remote of remoteRequests) {
      const existing = s.requests.find((item) => item.id === remote.id);
      const conversationId = existing?.conversationId || s.active[remote.buyer_id];
      const sourceDraftId = existing?.sourceDraftId || remote.id;
      const mapped = mapAfterSaleRequest(remote, conversationId, sourceDraftId);
      if (existing) {
        Object.assign(existing, mapped);
      } else {
        s.requests.push(mapped);
      }
    }
  });
}

interface Plan {
  module: string;
  text: string;
  steps: Array<{ type: RunEvent['type']; name: string; label: string; result: string }>;
  products?: string[];
  orders?: string[];
  sources?: string[];
  requestId?: string;
  draft?: Draft;
  context?: Partial<Conversation['context']>;
}
const step = (type: RunEvent['type'], name: string, label: string, result: string) => ({
  type,
  name,
  label,
  result,
});
function planReply(c: Conversation, text: string): Plan {
  const base: Plan = { module: '客服协调', text: '', steps: [] };
  const orderId = text.match(/\b\d{5}\b/)?.[0];
  const wantsAfter =
    /退货|退款|售后|换货|坏了|没声音/.test(text) ||
    (c.context.intent === 'aftersales' && !!c.context.awaiting);
  const wantsOrder =
    /订单|物流|快递|到哪|发货|查单/.test(text) || !!orderId || c.context.awaiting === 'order';
  if (/忽略.*规则|跳过.*审核|直接.*批准|泄露|系统提示词/.test(text))
    return {
      ...base,
      text: '订单归属和人工审核不能跳过。我可以帮你查询自己的订单，或提交申请等待客服审核。',
    };
  if (/售后.*进度|申请.*进度/.test(text)) {
    const r = state.requests.find(
      (r) => r.buyer === c.buyer && (!orderId || r.orderId === orderId),
    );
    return {
      ...base,
      module: '售后服务',
      text: r
        ? '这是你的最新售后申请状态。'
        : '目前没有找到已提交的售后申请。你可以先选择订单并说明问题。',
      requestId: r?.id,
      steps: [step('Tool', 'get_after_sale', '正在查询售后进度', r ? r.status : '没有已提交申请')],
    };
  }
  if (wantsAfter || wantsOrder || c.context.awaiting === 'reason') {
    const selectedId = orderId || c.context.orderId;
    const visible = orders.filter((o) => o.buyer === c.buyer);
    const intent = wantsAfter || c.context.awaiting === 'reason' ? 'aftersales' : 'order';
    if (!selectedId)
      return {
        ...base,
        module: intent === 'aftersales' ? '售后服务' : '订单服务',
        text:
          intent === 'aftersales'
            ? '你想为哪笔订单申请售后？请选择订单，再告诉我遇到的问题。'
            : '这是你可查询的订单。请选择一笔，我来查看详细进度。',
        orders: visible.map((o) => o.id),
        context: { awaiting: 'order', intent },
        steps: [
          step('Tool', 'list_my_orders', '正在查找你的订单', `找到 ${visible.length} 笔订单`),
        ],
      };
    const order = findOrder(selectedId, c.buyer);
    const lookup = step(
      'Tool',
      'get_order',
      '正在查询订单',
      order ? `订单 ${order.id} · ${order.status} · 已校验演示归属` : '未找到可查询的订单',
    );
    if (!order)
      return {
        ...base,
        module: '订单服务',
        text: '未找到可查询的订单，请核对或联系人工。',
        context: { orderId: undefined, awaiting: 'order', intent },
        steps: [lookup],
      };
    const context: Plan['context'] = {
      orderId: order.id,
      productId: order.productId,
      awaiting: undefined,
      intent,
    };
    if (intent === 'aftersales') {
      const existing = state.requests.find(
        (r) => r.buyer === c.buyer && r.orderId === order.id && r.status === 'pending',
      );
      if (existing)
        return {
          ...base,
          module: '售后服务',
          text: '这笔订单已有待审核的售后申请，无需重复提交。',
          requestId: existing.id,
          context,
          steps: [lookup],
        };
      const hasReason =
        /没声音|故障|损坏|坏了|不喜欢|不合适|断连|不能|无法|质量|破损|杂音|不充电/.test(text) ||
        (c.context.awaiting === 'reason' && text.trim().length >= 2);
      if (!hasReason)
        return {
          ...base,
          module: '售后服务',
          text: '已找到订单。请具体说说遇到的问题，例如「耳机有一边没声音」，我会整理申请供你确认。',
          orders: [order.id],
          context: { ...context, awaiting: 'reason' },
          steps: [lookup],
        };
      const draft: Draft = {
        id: uid(),
        orderId: order.id,
        kind: /换货/.test(text) ? '换货申请' : '退货申请',
        reason: text,
        revision: 1,
      };
      return {
        ...base,
        module: '售后服务',
        text: '我已整理好售后申请草稿。请核对商品和问题描述，确认提交后将由客服审核。',
        orders: [order.id],
        draft,
        context,
        sources: ['POL-02 · 售后申请与审核 v1.0'],
        steps: [
          lookup,
          step('Retrieval', 'retrieve_policy', '正在查阅售后规则', policies[1].content),
        ],
      };
    }
    return {
      ...base,
      module: '订单服务',
      text: order.logistics.length
        ? `订单 ${order.id} 当前${order.status}。${order.logistics[0].detail}\n以下是已查询到的物流记录，暂未提供预计送达时间。`
        : `订单 ${order.id} 当前待发货，还没有物流信息。演示店铺通常在订单确认后 48 小时内安排发货，实际以订单更新为准。`,
      orders: [order.id],
      context,
      sources: !order.logistics.length ? ['POL-01 · 发货与物流 v1.0'] : undefined,
      steps: [
        lookup,
        step(
          'Tool',
          'get_logistics',
          '正在获取物流信息',
          order.logistics.length ? `${order.logistics.length} 条物流记录` : '暂无物流信息',
        ),
      ],
    };
  }
  const namedProduct = products.find(
    (p) => text.toLowerCase().includes(p.name.toLowerCase()) || text.includes(p.id),
  );
  const category =
    (['耳机', '充电器', '扩展坞'] as const).find((cat) => text.includes(cat)) || c.context.category;
  const budgetMatch = text.match(/(?:预算\s*)?(\d{2,4})\s*(?:元|块|以内|以下)/);
  const budget = budgetMatch ? Number(budgetMatch[1]) : c.context.budget;
  const wantsCompatibility =
    /兼容|适配|支持|能用|可以用|连接|怎么用/.test(text) || c.context.awaiting === 'device';
  if (category || namedProduct || /商品|推荐|数码|选购/.test(text)) {
    const selected =
      namedProduct || (c.context.productId ? findProduct(c.context.productId) : undefined);
    const deviceSupplied = /MacBook|ThinkPad|iPhone|iPad|华为|小米|联想|戴尔|华硕|Surface/i.test(
      text,
    );
    if (wantsCompatibility && !deviceSupplied && !c.context.device)
      return {
        ...base,
        module: '商品服务',
        text: '为了核对兼容性，请告诉我设备的具体型号和接口信息。仅有 USB-C 接口，还不能确认支持视频输出或充电功率。',
        context: { category, productId: selected?.id, awaiting: 'device', intent: undefined },
      };
    if (state.fault === 'no-knowledge' || /资料.*没有|量子|卫星通信/.test(text))
      return {
        ...base,
        module: '商品服务',
        text: '当前资料中没有找到足够依据，我无法确认这个功能。可以换一个具体问题，或转人工进一步核实。',
        context: { awaiting: undefined, intent: undefined },
        steps: [
          step('Retrieval', 'search_knowledge', '正在查阅商品资料', '未检索到支持该问题的资料'),
        ],
      };
    const filtered = namedProduct
      ? [namedProduct]
      : products
          .filter((p) => (!category || p.category === category) && (!budget || p.price <= budget))
          .slice(0, 3);
    const textAnswer = wantsCompatibility
      ? '已记录你的设备信息。现有说明书没有覆盖该具体型号，因此还不能承诺完全兼容。请核对主机的接口协议；我可以把这项问题交给人工核实。'
      : namedProduct
        ? `${namedProduct.name}：${namedProduct.description}\n${namedProduct.stock > 0 ? `当前演示库存 ${namedProduct.stock} 件。` : '当前暂时缺货，不能承诺补货时间。'}${namedProduct.category === '扩展坞' ? '视频输出能力还需结合主机协议确认。' : ''}`
        : filtered.length
          ? `为你找到${budget ? ` ${budget} 元以内的` : '这些'}${category || '数码好物'}。以下价格和库存来自当前演示商品数据，可以点开继续了解。`
          : '当前没有找到符合这些条件的商品。可以调整预算或商品类别。';
    return {
      ...base,
      module: '商品服务',
      text: textAnswer,
      products: filtered.map((p) => p.id),
      sources: [
        ...filtered.map((p) => p.source),
        ...(wantsCompatibility ? ['POL-03 · 兼容性与充电功率 v1.0'] : []),
      ],
      context: {
        category,
        budget,
        productId: namedProduct?.id || filtered[0]?.id,
        awaiting: undefined,
        intent: undefined,
        device: deviceSupplied ? text : c.context.device,
      },
      steps: [
        step('Tool', 'search_products', '正在查找商品与库存', `找到 ${filtered.length} 个商品`),
        step(
          'Retrieval',
          'search_knowledge',
          '正在查阅商品资料',
          `检索到 ${filtered.length} 份演示说明书`,
        ),
      ],
    };
  }
  if (/规则|运费|多久|FAQ|政策/.test(text))
    return {
      ...base,
      module: '商品服务',
      text: policies[0].content + '\n' + policies[1].content,
      sources: ['POL-01 · 发货与物流 v1.0', 'POL-02 · 售后申请与审核 v1.0'],
      steps: [step('Retrieval', 'retrieve_faq', '正在查阅店铺规则', '找到发货及售后规则')],
    };
  return {
    ...base,
    text: `我可以帮${buyers[c.buyer]}查找耳机、充电器和扩展坞，也可以查询订单或申请售后。你想先处理哪一项？\n当前是脚本驱动的交互演示，暂未连接真实大模型。`,
  };
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function isHandoffRequest(text: string): boolean {
  // 只匹配明确的转人工请求，避免把“你是人工智能吗”当成人工接管。
  const normalizedText = text.replace(/[，,。！!？?\s]/g, '');
  const asksForStaff =
    /^(?:请|麻烦|帮我|我要|我想|我需要)?(?:转|找|联系|接入|切换到)?(?:人工|真人)(?:客服|服务)?$/.test(
      normalizedText,
    );
  const asksForTakeover = /^(?:请|麻烦|帮我)?客服接管$/.test(normalizedText);

  return asksForStaff || asksForTakeover;
}

function isSimulatedBusinessRequest(text: string): boolean {
  // 售后已接入 LangGraph，只有尚未建立后端资料库的店铺规则继续走脚本。
  const asksStorePolicy = /店铺.*规则|运费|发货政策/.test(text);
  return asksStorePolicy;
}

interface BackendReplyInput {
  conversationId: string;
  text: string;
  token: string;
  runId: string;
  started: number;
  isLive: () => boolean;
}

function moduleForIntent(intent: string): string {
  const moduleNames: Record<string, string> = {
    product_inquiry: '商品咨询',
    order_inquiry: '订单查询',
    after_sale: '售后服务',
    knowledge_inquiry: '知识问答',
    human_service: '人工服务',
    general_conversation: '通用咨询',
  };
  return moduleNames[intent] || '通用咨询';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function draftFromToolResults(
  toolResults: Array<{ name: string; output: unknown }> | undefined,
): Draft | undefined {
  if (!toolResults) return undefined;

  for (const toolResult of toolResults) {
    if (toolResult.name !== 'prepare_after_sale_draft' || !isRecord(toolResult.output)) continue;
    const artifact = toolResult.output.artifact;
    if (!isRecord(artifact) || artifact.type !== 'after_sale_draft') continue;
    const remoteDraft = artifact.draft;
    if (!isRecord(remoteDraft)) continue;

    const orderId = remoteDraft.order_id;
    const requestType = remoteDraft.request_type;
    const reason = remoteDraft.reason;
    if (
      typeof orderId !== 'string' ||
      typeof requestType !== 'string' ||
      typeof reason !== 'string'
    ) {
      continue;
    }
    return {
      id: uid(),
      orderId,
      kind: requestType === '换货' ? '换货申请' : '退货申请',
      reason,
      revision: 1,
    };
  }
  return undefined;
}

function sourcesFromToolResults(
  toolResults: Array<{ name: string; output: unknown }> | undefined,
): string[] {
  if (!toolResults) return [];
  const sources: string[] = [];
  for (const toolResult of toolResults) {
    if (toolResult.name !== 'search_knowledge' || !isRecord(toolResult.output)) continue;
    const remoteSources = toolResult.output.sources;
    if (!Array.isArray(remoteSources)) continue;
    for (const source of remoteSources) {
      if (!isRecord(source)) continue;
      const document = source.document;
      const section = source.section;
      const content = source.content;
      if (typeof document !== 'string' || typeof section !== 'string') continue;
      const label = `${document} · ${section}`;
      sources.push(typeof content === 'string' ? `${label}\n${content}` : label);
    }
  }
  return sources;
}

async function sendBackendReply(input: BackendReplyInput) {
  const { conversationId, text, token, runId, started, isLive } = input;
  const conversation = getConversation(conversationId)!;
  const controller = new AbortController();
  let streamingMessageId: string | undefined;
  backendRequests.set(conversationId, controller);

  update((s) => {
    s.runs.unshift({
      id: runId,
      ownerTab: tabId,
      conversationId,
      query: text,
      module: '意图识别中',
      origin: 'backend',
      status: 'running',
      startedAt: now(),
      events: [
        {
          id: uid(),
          type: 'Router',
          name: 'intent_router',
          label: '分流 Router',
          status: 'running',
          output: '正在进行语义路由',
          description: '后端正在规范化 Query 并计算意图相似度。',
        },
      ],
    });
  });

  const callbacks: ChatStreamCallbacks = {
    onStart(data) {
      streamingMessageId = data.assistant_message_id;
      update((s) => {
        const target = s.conversations.find((item) => item.id === conversationId)!;
        target.remoteId = data.conversation_id;
        target.mode = data.mode;
        addMessage(target, 'assistant', '', {
          id: data.assistant_message_id,
          streaming: true,
          origin: 'backend',
        });
      });
    },
    onStatus(data) {
      if (!isLive()) {
        return;
      }

      update((s) => {
        const target = s.conversations.find((item) => item.id === conversationId)!;
        const run = s.runs.find((item) => item.id === runId)!;

        if (data.phase === 'router' && data.state === 'completed') {
          const analysis = data.query_analysis;
          if (!analysis) {
            return;
          }
          const routerEvent = run.events.find((event) => event.type === 'Router');
          run.module = moduleForIntent(analysis.primary_intent);
          if (routerEvent) {
            const secondaryText = analysis.secondary_intents.length
              ? `；次要意图：${analysis.secondary_intents.join('、')}`
              : '';
            Object.assign(routerEvent, {
              status: 'success',
              output: analysis.primary_intent,
              description: `${analysis.description} 相似度 ${(
                analysis.similarity_score * 100
              ).toFixed(1)}%${secondaryText}`,
              result: analysis.optimized_query,
            });
          }
          return;
        }

        if (data.phase === 'model' && data.state === 'started') {
          const message = target.messages.find((item) => item.id === streamingMessageId);
          if (message) {
            message.text = '';
          }
          const summaryIsRunning = run.events.some(
            (event) => event.type === 'Summary' && event.status === 'running',
          );
          if (!summaryIsRunning) {
            run.events.push({
              id: uid(),
              type: 'Summary',
              name: 'response_summary',
              label: '汇总',
              status: 'running',
              output: '正在生成客服回复',
              description: '模型正在根据对话与工具结果组织答案。',
            });
          }
          return;
        }

        if (data.phase === 'tool' && data.state === 'started') {
          run.events = run.events.filter(
            (event) => !(event.type === 'Summary' && event.status === 'running'),
          );
          const toolNames = data.tool_names?.length ? data.tool_names : ['unknown_tool'];
          const retrievingKnowledge = toolNames.includes('search_knowledge');
          run.events.push({
            id: uid(),
            type: retrievingKnowledge ? 'Retrieval' : 'Tool',
            name: toolNames.join(', '),
            label: retrievingKnowledge ? '检索' : '工具',
            status: 'running',
            output: retrievingKnowledge
              ? '正在检索商城知识库'
              : `正在执行 ${data.tool_calls} 个工具调用`,
            description: `调用 ${toolNames.join('、')}`,
          });
          return;
        }

        let toolEvent: RunEvent | undefined;
        for (let index = run.events.length - 1; index >= 0; index -= 1) {
          const event = run.events[index];
          if ((event.type === 'Tool' || event.type === 'Retrieval') && event.status === 'running') {
            toolEvent = event;
            break;
          }
        }
        if (toolEvent) {
          toolEvent.status = 'success';
          toolEvent.output = `执行了 ${data.tool_calls} 个工具调用`;
          toolEvent.result = toolEvent.description;
          toolEvent.toolResults = data.tool_results || [];
          if (toolEvent.type === 'Retrieval') {
            const knowledgeSources = sourcesFromToolResults(data.tool_results);
            toolEvent.output = `命中 ${knowledgeSources.length} 条资料`;
            const message = target.messages.find((item) => item.id === streamingMessageId);
            if (message && knowledgeSources.length) message.sources = knowledgeSources;
          }
          const afterSaleDraft = draftFromToolResults(data.tool_results);
          if (afterSaleDraft) {
            target.draft = afterSaleDraft;
            target.context = { orderId: afterSaleDraft.orderId, intent: 'aftersales' };
            const message = target.messages.find((item) => item.id === streamingMessageId);
            if (message) message.draftId = afterSaleDraft.id;
          }
        }
      });
    },
    onDelta(content) {
      if (!isLive()) {
        return;
      }

      update((s) => {
        const target = s.conversations.find((item) => item.id === conversationId)!;
        const message = target.messages.find((item) => item.id === streamingMessageId);
        if (message) {
          message.text += content;
        }
      });
    },
    onComplete(response) {
      if (!isLive()) {
        return;
      }

      update((s) => {
        const target = s.conversations.find((item) => item.id === conversationId)!;
        const run = s.runs.find((item) => item.id === runId)!;
        const message = target.messages.find((item) => item.id === streamingMessageId)!;

        target.remoteId = response.conversation_id;
        target.mode = response.mode;
        // 真实对话的上下文由后端历史消息维护，不使用脚本猜测商品或预算。
        target.context = {};
        Object.assign(message, {
          id: response.assistant_message.id,
          text: response.assistant_message.content,
          time: response.assistant_message.created_at,
          streaming: false,
        });

        run.status = 'success';
        run.duration = Math.round(performance.now() - started);
        const summary = run.events.find((event) => event.type === 'Summary');
        if (summary) {
          Object.assign(summary, {
            status: 'success',
            duration: run.duration,
            output: '已生成最终客服回复',
            description: `模型调用 ${response.run.model_calls} 次，工具调用 ${response.run.tool_calls} 次。`,
            result: response.assistant_message.content,
          });
        }
        const analysis = response.run.query_analysis;
        const routerEvent = run.events.find((event) => event.type === 'Router');
        if (analysis && routerEvent?.status === 'running') {
          run.module = moduleForIntent(analysis.primary_intent);
          Object.assign(routerEvent, {
            status: 'success',
            output: analysis.primary_intent,
            description: `${analysis.description} 相似度 ${(
              analysis.similarity_score * 100
            ).toFixed(1)}%`,
            result: analysis.optimized_query,
          });
        }
      });
    },
  };

  async function requestStream(remoteId?: string) {
    await streamChatMessage(conversation.buyer, text, remoteId, callbacks, controller.signal);
  }

  try {
    try {
      await requestStream(conversation.remoteId);
    } catch (error) {
      const remoteConversationExpired =
        error instanceof ChatApiError && error.status === 404 && Boolean(conversation.remoteId);

      if (!remoteConversationExpired) {
        throw error;
      }

      update((s) => {
        const target = s.conversations.find((item) => item.id === conversationId);
        if (target) {
          delete target.remoteId;
        }
      });
      await requestStream();
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      return;
    }
    if (!isLive()) {
      return;
    }

    const message =
      error instanceof ChatApiError
        ? error.message
        : '无法连接 AI 客服后端，请确认 FastAPI 服务已经启动。';
    update((s) => {
      const target = s.conversations.find((item) => item.id === conversationId)!;
      const run = s.runs.find((item) => item.id === runId)!;
      run.status = 'error';
      run.duration = Math.round(performance.now() - started);
      const runningEvent = run.events.find((event) => event.status === 'running');
      Object.assign(runningEvent || run.events[0], {
        status: 'error',
        duration: run.duration,
        result: message,
      });
      const streamingMessage = target.messages.find((item) => item.id === streamingMessageId);
      if (streamingMessage) {
        Object.assign(streamingMessage, {
          text: message,
          streaming: false,
          retryText: text,
        });
      } else {
        addMessage(target, 'assistant', message, {
          retryText: text,
          origin: 'backend',
        });
      }
    });
  } finally {
    if (backendRequests.get(conversationId) === controller) {
      backendRequests.delete(conversationId);
    }
    if (tokens.get(conversationId) === token) tokens.delete(conversationId);
  }
}

export async function sendMessage(conversationId: string, value: string) {
  const text = value.trim().slice(0, 1000),
    c = getConversation(conversationId);
  if (
    !text ||
    !c ||
    c.buyer !== state.buyer ||
    c.mode === 'closed' ||
    tokens.has(conversationId) ||
    state.runs.some((r) => r.conversationId === conversationId && r.status === 'running')
  )
    return;
  update((s) => {
    const target = s.conversations.find((c) => c.id === conversationId)!;
    addMessage(target, 'user', text);
    if (target.title === '新的咨询') target.title = text.slice(0, 20);
  });
  if (c.mode !== 'ai') return;
  if (isHandoffRequest(text)) {
    await handoff(conversationId);
    return;
  }
  const token = uid(),
    runId = uid(),
    started = performance.now();
  tokens.set(conversationId, token);
  const isLive = () => {
    const latest = latestState();
    return (
      tokens.get(conversationId) === token &&
      latest.conversations.find((c) => c.id === conversationId)?.mode === 'ai' &&
      latest.runs.some((r) => r.id === runId && r.status === 'running')
    );
  };
  const simulatedBusinessRequest = isSimulatedBusinessRequest(text);
  const shouldCallBackend = useBackendChat && !simulatedBusinessRequest;
  if (shouldCallBackend) {
    await sendBackendReply({ conversationId, text, token, runId, started, isLive });
    return;
  }

  const plan = planReply(getConversation(conversationId)!, text);
  update((s) => {
    s.runs.unshift({
      id: runId,
      ownerTab: tabId,
      conversationId,
      query: text,
      module: plan.module,
      origin: 'script',
      status: 'running',
      startedAt: now(),
      events: [],
    });
  });
  const steps = [
    step('Agent', 'route_request', '正在识别你的需求', `转入${plan.module}（模拟）`),
    ...plan.steps,
  ];
  for (const next of steps) {
    if (!isLive()) return;
    const eventId = uid();
    update((s) => {
      s.runs
        .find((r) => r.id === runId)!
        .events.push({ ...next, id: eventId, status: 'running', result: undefined });
    });
    await wait(320);
    if (!isLive()) return;
    const failed = state.fault === 'tool-error' && next.type === 'Tool';
    update((s) => {
      const run = s.runs.find((r) => r.id === runId)!;
      Object.assign(
        run.events.find((e) => e.id === eventId)!,
        {
          status: failed ? 'error' : 'success',
          result: failed ? '模拟查询服务超时；未返回业务结果' : next.result,
          duration: 320,
        },
      );
      if (failed) {
        run.status = 'error';
        run.duration = Math.round(performance.now() - started);
        const target = s.conversations.find((c) => c.id === conversationId)!;
        addMessage(
          target,
          'assistant',
          '查询服务暂时不可用，本次没有返回业务结果。请在演示控制中关闭故障后重试，或联系人工。',
          { retryText: text },
        );
      }
    });
    if (failed) {
      tokens.delete(conversationId);
      return;
    }
  }
  if (!isLive()) return;
  const messageId = uid();
  update((s) => {
    const target = s.conversations.find((c) => c.id === conversationId)!;
    addMessage(target, 'assistant', '', { id: messageId, streaming: true });
  });
  const chunk = Math.max(8, Math.ceil(plan.text.length / 12));
  for (let end = chunk; end < plan.text.length + chunk; end += chunk) {
    if (!isLive()) return;
    update((s) => {
      const message = s.conversations
        .find((c) => c.id === conversationId)!
        .messages.find((m) => m.id === messageId)!;
      message.text = plan.text.slice(0, end);
    });
    await wait(32);
  }
  if (!isLive()) return;
  update((s) => {
    const target = s.conversations.find((c) => c.id === conversationId)!;
    if (plan.context) Object.assign(target.context, plan.context);
    if (plan.draft) target.draft = plan.draft;
    Object.assign(
      target.messages.find((m) => m.id === messageId)!,
      {
        text: plan.text,
        streaming: false,
        products: plan.products,
        orders: plan.orders,
        sources: plan.sources,
        draftId: plan.draft?.id,
        requestId: plan.requestId,
      },
    );
    const run = s.runs.find((r) => r.id === runId)!;
    run.status = 'success';
    run.duration = Math.round(performance.now() - started);
  });
  tokens.delete(conversationId);
}
