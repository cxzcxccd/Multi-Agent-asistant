import { expect, test } from '@playwright/test';
import type { Page, Route } from '@playwright/test';

const conversationId = '11111111-1111-4111-8111-111111111111';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/login', async (route) => {
    const body = route.request().postDataJSON() as { username: string };
    const role = body.username === 'staff' ? 'staff' : 'buyer';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: `test-token-${body.username}`,
        refresh_token: `test-refresh-token-${body.username}`,
        token_type: 'bearer',
        expires_in: 1800,
        principal: {
          subject: body.username,
          display_name: role === 'staff' ? '客服小周' : '演示买家',
          role,
          buyer_id: role === 'buyer' ? 'A' : null,
        },
      }),
    });
  });
  await page.route('**/api/staff/after-sales/pending', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });
});

interface ChatRequestBody {
  buyer_id: 'A' | 'B';
  conversation_id?: string;
  message: string;
}

async function send(page: Page, message: string) {
  await page.getByLabel('输入咨询内容').fill(message);
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
}

async function login(page: Page, username: 'buyer_a' | 'buyer_b' | 'staff' = 'buyer_a') {
  await page.goto('/');
  const password = username === 'staff' ? 'staff-demo' : `${username.replace('_', '-')}-demo`;
  await page.getByLabel('账号').fill(username);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
}

async function switchToStaff(page: Page) {
  await page.getByRole('button', { name: '退出登录' }).click();
  await page.getByLabel('账号').fill('staff');
  await page.getByLabel('密码').fill('staff-demo');
  await page.getByRole('button', { name: '登录', exact: true }).click();
}

async function returnReply(
  route: Route,
  reply: string,
  toolNames: string[] = [],
  toolResults?: Array<{ name: string; output: unknown }>,
) {
  // 模拟 SSE 响应，只验证浏览器是否真的处理事件流，不消耗模型额度。
  const request: ChatRequestBody = route.request().postDataJSON();
  const createdAt = new Date().toISOString();
  const assistantMessageId = crypto.randomUUID();
  const middle = Math.max(1, Math.floor(reply.length / 2));
  const firstPart = reply.slice(0, middle);
  const secondPart = reply.slice(middle);
  const userMessage = {
    id: crypto.randomUUID(),
    role: 'user',
    content: request.message,
    created_at: createdAt,
  };
  const assistantMessage = {
    id: assistantMessageId,
    role: 'assistant',
    content: reply,
    created_at: createdAt,
  };
  const queryAnalysis = {
    original_query: request.message,
    normalized_query: request.message.trim(),
    routing_query: request.message.trim(),
    optimized_query: `用户原意：${request.message.trim()}；主要意图：product_inquiry`,
    primary_intent: 'product_inquiry',
    secondary_intents: [],
    similarity_score: 0.88,
    route_margin: 0.2,
    router_source: 'semantic_router',
    embedding_model: 'BAAI/bge-small-zh-v1.5',
    description: '用户正在咨询商品、价格、库存、参数或选购建议。',
    entities: {
      order_id: null,
      budget: null,
      product_category: request.message.includes('耳机') ? '耳机' : null,
      after_sale_type: null,
    },
    candidates: [
      {
        intent: 'product_inquiry',
        similarity_score: 0.88,
      },
    ],
  };
  const response = {
    conversation_id: conversationId,
    mode: 'ai',
    user_message: userMessage,
    assistant_message: assistantMessage,
    run: {
      model_calls: toolNames.length ? 2 : 1,
      tool_rounds: toolNames.length ? 1 : 0,
      tool_calls: toolNames.length,
      query_analysis: queryAnalysis,
    },
  };

  const events: string[] = [];
  events.push(
    createSseEvent('start', {
      conversation_id: conversationId,
      mode: 'ai',
      user_message: userMessage,
      assistant_message_id: assistantMessageId,
    }),
  );
  events.push(
    createSseEvent('status', {
      phase: 'router',
      state: 'started',
      tool_calls: 0,
    }),
  );
  events.push(
    createSseEvent('status', {
      phase: 'router',
      state: 'completed',
      tool_calls: 0,
      query_analysis: queryAnalysis,
    }),
  );
  if (toolNames.length) {
    events.push(
      createSseEvent('status', {
        phase: 'tool',
        state: 'started',
        tool_calls: toolNames.length,
        tool_names: toolNames,
      }),
    );
    events.push(
      createSseEvent('status', {
        phase: 'tool',
        state: 'completed',
        tool_calls: toolNames.length,
        tool_names: toolNames,
        tool_results:
          toolResults ||
          toolNames.map((name) => ({
            name,
            output: {
              success: true,
              items: [{ id: 'p01', name: 'AirBeat Pro 降噪耳机', price: 299 }],
              total: 1,
              returned: 1,
              has_more: false,
            },
          })),
      }),
    );
    events.push(
      createSseEvent('status', {
        phase: 'model',
        state: 'started',
        tool_calls: toolNames.length,
        tool_names: [],
      }),
    );
  }
  events.push(
    createSseEvent('status', {
      phase: 'model',
      state: 'started',
      tool_calls: 0,
    }),
  );
  events.push(createSseEvent('delta', { content: firstPart }));
  if (secondPart) {
    events.push(createSseEvent('delta', { content: secondPart }));
  }
  events.push(createSseEvent('complete', response));

  await route.fulfill({
    status: 200,
    contentType: 'text/event-stream',
    body: events.join(''),
  });
}

