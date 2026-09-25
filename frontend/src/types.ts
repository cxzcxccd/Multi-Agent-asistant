export type BuyerId = 'A' | 'B';
export type View = 'chat' | 'workbench' | 'lab' | 'shop';
export type Category = '耳机' | '充电器' | '扩展坞';
export interface Product {
  id: string;
  name: string;
  category: Category;
  series: string;
  price: number;
  stock: number;
  specs: string[];
  description: string;
  color: string;
  source: string;
}
export interface Order {
  id: string;
  buyer: BuyerId;
  productId: string;
  quantity: number;
  status: string;
  date: string;
  logistics: { time: string; title: string; detail: string }[];
}
export interface Draft {
  id: string;
  orderId: string;
  kind: string;
  reason: string;
  revision: number;
}
export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'staff' | 'system';
  text: string;
  time: string;
  products?: string[];
  orders?: string[];
  sources?: string[];
  draftId?: string;
  requestId?: string;
  retryText?: string;
  streaming?: boolean;
  origin?: 'backend' | 'script';
}
export interface Conversation {
  id: string;
  remoteId?: string;
  buyer: BuyerId;
  title: string;
  createdAt: string;
  mode: 'ai' | 'waiting' | 'human' | 'closed';
  messages: Message[];
  context: {
    orderId?: string;
    productId?: string;
    category?: Category;
    budget?: number;
    awaiting?: 'order' | 'device' | 'reason';
    intent?: 'order' | 'aftersales';
    device?: string;
  };
  draft?: Draft;
}
export interface AfterSale {
  id: string;
  conversationId: string;
  buyer: BuyerId;
  orderId: string;
  kind: string;
  reason: string;
  status: 'pending' | 'approved' | 'rejected';
  createdAt: string;
  reviewedAt?: string;
  reviewer?: string;
  reviewReason?: string;
  sourceDraftId: string;
  version?: number;
  origin?: 'backend' | 'script';
}
export interface RunEvent {
  id: string;
  type: 'Router' | 'Agent' | 'Tool' | 'Retrieval' | 'Summary';
  name: string;
  label: string;
  status: 'running' | 'success' | 'error' | 'stopped';
  result?: string;
  output?: string;
  description?: string;
  toolResults?: Array<{ name: string; output: unknown }>;
  duration?: number;
}
export interface Run {
  id: string;
  ownerTab?: string;
  conversationId: string;
  query: string;
  module: string;
  origin?: 'backend' | 'script';
  status: 'running' | 'success' | 'error' | 'stopped';
  startedAt: string;
  duration?: number;
  events: RunEvent[];
}
export interface DemoState {
  version: 1;
  buyer: BuyerId;
  active: Record<BuyerId, string>;
  conversations: Conversation[];
  requests: AfterSale[];
  runs: Run[];
  fault: 'none' | 'tool-error' | 'no-knowledge';
  storageWarning?: string;
}
