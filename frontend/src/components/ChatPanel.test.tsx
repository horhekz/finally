import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { assistant } from "@/test/fixtures";
import type { ChatMessage } from "@/lib/types";
import { ChatPanel } from "./ChatPanel";

const user: ChatMessage = { id: "u1", role: "user", content: "buy apple", actions: null, created_at: "" };

function setup(props: Partial<Parameters<typeof ChatPanel>[0]> = {}) {
  const all = { messages: [], loading: false, open: true, onToggle: vi.fn(), onSend: vi.fn(), ...props };
  render(<ChatPanel {...all} />);
  return all;
}

describe("ChatPanel", () => {
  it("renders messages with roles and inline actions", () => {
    setup({
      messages: [
        user,
        assistant("Mock: buying 1 share of AAPL.", {
          trades: [
            { ticker: "AAPL", side: "buy", quantity: 1, status: "executed", price: 190.12 },
            { ticker: "TSLA", side: "sell", quantity: 5, status: "failed", error: "Insufficient shares" },
          ],
          watchlist_changes: [{ ticker: "PYPL", action: "add", status: "executed" }],
        }),
      ],
    });
    const msgs = screen.getAllByTestId("chat-message");
    expect(msgs.map((m) => m.getAttribute("data-role"))).toEqual(["user", "assistant"]);
    const actions = screen.getAllByTestId("chat-action");
    expect(actions).toHaveLength(3);
    expect(actions[0]).toHaveAttribute("data-status", "executed");
    expect(actions[0]).toHaveAttribute("data-kind", "trade");
    expect(actions[0]).toHaveTextContent("Buy 1 AAPL at 190.12");
    expect(actions[1]).toHaveAttribute("data-status", "failed");
    expect(actions[1]).toHaveTextContent("Insufficient shares");
    expect(actions[2]).toHaveAttribute("data-kind", "watchlist");
  });

  it("shows the loading indicator and blocks sending while waiting", () => {
    setup({ loading: true });
    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "hi" } });
    expect(screen.getByTestId("chat-send")).toBeDisabled();
  });

  it("hides the loading indicator when idle", () => {
    setup();
    expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument();
  });

  it("sends trimmed text and clears the input", () => {
    const props = setup();
    const input = screen.getByTestId("chat-input");
    fireEvent.change(input, { target: { value: "  hello  " } });
    fireEvent.click(screen.getByTestId("chat-send"));
    expect(props.onSend).toHaveBeenCalledWith("hello");
    expect(input).toHaveValue("");
  });

  it("collapses to just the toggle", () => {
    const props = setup({ open: false });
    expect(screen.getByTestId("chat-panel")).toHaveAttribute("data-open", "false");
    expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("chat-toggle"));
    expect(props.onToggle).toHaveBeenCalled();
  });
});
