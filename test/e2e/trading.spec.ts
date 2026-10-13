import { expect, test } from '@playwright/test';
import { apiQuantity, cash, openApp, resetApp, trade, waitForPrice } from './helpers';

test.beforeEach(async ({ request }) => {
  await resetApp(request);
});

test('buy shares: cash drops, position row and heatmap tile appear', async ({ page, request }) => {
  await openApp(page);
  await waitForPrice(page, 'AAPL');
  await expect(page.getByTestId('positions-empty')).toBeVisible();

  await trade(page, 'AAPL', 5, 'buy');

  await expect.poll(() => cash(page)).toBeLessThan(10_000);
  const row = page.getByTestId('position-row-AAPL');
  await expect(row).toBeVisible();
  await expect(row).toContainText('AAPL');
  expect(await apiQuantity(request, 'AAPL')).toBe(5);
  await expect(page.getByTestId('positions-empty')).toHaveCount(0);

  const tile = page.getByTestId('heatmap-tile-AAPL');
  await expect(tile).toBeVisible();
  await expect(tile).toHaveAttribute('data-pnl', /^(positive|negative|flat)$/);
  await expect(page.getByTestId('trade-error')).toBeHidden();
});

test('sell shares: position shrinks, then disappears when fully sold', async ({ page, request }) => {
  await openApp(page);
  await waitForPrice(page, 'MSFT');

  await trade(page, 'MSFT', 3, 'buy');
  const row = page.getByTestId('position-row-MSFT');
  await expect(row).toBeVisible();
  await expect.poll(() => cash(page)).toBeLessThan(10_000);
  const cashAfterBuy = await cash(page);

  await trade(page, 'MSFT', 1, 'sell');
  await expect.poll(() => cash(page)).toBeGreaterThan(cashAfterBuy);
  await expect(row).toBeVisible();
  await expect.poll(() => apiQuantity(request, 'MSFT')).toBe(2);

  await trade(page, 'MSFT', 2, 'sell');
  await expect(row).toHaveCount(0);
  await expect(page.getByTestId('heatmap-tile-MSFT')).toHaveCount(0);
  await expect(page.getByTestId('positions-empty')).toBeVisible();
});

test('selling shares you do not own shows trade-error and changes nothing', async ({ page }) => {
  await openApp(page);
  await waitForPrice(page, 'TSLA');

  await trade(page, 'TSLA', 1, 'sell');

  await expect(page.getByTestId('trade-error')).toBeVisible();
  await expect(page.getByTestId('trade-error')).not.toBeEmpty();
  await expect(page.getByTestId('header-cash')).toHaveText('$10,000.00');
  await expect(page.getByTestId('position-row-TSLA')).toHaveCount(0);
});

test('a buy with insufficient cash shows trade-error', async ({ page }) => {
  await openApp(page);
  await waitForPrice(page, 'NVDA');

  await trade(page, 'NVDA', 1_000_000, 'buy');

  await expect(page.getByTestId('trade-error')).toBeVisible();
  await expect(page.getByTestId('header-cash')).toHaveText('$10,000.00');
  await expect(page.getByTestId('position-row-NVDA')).toHaveCount(0);
});

test('P&L chart has data points and gains one after a trade', async ({ page }) => {
  await openApp(page);
  const chart = page.getByTestId('pnl-chart');
  await expect(chart).toBeVisible();

  const points = async () => Number((await chart.getAttribute('data-points')) ?? '0');
  // Reset records a snapshot, so the chart is never empty.
  await expect.poll(points).toBeGreaterThanOrEqual(1);
  const before = await points();

  await waitForPrice(page, 'AAPL');
  await trade(page, 'AAPL', 1, 'buy');
  await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
  await expect.poll(points).toBeGreaterThan(before);
});
