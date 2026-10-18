# Code Review

Reviewed commit `e7298d3` against parent `0adcc2f` on 2026-10-18. Static review only; tests and builds were not run.

## Findings

### [P2] Update the market-data implementation reference for the Polygon key alias

The factory now accepts `POLYGON_API_KEY` when `MASSIVE_API_KEY` is blank, and gives `MASSIVE_API_KEY` precedence when both are set. `planning/MARKET_DATA_DESIGN.md` still says the factory checks only `MASSIVE_API_KEY` in its overview, decision flow, copied factory implementation, and environment table. Following that reference would lead readers to expect the simulator when only `POLYGON_API_KEY` is configured, despite the new factory behavior.

`planning/MARKET_DATA_DESIGN.md:3,67,1114-1115,1133`

## Reviewed behavior

The factory trims both variables and correctly falls back from a blank Massive key to the Polygon alias. Snapshot polling now passes the SDK enum value (`"stocks"`). EOD polling retries after 60 seconds when a fetch raises or returns no grouped data, then waits 30 minutes after a successful nonempty fetch. No additional actionable issues were found in the changed implementation.

## Scope and validation

Reviewed the commit diff, including source selection, snapshot requests, EOD retry handling, tests, and related documentation. Tests and builds were not run.
