# Code Review

Reviewed commit `a71e1b6` (PR #4 merge) against its first parent. Review was static; tests were not run.

## Findings

### [P2] Validate simulator event probability

`SIM_EVENT_PROBABILITY` is parsed as an unrestricted float and passed to `GBMSimulator`. Values above `1` make every ticker trigger a 2–5% jump on every tick; negative values silently disable jumps, and `nan` is accepted and also disables them. This can make a valid-looking environment setting produce extreme or unexpected simulation behavior. Reject non-finite values and values outside `[0, 1]` in `factory.py`, falling back to the documented default.

`backend/app/market/factory.py:17` (`_env_float`) and `backend/app/market/factory.py:41` (use of `SIM_EVENT_PROBABILITY`)

## Scope and validation

Reviewed the market data source, Massive polling and EOD fallback, simulator, price cache and models, ticker validation, market data service, and SSE stream. No other actionable defects were identified in the reviewed diff. Tests were not run.