function createSseEvent(name: string, data: unknown): string {
  const jsonData = JSON.stringify(data);
  return `event: ${name}\ndata: ${jsonData}\n\n`;
}

async function returnStreamError(route: Route, status: number, detail: string) {
  const body = createSseEvent('error', { status, detail });
  await route.fulfill({
    status: 200,
    contentType: 'text/event-stream',
    body,
  });
}

test('登录身份决定可访问的工作空间', async ({ page }) => {
  await login(page);
  await expect(page.getByRole('link', { name: '买家客服' })).toBeVisible();
  await expect(page.getByRole('link', { name: '客服工作台' })).toHaveCount(0);

  await switchToStaff(page);
  await expect(page.getByRole('link', { name: '客服工作台' })).toBeVisible();
  await expect(page.getByRole('link', { name: '买家客服' })).toHaveCount(0);
});

test('客服可以运行真实 RAG 检索评测并查看指标', async ({ page }) => {
  await page.route('**/api/staff/knowledge/evaluate', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        cases: 12,
        recall_at_k: 0.95,
        mrr: 0.95,
        rejection_accuracy: 1,
        average_latency_ms: 2.5,
        results: [
          {
            id: 'RAG-001',
            question: '耳机进水可以免费保修吗？',
            expected_sources: ['warranty_policy.md#保修范围'],
            retrieved_sources: ['warranty_policy.md#保修范围'],
            first_relevant_rank: 1,
            passed: true,
          },
        ],
      }),
    });
  });

  await login(page, 'staff');
  await page.getByRole('link', { name: '开发者与评测' }).click();
  await page.getByRole('tab', { name: '评测预览' }).click();
  await page.getByRole('button', { name: '运行 RAG 评测' }).click();

  await expect(page.getByText('真实检索评测完成，结果已更新。')).toBeVisible();
  await expect(page.getByText('Recall@3').first()).toBeVisible();
  await expect(page.locator('.metric-card').filter({ hasText: 'Recall@3' })).toContainText('95%');
  await expect(page.getByText('warranty_policy.md#保修范围')).toBeVisible();
});

test('你是谁和无商品关键词的追问都调用后端，并沿用会话', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  await page.route('**/api/chat/stream', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, `后端回复：${body.message}`);
  });
  await login(page);
  const log = page.getByRole('log', { name: '聊天记录' });

  await send(page, '你是谁');
  await expect(log.getByText('后端回复：你是谁', { exact: true })).toBeVisible();
  expect(requests[0]).toEqual({ buyer_id: 'A', message: '你是谁' });

  await send(page, '你好');
  await expect(log.getByText('后端回复：你好', { exact: true })).toBeVisible();
  await send(page, '刚才说的再详细一点');
  await expect(log.getByText('后端回复：刚才说的再详细一点')).toBeVisible();

  expect(requests).toHaveLength(3);
  expect(requests[1].conversation_id).toBe(conversationId);
  expect(requests[2].conversation_id).toBe(conversationId);
  await expect(log.getByText(/暂未连接真实大模型/)).toHaveCount(0);
});

