import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

const log = (page: Page) => page.getByRole('log', { name: '聊天记录' });
async function send(page: Page, text: string) {
  await page.getByLabel('输入咨询内容').fill(text);
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect(page.getByRole('button', { name: '停止输出', exact: true })).toHaveCount(0);
}
async function navigate(page: Page, name: string) {
  if (await page.getByRole('button', { name: '打开导航' }).isVisible())
    await page.getByRole('button', { name: '打开导航' }).click();
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name }).click();
}
async function draft(page: Page) {
  await send(page, '订单 10002 耳机有一边没声音，想退货');
  await expect(page.getByRole('heading', { name: '请核对售后申请' })).toBeVisible();
}
async function submitDraft(page: Page) {
  await page.getByRole('button', { name: '确认申请内容', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '确认提交售后申请' });
  await expect(dialog.getByRole('button', { name: '提交申请', exact: true })).toBeDisabled();
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '提交申请', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(log(page).getByText('待人工审核', { exact: true }).last()).toBeVisible();
}
test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('商品筛选、来源、缺货和预算不足不会编造库存', async ({ page }) => {
  await send(page, '推荐 300 元以内的耳机');
  await expect(log(page).getByTestId('product-p01')).toBeVisible();
  await expect(log(page).getByTestId('product-p02')).toHaveCount(0);
  await log(page).getByText('参考了 3 份资料').click();
  await expect(log(page).getByText('KB-P01 · AirBeat Pro 降噪耳机说明书 v1.0')).toBeVisible();
  await send(page, '了解 AirBeat Lite 无线耳机');
  await expect(log(page).getByText(/当前暂时缺货，不能承诺补货时间/)).toBeVisible();
  await send(page, '推荐 50 元以内的耳机');
  await expect(
    log(page).getByText('当前没有找到符合这些条件的商品。可以调整预算或商品类别。'),
  ).toBeVisible();
});

test('兼容性追问保留当前商品并承认证据不足', async ({ page }) => {
  await send(page, 'LinkHub 6 合 1 扩展坞可以用在我的电脑上吗');
  await expect(log(page).getByText(/请告诉我设备的具体型号/)).toBeVisible();
  await send(page, 'MacBook Air M2');
  await expect(log(page).getByText(/还不能承诺完全兼容/)).toBeVisible();
});

test('订单选择、物流与无物流状态一致', async ({ page }) => {
  await send(page, '查一下我的订单');
  await expect(log(page).locator('.order-card')).toHaveCount(4);
  await log(page).getByRole('button', { name: '查看订单 10001', exact: true }).click();
  await expect(log(page).getByText(/订单 10001 当前运输中/)).toBeVisible();
  await expect(
    log(page).getByText('快件已到达杭州转运中心，正在发往目的站点。', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '停止输出' })).toHaveCount(0);
  await send(page, '查询订单 10004');
  await expect(log(page).getByText('尚无物流信息，商家正在准备发货。')).toBeVisible();
});

test('买家隔离：越权订单与不存在订单使用统一提示', async ({ page }) => {
  const denied = '未找到可查询的订单，请核对或联系人工。';
  await send(page, '查询订单 20001');
  await expect(log(page).getByText(denied, { exact: true })).toHaveCount(1);
  await expect(log(page).getByTestId('order-20001')).toHaveCount(0);
  await send(page, '查询订单 99999');
  await expect(log(page).getByText(denied, { exact: true })).toHaveCount(2);
  await page.getByLabel('切换模拟买家').selectOption('B');
  await expect(log(page).getByText(denied, { exact: true })).toHaveCount(0);
  await send(page, '查询订单 20001');
  await expect(log(page).getByTestId('order-20001')).toBeVisible();
  await send(page, '查询订单 10001');
  await expect(log(page).getByText(denied, { exact: true })).toBeVisible();
});

test('售后草稿需要补充原因，取消不会提交申请', async ({ page }) => {
  await send(page, '订单 10002 申请退货');
  await expect(log(page).getByText(/请具体说说遇到的问题/)).toBeVisible();
  await send(page, '有一边没声音');
  await expect(page.getByRole('heading', { name: '请核对售后申请' })).toBeVisible();
  await page.getByRole('button', { name: '取消草稿', exact: true }).click();
  await expect(log(page).getByText('申请草稿已取消，没有提交售后申请。')).toBeVisible();
  await navigate(page, '客服工作台');
  await page.getByRole('tab', { name: '售后审核' }).click();
  await expect(page.getByRole('heading', { name: '暂时没有售后申请' })).toBeVisible();
});

