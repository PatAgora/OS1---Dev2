import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  retries: 0,
  workers: 1, // Serial execution — hitting a live DB, avoid race conditions
  fullyParallel: false,

  reporter: [['html'], ['list']],

  use: {
    baseURL: 'https://os1-dev-production.up.railway.app',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
  },

  projects: [
    // Auth setup — runs first, saves session state for staff tests
    {
      name: 'staff-auth-setup',
      testMatch: /auth\.setup\.ts/,
    },
    // Associate portal auth setup
    {
      name: 'portal-auth-setup',
      testMatch: /portal-auth\.setup\.ts/,
    },
    // Seed test data (runs after auth, before tests)
    {
      name: 'seed-data',
      testMatch: /seed-test-data\.setup\.ts/,
      dependencies: ['staff-auth-setup'],
      use: {
        storageState: 'playwright/.auth/admin.json',
      },
    },
    // Staff portal tests — Chromium (primary)
    {
      name: 'staff',
      testDir: './tests/staff',
      dependencies: ['staff-auth-setup', 'seed-data'],
      use: {
        storageState: 'playwright/.auth/admin.json',
      },
    },
    // Public portal tests (no auth needed)
    {
      name: 'public',
      testDir: './tests/public',
    },
    // Associate portal tests (use saved associate session)
    {
      name: 'associate',
      testDir: './tests/associate',
      dependencies: ['portal-auth-setup'],
      use: {
        storageState: 'playwright/.auth/associate.json',
      },
    },
    // AI tests (Gemini — included in main suite)
    {
      name: 'ai',
      testDir: './tests/ai',
      dependencies: ['staff-auth-setup'],
      use: {
        storageState: 'playwright/.auth/admin.json',
      },
    },

    // ================================================================
    // Cross-browser: Safari (WebKit)
    // ================================================================
    {
      name: 'webkit',
      testDir: './tests/staff',
      dependencies: ['staff-auth-setup'],
      use: {
        ...devices['Desktop Safari'],
        storageState: 'playwright/.auth/admin.json',
      },
    },
  ],
});