test('商品咨询后的省略主语追问使用同一个后端会话', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  await page.route('**/api/chat/stream', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, `后端回复：${body.message}`, ['search_products']);
  });
  await login(page);
  const log = page.getByRole('log', { name: '聊天记录' });

  await send(page, '推荐一款耳机');
  await expect(log.getByText('后端回复：推荐一款耳机')).toBeVisible();
  await expect(page.getByText('product_inquiry', { exact: true })).toBeVisible();
  await expect(page.getByText('调用 search_products', { exact: true })).toBeVisible();
  await expect(page.getByText('工具返回 · search_products', { exact: true })).toBeVisible();
  await expect(page.locator('.trace-tool-result pre')).toContainText('AirBeat Pro 降噪耳机');
  await expect(page.getByText('已生成最终客服回复', { exact: true })).toBeVisible();
  await send(page, '它有货吗');
  await expect(log.getByText('后端回复：它有货吗')).toBeVisible();

  expect(requests).toHaveLength(2);
  expect(requests[1].conversation_id).toBe(conversationId);
});

test('知识检索结果显示在执行时间线和回复来源中', async ({ page }) => {
  await page.route('**/api/chat/stream', async (route) => {
    await returnReply(
      route,
      '签收后 7 天内且商品完好时，可以申请无理由退货。',
      ['search_knowledge'],
      [
        {
          name: 'search_knowledge',
          output: {
            success: true,
            answer_context: '签收后 7 天内，商品保持完好时可以申请。',
            sources: [
              {
                document: '退换货规则',
                category: 'return_policy',
                section: '七天无理由退货',
                content: '签收后 7 天内，商品保持完好、配件和包装齐全时可以申请。',
                source: 'return_policy.md#七天无理由退货',
                score: 0.9,
              },
            ],
            total: 1,
          },
        },
      ],
    );
  });

  await login(page);
  await send(page, '耳机拆封后还能退货吗？');

  await expect(page.getByText('参考了 1 份资料')).toBeVisible();
  await page.getByText('参考了 1 份资料').click();
  await expect(page.getByText('退换货规则 · 七天无理由退货')).toBeVisible();
  await expect(page.getByText('命中 1 条资料')).toBeVisible();
});

test('询问人工智能或真人身份时仍然调用后端', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  await page.route('**/api/chat/stream', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, `后端回复：${body.message}`);
  });
  await login(page);
  const log = page.getByRole('log', { name: '聊天记录' });

  await send(page, '你是人工智能吗');
  await expect(log.getByText('后端回复：你是人工智能吗')).toBeVisible();
  await send(page, '你是真人吗');
  await expect(log.getByText('后端回复：你是真人吗')).toBeVisible();
  await expect(page.getByRole('button', { name: '转人工', exact: true })).toBeEnabled();
  expect(requests).toHaveLength(2);
});

test('转人工、客服接管与回复调用后端', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  const remoteConversationId = '33333333-3333-4333-8333-333333333333';
  const createdAt = new Date().toISOString();
  let mode: 'waiting' | 'human' | 'ai' | 'closed' = 'waiting';
  const messages: Array<{ id: string; role: string; content: string; created_at: string }> = [];
  const conversation = () => ({
    id: remoteConversationId,
    buyer_id: 'A',
    title: '请转人工客服',
    mode,
    messages,
    created_at: createdAt,
    updated_at: createdAt,
  });
  await page.route('**/api/chat/stream', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, '后端回复。');
  });
  await page.route('**/api/handoff', async (route) => {
    const body = route.request().postDataJSON();
    expect(body.buyer_id).toBe('A');
    mode = 'waiting';
    messages.push({
      id: crypto.randomUUID(),
      role: 'system',
      content: '已进入人工服务队列。咨询记录会一并交给客服，自动回复已暂停。',
      created_at: createdAt,
    });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(conversation()),
    });
  });
  await page.route(`**/api/staff/conversations/${remoteConversationId}/mode`, async (route) => {
    const body = route.request().postDataJSON() as { mode: typeof mode };
    mode = body.mode;
    messages.push({
      id: crypto.randomUUID(),
      role: 'system',
      content: mode === 'human' ? '客服小周 已接入，会继续为你处理。' : '状态已更新。',
      created_at: createdAt,
    });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(conversation()),
    });
  });
  await page.route(`**/api/staff/conversations/${remoteConversationId}/messages`, async (route) => {
    const body = route.request().postDataJSON() as { message: string };
    messages.push({
      id: crypto.randomUUID(),
      role: 'staff',
      content: body.message,
      created_at: createdAt,
    });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(conversation()),
    });
  });
  await login(page);
  await send(page, '请转人工客服');

  const log = page.getByRole('log', { name: '聊天记录' });
  await expect(log.getByText(/已进入人工服务队列/)).toBeVisible();
  expect(requests).toHaveLength(0);

  await switchToStaff(page);
  await page.getByRole('button', { name: '接管会话' }).click();
  await expect(page.getByRole('button', { name: '恢复 AI' })).toBeVisible();
  await page.getByLabel('人工回复内容').fill('你好，我是客服小周。');
  await page.getByRole('button', { name: '发送人工回复' }).click();
  await expect(page.locator('.staff-conversation').getByText('你好，我是客服小周。')).toBeVisible();
});

