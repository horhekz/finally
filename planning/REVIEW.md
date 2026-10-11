# Code review

Compared `market-data-migration` with `origin/main` at `341bbac` (six commits, through `0104d60`). The checked out `main` branch does not contain this implementation; it has a separate local merge commit. Review was static; tests were not rerun.

## Findings

### [P2] Validate simulator event probability bounds

`SIM_EVENT_PROBABILITY` is parsed as any floating-point value and passed directly to the simulator. Values above 1 make every ticker jump on every tick, while negative values silently disable events; `nan` is also accepted. This makes a syntactically valid but out-of-range environment setting change simulation behavior dramatically rather than falling back to the documented default. Reject non-finite values and values outside `[0, 1]` in `factory.py`.

`backend/app/market/factory.py:40`

## Scope notes

- Reviewed the market data source, cache, ticker service, stream, simulator, Massive client, tests, and related configuration/docs changes.
- The reported test and live-check results from the provided session transcript were not independently verified.
- No other actionable defects were identified in the reviewed diff.
