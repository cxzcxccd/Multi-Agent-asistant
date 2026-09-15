import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

const shop = (page: Page) => page.locator('.shop-page');
async function openShop(page: Page) {
  if (await page.getByRole('button', { name: '打开导航' }).isVisible())
    await page.getByRole('button', { name: '打开导航' }).click();
  await page.getByRole('button', { name: '浏览数码旗舰店' }).click();
  await expect(page.getByRole('heading', { name: '极客优选数码旗舰店' })).toBeVisible();
}
test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('店铺入口展示完整陈列，分类价格库存筛选有效', async ({ page }) => {
  await openShop(page);
  await expect(shop(page).locator('.shop-product')).toHaveCount(12);
  await page
    .getByRole('navigation', { name: '商品分类' })
    .getByRole('button', { name: '耳机 4', exact: true })
    .click();
  await expect(shop(page).locator('.shop-product')).toHaveCount(4);
  await page.getByLabel('商品排序').selectOption('price-asc');
  await expect(shop(page).locator('.shop-price strong')).toHaveText(['159', '239', '299', '499']);
  await page.getByRole('checkbox', { name: '仅看有货' }).check();
  await expect(shop(page).locator('.shop-product')).toHaveCount(3);
  await page.getByLabel('商品价格区间').selectOption('under200');
  await expect(page.getByRole('heading', { name: '没有找到符合条件的商品' })).toBeVisible();
  await page.getByRole('button', { name: '查看全部商品', exact: true }).click();
  await expect(shop(page).locator('.shop-product')).toHaveCount(12);
  const response = await page.request.get('/images/catalog-products.png');
  expect(response.ok()).toBe(true);
  expect(response.headers()['content-type']).toContain('image/png');
});

test('搜索、详情与返回咨询沿用同一份商品数据', async ({ page }) => {
  await page.getByLabel('输入咨询内容').fill('我还想问一下配件');
  await openShop(page);
  await page.getByLabel('搜索店内商品').fill('65W');
  await page.getByRole('button', { name: '搜本店' }).click();
  await expect(shop(page).locator('.shop-product')).toHaveCount(1);
  await page.getByRole('button', { name: '查看 PowerMini 65W 氮化镓充电器 商品详情' }).click();
  const dialog = page.getByRole('dialog', { name: '商品详情' });
  await expect(dialog.getByText('当前库存 45 件', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: '咨询这件商品' }).click();
  await expect(page.getByLabel('输入咨询内容')).toHaveValue('我还想问一下配件');
  await expect(page.getByRole('log').getByTestId('product-p05')).toBeVisible();
  await expect(page.getByRole('log').getByText(/当前演示库存 45 件/)).toBeVisible();
});

test('客服逛店保留选中会话和草稿，引用商品不会自动发送', async ({ page }) => {
  await page
    .getByRole('navigation', { name: '主导航' })
    .getByRole('link', { name: '客服工作台' })
    .click();
  await page.locator('.conversation-item').filter({ hasText: '陈同学' }).click();
  await page.getByRole('button', { name: '接管会话', exact: true }).click();
  await page.getByLabel('人工回复内容').fill('您好，建议先看这款：');
  await openShop(page);
  await page.getByRole('button', { name: '查看 LinkHub 6 合 1 扩展坞 商品详情' }).click();
  await page.getByRole('button', { name: '引用到客服回复', exact: true }).click();
  await expect(page.getByRole('heading', { name: '陈同学的咨询' })).toBeVisible();
  await expect(page.getByLabel('人工回复内容')).toHaveValue(
    /您好，建议先看这款：\nLinkHub 6 合 1 扩展坞/,
  );
  await expect(page.getByRole('log').getByText(/您好，建议先看这款/)).toHaveCount(0);
  await page.getByRole('button', { name: '发送人工回复', exact: true }).click();
  await expect(page.getByRole('log').getByText(/您好，建议先看这款/)).toBeVisible();
});

test('浏览店铺再返回不丢失售后审核标签', async ({ page }) => {
  await page
    .getByRole('navigation', { name: '主导航' })
    .getByRole('link', { name: '客服工作台' })
    .click();
  await page.getByRole('tab', { name: '售后审核' }).click();
  await openShop(page);
  await page.getByRole('button', { name: '返回客服工作台' }).click();
  await expect(page.getByRole('tab', { name: '售后审核' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('移动端可以打开商品陈列与详情，没有横向溢出', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await openShop(page);
  await expect(shop(page).locator('.shop-product')).toHaveCount(12);
  await page
    .getByRole('navigation', { name: '商品分类' })
    .getByRole('button', { name: '扩展坞 4', exact: true })
    .click();
  await page.getByRole('button', { name: '查看 LinkHub 6 合 1 扩展坞 商品详情' }).click();
  await expect(
    page.getByRole('dialog').getByRole('heading', { name: 'LinkHub 6 合 1 扩展坞' }),
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({
    path: '.local/screenshots/shop-detail-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: '继续逛店', exact: true }).click();
  expect(errors).toEqual([]);
});
