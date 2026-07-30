#!/usr/bin/env python3
"""
generate.py — mock OHLCV market-data generator.

Design:
  - First run for a ticker: backfills `seed_days` of daily bars via a
    random walk (drift + volatility), so the dashboard has a real trend
    and enough history for a moving average on day one.
  - Every run (including the first): appends exactly ONE new "live" bar
    per ticker, continuing the random walk from the last stored close.
    This is what the scheduler (GitHub Actions / cron) triggers, and
    it's what makes "click refresh, watch new data appear" actually work
    on demand instead of only once per calendar day.
  - Rows are tagged source='seed' or source='live' so you can always
    tell backfilled history apart from data the scheduler actually
    produced during a run.

Usage:
    python generate.py [--config config.yaml]
"""

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def ensure_table(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'live'
        )
    """)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_ticker_ts ON {table}(ticker, timestamp)")
    conn.commit()


def make_ohlcv(prev_close: float, rng: np.random.Generator, drift: float, vol: float) -> dict:
    """One random-walk OHLCV bar derived from the previous close."""
    open_price = prev_close * (1 + rng.normal(0, vol * 0.3))
    ret = rng.normal(drift, vol)
    close_price = max(open_price * (1 + ret), 0.50)

    hi_wick = rng.uniform(0, vol * 0.6)
    lo_wick = rng.uniform(0, vol * 0.6)
    high_price = max(open_price, close_price) * (1 + hi_wick)
    low_price = max(min(open_price, close_price) * (1 - lo_wick), 0.10)

    base_volume = rng.lognormal(mean=14.0, sigma=0.5)  # roughly hundreds of thousands to low millions
    volume = int(base_volume * (1 + abs(ret) * 10))     # bigger moves -> more volume

    return {
        "open": round(open_price, 2),
        "high": round(high_price, 2),
        "low": round(low_price, 2),
        "close": round(close_price, 2),
        "volume": volume,
    }


def get_last_close(conn: sqlite3.Connection, table: str, ticker: str):
    cur = conn.execute(
        f"SELECT close FROM {table} WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        (ticker,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def seed_history(conn, table, ticker, cfg, rng) -> tuple[int, float]:
    """Backfill seed_days of daily bars ending yesterday. Returns (rows_written, last_close)."""
    gen = cfg["generator"]
    days = gen["seed_days"]
    lo, hi = gen["starting_price_range"]
    prev_close = float(rng.uniform(lo, hi))

    end_date = datetime.now(timezone.utc).date() - timedelta(days=1)
    start_date = end_date - timedelta(days=days - 1)

    rows = []
    d = start_date
    while d <= end_date:
        if d.weekday() < 5:  # weekdays only, like real trading calendars
            bar = make_ohlcv(prev_close, rng, gen["daily_drift"], gen["daily_volatility"])
            ts = datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=timezone.utc)  # ~market close
            rows.append({"ticker": ticker, "timestamp": ts.isoformat(), **bar, "source": "seed"})
            prev_close = bar["close"]
        d += timedelta(days=1)

    if rows:
        pd.DataFrame(rows).to_sql(table, conn, if_exists="append", index=False)
    return len(rows), prev_close


def append_live_bar(conn, table, ticker, cfg, rng, last_close: float) -> dict:
    """Append exactly one new bar continuing from last_close, timestamped now(). Always runs."""
    gen = cfg["generator"]
    bar = make_ohlcv(last_close, rng, gen["daily_drift"], gen["daily_volatility"])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    row = {"ticker": ticker, "timestamp": now.isoformat(), **bar, "source": "live"}
    conn.execute(
        f"INSERT INTO {table} (ticker, timestamp, open, high, low, close, volume, source) "
        f"VALUES (:ticker, :timestamp, :open, :high, :low, :close, :volume, :source)",
        row,
    )
    return row


def main():
    parser = argparse.ArgumentParser(description="Generate mock OHLCV market data.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    tickers = cfg["tickers"]
    storage = cfg["storage"]
    db_path = Path(storage["path"])
    table = storage["table"]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    ensure_table(conn, table)

    # Fixed seed -> the historical backfill looks the same for anyone who
    # clones the repo and runs this for the first time (reproducible demo).
    seed_rng = np.random.default_rng(cfg["generator"]["random_seed"])
    # No fixed seed -> every scheduled run produces a genuinely new move,
    # so re-running actually shows new numbers (the point of the demo).
    live_rng = np.random.default_rng()

    print(f"refresh_interval (informational, controlled by the scheduler): {cfg['refresh_interval']}")

    for ticker in tickers:
        last_close = get_last_close(conn, table, ticker)

        if last_close is None:
            n, last_close = seed_history(conn, table, ticker, cfg, seed_rng)
            print(f"  {ticker}: seeded {n} historical daily bars")

        bar = append_live_bar(conn, table, ticker, cfg, live_rng, last_close)
        pct = (bar["close"] - last_close) / last_close * 100
        print(f"  {ticker}: +1 live bar @ {bar['timestamp']}  close={bar['close']:<8} ({pct:+.2f}%)  vol={bar['volume']:,}")

    conn.commit()
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    print(f"\nDone. {db_path} now has {total} total rows across {len(tickers)} tickers.")


if __name__ == "__main__":
    main()
