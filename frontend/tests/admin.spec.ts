import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { products } from '../src/data';
import type { Product } from '../src/types';

let catalog: Product[];

async function mockAuth(page: Page) {
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
}

async function mockCatalog(page: Page) {
  catalog = products.map((product) => ({ ...product, specs: [...product.specs] }));

  await page.route('**/api/products**', async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const stockMatch = url.pathname.match(/\/products\/(p\d{2})\/stock$/);
    const detailMatch = url.pathname.match(/\/products\/(p\d{2})$/);

    if (stockMatch && method === 'PATCH') {
      const body = route.request().postDataJSON() as {
        operation: 'set' | 'add' | 'subtract';
        quantity: number;
      };
      const product = catalog.find((item) => item.id === stockMatch[1]);
      if (!product) {
        await route.fulfill({ status: 404, json: { detail: '未找到商品' } });
        return;
      }
      if (body.operation === 'subtract' && body.quantity > product.stock) {
        await route.fulfill({
          status: 409,
          json: { detail: `库存不足：当前 ${product.stock} 件` },
        });
        return;
      }
      product.stock =
        body.operation === 'set'
          ? body.quantity
          : body.operation === 'add'
            ? product.stock + body.quantity
            : product.stock - body.quantity;
      await route.fulfill({ json: product });
      return;
    }

    if (detailMatch) {
      const product = catalog.find((item) => item.id === detailMatch[1]);
      if (!product) {
        await route.fulfill({ status: 404, json: { detail: '未找到商品' } });
        return;
      }
      if (method === 'PATCH') {
        Object.assign(product, route.request().postDataJSON());
        await route.fulfill({ json: product });
        return;
      }
      if (method === 'DELETE') {
        catalog = catalog.filter((item) => item.id !== detailMatch[1]);
        await route.fulfill({ status: 204, body: '' });
        return;
      }
      await route.fulfill({ json: product });
      return;
    }

    if (method === 'POST') {
      const body = route.request().postDataJSON() as Omit<Product, 'id'>;
      const nextNumber =
        catalog.reduce((max, item) => Math.max(max, Number(item.id.slice(1))), 0) + 1;
      const created: Product = {
        ...body,
        id: `p${String(nextNumber).padStart(2, '0')}`,
      };
      catalog.push(created);
      await route.fulfill({ status: 201, json: created });
      return;
    }

    const offset = Number(url.searchParams.get('offset') || 0);
    const limit = Number(url.searchParams.get('limit') || 20);
    await route.fulfill({
      json: {
        items: catalog.slice(offset, offset + limit),
        total: catalog.length,
        offset,
        limit,
      },
    });
  });
}

async function login(page: Page, username: 'buyer_a' | 'staff' | 'admin' = 'admin') {
  await page.goto('/');
  const password =
    username === 'staff' || username === 'admin'
      ? `${username}-demo`
      : `${username.replace('_', '-')}-demo`;
  await page.getByLabel('账号').fill(username);
  await page.getByLabel('密码').fill(password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
}

async function openAdmin(page: Page) {
  await page.getByRole('link', { name: '商品管理' }).click();
  await expect(page.getByRole('heading', { name: '商品与库存管理' })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await mockAuth(page);
  await mockCatalog(page);
});

test('管理员进入商品管理，表格与统计正确', async ({ page }) => {
  await login(page);
  await openAdmin(page);
  await expect(page.locator('.admin-table tbody tr')).toHaveCount(12);
  const totalStock = products.reduce((sum, product) => sum + product.stock, 0);
  await expect(page.locator('.admin-stat').first()).toContainText('12');
  await expect(page.locator('.admin-stat').nth(1)).toContainText(String(totalStock));
});

test('商品超过单页上限时，管理页自动分页加载完整目录', async ({ page }) => {
  catalog = Array.from({ length: 55 }, (_, index) => {
    const template = products[index % products.length];
    return {
      ...template,
      id: `p${String(index + 1).padStart(2, '0')}`,
      name: `${template.name} ${index + 1}`,
      specs: [...template.specs],
    };
  });
  const listRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname.endsWith('/api/products')) {
      listRequests.push(url.search);
    }
  });

  await login(page);
  await openAdmin(page);

  await expect(page.locator('.admin-table tbody tr')).toHaveCount(55);
  expect([...new Set(listRequests)]).toEqual(['?offset=0&limit=50', '?offset=50&limit=50']);
});

test('管理员可以新增商品，编号自动分配', async ({ page }) => {
  await login(page);
  await openAdmin(page);
  await page.getByTestId('add-product').click();
  const dialog = page.getByRole('dialog', { name: '新增商品' });
  await dialog.getByLabel('商品名称').fill('测试新品耳机');
  await dialog.getByLabel('分类').fill('  键盘  ');
  await dialog.getByLabel('系列').fill('TestSeries');
  await dialog.getByLabel('价格（元）').fill('199');
  await dialog.getByLabel('库存（件）').fill('20');
  await dialog.getByLabel('规格（每行一条）').fill('规格一\n规格二');
  await dialog.getByLabel('商品描述').fill('这是一个测试商品');
  await dialog.getByLabel('颜色标识').fill('blue');
  await dialog.getByTestId('save-product').click();

  await expect(page.getByTestId('admin-row-p13')).toBeVisible();
  await expect(page.getByTestId('admin-row-p13')).toContainText('测试新品耳机');
  await expect(page.getByTestId('admin-row-p13')).toContainText('¥199');
  await expect(page.getByTestId('admin-notice')).toContainText('已新增商品 p13');
  await expect(page.locator('.admin-table tbody tr')).toHaveCount(13);
  await expect(page.getByTestId('admin-row-p13')).toContainText('键盘');
  await page.getByTestId('add-product').click();
  await expect(page.locator('#product-categories option[value="键盘"]')).toHaveCount(1);
});

