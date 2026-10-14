import { expect, test } from '@playwright/test';
import type { Page, Route } from '@playwright/test';

const conversationId = '11111111-1111-4111-8111-111111111111';

test('知识库读取后端正文并自动更新，失败保留上次内容', async ({ page }) => {
  let revision = 1;
  let offline = false;
  let requests = 0;
  const snapshot = () => ({
    total_documents: 1,
    total_chunks: 1,
    revision: String(revision),
    documents: [
      {
        document_key: 'actual_returns',
        title: '当前退换货资料',
        category: 'return_policy',
        document_versions: [String(revision)],
        updated_at: '2026-10-12T07:00:00Z',
        chunks: [
          {
            id: 1,
            document_key: 'actual_returns',
            title: '当前退换货资料',
            category: 'return_policy',
            section: '真实规则章节',
            content: `**实际正文第${revision}版**\n\n原文：https://example.com/policy`,
            source: 'actual_returns.md#真实规则章节',
            position: 0,
            document_version: String(revision),
            content_hash: String(revision),
            updated_at: '2026-10-12T07:00:00Z',
          },
        ],
      },
    ],
  });
  await page.route('**/api/staff/knowledge/documents', async (route) => {
    requests += 1;
    if (offline) {
      await route.fulfill({ status: 503, json: { detail: '服务不可用' } });
      return;
    }
    const etag = `"${revision}"`;
    if (route.request().headers()['if-none-match'] === etag) {
      await route.fulfill({
        status: 304,
        headers: { ETag: etag, 'Access-Control-Expose-Headers': 'ETag' },
        body: '',
      });
      return;
    }
    await route.fulfill({
      json: snapshot(),
      headers: { ETag: etag, 'Access-Control-Expose-Headers': 'ETag' },
    });
  });
  await login(page, 'staff');
  await page.getByRole('link', { name: '开发者与评测' }).click();
  await page.getByRole('tab', { name: '知识库', exact: true }).click();
  const library = page.getByRole('region', { name: '实时知识库' });
  await expect(library).toContainText('1 份文档');
  await library.getByText('真实规则章节', { exact: true }).click();
  await expect(library).toContainText('实际正文第1版');
  await expect(library).toContainText('actual_returns:0');
  revision = 2;
  await expect(library).toContainText('实际正文第2版', { timeout: 12000 });
  await expect(library).not.toContainText('实际正文第1版');
  await library.getByRole('button', { name: '刷新知识库' }).click();
  await expect(library.getByRole('button', { name: '刷新知识库' })).toBeEnabled();
  await expect(library).toContainText('实际正文第2版');
  offline = true;
  await library.getByRole('button', { name: '刷新知识库' }).click();
  await expect(library.getByRole('alert')).toContainText('上次成功同步');
  await expect(library).toContainText('实际正文第2版');
  offline = false;
  await library.getByLabel('搜索知识库').fill('不存在的内容');
  await expect(library).toContainText('没有匹配的文档');
  await library.getByLabel('搜索知识库').fill('实际正文');
  await expect(library).toContainText('当前退换货资料');
  await page.getByRole('link', { name: '客服工作台' }).click();
  const previous = requests;
  await page.waitForTimeout(5500);
  expect(requests).toBe(previous);
  await page.getByRole('link', { name: '开发者与评测' }).click();
  await expect.poll(() => requests).toBeGreaterThan(previous);
});

test('买家可以保存、重新读取和删除跨会话购物偏好', async ({ page }) => {
  let saved = '';
  await page.route('**/api/memory/preferences', async (route) => {
    expect(route.request().headers().authorization).toBe('Bearer test-token-buyer_a');
    if (route.request().method() === 'PUT') {
      saved = (route.request().postDataJSON() as { content: string }).content;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ content: saved }),
    });
  });
  await login(page);
  await page.getByRole('button', { name: '购物偏好', exact: true }).click();
  await expect(page.getByLabel('购物偏好', { exact: true })).toBeEnabled();
  await page.getByLabel('购物偏好', { exact: true }).fill('喜欢头戴式耳机');
  await page.getByRole('button', { name: '保存偏好' }).click();
  await expect(page.getByRole('status')).toContainText('已保存');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '购物偏好', exact: true }).click();
  await expect(page.getByLabel('购物偏好', { exact: true })).toHaveValue('喜欢头戴式耳机');
  await page.getByRole('button', { name: '删除偏好' }).click();
  await expect(page.getByRole('status')).toContainText('已删除');
  await expect(page.getByLabel('购物偏好', { exact: true })).toHaveValue('');
});