test('客服工作台无需刷新即可同步新的转人工会话', async ({ page }) => {
  const createdAt = new Date().toISOString();
  let waitingConversationVisible = false;
  const waitingConversation = {
    id: '44444444-4444-4444-8444-444444444444',
    buyer_id: 'A',
    title: '新的转人工请求',
    mode: 'waiting',
    messages: [
      {
        id: crypto.randomUUID(),
        role: 'system',
        content: '已进入人工服务队列。',
        created_at: createdAt,
      },
    ],
    created_at: createdAt,
    updated_at: createdAt,
  };
  await page.route('**/api/staff/conversation-events?*', async (route) => {
    const items = waitingConversationVisible ? [waitingConversation] : [];
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: createSseEvent('snapshot', items),
    });
  });

  waitingConversationVisible = true;
  await login(page, 'staff');

  const synchronizedConversation = page
    .locator('.conversation-list')
    .getByRole('button', { name: /新的转人工请求/ });
  await expect(synchronizedConversation).toBeVisible({ timeout: 6000 });
  await synchronizedConversation.click();
  await expect(page.getByRole('button', { name: '接管会话' })).toBeVisible();
});

test('订单查询调用后端并沿用同一会话', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  await page.route('**/api/chat/stream', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, `后端回复：${body.message}`);
  });
  await login(page);
  const log = page.getByRole('log', { name: '聊天记录' });

  await send(page, '查一下我的订单');
  await expect(log.getByText('后端回复：查一下我的订单')).toBeVisible();
  await send(page, '订单 10001 到哪里了');
  await expect(log.getByText('后端回复：订单 10001 到哪里了')).toBeVisible();

  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual({ buyer_id: 'A', message: '查一下我的订单' });
  expect(requests[1].conversation_id).toBe(conversationId);
});

test('后端失败时显示错误，重试仍然调用后端', async ({ page }) => {
  let requestCount = 0;
  await page.route('**/api/chat/stream', async (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await returnStreamError(route, 502, 'AI 客服暂时无法生成回复');
      return;
    }
    await returnReply(route, '重试后收到的后端回复。');
  });
  await login(page);
  const log = page.getByRole('log', { name: '聊天记录' });

  await send(page, '你是谁');
  await expect(log.getByText('AI 客服暂时无法生成回复')).toBeVisible();
  await expect(log.getByText(/脚本驱动的交互演示/)).toHaveCount(0);
  await page.getByRole('button', { name: '重试查询', exact: true }).click();
  await expect(log.getByText('重试后收到的后端回复。')).toBeVisible();
  expect(requestCount).toBe(2);
});

test('后端重启导致会话失效时，仅重建一次并重试', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  await page.route('**/api/chat/stream', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    if (requests.length === 2) {
      await returnStreamError(route, 404, '未找到会话');
      return;
    }
    await returnReply(route, `后端回复：${body.message}`);
  });
  await login(page);
  const log = page.getByRole('log', { name: '聊天记录' });

  await send(page, '你是谁');
  await expect(log.getByText('后端回复：你是谁')).toBeVisible();
  await send(page, '你好');
  await expect(log.getByText('后端回复：你好')).toBeVisible();

  expect(requests).toHaveLength(3);
  expect(requests[1].conversation_id).toBe(conversationId);
  expect(requests[2].conversation_id).toBeUndefined();
});

