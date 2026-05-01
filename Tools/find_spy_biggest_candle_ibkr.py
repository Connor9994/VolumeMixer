"""
find_spy_biggest_candle_ibkr.py

Finds the 1-minute candle with the **highest volume** for SPY.

Data source: Interactive Brokers (IBKR) via ib_insync.
IBKR provides up to ~1 year of 1-minute historical data per request,
and can be paginated to go back much further — far more than
Yahoo/TradingView free tiers.

Prerequisites:
  1. TWS or IB Gateway running with API enabled
     (File → Global Configuration → API → Enable)
  2. Market data subscriptions for SPY (or use paper trading account)
  3. Recommended: set socket port 7497 (TWS live) or 7496 (TWS paper)
     Default in this script is 7497 (live TWS).
"""

import os
import time
import pandas as pd
from datetime import datetime, timedelta, timezone
from ib_insync import IB, Stock, Contract

# ── Configuration ────────────────────────────────────────────────────────
HOST = "127.0.0.1"
PORT = 7496          # 7497 = TWS live,  7496 = TWS paper,  4002 = IB Gateway live
CLIENT_ID = 99

# ── Date range ───────────────────────────────────────────────────────────
# Set START_DATE and END_DATE to query a specific range.
# Leave both as None to fall back to MAX_CHUNKS days back from today.
START_DATE = "2026-03-01"  # e.g. "2026-03-31"
END_DATE   = "2026-03-31"  # e.g. "2026-04-30"
MAX_CHUNKS = 30    # fallback when START_DATE/END_DATE are None

# ── Disk cache ───────────────────────────────────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(__file__), "_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


# ── IBKR connection / data download ──────────────────────────────────────


def connect_ibkr() -> IB:
    """Connect to TWS / IB Gateway."""
    ib = IB()
    print(f"Connecting to TWS at {HOST}:{PORT} (clientId={CLIENT_ID}) …")
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
    server_time = ib.reqCurrentTime()
    print(f"  ✅ Connected. Server time: {server_time}")
    return ib


def make_spy_contract() -> Contract:
    """SPY stock contract on SmartExchange (IBKR routing)."""
    return Stock("SPY", "SMART", "USD")


def _cache_path(date_str: str) -> str:
    """Path to the cached CSV for a given trading date."""
    return os.path.join(CACHE_DIR, f"SPY_1m_{date_str}.csv")


_SKIP_FILE = os.path.join(CACHE_DIR, "_skip.txt")


def _load_skip_dates() -> set[str]:
    """Load dates that are known non-trading days (weekends/holidays)."""
    if not os.path.isfile(_SKIP_FILE):
        return set()
    with open(_SKIP_FILE) as _f:
        return {line.strip() for line in _f if line.strip()}


def _save_skip_date(date_str: str):
    """Append a date to the skip list so it's never queried again."""
    with open(_SKIP_FILE, "a") as _f:
        _f.write(date_str + "\n")


def _load_cached_dates() -> set[str]:
    """Return the set of trading dates already cached on disk."""
    cached = set()
    if not os.path.isdir(CACHE_DIR):
        return cached
    for fname in os.listdir(CACHE_DIR):
        if fname.startswith("SPY_1m_") and fname.endswith(".csv"):
            date_part = fname.replace("SPY_1m_", "").replace(".csv", "")
            cached.add(date_part)
    return cached


