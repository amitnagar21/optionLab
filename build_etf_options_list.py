"""
Build symbols_etf_options.txt = US-listed ETFs that are liquid AND have listed options.

Steps
  1. Download NASDAQ's official symbol directory (nasdaqtraded.txt) and keep ETFs
     (ETF flag = Y, not a test issue).
  2. Pull ~3 months of prices in batches and keep ETFs whose average daily
     dollar volume >= MIN_DOLLAR_VOLUME (options on illiquid ETFs are rarely usable).
  3. Ask Yahoo for each remaining ETF's option expirations; keep it only if
     it has at least one.

Output: symbols_etf_options.txt (one ticker per line), read by update_cycles.py.
Run:    python build_etf_options_list.py
"""
import io
import re
import time

import pandas as pd
import requests
import yfinance as yf

NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
OUT_FILE = "symbols_etf_options.txt"
MIN_DOLLAR_VOLUME = 10_000_000   # average $ traded per day
BATCH_SIZE = 100
SLEEP = 0.7                      # seconds between option-chain checks
SYMBOL_OK = re.compile(r"^[A-Z]{1,5}$")   # plain tickers only (skips warrants, units, etc.)


def parse_etf_symbols(text: str) -> list:
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str, keep_default_na=False)
    df = df[(df["ETF"] == "Y") & (df["Test Issue"] == "N")]
    syms = [s.strip().upper() for s in df["Symbol"]]
    return sorted({s for s in syms if SYMBOL_OK.match(s)})


def liquid_only(close: pd.DataFrame, volume: pd.DataFrame, min_dollar_vol: float) -> list:
    dollar_vol = (close * volume).mean()
    return sorted(dollar_vol[dollar_vol >= min_dollar_vol].index)


def get_liquid(symbols: list) -> list:
    keep = []
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i:i + BATCH_SIZE]
        print(f"Liquidity check {i + 1}-{i + len(batch)} of {len(symbols)}")
        try:
            data = yf.download(batch, period="3mo", interval="1d", auto_adjust=True,
                               progress=False, threads=True)
            if data is None or data.empty:
                continue
            close, volume = data["Close"], data["Volume"]
            if isinstance(close, pd.Series):          # single-ticker batch
                close, volume = close.to_frame(batch[0]), volume.to_frame(batch[0])
            keep += liquid_only(close, volume, MIN_DOLLAR_VOLUME)
        except Exception as e:
            print(f"  batch failed: {e}")
        time.sleep(2)
    return keep


def has_options(symbol: str) -> bool:
    for attempt in range(2):
        try:
            return len(yf.Ticker(symbol).options) > 0
        except Exception:
            time.sleep(2)
    return False


def main():
    r = requests.get(NASDAQ_TRADED_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    etfs = parse_etf_symbols(r.text)
    print(f"{len(etfs)} ETFs in NASDAQ directory")

    liquid = get_liquid(etfs)
    print(f"{len(liquid)} ETFs pass the liquidity filter (>= ${MIN_DOLLAR_VOLUME:,}/day)")

    optionable = []
    for n, s in enumerate(liquid, 1):
        if has_options(s):
            optionable.append(s)
        if n % 25 == 0:
            print(f"  options check {n}/{len(liquid)}  ({len(optionable)} optionable so far)")
        time.sleep(SLEEP)

    if len(optionable) < 20:
        raise SystemExit("Too few results - Yahoo probably blocked us. File not overwritten.")

    with open(OUT_FILE, "w") as f:
        f.write("\n".join(optionable) + "\n")
    print(f"Saved {len(optionable)} optionable ETFs -> {OUT_FILE}")


if __name__ == "__main__":
    main()