test('停止后不显示迟到回复，可以继续发送新消息', async ({ page }) => {
  let releaseReply: () => void = () => {};
  const responseGate = new Promise<void>((resolve) => {
    releaseReply = resolve;
  });
  await page.route('**/api/chat/stream', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    if (body.message === '你是谁') {
      await responseGate;
      await returnReply(route, '已经停止的迟到回复。');
      return;
    }
    await returnReply(route, '新的后端回复。');
  });
  await login(page);
  const log = page.getByRole('log', { name: '聊天记录' });

  const requestStarted = page.waitForRequest('**/api/chat/stream');
  await send(page, '你是谁');
  await requestStarted;
  await page.getByRole('button', { name: '停止输出', exact: true }).click();
  releaseReply();
  await send(page, '你好');
  await expect(log.getByText('新的后端回复。')).toBeVisible();
  await expect(log.getByText('已经停止的迟到回复。')).toHaveCount(0);
});

test('售后确认与客服审核调用后端接口', async ({ page }) => {
  const requestId = '22222222-2222-4222-8222-222222222222';
  const createdAt = new Date().toISOString();
  let requestStatus: 'draft' | 'pending' | 'approved' = 'draft';
  let requestVersion = 1;
  let createBody: Record<string, unknown> | undefined;
  let submitBody: Record<string, unknown> | undefined;
  let reviewBody: Record<string, unknown> | undefined;

  const responseBody = () => ({
    id: requestId,
    buyer_id: 'A',
    order_id: '10002',
    request_type: '退货',
    reason: '左耳没声音，已经更换设备重试。',
    status: requestStatus,
    version: requestVersion,
    created_at: createdAt,
    updated_at: createdAt,
    submitted_at: requestStatus === 'draft' ? null : createdAt,
    reviewed_at: requestStatus === 'approved' ? createdAt : null,
    reviewer: requestStatus === 'approved' ? '客服小周' : null,
    review_reason: requestStatus === 'approved' ? '符合退货条件。' : null,
    operations: [],
  });

  await page.route('**/api/chat/stream', async (route) => {
    await returnReply(
      route,
      '已整理好售后草稿，请核对后确认提交。',
      ['prepare_after_sale_draft'],
      [
        {
          name: 'prepare_after_sale_draft',
          output: {
            success: true,
            artifact: {
              type: 'after_sale_draft',
              requires_confirmation: true,
              draft: {
                order_id: '10002',
                request_type: '退货',
                reason: '左耳没声音，已经更换设备重试。',
              },
            },
          },
        },
      ],
    );
  });

  await page.route('**/api/after-sales?*', async (route) => {
    const items = requestStatus === 'draft' ? [] : [responseBody()];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(items),
    });
  });
  await page.route('**/api/after-sales/drafts', async (route) => {
    createBody = route.request().postDataJSON();
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(responseBody()),
    });
  });
  await page.route(`**/api/after-sales/drafts/${requestId}/submit`, async (route) => {
    submitBody = route.request().postDataJSON();
    requestStatus = 'pending';
    requestVersion = 2;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(responseBody()),
    });
  });
  await page.route(`**/api/staff/after-sales/${requestId}/review`, async (route) => {
    reviewBody = route.request().postDataJSON();
    requestStatus = 'approved';
    requestVersion = 3;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(responseBody()),
    });
  });

  await login(page);
  await send(page, '订单 10002 左耳没声音，已经更换设备重试，申请退货。');
  await expect(page.getByRole('heading', { name: '请核对售后申请' })).toBeVisible();
  await page.getByLabel('问题描述').fill('左耳没声音，已经更换设备重试。');
  await page.getByRole('button', { name: '确认申请内容' }).click();
  const submitDialog = page.getByRole('dialog');
  await submitDialog.getByRole('checkbox').check();
  await submitDialog.getByRole('button', { name: '提交申请', exact: true }).click();
  await expect(page.getByText('待人工审核', { exact: true })).toBeVisible();

  expect(createBody).toEqual({
    buyer_id: 'A',
    order_id: '10002',
    request_type: '退货',
    reason: '左耳没声音，已经更换设备重试。',
  });
  expect(submitBody).toEqual({ buyer_id: 'A', expected_version: 1 });

  await switchToStaff(page);
  await page.getByRole('tab', { name: /售后审核/ }).click();
  await page.getByRole('button', { name: '批准申请' }).click();
  const reviewDialog = page.getByRole('dialog');
  await reviewDialog.getByLabel('审核意见').fill('符合退货条件。');
  await reviewDialog.getByRole('button', { name: '确认批准' }).click();
  await expect(page.getByText('审核结果已记录，并发送到买家会话。')).toBeVisible();
  expect(reviewBody).toEqual({
    reviewer: '客服小周',
    decision: 'approved',
    reason: '符合退货条件。',
    expected_version: 2,
  });
});