test('售后确认、审批及刷新恢复形成完整流程', async ({ page }) => {
  await draft(page);
  await page.getByLabel('问题描述').fill('左耳无声音，已更换设备重试，申请退货。');
  await submitDraft(page);
  await page.reload();
  await expect(log(page).getByText('待人工审核', { exact: true })).toBeVisible();
  await navigate(page, '客服工作台');
  await page.getByRole('tab', { name: /售后审核/ }).click();
  await page.getByRole('button', { name: '批准申请', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: '确认批准' })).toBeDisabled();
  await dialog.getByLabel('审核意见').fill('同意退货申请，请联系人工确认退回方式。');
  await dialog.getByRole('button', { name: '确认批准' }).click();
  await expect(page.getByRole('button', { name: '批准申请', exact: true })).toHaveCount(0);
  await navigate(page, '买家客服');
  await expect(log(page).getByText('已批准', { exact: true }).last()).toBeVisible();
  await expect(log(page).getByText('申请审核通过 · 未执行资金退款').last()).toBeVisible();
  await page.reload();
  await expect(log(page).getByText('同意退货申请，请联系人工确认退回方式。').last()).toBeVisible();
});

test('重复确认及再次申请不会重复建待审单，拒绝需原因', async ({ page }) => {
  await draft(page);
  await page.getByRole('button', { name: '确认申请内容' }).click();
  await page.getByRole('dialog').getByRole('checkbox').check();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: '提交申请', exact: true })
    .evaluate((el) => {
      (el as HTMLButtonElement).click();
      (el as HTMLButtonElement).click();
    });
  await send(page, '订单 10002 耳机坏了，我要退货');
  await expect(log(page).getByText('这笔订单已有待审核的售后申请，无需重复提交。')).toBeVisible();
  await navigate(page, '客服工作台');
  await page.getByRole('tab', { name: /售后审核/ }).click();
  await expect(page.locator('.approval-item')).toHaveCount(1);
  await page.getByRole('button', { name: '拒绝申请', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认拒绝' })).toBeDisabled();
  await page.getByLabel('审核意见').fill('问题信息尚不完整，请补充检测情况。');
  await page.getByRole('button', { name: '确认拒绝' }).click();
  await navigate(page, '买家客服');
  await expect(log(page).getByText('已拒绝', { exact: true }).last()).toBeVisible();
  await expect(
    log(page).getByText('问题信息尚不完整，请补充检测情况。', { exact: true }).last(),
  ).toBeVisible();
});

test('人工接管阻止延迟回复，恢复后才重新自动服务', async ({ page }) => {
  await page.getByLabel('输入咨询内容').fill('查询订单 10001');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect(page.getByRole('button', { name: '停止输出' })).toBeVisible();
  await page.getByRole('button', { name: '转人工', exact: true }).click();
  await navigate(page, '客服工作台');
  await page.getByRole('button', { name: '接管会话' }).click();
  await page.getByLabel('人工回复内容').fill('你好，我是小周，正在为你核对订单。');
  await page.getByRole('button', { name: '发送人工回复' }).click();
  await navigate(page, '买家客服');
  await expect(log(page).getByText('你好，我是小周，正在为你核对订单。')).toBeVisible();
  await send(page, '请继续帮我处理');
  await expect(log(page).getByTestId('order-10001')).toHaveCount(0);
  await navigate(page, '客服工作台');
  await page.getByRole('button', { name: '恢复 AI', exact: true }).click();
  await navigate(page, '买家客服');
  await send(page, '查询订单 10001');
  await expect(log(page).getByTestId('order-10001')).toBeVisible();
});

test('模拟工具失败无成功卡片，关闭故障后可以重试', async ({ page }) => {
  await navigate(page, '开发者与评测');
  await page.getByRole('tab', { name: '演示控制' }).click();
  await page.getByLabel('模拟查询模式').selectOption('tool-error');
  await navigate(page, '买家客服');
  await send(page, '查询订单 10001');
  await expect(log(page).getByText(/查询服务暂时不可用/)).toBeVisible();
  await expect(log(page).getByTestId('order-10001')).toHaveCount(0);
  await navigate(page, '开发者与评测');
  await page.getByRole('tab', { name: '演示控制' }).click();
  await page.getByLabel('模拟查询模式').selectOption('none');
  await navigate(page, '买家客服');
  await page.getByRole('button', { name: '重试查询' }).click();
  await expect(log(page).getByTestId('order-10001')).toBeVisible();
});

