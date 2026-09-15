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

export interface ChatStreamStart {
  conversation_id: string;
  mode: ChatResponse['mode'];
  user_message: ApiMessage;
  assistant_message_id: string;
}

export interface ChatStreamStatus {
  phase: 'model' | 'tool';
  state: 'started' | 'completed';
  tool_calls: number;
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
  const response = await fetch(`${apiBaseUrl}/chat/stream`, {
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
  });

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
