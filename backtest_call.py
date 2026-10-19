import pandas as pd

# Reuses the CSV loader and helpers from backtest.py (same data/{SYMBOL}_cycles.csv files)
from backtest import load_cycles, _records, _cagr


def run_naked_call_full_report(symbol="EWY", years=5,
                               cycle_days=21,
                               call_otm=0.10, call_prp=0.03,
                               call_otm_hedge=None,
                               total_margin=None, utilize=None,
                               data_dir="data",
                               include_cycles=True):
    """
    NAKED CALL (default) or CALL CREDIT SPREAD (if a hedge width is given).
    Fixed cycle length, strike and premium for every cycle.

    call_otm       : how far ABOVE spot the short call is, positive decimal (0.10 = 10% OTM)
    call_prp       : fixed net credit per cycle as a decimal of notional (0.03 = 3%)
    call_otm_hedge : spread WIDTH, positive decimal (0.10 = long call bought 10% above the
                     short strike). Caps max loss at this width. None = uncapped naked call.

    Loss for a cycle: raw_loss = -(Market_Ret - call_otm) if Market_Ret > call_otm else 0
                      capped_loss = max(raw_loss, -hedge_width) when a hedge is set
                      Strategy_Ret = call_prp + capped_loss
    """
    if round(cycle_days / 7) < 1:
        raise ValueError("cycle_days must be at least ~7 days (snaps to whole Fridays).")

    cycles = load_cycles(symbol, cycle_days, years, data_dir)

    rows = []
    cum_equity = 1.0

    for _, r in cycles.iterrows():
        start_price = r['Start_Price_Close']
        end_price = r['End_Price_Close']
        mkt_ret = (end_price / start_price) - 1

        raw_loss = -(mkt_ret - call_otm) if mkt_ret > call_otm else 0.0
        if call_otm_hedge is not None and call_otm_hedge > 0:
            capped_loss = max(raw_loss, -call_otm_hedge)
            if mkt_ret >= call_otm + call_otm_hedge:
                status = "Max Loss (Above Long Strike)"
            elif capped_loss < 0:
                status = "Breach (UP)"
            else:
                status = "Success (OTM)"
        else:
            capped_loss = raw_loss
            status = "Breach (UP)" if capped_loss < 0 else "Success (OTM)"

        strategy_ret = call_prp + capped_loss
        cum_equity *= (1 + strategy_ret)

        rows.append({
            'Start_Date': r['Start_Date'], 'End_Date': r['End_Date'],
            'Cycle_Days_Used': cycle_days,
            'Call_OTM_Used': call_otm,
            'Hedge_Width_Used': call_otm_hedge if call_otm_hedge is not None else 0.0,
            'Start_Price_Close': start_price, 'End_Price_Close': end_price,
            'Market_Ret': mkt_ret,
            'Call_Prp': call_prp, 'Cashflow': call_prp,
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
            "call_otm": call_otm, "call_prp": call_prp, "call_otm_hedge": call_otm_hedge,
            "total_margin": total_margin, "utilize": utilize,
        },
        "metrics": metrics,
        "account": account,
        "yearly": _records(yearly_df),
        "cycles": _records(monthly.drop(columns=['Year'])) if include_cycles else None,
    }