test('管理员可以编辑商品字段', async ({ page }) => {
  let submittedPayload: Record<string, unknown> | undefined;
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'PATCH' && url.pathname.endsWith('/api/products/p01')) {
      submittedPayload = request.postDataJSON() as Record<string, unknown>;
    }
  });

  await login(page);
  await openAdmin(page);
  await page.getByTestId('admin-row-p01').getByTitle('编辑商品').click();
  const dialog = page.getByRole('dialog', { name: '编辑商品 p01' });
  await expect(dialog.getByLabel('库存（件）')).toBeDisabled();
  await expect(dialog).toContainText('库存请使用列表中的“库存调整”');
  await dialog.getByLabel('商品名称').fill('改名后的耳机');
  await dialog.getByLabel('价格（元）').fill('888');
  await dialog.getByTestId('save-product').click();

  const row = page.getByTestId('admin-row-p01');
  await expect(row).toContainText('改名后的耳机');
  await expect(row).toContainText('¥888');
  await expect(page.getByTestId('admin-notice')).toContainText('已保存 p01');
  expect(submittedPayload).not.toHaveProperty('stock');
});

test('管理员可以入库，出库超量会被拦截', async ({ page }) => {
  await login(page);
  await openAdmin(page);
  const initialStock = products.find((product) => product.id === 'p01')!.stock;

  await page.getByTestId('admin-row-p01').getByTitle('库存调整').click();
  let dialog = page.getByRole('dialog', { name: '库存调整 · p01' });
  await dialog.getByRole('button', { name: '入库 +' }).click();
  await dialog.getByTestId('stock-quantity').fill('10');
  await expect(dialog.locator('.admin-stock-preview')).toContainText(
    `调整后库存：${initialStock + 10} 件`,
  );
  await dialog.getByTestId('confirm-stock').click();
  await expect(page.getByTestId('admin-row-p01')).toContainText(String(initialStock + 10));

  await page.getByTestId('admin-row-p01').getByTitle('库存调整').click();
  dialog = page.getByRole('dialog', { name: '库存调整 · p01' });
  await dialog.getByRole('button', { name: '出库 −' }).click();
  await dialog.getByTestId('stock-quantity').fill('9999');
  await dialog.getByTestId('confirm-stock').click();
  await expect(dialog.locator('.admin-form-error')).toContainText('库存不足');
  await expect(page.getByTestId('admin-row-p01')).toContainText(String(initialStock + 10));
});

test('管理员可以删除商品', async ({ page }) => {
  await login(page);
  await openAdmin(page);
  await page.getByTestId('admin-row-p12').getByTitle('删除商品').click();
  const dialog = page.getByRole('dialog', { name: '删除商品' });
  await dialog.getByTestId('confirm-delete').click();
  await expect(page.getByTestId('admin-row-p12')).toHaveCount(0);
  await expect(page.getByTestId('admin-notice')).toContainText('已删除商品 p12');
  await expect(page.locator('.admin-table tbody tr')).toHaveCount(11);
});

test('客服看不到商品管理入口，直接访问地址会被重定向', async ({ page }) => {
  await login(page, 'staff');
  await expect(page.getByRole('link', { name: '商品管理' })).toHaveCount(0);

  await page.goto('/#admin');
  await expect(page.getByRole('heading', { name: '商品与库存管理' })).toHaveCount(0);
  await expect(page).toHaveURL(/#workbench/);
});

test('编辑商品可上传、预览、保存和移除图片', async ({ page }) => {
  const imagePath = '/api/products/images/' + 'a'.repeat(32) + '.png';
  await page.route('**/api/products/images', async (route) => {
    await route.fulfill({ status: 201, json: { image_url: imagePath } });
  });
  await page.route('**/api/products/images/*.png', async (route) => {
    await route.fulfill({
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHoQAAAAASUVORK5CYII=',
        'base64',
      ),
    });
  });
  await login(page);
  await openAdmin(page);
  await page.getByTestId('admin-row-p01').getByTitle('编辑商品').click();
  let dialog = page.getByRole('dialog', { name: '编辑商品 p01' });
  await dialog
    .getByLabel('商品图片')
    .setInputFiles({
      name: 'monitor.png',
      mimeType: 'image/png',
      buffer: Buffer.from('test-image'),
    });
  await expect(dialog.getByAltText('商品图片预览')).toBeVisible();
  await dialog.getByTestId('save-product').click();
  await expect(dialog).not.toBeVisible();
  expect(catalog[0].image_url).toBe(imagePath);
  await page.getByTestId('admin-row-p01').getByTitle('编辑商品').click();
  dialog = page.getByRole('dialog', { name: '编辑商品 p01' });
  await expect(dialog.getByAltText('商品图片预览')).toBeVisible();
  await dialog.getByRole('button', { name: '移除图片' }).click();
  await expect(dialog.getByAltText('商品图片预览')).toHaveCount(0);
  await dialog.getByTestId('save-product').click();
  await expect(dialog).not.toBeVisible();
  expect(catalog[0].image_url).toBe('');
});
