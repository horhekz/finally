import { expect, test } from '@playwright/test';
import { DEFAULT_TICKERS, openApp, resetApp, watchlistRows } from './helpers';

test.beforeEach(async ({ request }) => {
  await resetApp(request);
});

test('shows the default watchlist, $10k cash and a connected stream', async ({ page }) => {
  await openApp(page);

  await expect(page.getByTestId('header-cash')).toHaveText('$10,000.00');
  await expect(page.getByTestId('header-total-value')).toHaveText('$10,000.00');

  await expect(watchlistRows(page)).toHaveCount(DEFAULT_TICKERS.length);
  for (const ticker of DEFAULT_TICKERS) {
    await expect(page.getByTestId(`watchlist-row-${ticker}`)).toBeVisible();
  }

  await expect(page.getByTestId('positions-empty')).toBeVisible();
  await expect(page.getByTestId('chat-panel')).toBeVisible();
});

test('prices stream in and change over time', async ({ page }) => {
  await openApp(page);

  for (const ticker of DEFAULT_TICKERS) {
    await expect(page.getByTestId(`price-${ticker}`)).toHaveText(/\d/, { timeout: 20_000 });
  }

  const readPrices = () => Promise.all(DEFAULT_TICKERS.map((t) => page.getByTestId(`price-${t}`).textContent()));
  const initial = await readPrices();

  // At least one of the ten tickers must tick to a new displayed price.
  await expect
    .poll(async () => (await readPrices()).some((p, i) => p !== initial[i]), { timeout: 20_000 })
    .toBe(true);
});

test('connection status indicator reports connected', async ({ page }) => {
  await openApp(page);
  const status = page.getByTestId('connection-status');
  await expect(status).toBeVisible();
  await expect(status).toHaveAttribute('data-status', 'connected');
});

test('clicking a watchlist ticker selects it in the main chart', async ({ page }) => {
  await openApp(page);
  await page.getByTestId('watchlist-row-MSFT').click();
  await expect(page.getByTestId('main-chart')).toHaveAttribute('data-ticker', 'MSFT');
});
