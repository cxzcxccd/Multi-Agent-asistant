import type { BuyerId } from './types';

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api').replace(
  /\/$/,
  '',
);

interface ApiMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
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
  };
}

export class ChatApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ChatApiError';
    this.status = status;
  }
}

export async function sendChatMessage(
  buyerId: BuyerId,
  message: string,
  conversationId?: string,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const response = await fetch(`${apiBaseUrl}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      buyer_id: buyerId,
      conversation_id: conversationId,
      message,
    }),
    signal,
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    const message = body?.detail || `客服接口请求失败（${response.status}）`;
    throw new ChatApiError(message, response.status);
  }

  return (await response.json()) as ChatResponse;
}
