import json
import os
import re
from functools import lru_cache

import pandas as pd

# Only allow plain ticker-like names, since `symbol` becomes part of a file path
SYMBOL_RE = re.compile(r"^[A-Za-z0-9._-]{1,20}$")


# ─────────────────────────────────────────────────────────────────────────────
#  DATA LOADER  (reads data/{symbol}_cycles.csv)
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=32)
def _read_csv_cached(path, mtime):
    # mtime is part of the cache key, so a changed CSV is re-read automatically
    return pd.read_csv(path, parse_dates=["Start_Date", "End_Date"])


def load_cycles(symbol, cycle_days, years, data_dir="data"):
    if not SYMBOL_RE.match(symbol):
        raise ValueError("Invalid symbol.")

    path = os.path.join(data_dir, f"{symbol}_cycles.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No data file for symbol '{symbol}'.")

    df = _read_csv_cached(path, os.path.getmtime(path)).copy()

    available = sorted(int(x) for x in df["Cycle_Days_Used"].unique())
    df = df[df["Cycle_Days_Used"] == cycle_days]
    if df.empty:
        raise ValueError(f"No rows for cycle_days={cycle_days}. Available: {available}")

    if years:
        cutoff = df["End_Date"].max() - pd.Timedelta(days=int(years * 365.25))
        df = df[df["Start_Date"] >= cutoff]

    df = df.sort_values("Start_Date").reset_index(drop=True)
    if df.empty:
        raise ValueError("No cycles left after applying the period filter.")

    by_start = {d: idx for idx, d in zip(df.index, df["Start_Date"])}
    chain, idx = [], 0
    while idx is not None:
        chain.append(idx)
        idx = by_start.get(df.at[idx, "End_Date"])
    return df.loc[chain].reset_index(drop=True)


