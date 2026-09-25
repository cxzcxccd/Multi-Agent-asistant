import type { BuyerId, Category, Product } from './types';

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api').replace(
  /\/$/,
  '',
);

type AuthIdentity = { role: 'buyer'; buyerId: BuyerId } | { role: 'staff' };

export interface AuthPrincipal {
  subject: string;
  display_name: string;
  role: 'buyer' | 'staff';
  buyer_id: BuyerId | null;
}

export interface AuthSession {
  accessToken: string;
  refreshToken: string;
  principal: AuthPrincipal;
}

const authStorageKey = 'geek-select-auth-session';
let authSession = readStoredSession();

function readStoredSession(): AuthSession | null {
  try {
    const stored = sessionStorage.getItem(authStorageKey);
    return stored ? (JSON.parse(stored) as AuthSession) : null;
  } catch {
    return null;
  }
}

function saveSession(session: AuthSession | null): void {
  authSession = session;
  if (session === null) {
    sessionStorage.removeItem(authStorageKey);
    return;
  }
  sessionStorage.setItem(authStorageKey, JSON.stringify(session));
}

export function getAuthSession(): AuthSession | null {
  return authSession;
}

export async function loginAccount(username: string, password: string): Promise<AuthSession> {
  const response = await fetch(`${apiBaseUrl}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ChatApiError(body?.detail || '登录失败，请检查账号和密码', response.status);
  }

  const data = (await response.json()) as {
    access_token: string;
    refresh_token: string;
    principal: AuthPrincipal;
  };
  const session = {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    principal: data.principal,
  };
  saveSession(session);
  return session;
}

export async function logoutAccount(): Promise<void> {
  const currentSession = authSession;
  saveSession(null);
  if (currentSession === null) {
    return;
  }
  await fetch(`${apiBaseUrl}/auth/logout`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: currentSession.refreshToken }),
  }).catch(() => undefined);
}

function requireSession(identity: AuthIdentity): AuthSession {
  if (authSession === null) {
    throw new ChatApiError('登录状态已失效，请重新登录', 401);
  }
  const principal = authSession.principal;
  const wrongRole = principal.role !== identity.role;
  const wrongBuyer = identity.role === 'buyer' && principal.buyer_id !== identity.buyerId;
  if (wrongRole || wrongBuyer) {
    throw new ChatApiError('当前账号无权执行此操作', 403);
  }
  return authSession;
}

async function refreshAccessToken(identity: AuthIdentity): Promise<string> {
  const currentSession = requireSession(identity);
  const response = await fetch(`${apiBaseUrl}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: currentSession.refreshToken }),
  });
  if (!response.ok) {
    saveSession(null);
    throw new ChatApiError('登录状态已失效，请重新登录', 401);
  }

  const data = (await response.json()) as {
    access_token: string;
    refresh_token: string;
    principal: AuthPrincipal;
  };
  const refreshedSession = {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    principal: data.principal,
  };
  saveSession(refreshedSession);
  return data.access_token;
}

