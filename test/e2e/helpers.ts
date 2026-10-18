import { expect, type APIRequestContext, type Page } from '@playwright/test';

export const DEFAULT_TICKERS = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META', 'JPM', 'V', 'NFLX'];

/** Reset the backend to a fresh seeded state (requires ENABLE_RESET=true). */
export async function resetApp(request: APIRequestContext): Promise<void> {
  const res = await request.post('/api/reset');
  expect(res.status(), `POST /api/reset failed: ${await res.text()}`).toBe(200);
}

/** Quantity held for a ticker per GET /api/portfolio (0 if no position). */
export async function apiQuantity(request: APIRequestContext, ticker: string): Promise<number> {
  const res = await request.get('/api/portfolio');
  expect(res.ok()).toBe(true);
  const body = (await res.json()) as { positions: { ticker: string; quantity: number }[] };
  return body.positions.find((p) => p.ticker === ticker)?.quantity ?? 0;
}

/** Parse "$10,000.00" / "-$12.34" into a number. */
export function parseMoney(text: string | null): number {
  const cleaned = (text ?? '').replace(/[^0-9.\-]/g, '');
  const value = Number.parseFloat(cleaned);
  if (Number.isNaN(value)) throw new Error(`Not a money value: "${text}"`);
  return value;
}

/** Open the app and wait until the SSE stream is connected. */
export async function openApp(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'connected', { timeout: 20_000 });
}

/** Wait until a ticker in the watchlist shows a real price (not the "—" placeholder). */
export async function waitForPrice(page: Page, ticker: string): Promise<void> {
  await expect(page.getByTestId(`price-${ticker}`)).toHaveText(/\d/, { timeout: 20_000 });
}

export async function trade(page: Page, ticker: string, quantity: number | string, side: 'buy' | 'sell'): Promise<void> {
  await page.getByTestId('trade-ticker').fill(ticker);
  await page.getByTestId('trade-quantity').fill(String(quantity));
  await page.getByTestId(side === 'buy' ? 'trade-buy' : 'trade-sell').click();
}

export async function cash(page: Page): Promise<number> {
  return parseMoney(await page.getByTestId('header-cash').textContent());
}

export async function sendChat(page: Page, text: string): Promise<void> {
  await page.getByTestId('chat-input').fill(text);
  await page.getByTestId('chat-send').click();
}

export const watchlistRows = (page: Page) =>
  page.getByTestId('watchlist').locator('[data-testid^="watchlist-row-"]');

export const assistantMessages = (page: Page) =>
  page.locator('[data-testid="chat-message"][data-role="assistant"]');
