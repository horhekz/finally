import { expect, test } from '@playwright/test';
import { assistantMessages, cash, openApp, resetApp, sendChat, waitForPrice } from './helpers';

// Mock responses (LLM_MOCK=true) are defined in planning/TEAM_CONTRACT.md §6.

test.beforeEach(async ({ request }) => {
  await resetApp(request);
});

test('mocked chat replies with the greeting', async ({ page }) => {
  await openApp(page);
  await sendChat(page, 'Hello there');

  await expect(page.locator('[data-testid="chat-message"][data-role="user"]').last()).toContainText('Hello there');
  await expect(assistantMessages(page).last()).toContainText("Mock: Hello! I'm FinAlly, your AI trading assistant.");
  await expect(page.getByTestId('chat-loading')).toHaveCount(0);
  await expect(page.getByTestId('chat-action')).toHaveCount(0);
});

test('a "buy" message executes a trade shown inline and the position appears', async ({ page }) => {
  await openApp(page);
  await waitForPrice(page, 'AAPL');

  await sendChat(page, 'Please buy some apple');

  await expect(assistantMessages(page).last()).toContainText('Mock: buying 1 share of AAPL.');
  const action = page.locator('[data-testid="chat-action"][data-kind="trade"]').last();
  await expect(action).toHaveAttribute('data-status', 'executed');
  await expect(action).toContainText('AAPL');

  await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
  await expect.poll(() => cash(page)).toBeLessThan(10_000);
});

test('a "sell" message with no shares shows a failed trade action', async ({ page }) => {
  await openApp(page);
  await sendChat(page, 'sell everything');

  await expect(assistantMessages(page).last()).toContainText('Mock: selling 1 share of AAPL.');
  const action = page.locator('[data-testid="chat-action"][data-kind="trade"]').last();
  await expect(action).toHaveAttribute('data-status', 'failed');
  await expect(page.getByTestId('header-cash')).toHaveText('$10,000.00');
});

test('an "add" message adds the ticker to the watchlist', async ({ page }) => {
  await openApp(page);
  await sendChat(page, 'add pypl');

  await expect(assistantMessages(page).last()).toContainText('Mock: adding PYPL to your watchlist.');
  const action = page.locator('[data-testid="chat-action"][data-kind="watchlist"]').last();
  await expect(action).toHaveAttribute('data-status', 'executed');
  await expect(page.getByTestId('watchlist-row-PYPL')).toBeVisible();
});

test('chat history survives a reload', async ({ page }) => {
  await openApp(page);
  await sendChat(page, 'Hello there');
  await expect(assistantMessages(page)).toHaveCount(1);

  await page.reload();
  await expect(assistantMessages(page).last()).toContainText('Mock: Hello!');
});
