# Auto-Refreshing Finance Dashboard

A small end-to-end BI pipeline built as a portfolio piece: a data generator that
runs on a schedule, feeding a Power BI dashboard. It demonstrates pipeline
design, scheduling/automation as code, and BI tool proficiency — not a trading
tool, and not dependent on a live market data feed.

```
generator (Python, random walk)
        |
        v
  SQLite file  <---- GitHub Actions cron (the "auto-refresh" scheduler)
        |
        v
   Power BI Desktop (Get Data -> Python script)
        |
        v
  price trend / % change / volume / moving average visuals
```

## Repo structure

```
finance-dashboard-pipeline/
├── generate.py
├── config.yaml
├── requirements.txt
├── .gitignore
├── data/
│   └── market_data.db          # created automatically on first run
└── .github/
    └── workflows/
        └── generate.yml
```

## Quickstart

```bash
cd finance-dashboard-pipeline
pip install -r requirements.txt
python generate.py
```

First run backfills ~180 days of daily history per ticker, then appends one
"live" bar. Run it again and you'll see one new row per ticker land
immediately — that's the behavior the scheduler will trigger automatically.

## How the data is generated

`generate.py` uses a random walk (small daily drift + volatility, seeded from
`config.yaml`) rather than pure noise, so price series look like plausible
stock behavior — trends, drawdowns, volatility clustering-ish movement — without
depending on any real market data source.

Each row is tagged `source = 'seed'` (backfilled history) or `source = 'live'`
(written during an actual generator run), so you can always point at the data
and show exactly which rows the scheduler produced.

Table `ohlcv` in `data/market_data.db`:

| column    | type | notes                                  |
|-----------|------|-----------------------------------------|
| id        | INTEGER | autoincrement primary key            |
| ticker    | TEXT | e.g. AAPL                               |
| timestamp | TEXT | ISO-8601 UTC                            |
| open, high, low, close | REAL | rounded to cents             |
| volume    | INTEGER | loosely scaled with move size         |
| source    | TEXT | 'seed' or 'live'                        |

## Config (`config.yaml`)

```yaml
refresh_interval: 1h   # 1h, 6h, or 24h -- must match the cron in generate.yml
tickers: [AAPL, MSFT, GOOGL, AMZN, TSLA, NVDA, META]
generator:
  seed_days: 180
  daily_drift: 0.0003
  daily_volatility: 0.018
  random_seed: 42       # fixed -> reproducible historical backfill
```

`refresh_interval` is informational (printed to the log) — the thing that
actually controls cadence is the `cron` line in the GitHub Actions workflow.
Keep the two in sync when you change one.

## Scheduling: GitHub Actions

`.github/workflows/generate.yml` runs `generate.py` on a cron schedule and
commits the updated `market_data.db` back into the repo, so every refresh is a
visible commit — that's the "automation as code" angle, and why GitHub Actions
was chosen over a local cron job / Task Scheduler entry (nothing to configure
on a specific machine, and the schedule lives in version control next to the
code it runs).

Default schedule: `0 * * * *` (hourly, matching `refresh_interval: 1h`).
To change cadence:

| refresh_interval | cron            |
|-------------------|-----------------|
| 1h                | `0 * * * *`     |
| 6h                | `0 */6 * * *`   |
| 24h               | `0 0 * * *`     |

The workflow also has a `workflow_dispatch` trigger — a manual "Run workflow"
button in the Actions tab. That's the one to use live in a demo instead of
waiting for the cron.

## Power BI Desktop setup

Power BI Desktop has no built-in native SQLite connector. The simplest route
that avoids installing an ODBC driver:

1. Get Data → More → Other → **Python script**
2. Paste:
   ```python
   import sqlite3, pandas as pd
   conn = sqlite3.connect(r"C:\path\to\finance-dashboard-pipeline\data\market_data.db")
   df = pd.read_sql_query("SELECT * FROM ohlcv", conn)
   ```
3. Power BI surfaces `df` as a table — load it, set `timestamp` to Date/Time
   type in Power Query.

(Alternative: install the community SQLite ODBC driver and connect via
Get Data → ODBC if you'd rather not depend on a Python script step — either
is a legitimate answer if asked, but the script route needs zero extra
install.)

### Suggested visuals

- **Price trend line** — line chart, axis = `timestamp`, values = `close`,
  legend = `ticker`.
- **Daily % change** — DAX measure comparing each row to the previous one per
  ticker:
  ```
  Pct Change =
  VAR PrevClose =
      CALCULATE(
          MAX(ohlcv[close]),
          FILTER(
              ALLEXCEPT(ohlcv, ohlcv[ticker]),
              ohlcv[timestamp] < MAX(ohlcv[timestamp])
          )
      )
  RETURN DIVIDE(MAX(ohlcv[close]) - PrevClose, PrevClose)
  ```
  Format as a KPI card or conditional-formatted table, one row per ticker.
- **Volume bar chart** — clustered column, axis = `timestamp`, values =
  `volume`.
- **Moving average overlay** — add as a second line on the price chart:
  ```
  7-Day MA =
  AVERAGEX(
      TOPN(7, FILTER(ALLEXCEPT(ohlcv, ohlcv[ticker]), ohlcv[timestamp] <= MAX(ohlcv[timestamp])), ohlcv[timestamp], DESC),
      ohlcv[close]
  )
  ```
- Add a ticker slicer and a date-range slicer so the deck reads as an actual
  filterable dashboard rather than a fixed chart.

Style: pick one accent color per ticker, a clean/minimal Power BI theme, and a
title text box explaining the refresh cadence, e.g. "Auto-refreshes hourly via
GitHub Actions."

## Demo script (phase 4)

1. Open the dashboard, walk through the four visuals.
2. Go to the repo's Actions tab, click **Run workflow** on `generate.yml`,
   let it finish (~30 sec).
3. Back in Power BI Desktop, hit **Refresh**. New rows appear — that's the
   "click Refresh, watch new data appear" moment.

**30-second architecture explanation:** "A Python script generates realistic
OHLCV data with a random walk instead of pure noise, so the trends look real.
GitHub Actions runs it on a schedule and commits the results to a SQLite file
that's the pipeline's single source of truth — that's the automation-as-code
piece, done outside the BI tool rather than fought for inside its refresh
limits. Power BI just reads whatever's in that file and re-renders it."

**Why mock data, not live data:** a demo shouldn't depend on a fragile,
unofficial scraping API being up at the exact moment someone's watching. Mock
data means the demo is reliable and repeatable, and it isolates the thing
actually being demonstrated — the pipeline and scheduling — from an external
dependency that has nothing to do with either.

**"How would this work with real data?"** — swap `generate.py`'s random walk
for `yfinance` (or a paid data vendor) calls, same schema; point the schedule
at Power BI Service's scheduled refresh (or Premium capacity for sub-hourly
cadence) via an on-premises data gateway if the source stays local; and swap
SQLite for a real database (Postgres, etc.) once more than one process needs
to read/write concurrently.

## Not implemented (documented as future work, per the brief's stretch goals)

- A `yfinance` toggle to blend in one real ticker as a "bonus realism" layer.
- Simple alert logic (flag any bar with a >5% daily move) as a visual
  indicator in the dashboard.

Both are natural next additions and worth mentioning if asked what you'd do
next, but weren't built into this pass.
