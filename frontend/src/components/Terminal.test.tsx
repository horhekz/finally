import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockEventSource } from "@/test/mockEventSource";
import { assistant, emptyPortfolio, portfolioFixture, tick, watchItem } from "@/test/fixtures";
import type { Portfolio } from "@/lib/types";
import { Terminal } from "./Terminal";

type Handler = (init?: RequestInit) => { status?: number; body?: unknown };

function json(status: number, body: unknown) {
  return Promise.resolve(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

let routes: Record<string, Handler>;
let fetchMock: ReturnType<typeof vi.fn>;

function mockApi(portfolio: Portfolio = emptyPortfolio) {
  routes = {
    "GET /api/portfolio": () => ({ body: portfolio }),
    "GET /api/watchlist": () => ({ body: { tickers: [watchItem("AAPL", 190), watchItem("MSFT", 410)] } }),
    "GET /api/portfolio/history": () => ({
      body: { snapshots: [{ total_value: 10000, recorded_at: "2026-10-13T00:00:00Z" }] },
    }),
    "GET /api/chat/history": () => ({ body: { messages: [] } }),
  };
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const handler = routes[key];
    if (!handler) return json(404, { error: `no mock for ${key}` });
    const { status = 200, body } = handler(init);
    return json(status, body);
  });
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => mockApi());
afterEach(() => vi.unstubAllGlobals());

describe("Terminal", () => {
  it("loads the account and shows cash and total value", async () => {
    render(<Terminal />);
    await waitFor(() => expect(screen.getByTestId("header-total-value")).toHaveTextContent("$10,000.00"));
    expect(screen.getByTestId("header-cash")).toHaveTextContent("$10,000.00");
    expect(await screen.findByTestId("watchlist-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("positions-empty")).toBeInTheDocument();
    expect(screen.getByTestId("pnl-chart")).toHaveAttribute("data-points", "1");
    await waitFor(() => expect(screen.getByTestId("main-chart")).toHaveAttribute("data-ticker", "AAPL"));
    expect(screen.getByTestId("chat-panel")).toHaveAttribute("data-open", "true");
  });

  it("tracks the connection state of the price stream", async () => {
    render(<Terminal />);
    const status = screen.getByTestId("connection-status");
    expect(status).toHaveAttribute("data-status", "reconnecting");
    const es = MockEventSource.latest();
    expect(es.url).toBe("/api/stream/prices");

    act(() => es.open());
    expect(status).toHaveAttribute("data-status", "connected");
    act(() => es.fail());
    expect(status).toHaveAttribute("data-status", "reconnecting");
    act(() => es.fail(true));
    expect(status).toHaveAttribute("data-status", "disconnected");
  });

  it("re-opens the stream after the browser gives up", async () => {
    vi.useFakeTimers();
    try {
      render(<Terminal />);
      const first = MockEventSource.latest();
      act(() => first.fail(true));
      act(() => vi.advanceTimersByTime(3000));
      expect(MockEventSource.instances.length).toBe(2);
      act(() => MockEventSource.latest().open());
      expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");
    } finally {
      vi.useRealTimers();
    }
  });

  it("revalues the portfolio live from streamed prices", async () => {
    mockApi(portfolioFixture);
    render(<Terminal />);
    await waitFor(() => expect(screen.getByTestId("header-total-value")).toHaveTextContent("$10,000.00"));
    act(() => MockEventSource.latest().emit({ AAPL: tick("AAPL", 160), MSFT: tick("MSFT", 400) }));
    // 8000 cash + 10 * 160 + TSLA at cost (500)
    expect(screen.getByTestId("header-total-value")).toHaveTextContent("$10,100.00");
    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("160.00");
    const row = screen.getByTestId("position-row-AAPL");
    expect(within(row).getByText("+$100.00")).toBeInTheDocument();
    expect(screen.getByTestId("heatmap-tile-AAPL")).toHaveAttribute("data-pnl", "positive");
    expect(screen.getByTestId("heatmap-tile-TSLA")).toHaveAttribute("data-pnl", "flat");
  });

  it("executes a trade and shows server errors inline", async () => {
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    routes["POST /api/portfolio/trade"] = () => ({ body: { trade: {}, portfolio: portfolioFixture } });

    fireEvent.change(screen.getByTestId("trade-ticker"), { target: { value: "aapl" } });
    fireEvent.change(screen.getByTestId("trade-quantity"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByTestId("trade-buy"));
    await waitFor(() => expect(screen.getByTestId("header-cash")).toHaveTextContent("$8,000.00"));
    const call = fetchMock.mock.calls.find(([u]) => u === "/api/portfolio/trade")!;
    expect(JSON.parse(call[1].body)).toEqual({ ticker: "AAPL", quantity: 0.5, side: "buy" });

    routes["POST /api/portfolio/trade"] = () => ({ status: 400, body: { error: "Insufficient shares" } });
    fireEvent.change(screen.getByTestId("trade-quantity"), { target: { value: "999" } });
    fireEvent.click(screen.getByTestId("trade-sell"));
    await waitFor(() => expect(screen.getByTestId("trade-error")).toHaveTextContent("Insufficient shares"));
  });

  it("rejects a non-positive quantity before calling the API", async () => {
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    fireEvent.change(screen.getByTestId("trade-quantity"), { target: { value: "0" } });
    fireEvent.click(screen.getByTestId("trade-buy"));
    expect(screen.getByTestId("trade-error")).toHaveTextContent("greater than 0");
    expect(fetchMock.mock.calls.some(([u]) => u === "/api/portfolio/trade")).toBe(false);
  });

  it("removes a ticker from the watchlist", async () => {
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-MSFT");
    routes["DELETE /api/watchlist/MSFT"] = () => ({ status: 204 });
    routes["GET /api/watchlist"] = () => ({ body: { tickers: [watchItem("AAPL", 190)] } });
    fireEvent.click(screen.getByTestId("watchlist-remove-MSFT"));
    await waitFor(() => expect(screen.queryByTestId("watchlist-row-MSFT")).not.toBeInTheDocument());
  });

  it("sends a chat message, shows loading, then refreshes after actions", async () => {
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    let resolveChat!: () => void;
    const gate = new Promise<void>((r) => (resolveChat = r));
    routes["POST /api/chat"] = () => ({
      body: assistant("Mock: buying 1 share of AAPL.", {
        trades: [{ ticker: "AAPL", side: "buy", quantity: 1, status: "executed", price: 190 }],
        watchlist_changes: [],
      }),
    });
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      url === "/api/chat" ? gate.then(() => original(url, init)) : original(url, init),
    );

    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "buy apple" } });
    fireEvent.click(screen.getByTestId("chat-send"));
    expect(await screen.findByTestId("chat-loading")).toBeInTheDocument();
    const portfolioCalls = () => fetchMock.mock.calls.filter(([u]) => u === "/api/portfolio").length;
    const before = portfolioCalls();

    resolveChat();
    await waitFor(() => expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument());
    expect(screen.getAllByTestId("chat-message").map((m) => m.getAttribute("data-role"))).toEqual(["user", "assistant"]);
    expect(screen.getByTestId("chat-action")).toHaveAttribute("data-status", "executed");
    await waitFor(() => expect(portfolioCalls()).toBeGreaterThan(before));
  });
});
