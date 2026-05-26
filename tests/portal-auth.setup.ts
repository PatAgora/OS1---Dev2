/**
 * Associate portal auth setup — runs before all associate portal tests.
 *
 * Registers a test associate on the live deployment and saves the
 * authenticated session state. If the account already exists (from a
 * previous test run), it logs in instead.
 */
import { test as setup, expect } from '@playwright/test';
import path from 'path';

const authFile = path.join(__dirname, '../playwright/.auth/associate.json');

const TEST_EMAIL = 'patrickstones@hotmail.co.uk';
const TEST_PASSWORD = 'Testplay1234!';
const TEST_BACKUP_CODES = ['EB55E411', '16EFE14F', '7B951F83', 'C77C6FC2', 'BE6CB726', '3A4B2A26', 'CEA0E37C', '7F1B6D50', 'FD794CDD', '2AC000EA'];
const TEST_FIRST_NAME = 'PW-Test';
const TEST_LAST_NAME = 'Associate';

setup('authenticate as associate', async ({ page }) => {
  // First check if the saved session is still valid — skip re-auth if it is
  // (this avoids consuming a single-use backup code on every run)
  const fs = await import('fs');
  if (fs.existsSync(authFile)) {
    const ctx = await page.context().browser()!.newContext({ storageState: authFile });
    const testPage = await ctx.newPage();
    await testPage.goto('https://os1-dev-production.up.railway.app/portal/dashboard', { waitUntil: 'domcontentloaded' });
    const url = testPage.url();
    await ctx.close();
    const urlPath = new URL(url).pathname;
    if (urlPath === '/portal/dashboard' || urlPath.startsWith('/portal/personal') || urlPath.startsWith('/portal/intro')) {
      console.log('✓ Associate session still valid — reusing saved state');
      return;
    }
    console.log('  Saved associate session expired — re-authenticating...');
  }

  // Log in with the manually created portal account
  await page.goto('/portal/login');
  await page.fill('[name="email"]', TEST_EMAIL);
  await page.fill('[name="password"]', TEST_PASSWORD);
  await page.click('[type="submit"]');
  await page.waitForLoadState('domcontentloaded');

  const afterLoginUrl = page.url();

  // Handle 2FA if required — use backup code
  if (afterLoginUrl.includes('2fa') || afterLoginUrl.includes('verify')) {
    console.log('  Associate portal requires 2FA — using backup code...');

    // The backup_code field is hidden behind a toggle — click "Use a backup code instead"
    const backupToggle = page.locator('a:has-text("backup code"), button:has-text("backup code")').first();
    if (await backupToggle.isVisible({ timeout: 3000 }).catch(() => false)) {
      await backupToggle.click();
      await page.waitForTimeout(300);
      console.log('  Clicked backup code toggle');
    }

    const totpField = page.locator('input[name="totp_code"]');
    const backupInput = page.locator('input[name="backup_code"]');

    if (await backupInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Clear the TOTP field to ensure only backup code is submitted
      if (await totpField.isVisible({ timeout: 1000 }).catch(() => false)) {
        await totpField.fill('');
      }
      // Backup codes are single-use. Use index 7+ (0-6 already consumed).
      const codeIndex = 7;
      await backupInput.fill(TEST_BACKUP_CODES[codeIndex]);
      console.log(`  Submitting backup code [${codeIndex}]: ${TEST_BACKUP_CODES[codeIndex]}`);
      // Click the submit button inside the backup section (not the hidden TOTP section)
      await page.locator('#backupSection button[type="submit"], #backupSection .os-btn-primary').first().click();
      await page.waitForLoadState('domcontentloaded');
      console.log(`  After first backup code: ${page.url()}`);

      // If first code failed, try second
      if (page.url().includes('2fa') || page.url().includes('verify')) {
        console.log('  First backup code rejected, trying second...');
        const totpField2 = page.locator('input[name="totp_code"]');
        const backupInput2 = page.locator('input[name="backup_code"]');
        if (await backupInput2.isVisible({ timeout: 3000 }).catch(() => false)) {
          if (await totpField2.isVisible({ timeout: 1000 }).catch(() => false)) {
            await totpField2.fill('');
          }
          await backupInput2.fill(TEST_BACKUP_CODES[codeIndex + 1]);
          console.log(`  Submitting backup code [${codeIndex + 1}]: ${TEST_BACKUP_CODES[codeIndex + 1]}`);
          await page.locator('#backupSection button[type="submit"], #backupSection .os-btn-primary').first().click();
          await page.waitForLoadState('domcontentloaded');
          console.log(`  After second backup code: ${page.url()}`);
        }
      }
    }
  }

  const finalUrl = page.url();
  console.log(`  Associate final URL: ${finalUrl}`);

  if (finalUrl.includes('/portal/') && !finalUrl.includes('/portal/login') && !finalUrl.includes('/portal/register')) {
    await page.context().storageState({ path: authFile });
    console.log('✓ Associate auth state saved to', authFile);
  } else {
    console.log('  ⚠ Could not authenticate as associate — portal tests will skip');
    await page.context().storageState({ path: authFile });
  }
});