async function authenticatedFetch(
  url: string,
  init: RequestInit,
  identity: AuthIdentity,
): Promise<Response> {
  const session = requireSession(identity);
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${session.accessToken}`);
  const response = await fetch(url, { ...init, headers });
  if (response.status !== 401) {
    return response;
  }

  const refreshedToken = await refreshAccessToken(identity);
  headers.set('Authorization', `Bearer ${refreshedToken}`);
  return fetch(url, { ...init, headers });
}

interface ApiMessage {
  id: string;
  role: 'user' | 'assistant' | 'staff' | 'system';
  content: string;
  created_at: string;
}

export interface ApiConversation {
  id: string;
  buyer_id: BuyerId;
  title: string;
  mode: 'ai' | 'waiting' | 'human' | 'closed';
  messages: ApiMessage[];
  created_at: string;
  updated_at: string;
}

async function readConversationResponse(response: Response): Promise<ApiConversation> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    const message = body?.detail || `会话接口请求失败（${response.status}）`;
    throw new ChatApiError(message, response.status);
  }
  return (await response.json()) as ApiConversation;
}

export async function requestHumanHandoff(data: {
  buyerId: BuyerId;
  conversationId?: string;
  title: string;
}): Promise<ApiConversation> {
  const response = await authenticatedFetch(
    `${apiBaseUrl}/handoff`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        buyer_id: data.buyerId,
        conversation_id: data.conversationId,
        title: data.title,
      }),
    },
    { role: 'buyer', buyerId: data.buyerId },
  );
  return readConversationResponse(response);
}

export async function updateConversationMode(data: {
  conversationId: string;
  mode: 'human' | 'ai' | 'closed';
}): Promise<ApiConversation> {
  const conversationId = encodeURIComponent(data.conversationId);
  const response = await authenticatedFetch(
    `${apiBaseUrl}/staff/conversations/${conversationId}/mode`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ staff_id: '客服小周', mode: data.mode }),
    },
    { role: 'staff' },
  );
  return readConversationResponse(response);
}

export async function sendStaffMessage(data: {
  conversationId: string;
  message: string;
}): Promise<ApiConversation> {
  const conversationId = encodeURIComponent(data.conversationId);
  const response = await authenticatedFetch(
    `${apiBaseUrl}/staff/conversations/${conversationId}/messages`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ staff_id: '客服小周', message: data.message }),
    },
    { role: 'staff' },
  );
  return readConversationResponse(response);
}

export async function listConversations(buyerId: BuyerId): Promise<ApiConversation[]> {
  const searchParams = new URLSearchParams({ buyer_id: buyerId });
  const response = await authenticatedFetch(
    `${apiBaseUrl}/conversations?${searchParams.toString()}`,
    {},
    { role: 'buyer', buyerId },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    const message = body?.detail || `会话列表请求失败（${response.status}）`;
    throw new ChatApiError(message, response.status);
  }
  return (await response.json()) as ApiConversation[];
}

export function subscribeToConversations(
  buyerId: BuyerId,
  onSnapshot: (conversations: ApiConversation[]) => void,
): () => void {
  let source: EventSource | undefined;
  let closed = false;

  async function connect(): Promise<void> {
    try {
      const session = requireSession({ role: 'buyer', buyerId });
      if (closed) {
        return;
      }
      const searchParams = new URLSearchParams({
        buyer_id: buyerId,
        access_token: session.accessToken,
      });
      source = new EventSource(`${apiBaseUrl}/conversation-events?${searchParams.toString()}`);
      source.addEventListener('snapshot', (event) => {
        const message = event as MessageEvent<string>;
        const conversations = JSON.parse(message.data) as ApiConversation[];
        onSnapshot(conversations);
      });
    } catch {
      // 登录接口暂时不可用时保留本地会话，下一次业务请求会显示明确错误。
    }
  }

  void connect();

  return () => {
    closed = true;
    source?.close();
  };
}

export function subscribeToStaffConversations(
  onSnapshot: (conversations: ApiConversation[]) => void,
): () => void {
  let source: EventSource | undefined;

  try {
    const session = requireSession({ role: 'staff' });
    const searchParams = new URLSearchParams({ access_token: session.accessToken });
    source = new EventSource(`${apiBaseUrl}/staff/conversation-events?${searchParams.toString()}`);
    source.addEventListener('snapshot', (event) => {
      const message = event as MessageEvent<string>;
      const conversations = JSON.parse(message.data) as ApiConversation[];
      onSnapshot(conversations);
    });
  } catch {
    // 登录状态失效时保留工作台现有快照，由下一次操作显示错误。
  }

  return () => {
    source?.close();
  };
}

export interface ChatResponse {
  conversation_id: string;
  mode: 'ai' | 'waiting' | 'human' | 'closed';
  user_message: ApiMessage;
  assistant_message: ApiMessage;
  run: {
    model_calls: number;
    tool_rounds: number;
    tool_calls: number;
    query_analysis: QueryAnalysis | null;
  };
}

export type IntentName =
  | 'product_inquiry'
  | 'order_inquiry'
  | 'after_sale'
  | 'knowledge_inquiry'
  | 'human_service'
  | 'general_conversation';

export interface QueryAnalysis {
  original_query: string;
  normalized_query: string;
  routing_query: string;
  optimized_query: string;
  primary_intent: IntentName;
  secondary_intents: IntentName[];
  similarity_score: number;
  route_margin: number;
  router_source: string;
  embedding_model: string;
  description: string;
  entities: {
    order_id: string | null;
    budget: number | null;
    product_category: string | null;
    after_sale_type: string | null;
  };
  candidates: Array<{
    intent: IntentName;
    similarity_score: number;
  }>;
}

export interface ChatStreamStart {
  conversation_id: string;
  mode: ChatResponse['mode'];
  user_message: ApiMessage;
  assistant_message_id: string;
}

export interface ChatStreamStatus {
  phase: 'router' | 'model' | 'tool';
  state: 'started' | 'completed';
  query_analysis?: QueryAnalysis | null;
  tool_calls: number;
  tool_names?: string[];
  tool_results?: Array<{ name: string; output: unknown }>;
}

export interface ChatStreamCallbacks {
  onStart: (data: ChatStreamStart) => void;
  onStatus: (data: ChatStreamStatus) => void;
  onDelta: (content: string) => void;
  onComplete: (response: ChatResponse) => void;
}

export class ChatApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ChatApiError';
    this.status = status;
  }
}

export interface ProductFilters {
  keyword?: string;
  category?: Category;
  minPrice?: number;
  maxPrice?: number;
  inStock?: boolean;
  sort?: 'default' | 'price-asc' | 'price-desc';
  offset?: number;
  limit?: number;
}

export interface ProductListResponse {
  items: Product[];
  total: number;
  offset: number;
  limit: number;
}

export class CatalogApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'CatalogApiError';
    this.status = status;
  }
}

export interface RagEvaluationReport {
  cases: number;
  recall_at_k: number;
  mrr: number;
  rejection_accuracy: number;
  average_latency_ms: number;
  results: Array<{
    id: string;
    question: string;
    expected_sources: string[];
    retrieved_sources: string[];
    first_relevant_rank: number | null;
    passed: boolean;
  }>;
}

export async function runRagEvaluation(): Promise<RagEvaluationReport> {
  const response = await authenticatedFetch(
    `${apiBaseUrl}/staff/knowledge/evaluate`,
    { method: 'POST' },
    { role: 'staff' },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ChatApiError(body?.detail || 'RAG 评测运行失败', response.status);
  }
  return (await response.json()) as RagEvaluationReport;
}

export interface ApiAfterSaleRequest {
  id: string;
  buyer_id: BuyerId;
  order_id: string;
  request_type: '退货' | '换货';
  reason: string;
  status: 'draft' | 'pending' | 'approved' | 'rejected' | 'cancelled';
  version: number;
  created_at: string;
  updated_at: string;
  submitted_at: string | null;
  reviewed_at: string | null;
  reviewer: string | null;
  review_reason: string | null;
}

export class AfterSalesApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'AfterSalesApiError';
    this.status = status;
  }
}

async function readAfterSalesResponse(response: Response): Promise<ApiAfterSaleRequest> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    const message = body?.detail || `售后接口请求失败（${response.status}）`;
    throw new AfterSalesApiError(message, response.status);
  }

  return (await response.json()) as ApiAfterSaleRequest;
}

export async function createAfterSaleDraft(data: {
  buyerId: BuyerId;
  orderId: string;
  requestType: '退货' | '换货';
  reason: string;
}): Promise<ApiAfterSaleRequest> {
  const response = await authenticatedFetch(
    `${apiBaseUrl}/after-sales/drafts`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        buyer_id: data.buyerId,
        order_id: data.orderId,
        request_type: data.requestType,
        reason: data.reason,
      }),
    },
    { role: 'buyer', buyerId: data.buyerId },
  );

  return readAfterSalesResponse(response);
}

export async function submitAfterSaleDraft(data: {
  requestId: string;
  buyerId: BuyerId;
  expectedVersion: number;
  idempotencyKey: string;
}): Promise<ApiAfterSaleRequest> {
  const requestId = encodeURIComponent(data.requestId);
  const response = await authenticatedFetch(
    `${apiBaseUrl}/after-sales/drafts/${requestId}/submit`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': data.idempotencyKey,
      },
      body: JSON.stringify({
        buyer_id: data.buyerId,
        expected_version: data.expectedVersion,
      }),
    },
    { role: 'buyer', buyerId: data.buyerId },
  );

  return readAfterSalesResponse(response);
}

export async function listAfterSales(buyerId: BuyerId): Promise<ApiAfterSaleRequest[]> {
  const searchParams = new URLSearchParams({ buyer_id: buyerId });
  const response = await authenticatedFetch(
    `${apiBaseUrl}/after-sales?${searchParams.toString()}`,
    {},
    { role: 'buyer', buyerId },
  );

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    const message = body?.detail || `售后接口请求失败（${response.status}）`;
    throw new AfterSalesApiError(message, response.status);
  }

  return (await response.json()) as ApiAfterSaleRequest[];
}

export async function listPendingAfterSales(): Promise<ApiAfterSaleRequest[]> {
  const response = await authenticatedFetch(
    `${apiBaseUrl}/staff/after-sales/pending`,
    {},
    { role: 'staff' },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    const message = body?.detail || `售后待审队列请求失败（${response.status}）`;
    throw new AfterSalesApiError(message, response.status);
  }
  return (await response.json()) as ApiAfterSaleRequest[];
}

export async function reviewAfterSale(data: {
  requestId: string;
  reviewer: string;
  decision: 'approved' | 'rejected';
  reason: string;
  expectedVersion: number;
}): Promise<ApiAfterSaleRequest> {
  const requestId = encodeURIComponent(data.requestId);
  const response = await authenticatedFetch(
    `${apiBaseUrl}/staff/after-sales/${requestId}/review`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        reviewer: data.reviewer,
        decision: data.decision,
        reason: data.reason,
        expected_version: data.expectedVersion,
      }),
    },
    { role: 'staff' },
  );

  return readAfterSalesResponse(response);
}

async function readCatalogError(response: Response): Promise<CatalogApiError> {
  const body = (await response.json().catch(() => null)) as { detail?: string } | null;
  const message = body?.detail || `商品接口请求失败（${response.status}）`;
  return new CatalogApiError(message, response.status);
}

export async function getProducts(
  filters: ProductFilters = {},
  signal?: AbortSignal,
): Promise<ProductListResponse> {
  const searchParams = new URLSearchParams();

  if (filters.keyword) {
    searchParams.set('keyword', filters.keyword);
  }
  if (filters.category) {
    searchParams.set('category', filters.category);
  }
  if (filters.minPrice !== undefined) {
    searchParams.set('min_price', filters.minPrice.toString());
  }
  if (filters.maxPrice !== undefined) {
    searchParams.set('max_price', filters.maxPrice.toString());
  }
  if (filters.inStock) {
    searchParams.set('in_stock', 'true');
  }
  if (filters.sort && filters.sort !== 'default') {
    searchParams.set('sort', filters.sort);
  }
  if (filters.offset !== undefined) {
    searchParams.set('offset', filters.offset.toString());
  }
  if (filters.limit !== undefined) {
    searchParams.set('limit', filters.limit.toString());
  }

  const query = searchParams.toString();
  const url = query ? `${apiBaseUrl}/products?${query}` : `${apiBaseUrl}/products`;
  const response = await fetch(url, { signal });

  if (!response.ok) {
    throw await readCatalogError(response);
  }

  return (await response.json()) as ProductListResponse;
}

export async function getProductDetail(productId: string, signal?: AbortSignal): Promise<Product> {
  const encodedProductId = encodeURIComponent(productId);
  const response = await fetch(`${apiBaseUrl}/products/${encodedProductId}`, { signal });

  if (!response.ok) {
    throw await readCatalogError(response);
  }

  return (await response.json()) as Product;
}

export async function sendChatMessage(
  buyerId: BuyerId,
  message: string,
  conversationId?: string,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const response = await authenticatedFetch(
    `${apiBaseUrl}/chat`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        buyer_id: buyerId,
        conversation_id: conversationId,
        message,
      }),
      signal,
    },
    { role: 'buyer', buyerId },
  );

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    const message = body?.detail || `客服接口请求失败（${response.status}）`;
    throw new ChatApiError(message, response.status);
  }

  return (await response.json()) as ChatResponse;
}

interface ParsedSseEvent {
  name: string;
  data: unknown;
}

function parseSseEvent(block: string): ParsedSseEvent | null {
  let eventName = '';
  const dataLines: string[] = [];
  const lines = block.split('\n');

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventName = line.slice('event:'.length).trim();
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trimStart());
    }
  }

  if (!eventName || dataLines.length === 0) {
    return null;
  }

  const jsonData = dataLines.join('\n');
  return {
    name: eventName,
    data: JSON.parse(jsonData) as unknown,
  };
}

function handleSseEvent(event: ParsedSseEvent, callbacks: ChatStreamCallbacks) {
  if (event.name === 'start') {
    callbacks.onStart(event.data as ChatStreamStart);
    return;
  }

  if (event.name === 'status') {
    callbacks.onStatus(event.data as ChatStreamStatus);
    return;
  }

  if (event.name === 'delta') {
    const delta = event.data as { content: string };
    callbacks.onDelta(delta.content);
    return;
  }

  if (event.name === 'complete') {
    callbacks.onComplete(event.data as ChatResponse);
    return;
  }

  if (event.name === 'error') {
    const error = event.data as { detail: string; status: number };
    throw new ChatApiError(error.detail, error.status);
  }
}

export async function streamChatMessage(
  buyerId: BuyerId,
  message: string,
  conversationId: string | undefined,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const response = await authenticatedFetch(
    `${apiBaseUrl}/chat/stream`,
    {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        buyer_id: buyerId,
        conversation_id: conversationId,
        message,
      }),
      signal,
    },
    { role: 'buyer', buyerId },
  );

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    const errorMessage = body?.detail || `客服接口请求失败（${response.status}）`;
    throw new ChatApiError(errorMessage, response.status);
  }
  if (response.body === null) {
    throw new ChatApiError('客服接口没有返回流式响应', 502);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let streamCompleted = false;

  while (true) {
    const result = await reader.read();
    if (result.done) {
      break;
    }

    buffer += decoder.decode(result.value, { stream: true });
    buffer = buffer.replaceAll('\r\n', '\n');

    let boundary = buffer.indexOf('\n\n');
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseSseEvent(block);
      if (event !== null) {
        handleSseEvent(event, callbacks);
        if (event.name === 'complete') {
          streamCompleted = true;
        }
      }
      boundary = buffer.indexOf('\n\n');
    }
  }

  if (!streamCompleted) {
    throw new ChatApiError('客服流式响应提前结束', 502);
  }
}
