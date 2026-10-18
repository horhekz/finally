# Code Review

Reviewed the working tree against `HEAD` (`6ef2dc6`) on 2026-10-13. Static review only; tests and Docker builds were not run.

## Findings

### [P1] Include frontend library source in version control

The top-level `.gitignore` pattern `lib/` matches directories named `lib` at any depth. It ignores `frontend/src/lib/`, which contains modules imported throughout the frontend (`api`, chart helpers, types, portfolio calculations, and treemap layout). Those files are present in this working tree but will be absent from a fresh checkout, so the frontend build fails. Anchor the Python packaging ignore pattern (for example, `/lib/`) or explicitly unignore `frontend/src/lib/` and its contents.

`.gitignore:17`; `frontend/src/lib/`

### [P2] Reject non-finite trade inputs and computed values

`apply_trade` only checks `quantity > 0` and `price > 0`; positive infinity passes both checks. Infinite values, or finite quantity and price whose product overflows, can be written into cash, positions, and the trade log. Reject non-finite quantity, price, and notional before updating account state.

`backend/app/db/repository.py:125-149`

### [P2] Return failure when startup health checks time out

Both startup scripts end their 30-second health-check loops by printing that FinAlly is running, even if no health check succeeded. A failed startup or port conflict therefore returns success and may open a broken page. Track success and exit nonzero with a useful diagnostic when the deadline expires.

`scripts/start_mac.sh:58-69`; `scripts/start_windows.ps1:57-69`

## Scope and validation

Reviewed the new API, database, LLM and service code, frontend, deployment files, tests, and project configuration. No other actionable findings were identified. Tests and Docker builds were not run.
