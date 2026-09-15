import { expect, test } from '@playwright/test';
import type { Page, Route } from '@playwright/test';

const conversationId = '11111111-1111-4111-8111-111111111111';

interface ChatRequestBody {
  buyer_id: 'A' | 'B';
  conversation_id?: string;
  message: string;
}

async function send(page: Page, message: string) {
  await page.getByLabel('输入咨询内容').fill(message);
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
}

async function returnReply(route: Route, reply: string) {
  // 模拟 HTTP 响应，只验证浏览器是否真的发送请求，不消耗模型额度。
  const request: ChatRequestBody = route.request().postDataJSON();
  const createdAt = new Date().toISOString();

  await route.fulfill({
    status: 200,
    json: {
      conversation_id: conversationId,
      mode: 'ai',
      user_message: {
        id: crypto.randomUUID(),
        role: 'user',
        content: request.message,
        created_at: createdAt,
      },
      assistant_message: {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: reply,
        created_at: createdAt,
      },
      run: { model_calls: 1, tool_rounds: 0, tool_calls: 0 },
    },
  });
}

test('你是谁和无商品关键词的追问都调用后端，并沿用会话', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  await page.route('**/api/chat', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, `后端回复：${body.message}`);
  });
  await page.goto('/');
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
  await page.route('**/api/chat', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, `后端回复：${body.message}`);
  });
  await page.goto('/');
  const log = page.getByRole('log', { name: '聊天记录' });

  await send(page, '推荐一款耳机');
  await expect(log.getByText('后端回复：推荐一款耳机')).toBeVisible();
  await send(page, '它有货吗');
  await expect(log.getByText('后端回复：它有货吗')).toBeVisible();

  expect(requests).toHaveLength(2);
  expect(requests[1].conversation_id).toBe(conversationId);
});

test('询问人工智能或真人身份时仍然调用后端', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  await page.route('**/api/chat', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, `后端回复：${body.message}`);
  });
  await page.goto('/');
  const log = page.getByRole('log', { name: '聊天记录' });

  await send(page, '你是人工智能吗');
  await expect(log.getByText('后端回复：你是人工智能吗')).toBeVisible();
  await send(page, '你是真人吗');
  await expect(log.getByText('后端回复：你是真人吗')).toBeVisible();
  await expect(page.getByRole('button', { name: '转人工', exact: true })).toBeEnabled();
  expect(requests).toHaveLength(2);
});

test('明确请求转人工时进入人工队列', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  await page.route('**/api/chat', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, '后端回复。');
  });
  await page.goto('/');
  await send(page, '请转人工客服');

  const log = page.getByRole('log', { name: '聊天记录' });
  await expect(log.getByText(/已进入人工服务队列/)).toBeVisible();
  expect(requests).toHaveLength(0);
});

test('查看模拟订单后询问你是谁，不会被待选订单状态拦截', async ({ page }) => {
  const requests: ChatRequestBody[] = [];
  await page.route('**/api/chat', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    await returnReply(route, '我是后端客服。');
  });
  await page.goto('/');
  const log = page.getByRole('log', { name: '聊天记录' });

  await send(page, '查一下我的订单');
  await expect(log.locator('.order-card')).toHaveCount(4);
  expect(requests).toHaveLength(0);
  await send(page, '你是谁');
  await expect(log.getByText('我是后端客服。')).toBeVisible();
  expect(requests).toHaveLength(1);
});

test('后端失败时显示错误，重试仍然调用后端', async ({ page }) => {
  let requestCount = 0;
  await page.route('**/api/chat', async (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await route.fulfill({
        status: 502,
        json: { detail: 'AI 客服暂时无法生成回复' },
      });
      return;
    }
    await returnReply(route, '重试后收到的后端回复。');
  });
  await page.goto('/');
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
  await page.route('**/api/chat', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    requests.push(body);
    if (requests.length === 2) {
      await route.fulfill({ status: 404, json: { detail: '未找到会话' } });
      return;
    }
    await returnReply(route, `后端回复：${body.message}`);
  });
  await page.goto('/');
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
  await page.route('**/api/chat', async (route) => {
    const body: ChatRequestBody = route.request().postDataJSON();
    if (body.message === '你是谁') {
      await responseGate;
      await returnReply(route, '已经停止的迟到回复。');
      return;
    }
    await returnReply(route, '新的后端回复。');
  });
  await page.goto('/');
  const log = page.getByRole('log', { name: '聊天记录' });

  const requestStarted = page.waitForRequest('**/api/chat');
  await send(page, '你是谁');
  await requestStarted;
  await page.getByRole('button', { name: '停止输出', exact: true }).click();
  releaseReply();
  await send(page, '你好');
  await expect(log.getByText('新的后端回复。')).toBeVisible();
  await expect(log.getByText('已经停止的迟到回复。')).toHaveCount(0);
});