def download_spy_1m_ibkr(start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """
    Fetch SPY 1m data from IBKR, caching each day to disk.
    Previously cached days are loaded from disk — no re-query.

    Args:
        start_date:  Earliest date to include, e.g. "2026-04-01" (inclusive).
        end_date:    Latest   date to include, e.g. "2026-04-30" (inclusive).
                     If omitted, defaults to today.
                     If both omitted, falls back to MAX_CHUNKS days back.
    """
    cached_dates = _load_cached_dates()
    skip_dates = _load_skip_dates()
    print(f"  Found {len(cached_dates)} trading day(s) cached; {len(skip_dates)} day(s) in skip list.")

    all_frames: list[pd.DataFrame] = []
    collected_dates: set[str] = set()  # track unique trading days this run
    need_ibkr = False

    # ── Build date list ──────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    if start_date and end_date:
        s = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        e = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # Extend end by 14 calendar days so Next7d% has data (no N/A)
        e_pad = e + timedelta(days=14)
        date_list = [(s + timedelta(days=i)).strftime("%Y-%m-%d")
                     for i in range((e_pad - s).days + 1)]
        date_list.reverse()  # newest first (matching old behavior)
        print(f"  Date range: {start_date} → {end_date}  (+14 day pad for Next7d%)")
    else:
        # Fall back to MAX_CHUNKS days back from now
        date_list = [(now - timedelta(days=i)).strftime("%Y-%m-%d")
                     for i in range(MAX_CHUNKS)]

    # ── Walk through dates, load cache or mark for fetching ──
    pending: list[str] = []
    for date_str in date_list:

        # Skip if known non-trading day
        if date_str in skip_dates:
            continue

        cache_path = _cache_path(date_str)
        if os.path.isfile(cache_path):
            # ── Load from cache ───────────────────────────────
            chunk_df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            chunk_df.index = pd.to_datetime(chunk_df.index)
            if chunk_df.index.tz is not None:
                chunk_df.index = chunk_df.index.tz_localize(None)
            all_frames.append(chunk_df)
            collected_dates.add(date_str)
            continue

        # Not cached and not in skip list — queue for IBKR query
        pending.append(date_str)

    if pending:
        need_ibkr = True
        print(f"  {len(pending)} day(s) need fetching from IBKR (newest → oldest).")

    # ── Connect to TWS only if needed ────────────────────────────────
    if need_ibkr:
        ib = connect_ibkr()
        contract = make_spy_contract()
        ib.qualifyContracts(contract)
        print(f"  Contract: {contract.localSymbol} on {contract.exchange}")

        # Process newest first so endDateTime="" (now) works for the first
        pending.reverse()  # oldest first — IBKR walks backward from endDateTime
        for idx, target_date_str in enumerate(pending, 1):
            # Build endDateTime for the morning of this date
            target_dt = datetime.strptime(target_date_str, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
            end_dt = target_dt.strftime("%Y%m%d %H:%M:%S")
            print(f"\n  Fetch {idx}/{len(pending)}  ({target_date_str}) …")

            # Retry up to 2 times on timeout
            bars = None
            for attempt in range(1, 4):
                try:
                    bars = ib.reqHistoricalData(
                        contract,
                        endDateTime=end_dt,
                        durationStr="1 D",
                        barSizeSetting="1 min",
                        whatToShow="TRADES",
                        useRTH=True,
                        formatDate=1,
                        keepUpToDate=False,
                        timeout=120,
                    )
                    if bars:
                        break
                except Exception as e:
                    print(f"       Attempt {attempt} failed: {e}")
                    if attempt < 3:
                        print(f"       Retrying after 5s …")
                        time.sleep(5)
                    else:
                        bars = []

            if not bars:
                print(f"       (no data — likely weekend/holiday)")
                time.sleep(0.3)
                continue

            # Convert BarData list → DataFrame
            records = []
            for b in bars:
                records.append({
                    "date": b.date,
                    "Open": b.open,
                    "High": b.high,
                    "Low": b.low,
                    "Close": b.close,
                    "Volume": b.volume,
                })
            chunk_df = pd.DataFrame(records).set_index("date")
            chunk_df.index = pd.to_datetime(chunk_df.index)

            # Determine the actual trading day this data belongs to
            actual_date = str(chunk_df.index.min().date())

            if actual_date in collected_dates:
                print(f"       → {len(bars):,} bars  ({actual_date}) [already collected, skipping]")
                # Save the original requested date to the skip list too
                _save_skip_date(target_date_str)
                time.sleep(0.3)
                continue

            collected_dates.add(actual_date)

            # Strip timezone before saving so CSV round-trips cleanly
            chunk_df.index = chunk_df.index.tz_localize(None)

            # Save to cache
            cache_path = _cache_path(actual_date)
            chunk_df.to_csv(cache_path)
            all_frames.append(chunk_df)

            print(f"       → {len(bars):,} bars  ({actual_date}) [cached]")
            time.sleep(0.5)

        ib.disconnect()
        print(f"\n  Disconnected from TWS.")
    else:
        print(f"  All requested days are already cached — skipping IBKR query.")

    if not all_frames:
        raise RuntimeError("No data received from IBKR or cache.")

    combined = pd.concat(all_frames)
    combined.sort_index(inplace=True)

    # Ensure index is DatetimeIndex (all data is tz-naive US/Eastern)
    combined.index = pd.to_datetime(combined.index)

    print(f"\n  ✅  Total: {len(combined):,} unique 1m candles")
    print(f"      Date range: {combined.index.min().strftime('%Y-%m-%d')} → "
          f"{combined.index.max().strftime('%Y-%m-%d')}")
    print(f"      Trading days: {combined.index.normalize().nunique()}")
    return combined


# ── Analysis ─────────────────────────────────────────────────────────────


def add_buy_sell_volume(df: pd.DataFrame) -> pd.DataFrame:
    """Bulk-volume classification (Lee-Ready approx)."""
    df = df.copy()
    rng = df["High"] - df["Low"]
    rng_safe = rng.replace(0, float("nan"))
    df["BuyVol"] = (df["Volume"] * (df["Close"] - df["Low"]) / rng_safe).fillna(
        df["Volume"] * 0.5
    ).astype(float)
    df["SellVol"] = df["Volume"] - df["BuyVol"]
    df["BuyRatio"] = df["BuyVol"] / df["Volume"]
    return df


def add_next_7d_pct(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the % price change over the next 7 trading days for each candle."""
    df = df.copy()
    daily_close = df.resample("D")["Close"].last().dropna()
    daily_close.index = daily_close.index.normalize()
    close_map = dict(zip(daily_close.index, daily_close.values))
    dates_sorted = sorted(close_map.keys())

    # Build a lookup: date -> close 7 trading days later
    next7_map: dict = {}
    for i, d in enumerate(dates_sorted):
        idx = i + 7
        if idx < len(dates_sorted):
            later_close = close_map[dates_sorted[idx]]
            pct = (later_close - close_map[d]) / close_map[d] * 100
            next7_map[d] = pct
        else:
            next7_map[d] = float("nan")

    candle_dates = df.index.normalize()
    df["Next7d%"] = candle_dates.map(next7_map)
    return df


def format_candle(candle: pd.Series) -> str:
    dt = candle.name
    dt_str = dt.strftime("%Y-%m-%d %H:%M:%S %Z") if isinstance(dt, pd.Timestamp) else str(dt)
    hl_range = candle["High"] - candle["Low"]
    pct = (hl_range / candle["Open"]) * 100
    buy_vol = int(candle.get("BuyVol", 0))
    sell_vol = int(candle.get("SellVol", 0))
    buy_pct = candle.get("BuyRatio", 0.5) * 100
    nxt = candle.get("Next7d%")
    nxt_str = f"{nxt:+.2f}%" if pd.notna(nxt) else "N/A"
    return (
        f"Timestamp      : {dt_str}\n"
        f"Open / High / Low / Close : ${candle['Open']:.2f} / ${candle['High']:.2f} / "
        f"${candle['Low']:.2f} / ${candle['Close']:.2f}\n"
        f"Range (H-L)    : ${hl_range:.2f}  ({pct:.2f}%)\n"
        f"Volume         : {int(candle['Volume']):,}\n"
        f"Estimated Buy  : {buy_vol:,}  ({buy_pct:.0f}%)\n"
        f"Estimated Sell : {sell_vol:,}  ({100 - buy_pct:.0f}%)\n"
        f"Next 7 days    : {nxt_str}"
    )


def find_largest_by_volume(df: pd.DataFrame) -> pd.Series:
    return df.loc[df["Volume"].idxmax()]


def show_top_n_by_volume(df: pd.DataFrame, n: int = 10):
    top = df.nlargest(n, "Volume")
    print(f"\n{'=' * 100}")
    print(f"Top {n} 1-Minute SPY Candles by Volume")
    print(f"{'=' * 100}")
    hdr = (f"  {'#':<3} {'Timestamp':<24} {'Price Δ':>7} {'Range':>6}"
           f" {'Volume':>12} {'Buy Vol':>12} {'Sell Vol':>12}"
           f" {'Buy%':>5} {'Next7d%':>8}")
    print(hdr)
    print(f"  {'-'*3} {'-'*24} {'-'*7} {'-'*6} {'-'*12} {'-'*12} {'-'*12} {'-'*5} {'-'*8}")
    for i, (idx, row) in enumerate(top.iterrows(), 1):
        dt = idx
        ts = dt.strftime("%Y-%m-%d %H:%M") if isinstance(dt, pd.Timestamp) else str(dt)
        price_delta = row["Close"] - row["Open"]
        hl_range = row["High"] - row["Low"]
        buy_vol = int(row.get("BuyVol", 0))
        sell_vol = int(row.get("SellVol", 0))
        buy_pct = row.get("BuyRatio", 0.5) * 100
        nxt = row.get("Next7d%")
        nxt_str = f"{nxt:+.2f}%" if pd.notna(nxt) else "   N/A  "
        print(f"  {i:<3} {ts:<24} {price_delta:>+7.2f} {hl_range:>6.2f}"
              f" {int(row['Volume']):>12,} {buy_vol:>12,} {sell_vol:>12,}"
              f" {buy_pct:>4.0f}% {nxt_str:>8}")
    print()


def main():
    print("=" * 72)
    print("  SPY — Highest-Volume 1-Minute Candle (IBKR)")
    print("=" * 72)
    print()
    print("  Make sure TWS or IB Gateway is RUNNING with API enabled.")
    print(f"  Connecting to {HOST}:{PORT} (clientId={CLIENT_ID})")
    print()

    # ── 1. Download data from IBKR ────────────────────────────────
    df = download_spy_1m_ibkr(start_date=START_DATE, end_date=END_DATE)
    df.index = df.index.tz_localize("US/Eastern")
    df = add_buy_sell_volume(df)
    df = add_next_7d_pct(df)

    # Trim padded data back to original end_date (after Next7d% is computed)
    if END_DATE:
        _trim_cutoff = datetime.strptime(END_DATE, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        ).astimezone(df.index.tz)
        df = df[df.index <= _trim_cutoff]

    # ── 2. Find the biggest candle ────────────────────────────────
    biggest = find_largest_by_volume(df)

    print(f"\n{'=' * 72}")
    print("  🏆  HIGHEST-VOLUME 1-MINUTE CANDLE (IBKR DATA)")
    print(f"{'=' * 72}")
    print(format_candle(biggest))

    # ── 3. Context ────────────────────────────────────────────────
    dt = biggest.name
    window_start = dt - timedelta(minutes=5)
    window_end = dt + timedelta(minutes=5)
    nearby = df.loc[window_start:window_end]
    print(f"\n  Context (±5 minutes around the big candle):")
    for idx, row in nearby.iterrows():
        marker = " <<<<" if row.name == dt else ""
        buy_pct = row.get("BuyRatio", 0.5) * 100
        print(f"    {idx.strftime('%H:%M')}  "
              f"O:{row['Open']:.2f}  H:{row['High']:.2f}  "
              f"L:{row['Low']:.2f}  C:{row['Close']:.2f}  "
              f"Vol:{int(row['Volume']):,}  "
              f"B:{buy_pct:.0f}%/S:{100-buy_pct:.0f}%{marker}")

    # ── 4. Top N by volume ────────────────────────────────────────
    show_top_n_by_volume(df, 30)
    print(f"\n{'=' * 72}")
    print("  Summary Stats")
    print(f"{'=' * 72}")
    print(f"  Total candles            : {len(df):,}")
    print(f"  Trading days             : {df.index.normalize().nunique()}")
    print(f"  Date range               : {df.index.min().strftime('%Y-%m-%d')} → "
          f"{df.index.max().strftime('%Y-%m-%d')}")
    print(f"  Avg volume               : {df['Volume'].mean():,.0f}")
    print(f"  Median volume            : {df['Volume'].median():,.0f}")
    print(f"  Max volume               : {df['Volume'].max():,.0f}")
    print(f"  99th pctile volume       : {df['Volume'].quantile(0.99):,.0f}")
    print(f"  95th pctile volume       : {df['Volume'].quantile(0.95):,.0f}")
    print(f"  90th pctile volume       : {df['Volume'].quantile(0.90):,.0f}")
    print(f"{'=' * 72}\n")


if __name__ == "__main__":
    main()
