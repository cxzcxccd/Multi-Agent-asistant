import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: true,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    ...devices['Desktop Chrome'],
    channel: 'chrome',
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'script-demo',
      testIgnore: 'backend-chat.spec.ts',
      use: { baseURL: 'http://127.0.0.1:5174' },
    },
    {
      name: 'backend-chat',
      testMatch: 'backend-chat.spec.ts',
      use: { baseURL: 'http://127.0.0.1:5175' },
    },
  ],
  // 两种模式分别启动测试服务器，避免复用开发页面时误用脚本模式。
  webServer: [
    {
      command: 'npx vite --host 127.0.0.1 --port 5174 --strictPort',
      env: { VITE_CHAT_MODE: 'script' },
      url: 'http://127.0.0.1:5174',
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: 'npx vite --host 127.0.0.1 --port 5175 --strictPort',
      env: {
        VITE_CHAT_MODE: 'backend',
        VITE_API_BASE_URL: 'http://127.0.0.1:5175/api',
      },
      url: 'http://127.0.0.1:5175',
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
