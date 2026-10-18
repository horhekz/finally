import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { tick, watchItem } from "@/test/fixtures";
import type { PricePoint, PriceMap } from "@/lib/types";
import { FLASH_MS, Watchlist } from "./Watchlist";

function setup(prices: PriceMap = {}, overrides: Partial<Parameters<typeof Watchlist>[0]> = {}) {
  const props = {
    items: [watchItem("AAPL", 190), watchItem("PYPL")],
    prices,
    history: new Map<string, PricePoint[]>(),
    selected: "AAPL",
    onSelect: vi.fn(),
    onAdd: vi.fn(async () => null),
    onRemove: vi.fn(async () => null),
    ...overrides,
  };
  const utils = render(<Watchlist {...props} />);
  return { ...utils, props };
}

afterEach(() => vi.useRealTimers());

describe("Watchlist", () => {
  it("renders rows, a dash for missing prices, and sparklines", () => {
    setup({ AAPL: tick("AAPL", 191.5, { day_change_percent: 1.25 }) });
    expect(screen.getByTestId("watchlist-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("191.50");
    expect(screen.getByTestId("day-change-AAPL")).toHaveTextContent("+1.25%");
    expect(screen.getByTestId("price-PYPL")).toHaveTextContent("—");
    expect(screen.getByTestId("sparkline-PYPL")).toBeInTheDocument();
  });

  it("flashes green on an uptick and red on a downtick, then clears", () => {
    vi.useFakeTimers();
    const { rerender, props } = setup({ AAPL: tick("AAPL", 190) });
    const cell = () => screen.getByTestId("price-AAPL");
    expect(cell()).not.toHaveClass("flash-up");

    rerender(<Watchlist {...props} prices={{ AAPL: tick("AAPL", 191) }} />);
    expect(cell()).toHaveClass("flash-up");
    act(() => vi.advanceTimersByTime(FLASH_MS));
    expect(cell()).not.toHaveClass("flash-up");

    rerender(<Watchlist {...props} prices={{ AAPL: tick("AAPL", 189) }} />);
    expect(cell()).toHaveClass("flash-down");
    act(() => vi.advanceTimersByTime(FLASH_MS));
    expect(cell()).not.toHaveClass("flash-down");
  });

  it("adds a ticker, uppercasing input", async () => {
    const { props } = setup();
    fireEvent.change(screen.getByTestId("watchlist-add-input"), { target: { value: "nflx" } });
    fireEvent.click(screen.getByTestId("watchlist-add-button"));
    await waitFor(() => expect(props.onAdd).toHaveBeenCalledWith("NFLX"));
    await waitFor(() => expect(screen.getByTestId("watchlist-add-input")).toHaveValue(""));
  });

  it("shows the server error when adding fails", async () => {
    setup({}, { onAdd: vi.fn(async () => "Invalid ticker: 1BAD") });
    fireEvent.change(screen.getByTestId("watchlist-add-input"), { target: { value: "1bad" } });
    fireEvent.click(screen.getByTestId("watchlist-add-button"));
    expect(await screen.findByText("Invalid ticker: 1BAD")).toBeVisible();
    expect(screen.getByTestId("watchlist-error")).toHaveTextContent("Invalid ticker: 1BAD");
  });

  it("removes a ticker without selecting it", async () => {
    const { props } = setup();
    fireEvent.click(screen.getByTestId("watchlist-remove-PYPL"));
    await waitFor(() => expect(props.onRemove).toHaveBeenCalledWith("PYPL"));
    expect(props.onSelect).not.toHaveBeenCalled();
  });

  it("selects a ticker on click", () => {
    const { props } = setup();
    fireEvent.click(screen.getByTestId("watchlist-row-PYPL"));
    expect(props.onSelect).toHaveBeenCalledWith("PYPL");
  });
});
