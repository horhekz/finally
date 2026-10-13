import { expect, test } from '@playwright/test';
import { DEFAULT_TICKERS, openApp, resetApp, waitForPrice, watchlistRows } from './helpers';

test.beforeEach(async ({ request }) => {
  await resetApp(request);
});

test('add a ticker to the watchlist and it starts streaming', async ({ page }) => {
  await openApp(page);
  await expect(watchlistRows(page)).toHaveCount(DEFAULT_TICKERS.length);

  await page.getByTestId('watchlist-add-input').fill('pypl');
  await page.getByTestId('watchlist-add-button').click();

  await expect(page.getByTestId('watchlist-row-PYPL')).toBeVisible();
  await expect(watchlistRows(page)).toHaveCount(DEFAULT_TICKERS.length + 1);
  await waitForPrice(page, 'PYPL');

  // Persisted server-side: survives a reload.
  await page.reload();
  await expect(page.getByTestId('watchlist-row-PYPL')).toBeVisible();
});

test('remove a ticker from the watchlist', async ({ page }) => {
  await openApp(page);
  await expect(page.getByTestId('watchlist-row-NFLX')).toBeVisible();

  await page.getByTestId('watchlist-remove-NFLX').click();

  await expect(page.getByTestId('watchlist-row-NFLX')).toHaveCount(0);
  await expect(watchlistRows(page)).toHaveCount(DEFAULT_TICKERS.length - 1);

  await page.reload();
  await expect(page.getByTestId('watchlist-row-AAPL')).toBeVisible();
  await expect(page.getByTestId('watchlist-row-NFLX')).toHaveCount(0);
});

test('an invalid ticker shows a watchlist error', async ({ page }) => {
  await openApp(page);
  await page.getByTestId('watchlist-add-input').fill('NOT A TICKER!');
  await page.getByTestId('watchlist-add-button').click();

  await expect(page.getByTestId('watchlist-error')).toBeVisible();
  await expect(watchlistRows(page)).toHaveCount(DEFAULT_TICKERS.length);
});