test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/login', async (route) => {
    const body = route.request().postDataJSON() as { username: string };
    const role =
      body.username === 'staff' ? 'staff' : body.username === 'admin' ? 'admin' : 'buyer';
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
          display_name:
            role === 'staff' ? '客服小周' : role === 'admin' ? '平台管理员' : '演示买家',
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

async function login(page: Page, username: 'buyer_a' | 'buyer_b' | 'staff' | 'admin' = 'buyer_a') {
  await page.goto('/');
  const password =
    username === 'staff' || username === 'admin'
      ? `${username}-demo`
      : `${username.replace('_', '-')}-demo`;
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
  confirmation?: Record<string, unknown>,
  telemetry?: Record<string, unknown>,
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
      telemetry,
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
  events.push(
    createSseEvent(
      confirmation ? 'confirmation_required' : 'complete',
      confirmation
        ? {
            ...response,
            mode: 'awaiting_confirmation',
            assistant_message_id: assistantMessageId,
            confirmation,
          }
        : response,
    ),
  );

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

test('推荐链接打开新增商品详情，返回时保留会话和草稿', async ({ page, context }) => {
  const product = {
    id: 'p13',
    name: 'ROG SWIFT OLED PG259QWS Ace',
    category: '显示器',
    series: 'OLED 电竞显示器',
    price: 8999,
    stock: 10,
    specs: ['280Hz'],
    description: '新增的显示器',
    color: '黑色',
    source: '手动录入',
  };
  await page.route('**/api/products**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith('/categories')) await route.fulfill({ json: ['显示器'] });
    else if (pathname.endsWith('/p13')) await route.fulfill({ json: product });
    else if (pathname.endsWith('/p99'))
      await route.fulfill({ status: 404, json: { detail: '未找到商品：p99' } });
    else await route.fulfill({ json: { items: [product], total: 1, offset: 0, limit: 50 } });
  });
  let calls = 0;
  await page.route('**/api/chat/stream', async (route) => {
    calls += 1;
    await returnReply(
      route,
      calls === 1 ? `推荐 ${product.name}。` : `可以查看 [商品详情](#shop?product=p13)。`,
      ['get_product'],
      [{ name: 'get_product', output: { success: true, product } }],
    );
  });
  await login(page);
  await send(page, '推荐一款显示器');
  const link = page.getByRole('link', { name: `查看 ${product.name} 商品详情` });
  await expect(link).toHaveAttribute('href', '#shop?product=p13');
  await page.getByLabel('输入咨询内容').fill('我还想问保修');
  await link.click();
  await expect(page).toHaveURL(/#shop\?product=p13$/);
  await expect(page.getByRole('dialog', { name: '商品详情' })).toContainText('当前库存 10 件');
  expect(context.pages()).toHaveLength(1);
  expect(calls).toBe(1);
  await page
    .getByRole('dialog', { name: '商品详情' })
    .getByRole('button', { name: '继续逛店' })
    .click();
  await page.getByRole('button', { name: '返回当前咨询' }).click();
  await expect(page.getByLabel('输入咨询内容')).toHaveValue('我还想问保修');
  await expect(link).toBeVisible();
  await send(page, '查看商品详情');
  const markdownLink = page.getByRole('log').getByRole('link', { name: '商品详情', exact: true });
  await expect(markdownLink).not.toHaveAttribute('target', '_blank');
  await markdownLink.click();
  await page.reload();
  await expect(page.getByRole('dialog', { name: '商品详情' })).toContainText(product.name);
  await page.goto('/#shop?product=p99');
  await expect(page.getByRole('dialog', { name: '商品详情' }).getByRole('alert')).toContainText(
    '未找到商品',
  );
});

async function returnStreamError(route: Route, status: number, detail: string) {
  const body = createSseEvent('error', { status, detail });
  await route.fulfill({
    status: 200,
    contentType: 'text/event-stream',
    body,
  });
}

test('执行详情显示真实模型 Token 和 MCP 调用，刷新保留统计并兼容旧记录', async ({ page }) => {
  let missing = false;
  await page.route('**/api/staff/runtime/status', (route) =>
    route.fulfill({
      json: {
        model_name: 'configured-model',
        model_configured: true,
        mcp_enabled: ['catalog', 'orders'],
      },
    }),
  );
  await page.route('**/api/chat/stream', async (route) => {
    await returnReply(route, '已查询耳机', ['get_product'], [], undefined, {
      model_name: 'configured-model',
      model_names: ['provider-model'],
      token_usage: missing ? null : { input_tokens: 1000, output_tokens: 234, total_tokens: 1234 },
      usage_calls: missing ? 0 : 2,
      model_calls: 2,
      mcp_enabled: ['catalog', 'orders'],
      mcp_calls: missing
        ? []
        : [{ server: 'catalog', tool: 'get_product', calls: 2, successes: 1 }],
    });
  });
  await login(page, 'admin');
  await send(page, '查看耳机');
  await page.getByRole('link', { name: '开发者与评测' }).click();
  const model = page.locator('.run-model-metadata');
  const mcp = page.locator('.run-mcp-metadata');
  await expect(model).toContainText('provider-model / 1,234 Token');
  await expect(model).toContainText('输入 1,000 · 输出 234');
  await expect(mcp).toContainText('2 次调用 · 1 次成功');
  await expect(mcp).toContainText('商品 / get_product：1/2 成功');
  await page.reload();
  await expect(model).toContainText('1,234 Token');
  await expect(mcp).toContainText('2 次调用');
  missing = true;
  await page.getByRole('link', { name: '买家客服' }).click();
  await send(page, '再查一件');
  await page.getByRole('link', { name: '开发者与评测' }).click();
  await expect(model).toContainText('服务未返回用量');
  await expect(model).not.toContainText('0 Token');
  await expect(mcp).toContainText('本次未调用');
  // 旧浏览器记录缺少 telemetry，不应伪造 Token 或显示“未接入”。
  await page.evaluate(() => {
    const snapshot = JSON.parse(localStorage.getItem('geek-select-demo-v1')!);
    for (const run of snapshot.runs) delete run.telemetry;
    localStorage.setItem('geek-select-demo-v1', JSON.stringify(snapshot));
  });
  await page.reload();
  await expect(model).toContainText('configured-model');
  await expect(model).toContainText('历史记录未采集用量');
  await expect(mcp).toContainText('已启用商品、订单 MCP');
});

test('登录身份决定可访问的工作空间', async ({ page }) => {
  await login(page);
  await expect(page.getByRole('link', { name: '买家客服' })).toBeVisible();
  await expect(page.getByRole('link', { name: '客服工作台' })).toHaveCount(0);

  await switchToStaff(page);
  await expect(page.getByRole('link', { name: '客服工作台' })).toBeVisible();
  await expect(page.getByRole('link', { name: '买家客服' })).toHaveCount(0);
});

test('管理员可以访问全部工作空间并选择买家使用 AI 客服', async ({ page }) => {
  const buyerIds: string[] = [];
  await page.route('**/api/chat/stream', async (route) => {
    const request = route.request();
    const body = request.postDataJSON() as ChatRequestBody;
    expect(request.headers().authorization).toBe('Bearer test-token-admin');
    expect(request.headers()['x-acting-buyer-id']).toBe(body.buyer_id);
    buyerIds.push(body.buyer_id);
    await returnReply(route, `管理员正在为买家 ${body.buyer_id} 使用 AI 客服`);
  });

  await login(page, 'admin');
  await expect(page.getByRole('link', { name: '买家客服' })).toBeVisible();
  await expect(page.getByRole('link', { name: '客服工作台' })).toBeVisible();
  await expect(page.getByRole('link', { name: '开发者与评测' })).toBeVisible();
  await expect(page.getByText('平台管理员')).toBeVisible();

  await send(page, '先查看买家 A 的咨询');
  await expect(
    page.getByRole('log', { name: '聊天记录' }).getByText('管理员正在为买家 A 使用 AI 客服'),
  ).toBeVisible();
  await page.getByLabel('切换模拟买家').selectOption('B');
  await send(page, '再查看买家 B 的咨询');
  await expect(
    page.getByRole('log', { name: '聊天记录' }).getByText('管理员正在为买家 B 使用 AI 客服'),
  ).toBeVisible();
  expect(buyerIds).toEqual(['A', 'B']);

  await page.getByRole('link', { name: '客服工作台' }).click();
  await expect(page.getByText('平台管理员 · 管理员')).toBeVisible();
});

test('评测预览加载新版100条历史、真实指标与答案，刷新后仍可查看', async ({ page }) => {
  const rows = Array.from({ length: 100 }, (_, i) => ({
    id: `JDDC-${i + 1}`,
    question: i === 0 ? '耳机进水可以免费保修吗？' : `案例问题${i + 1}`,
    expected_sources: i < 4 ? ['jd_warranty.md#保修范围'] : [],
    retrieved_sources: ['jd_warranty.md#保修范围'],
    first_relevant_rank: i < 4 ? 1 : null,
    passed: i < 4,
    should_answer: i < 4,
    reference_answer: '参考答案',
    retrieval_latency_ms: 40,
    retrieval_mode: 'hybrid',
    retrieved_chunks: [
      {
        document: '官方保修规则',
        document_key: 'jd_warranty',
        chunk_id: 'jd_warranty:0',
        section: '保修范围',
        source: 'jd_warranty.md#保修范围',
        content: '知识块实际正文',
        score: 0.03,
        score_type: 'rrf',
      },
    ],
  }));
  const report = {
    evaluation_id: 'saved100',
    cases: 100,
    answerable_cases: 4,
    unanswerable_cases: 96,
    strategy: 'hybrid',
    retrieval_backend: 'MilvusKnowledgeVectorStore',
    recall_at_k: 0.6339,
    recall_at_1: 0.375,
    recall_at_3: 0.5982,
    recall_at_5: 0.6339,
    mrr: 0.7083,
    rejection_accuracy: 0,
    false_retrieval_rate: 1,
    average_latency_ms: 42,
    retrieval_latency_p50_ms: 40,
    retrieval_latency_p95_ms: 88,
    results: rows,
  };
  const summary = {
    id: 'saved100',
    created_at: '2026-10-12T07:00:00Z',
    dataset_name: 'JDDC 当前知识库 100 条',
    cases: 100,
    cases_sha256: 'abc100',
    annotation_status: 'ai_verified（尚非人工金标准）',
    has_retrieval: true,
    has_runtime: true,
  };
  const runtime = {
    cases: 100,
    model: 'test-model',
    workers: 4,
    created_at: summary.created_at,
    retrieval_backend: '真实BGE+Milvus',
    runtime_error_cases: 0,
    evaluation_failure_cases: 0,
    quality_cases: 100,
    tool_selection_correct: 45,
    tool_selection_cases: 100,
    tool_selection_accuracy: 0.45,
    parameter_valid_cases: 16,
    parameter_cases: 87,
    parameter_accuracy: 16 / 87,
    task_successes: 13,
    task_completion_rate: 0.13,
    end_to_end_p95_ms: 7700,
    usage_cases: 100,
    generation_usage: { input_tokens: 200, output_tokens: 100, total_tokens: 300 },
    quality_averages: {
      correctness: 0.88,
      faithfulness: 0.82,
      completeness: 0.88,
      citation_correctness: 0.93,
    },
    average_request_cost: null,
    cost_currency: null,
  };
  let rerun = false;
  await page.route('**/api/staff/knowledge/evaluations', async (route) => {
    await route.fulfill({
      json: {
        records: rerun
          ? [
              { ...summary, id: 'new100', created_at: '2026-10-12T08:00:00Z', has_runtime: false },
              summary,
            ]
          : [summary],
      },
    });
  });
  await page.route('**/api/staff/knowledge/evaluations/*', async (route) => {
    const id = route.request().url().split('/').pop();
    await route.fulfill({
      json: {
        ...summary,
        id,
        retrieval: report,
        runtime: id === 'saved100' ? runtime : null,
        runtime_results:
          id === 'saved100'
            ? [
                {
                  id: 'JDDC-1',
                  query: rows[0].question,
                  answer: '实际模型回答示例',
                  sources: [],
                  expected_tool: 'search_knowledge',
                  tool_selection_correct: true,
                  parameters_correct: true,
                  task_passed: true,
                  end_to_end_latency_ms: 800,
                  quality: {
                    correctness: 0.9,
                    faithfulness: 0.8,
                    completeness: 0.9,
                    citation_correctness: 1,
                    reason: '评审依据示例',
                  },
                  calls: [
                    {
                      name: 'search_knowledge',
                      args: { query: '进水保修' },
                      parameters_correct: true,
                    },
                  ],
                  error: null,
                  evaluation_error: null,
                },
              ]
            : [],
      },
    });
  });
  await page.route('**/api/staff/knowledge/evaluate', async (route) => {
    rerun = true;
    await route.fulfill({ json: { ...report, evaluation_id: 'new100' } });
  });
  await login(page, 'staff');
  await page.getByRole('link', { name: '开发者与评测' }).click();
  await page.getByRole('tab', { name: '评测预览' }).click();
  await expect(page.locator('.cases-panel tbody tr')).toHaveCount(100);
  await expect(page.locator('.metric-card').filter({ hasText: 'Recall@3' })).toContainText(
    '59.82%',
  );
  await expect(page.locator('.metric-card').filter({ hasText: 'Recall@5' })).toContainText(
    '63.39%',
  );
  await expect(page.getByText('Prompt V1 与 V2')).toHaveCount(0);
  const first = page.locator('.cases-panel tbody tr').first();
  await first.getByText('查看详情', { exact: true }).click();
  await expect(first).toContainText('实际模型回答示例');
  await expect(first).toContainText('jd_warranty:0');
  await page.getByLabel('筛选评测结果').selectOption('failed');
  await expect(page.locator('.cases-panel tbody tr')).toHaveCount(96);
  await page.reload();
  await page.getByRole('link', { name: '开发者与评测' }).click();
  await page.getByRole('tab', { name: '评测预览' }).click();
  await expect(page.locator('.cases-panel tbody tr')).toHaveCount(100);
  await page.getByRole('button', { name: '运行 RAG 评测' }).click();
  await expect(page.getByText('新版 100 条检索评测完成，报告已保存。')).toBeVisible();
  await expect(page.getByLabel('评测轮次')).toHaveValue('new100');
  await page.getByLabel('评测轮次').selectOption('saved100');
  await expect(page.locator('.metric-card').filter({ hasText: '工具选择准确率' })).toContainText(
    '45.00%',
  );
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

test('AI 回复按照 Markdown 渲染标题、列表、表格、代码和链接', async ({ page }) => {
  const markdown = [
    '## 推荐结果',
    '',
    '- **降噪能力**：支持主动降噪',
    '- 预算：`300 元`',
    '',
    '| 商品 | 价格 |',
    '| --- | ---: |',
    '| AirBeat Pro | 299 元 |',
    '',
    '```json',
    '{"product_id":"p01"}',
    '```',
    '',
    '[查看帮助](https://example.com/help)',
  ].join('\n');
  await page.route('**/api/chat/stream', async (route) => {
    await returnReply(route, markdown);
  });

  await login(page);
  await send(page, '用 Markdown 推荐一款耳机');

  const log = page.getByRole('log', { name: '聊天记录' });
  await expect(log.getByRole('heading', { name: '推荐结果', level: 2 })).toBeVisible();
  await expect(log.getByRole('listitem').filter({ hasText: '降噪能力' })).toBeVisible();
  await expect(log.getByRole('table')).toContainText('AirBeat Pro');
  await expect(log.locator('pre code')).toContainText('"product_id":"p01"');
  const link = log.getByRole('link', { name: '查看帮助' });
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
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
            query: '耳机拆封后还能退货吗？',
            retrieval_mode: 'hybrid',
            answer_context: '签收后 7 天内，商品保持完好时可以申请。',
            sources: [
              {
                document: '退换货规则',
                document_key: 'return_policy',
                chunk_id: 'return_policy:2',
                chunk_position: 2,
                document_version: '1',
                rank: 1,
                score_type: 'rrf',
                keyword_score: 3.5,
                keyword_rank: 1,
                vector_score: 0.82,
                vector_rank: 2,
                rrf_score: 0.032522,
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
  await switchToStaff(page);
  await page.getByRole('link', { name: '开发者与评测' }).click();
  const retrieval = page.getByRole('region', { name: 'RAG 召回详情' });
  await expect(retrieval).toContainText('return_policy:2');
  await expect(retrieval).toContainText('return_policy.md#七天无理由退货');
  await expect(retrieval).toContainText('0.900000（RRF）');
  await expect(retrieval).toContainText('BM25 + 向量混合检索');
  await retrieval.getByText('查看知识块正文', { exact: true }).click();
  await expect(retrieval.locator('.rag-chunk-content')).toContainText('配件和包装齐全');
  await page.reload();
  await page.getByRole('link', { name: '开发者与评测' }).click();
  await expect(page.getByRole('region', { name: 'RAG 召回详情' })).toContainText('return_policy:2');
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

test('退货先展示空原因草稿，用户补充并确认后才转交人工', async ({ page }) => {
  const createdAt = new Date().toISOString();
  const requestId = '33333333-3333-4333-8333-333333333333';
  const finalReason = '左耳没有声音，更换设备后仍然如此。';
  let stage = 'draft';
  const actions: string[] = [];
  const requestBody = () => ({
    id: requestId,
    buyer_id: 'A',
    order_id: '10002',
    request_type: '退货',
    reason: finalReason,
    status: stage,
    version: stage === 'draft' ? 1 : 2,
    created_at: createdAt,
    updated_at: createdAt,
    operations: [],
  });
  await page.route('**/api/chat/stream', async (route) => {
    const request = route.request().postDataJSON() as ChatRequestBody;
    const userMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: request.message,
      created_at: createdAt,
    };
    const assistantId = crypto.randomUUID();
    // 即使没有前面的工具状态事件，也必须从暂停事件展示草稿。
    await route.fulfill({
      contentType: 'text/event-stream',
      body:
        createSseEvent('start', {
          conversation_id: conversationId,
          mode: 'ai',
          user_message: userMessage,
          assistant_message_id: assistantId,
        }) +
        createSseEvent('confirmation_required', {
          conversation_id: conversationId,
          mode: 'awaiting_confirmation',
          user_message: userMessage,
          assistant_message_id: assistantId,
          confirmation: {
            type: 'after_sale_draft',
            requires_confirmation: true,
            draft: { order_id: '10002', request_type: '退货', reason: '' },
          },
          run: { model_calls: 1, tool_rounds: 1, tool_calls: 1, task_results: [] },
        }),
    });
  });
  await page.route('**/api/after-sales/drafts', async (route) => {
    actions.push('create');
    expect(route.request().postDataJSON().reason).toBe(finalReason);
    await route.fulfill({ status: 201, json: requestBody() });
  });
  await page.route(`**/api/after-sales/drafts/${requestId}/submit`, async (route) => {
    actions.push('submit');
    stage = 'pending';
    await route.fulfill({ json: requestBody() });
  });
  await page.route(`**/api/conversations/${conversationId}/resume`, async (route) => {
    actions.push('resume');
    expect(route.request().postDataJSON()).toEqual({
      action: 'confirm',
      draft: { order_id: '10002', request_type: '退货', reason: finalReason },
    });
    await route.fulfill({
      json: {
        conversation_id: conversationId,
        mode: 'ai',
        assistant_message: {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: '## 已转交人工\n申请已提交，正在等待人工客服审核。',
          created_at: createdAt,
        },
      },
    });
  });
  await login(page);
  await send(page, '订单10002申请退货');
  const draft = page.locator('.draft-card');
  await expect(draft).toContainText('待补充原因');
  await expect(draft.getByLabel('问题描述')).toHaveValue('');
  await expect(draft.getByRole('button', { name: '确认申请内容' })).toBeDisabled();
  await expect(page.getByRole('log').locator('.answer-module-blue')).toContainText('草稿已准备');
  await expect(page.getByRole('log').locator('.answer-module-amber')).toContainText('需要补充');
  await expect(page.getByRole('log').locator('.answer-module-green')).toContainText('下一步');
  expect(actions).toEqual([]);
  await page.reload();
  await expect(draft).toBeVisible();
  await draft.getByLabel('问题描述').fill('坏了');
  await expect(draft.getByRole('button', { name: '确认申请内容' })).toBeDisabled();
  await draft.getByLabel('问题描述').fill(finalReason);
  await draft.getByRole('button', { name: '确认申请内容' }).click();
  const confirmation = page.getByRole('dialog', { name: '确认提交售后申请' });
  await expect(confirmation).toContainText(finalReason);
  await expect(confirmation.getByRole('button', { name: '提交申请', exact: true })).toBeDisabled();
  expect(actions).toEqual([]);
  await confirmation.getByRole('button', { name: '返回修改' }).click();
  await draft.getByRole('button', { name: '确认申请内容' }).click();
  await confirmation.getByRole('checkbox').check();
  await confirmation.getByRole('button', { name: '提交申请', exact: true }).click();
  await expect(page.getByText('待人工审核', { exact: true })).toBeVisible();
  expect(actions).toEqual(['create', 'submit', 'resume']);
});

test('取消草稿立即显示处理状态，成功恢复咨询，失败保留草稿', async ({ page }) => {
  let requests = 0;
  let release: (() => void) | undefined;
  let fail = true;
  await page.route('**/api/chat/stream', async (route) => {
    await returnReply(route, '', [], [], {
      type: 'after_sale_draft',
      requires_confirmation: true,
      draft: { order_id: '10002', request_type: '退货', reason: '七天无理由' },
    });
  });
  await page.route(`**/api/conversations/${conversationId}/resume`, async (route) => {
    requests += 1;
    expect(route.request().postDataJSON().action).toBe('cancel');
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    if (fail) await route.fulfill({ status: 503, json: { detail: '暂时无法取消，请重试' } });
    else
      await route.fulfill({
        json: {
          conversation_id: conversationId,
          mode: 'ai',
          assistant_message: {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: '售后申请草稿已取消，没有提交售后申请。',
            created_at: new Date().toISOString(),
          },
        },
      });
  });
  await page.route('**/api/after-sales/drafts**', () => {
    throw new Error('取消不应创建或提交售后申请');
  });
  await login(page);
  await send(page, '订单10002退货');
  const draft = page.locator('.draft-card');
  await draft.getByRole('button', { name: '取消草稿', exact: true }).click();
  const cancelling = draft.getByRole('button', { name: '正在取消…', exact: true });
  await expect(cancelling).toBeDisabled();
  await expect(draft.getByRole('button', { name: '确认申请内容' })).toBeDisabled();
  await expect(draft.getByLabel('问题描述')).toBeDisabled();
  await expect.poll(() => Boolean(release)).toBe(true);
  expect(requests).toBe(1);
  release!();
  await expect(page.getByRole('log')).toContainText('暂时无法取消，请重试');
  await expect(draft.getByRole('button', { name: '取消草稿', exact: true })).toBeEnabled();
  fail = false;
  release = undefined;
  await draft.getByRole('button', { name: '取消草稿', exact: true }).click();
  await expect.poll(() => Boolean(release)).toBe(true);
  release!();
  await expect(draft).toHaveCount(0);
  await expect(page.getByLabel('输入咨询内容')).toBeEnabled();
  await expect(page.getByRole('log')).toContainText('售后申请草稿已取消，没有提交售后申请。');
  expect(requests).toBe(2);
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
