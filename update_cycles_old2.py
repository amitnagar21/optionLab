"""
Weekly updater for data/{SYMBOL}_cycles.csv

Normal run  : downloads the last ~1 year, builds cycle rows for every cycle
              length, and APPENDS only the rows that are not already in the
              existing CSV (key = Cycle_Days_Used + Start_Date).
Full rebuild: python update_cycles.py --full   (re-downloads "max" history and
              overwrites the file; also used automatically if the CSV is missing)

Only completed weeks are used: a week whose Friday is today or later is skipped,
so a half-finished week is never saved as a "Friday close".
"""
import os
import sys
import time

import pandas as pd
import yfinance as yf

SYMBOLS = list(dict.fromkeys([            # edit: tickers to keep updated (dupes removed)
    "SPY", "KORU", "EWY",
    "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK-B", "ORCL",
    "LLY", "JPM", "WMT", "V", "MA", "NFLX", "XOM", "COST", "JNJ", "HD",
    "PLTR", "PG", "BAC", "ABBV", "CVX", "KO", "AMD", "GE", "CSCO", "TMUS",
    "WFC", "CRM", "PM", "IBM", "UNH", "MS", "ABT", "GS", "LIN", "MCD",
    "AXP", "INTU", "DIS", "CAT", "RTX", "T", "NOW", "MRK", "UBER", "PEP",
    "VZ", "BKNG", "QCOM", "TMO", "SCHW", "ISRG", "C", "BA", "BLK", "AMGN",
    "SPGI", "TXN", "ADBE", "ACN", "AMAT", "NEE", "GILD", "DHR", "PGR", "BSX",
    "SYK", "ANET", "LRCX", "TJX", "HON", "UNP", "PFE", "DE", "APH", "KLAC",
    "COP", "CMCSA", "ADP", "LOW", "ETN", "MU", "ADI", "PANW", "MDT", "CB",
    "VRTX", "CRWD", "LMT", "SBUX", "KKR", "BX", "WELL", "CEG", "ICE", "CME",
]))
CYCLE_LENGTHS = [7, 14, 21, 28, 35, 42]   # must match your existing CSVs
OUT_DIR = "data"
FULL_PERIOD = "10y"                        # "max" = whole history (bigger files)
INCREMENTAL_PERIOD = "1y"                  # plenty: longest cycle is 6 weeks
KEY = ["Cycle_Days_Used", "Start_Date"]
SLEEP_BETWEEN_SYMBOLS = 1.0                # seconds, avoids Yahoo rate limits
DOWNLOAD_RETRIES = 3


def weekly_fridays(symbol: str, period: str) -> pd.DataFrame:
    """One close per completed calendar week (last valid trading day of the week)."""
    data = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            data = yf.download(symbol, period=period, interval="1d",
                               auto_adjust=True, progress=False)
        except Exception as e:
            print(f"  {symbol}: download error (attempt {attempt}): {e}")
            data = None
        if data is not None and not data.empty:
            break
        if attempt < DOWNLOAD_RETRIES:
            time.sleep(3 * attempt)  # back off: 3s, 6s
    if data is None or data.empty:
        return pd.DataFrame(columns=["Date", "Close"])
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    today = pd.Timestamp.today().normalize()
    records = []
    for label, group in data["Close"].resample("W-FRI"):
        if pd.Timestamp(label).tz_localize(None).normalize() >= today:
            continue  # week not finished yet
        valid = group.dropna()
        if valid.empty:
            continue
        records.append((valid.index[-1], float(valid.iloc[-1])))
    return pd.DataFrame(records, columns=["Date", "Close"])


def build_cycles(symbol: str, fridays: pd.DataFrame) -> pd.DataFrame:
    rows = []
    n = len(fridays)
    for cycle_days in CYCLE_LENGTHS:
        step = max(1, round(cycle_days / 7))
        for i in range(n - step):
            j = i + step
            sp, ep = fridays.at[i, "Close"], fridays.at[j, "Close"]
            rows.append({
                "Symbol": symbol,
                "Cycle_Days_Used": cycle_days,
                "Start_Date": fridays.at[i, "Date"].strftime("%Y-%m-%d"),
                "Start_Price_Close": round(sp, 4),
                "End_Date": fridays.at[j, "Date"].strftime("%Y-%m-%d"),
                "End_Price_Close": round(ep, 4),
                "Market_Ret": round(ep / sp - 1, 6),
            })
    return pd.DataFrame(rows)


def update_symbol(symbol: str, full: bool) -> None:
    path = os.path.join(OUT_DIR, f"{symbol}_cycles.csv")
    exists = os.path.exists(path)

    if full or not exists:
        fridays = weekly_fridays(symbol, FULL_PERIOD)
        if len(fridays) < 2:
            print(f"  {symbol}: not enough data, skipped")
            return
        out = build_cycles(symbol, fridays)
        out.to_csv(path, index=False)
        print(f"  {symbol}: full build, {len(out)} rows -> {path}")
        return

    fridays = weekly_fridays(symbol, INCREMENTAL_PERIOD)
    if len(fridays) < 2:
        print(f"  {symbol}: download returned too little data, file unchanged")
        return

    new = build_cycles(symbol, fridays)
    old = pd.read_csv(path, dtype={"Start_Date": str, "End_Date": str})

    is_new = ~pd.MultiIndex.from_frame(new[KEY]).isin(pd.MultiIndex.from_frame(old[KEY]))
    added = new[is_new]
    if added.empty:
        print(f"  {symbol}: already up to date")
        return

    out = (pd.concat([old, added], ignore_index=True)
             .sort_values(KEY, kind="stable")
             .reset_index(drop=True))
    out.to_csv(path, index=False)
    print(f"  {symbol}: +{len(added)} rows (latest end {added['End_Date'].max()}) -> {path}")


def main():
    full = "--full" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    failed = []
    total = len(SYMBOLS)
    for n, symbol in enumerate(SYMBOLS, 1):
        print(f"[{n}/{total}] Updating {symbol}...")
        try:
            update_symbol(symbol, full)
        except Exception as e:  # one bad ticker shouldn't stop the others
            print(f"  {symbol}: FAILED - {e}")
            failed.append(symbol)
        time.sleep(SLEEP_BETWEEN_SYMBOLS)

    print(f"\nDone. {total - len(failed)}/{total} symbols processed.")
    if failed:
        print("Failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