test('资料不足与执行事件如实标识，不展示虚构答案', async ({ page }) => {
  await navigate(page, '开发者与评测');
  await page.getByRole('tab', { name: '演示控制' }).click();
  await page.getByLabel('模拟查询模式').selectOption('no-knowledge');
  await navigate(page, '买家客服');
  await send(page, '了解 LinkHub 6 合 1 扩展坞');
  await expect(log(page).getByText(/没有找到足够依据/)).toBeVisible();
  await navigate(page, '开发者与评测');
  await page.getByRole('tab', { name: '执行记录' }).click();
  await expect(page.getByText('Retrieval · search_knowledge', { exact: true })).toBeVisible();
  await expect(page.getByText('未检索到支持该问题的资料', { exact: true })).toBeVisible();
});

test('停止和刷新中断的任务不会假装成功，可以重新查询', async ({ page }) => {
  await page.getByLabel('输入咨询内容').fill('查询订单 10001');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect(page.getByRole('button', { name: '停止输出' })).toBeVisible();
  await page.reload();
  await expect(log(page).getByText(/上次处理已中断/)).toBeVisible();
  await expect(log(page).getByTestId('order-10001')).toHaveCount(0);
  await page.getByRole('button', { name: '重新查询', exact: true }).click();
  await expect(log(page).getByTestId('order-10001')).toBeVisible();
  await expect(page.getByRole('button', { name: '停止输出' })).toHaveCount(0);
});

test('评测为示例值，重置需确认且恢复初始数据', async ({ page }) => {
  await send(page, '查询订单 10001');
  await navigate(page, '开发者与评测');
  await page.getByRole('tab', { name: '评测预览' }).click();
  await expect(page.getByText(/分数为虚构示例/)).toBeVisible();
  await page.getByRole('button', { name: '播放评测演示' }).click();
  await expect(page.getByText('演示播放完成。没有调用模型，也没有运行真实评测。')).toBeVisible();
  await page.getByRole('tab', { name: '演示控制' }).click();
  await page.getByRole('button', { name: '重置演示数据', exact: true }).click();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('button', { name: '重置演示数据', exact: true }).click();
  await page.getByRole('button', { name: '确认重置' }).click();
  await navigate(page, '买家客服');
  await expect(log(page).getByText(/订单 10001 当前运输中/)).toHaveCount(0);
  await expect(log(page).getByText('店内人气好物')).toBeVisible();
});

test('桌面和移动端核心页面无横向溢出与浏览器错误', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    for (const name of ['买家客服', '客服工作台', '开发者与评测']) {
      await navigate(page, name);
      await expect(page.locator('main')).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      await page.screenshot({
        path: `.local/screenshots/${name}-${viewport.width}.png`,
        fullPage: true,
        animations: 'disabled',
      });
    }
  }
  expect(errors).toEqual([]);
});

test('手机上完成售后提交与审批', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await draft(page);
  await submitDraft(page);
  await navigate(page, '客服工作台');
  await page.getByRole('tab', { name: /售后审核/ }).click();
  await page.getByRole('button', { name: '批准申请', exact: true }).click();
  await page.getByLabel('审核意见').fill('同意，后续由人工协助处理。');
  await page.getByRole('button', { name: '确认批准' }).click();
  await navigate(page, '买家客服');
  await expect(log(page).getByText('已批准', { exact: true }).last()).toBeVisible();
});

test('两个已打开的页面同步执行与人工接管，不误报中断', async ({ page, context }) => {
  await send(page, '店铺有哪些规则');
  const staff = await context.newPage();
  await staff.goto('/#workbench');
  await page.getByLabel('输入咨询内容').fill('查询订单 10001');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect(log(staff).getByText('查询订单 10001', { exact: true })).toBeVisible();
  await expect(log(staff).getByText(/上次处理已中断/)).toHaveCount(0);
  await staff.getByRole('button', { name: '接管会话' }).click();
  await expect(page.getByText('小周正在为你服务，AI 自动回复已暂停。')).toBeVisible();
  await staff.getByLabel('人工回复内容').fill('另一个页面的客服已接管。');
  await staff.getByRole('button', { name: '发送人工回复' }).click();
  await expect(log(page).getByText('另一个页面的客服已接管。')).toBeVisible();
  await expect(log(page).getByTestId('order-10001')).toHaveCount(0);
  await staff.close();
});

test('主动停止后不补发结果，新会话不继承旧上下文', async ({ page }) => {
  await page.getByLabel('输入咨询内容').fill('查询订单 10001');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await page.getByRole('button', { name: '停止输出' }).click();
  await send(page, '查询订单 10004');
  await expect(log(page).getByTestId('order-10001')).toHaveCount(0);
  await expect(log(page).getByTestId('order-10004')).toBeVisible();
  await page.getByRole('button', { name: '新建会话' }).click();
  await send(page, '我想申请售后');
  await expect(log(page).getByText(/你想为哪笔订单申请售后/)).toBeVisible();
  await expect(log(page).locator('.order-card')).toHaveCount(4);
});