def _records(df):
    """DataFrame -> JSON-safe list of dicts (ISO dates, NaN -> null)."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _cagr(final_equity_ratio, years_elapsed):
    if final_equity_ratio <= 0:
        return -100.0  # account wiped out
    return (final_equity_ratio ** (1 / years_elapsed) - 1) * 100


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN BACKTEST — same logic as before, but returns a dict (no printing)
# ─────────────────────────────────────────────────────────────────────────────

def run_naked_put_full_report(symbol="EWY", years=5,
                              cycle_days=21,
                              put_otm=-0.10, put_prp=0.03,
                              put_otm_hedge=None,
                              total_margin=None, utilize=None,
                              data_dir="data",
                              include_cycles=True):
    if round(cycle_days / 7) < 1:
        raise ValueError("cycle_days must be at least ~7 days (snaps to whole Fridays).")

    cycles = load_cycles(symbol, cycle_days, years, data_dir)

    rows = []
    cum_equity = 1.0

    for _, r in cycles.iterrows():
        start_price = r['Start_Price_Close']
        end_price = r['End_Price_Close']
        mkt_ret = (end_price / start_price) - 1

        raw_loss = (mkt_ret - put_otm) if mkt_ret < put_otm else 0.0
        if put_otm_hedge is not None and put_otm_hedge > 0:
            capped_loss = max(raw_loss, -put_otm_hedge)
            if mkt_ret <= put_otm - put_otm_hedge:
                status = "Max Loss (Below Long Strike)"
            elif capped_loss < 0:
                status = "Breach (DOWN)"
            else:
                status = "Success (OTM)"
        else:
            capped_loss = raw_loss
            status = "Breach (DOWN)" if capped_loss < 0 else "Success (OTM)"

        strategy_ret = put_prp + capped_loss
        cum_equity *= (1 + strategy_ret)

        rows.append({
            'Start_Date': r['Start_Date'], 'End_Date': r['End_Date'],
            'Cycle_Days_Used': cycle_days,
            'Put_OTM_Used': put_otm,
            'Hedge_Width_Used': put_otm_hedge if put_otm_hedge is not None else 0.0,
            'Start_Price_Close': start_price, 'End_Price_Close': end_price,
            'Market_Ret': mkt_ret,
            'Put_Prp': put_prp, 'Cashflow': put_prp,
            'Strategy_Ret': strategy_ret, 'Status': status,
            'Strategy_Equity': cum_equity,
        })

    if not rows:
        raise ValueError("No valid cycles could be built — check data length vs cycle length.")

    monthly = pd.DataFrame(rows)
    monthly['Cumulative_Return_%'] = (monthly['Strategy_Equity'] - 1) * 100

    # ── Performance metrics ────────────────────────────────────────────────
    n = len(monthly)
    total_days = (monthly['End_Date'].iloc[-1] - monthly['Start_Date'].iloc[0]).days
    years_elapsed = max(total_days / 365.25, 1e-9)

    losing_mask = monthly['Strategy_Ret'] < 0
    n_losing = int(losing_mask.sum())

    metrics = {
        "cagr_pct": _cagr(monthly['Strategy_Equity'].iloc[-1], years_elapsed),
        "max_drawdown_pct": float(((monthly['Strategy_Equity'] / monthly['Strategy_Equity'].cummax()) - 1).min() * 100),
        "total_cashflow_pct": float(monthly['Cashflow'].sum() * 100),
        "win_rate_inside_range_pct": float((monthly['Status'] == 'Success (OTM)').sum() / n * 100),
        "win_rate_positive_return_pct": float((monthly['Strategy_Ret'] > 0).sum() / n * 100),
        "avg_loss_pct": float(monthly.loc[losing_mask, 'Strategy_Ret'].mean() * 100) if n_losing else 0.0,
        "worst_loss_pct": float(monthly.loc[losing_mask, 'Strategy_Ret'].min() * 100) if n_losing else 0.0,
        "losing_cycles": n_losing,
        "total_cycles": n,
        "total_return_pct": float(monthly['Cumulative_Return_%'].iloc[-1]),
        "years_elapsed": years_elapsed,
    }

    # ── Yearly breakdown ───────────────────────────────────────────────────
    monthly['Year'] = pd.to_datetime(monthly['End_Date']).dt.year
    yearly_rows, prior_equity = [], 1.0
    for yr, grp in monthly.groupby('Year'):
        end_equity = grp['Strategy_Equity'].iloc[-1]
        yearly_rows.append({
            'Year': int(yr),
            'Yearly_Return_%': (end_equity / prior_equity - 1) * 100,
            'Cycles_In_Year': len(grp),
            'Cumulative_Return_%': (end_equity - 1) * 100,
        })
        prior_equity = end_equity
    yearly_df = pd.DataFrame(yearly_rows)

    # ── Account / margin simulation (optional) ─────────────────────────────
    account = None
    if total_margin is not None and utilize is not None:
        deployed_list, pnl_list, equity_list = [], [], []
        equity = float(total_margin)
        for ret in monthly['Strategy_Ret']:
            deployed = equity * utilize
            pnl = deployed * ret
            equity += pnl
            deployed_list.append(deployed)
            pnl_list.append(pnl)
            equity_list.append(equity)

        monthly['Margin_Deployed'] = deployed_list
        monthly['Dollar_PnL'] = pnl_list
        monthly['Account_Equity'] = equity_list
        monthly['Cum_Return_%'] = (monthly['Account_Equity'] / total_margin - 1) * 100
        cummax = monthly['Account_Equity'].cummax()
        monthly['Drawdown_$'] = monthly['Account_Equity'] - cummax
        monthly['Drawdown_%'] = (monthly['Account_Equity'] / cummax - 1) * 100

        final_equity = float(monthly['Account_Equity'].iloc[-1])
        account = {
            "starting_capital": float(total_margin),
            "utilization": float(utilize),
            "final_equity": final_equity,
            "total_dollar_pnl": final_equity - float(total_margin),
            "account_cagr_pct": _cagr(final_equity / total_margin, years_elapsed),
            "max_dollar_drawdown": float(monthly['Drawdown_$'].min()),
            "max_pct_drawdown": float(monthly['Drawdown_%'].min()),
        }

    return {
        "params": {
            "symbol": symbol, "years": years, "cycle_days": cycle_days,
            "put_otm": put_otm, "put_prp": put_prp, "put_otm_hedge": put_otm_hedge,
            "total_margin": total_margin, "utilize": utilize,
        },
        "metrics": metrics,
        "account": account,
        "yearly": _records(yearly_df),
        "cycles": _records(monthly.drop(columns=['Year'])) if include_cycles else None,
    }
