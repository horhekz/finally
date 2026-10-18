import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { MockEventSource } from "./mockEventSource";

// jsdom has no canvas; charts are exercised in the browser, not here.
vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn(), update: vi.fn() };
  const chart = {
    addSeries: vi.fn(() => series),
    applyOptions: vi.fn(),
    remove: vi.fn(),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
  };
  return {
    createChart: vi.fn(() => chart),
    AreaSeries: "Area",
    ColorType: { Solid: "solid" },
    LineStyle: { Dashed: 2 },
  };
});

// Re-stubbed per test because suites may call vi.unstubAllGlobals().
beforeEach(() => {
  vi.stubGlobal("EventSource", MockEventSource);
});

afterEach(() => {
  cleanup();
  MockEventSource.reset();
});
