"""
COT (Commitments of Traders) positioning vs. Price
====================================================

What this does, in plain English:
  1. Downloads the weekly CFTC "Legacy Futures Only" Commitments of Traders
     report for a commodity (this is the report that splits traders into:
        - Commercial       -> hedgers / "smart money" (producers, refiners...)
        - Non-Commercial   -> large speculators (hedge funds, CTAs)
        - Non-Reportable   -> everyone below reporting thresholds ("small/retail")
  2. Downloads the weekly futures price for the same commodity (via yfinance).
  3. Lines the two datasets up week by week.
  4. Plots price against net positioning, flags "extreme" positioning weeks
     (using a rolling z-score), and prints a simple study of what price does
     in the weeks AFTER those extremes.

You should NOT need to edit any function bodies. Only edit the
COMMODITIES dictionary near the bottom if you want to add/remove markets.

Run:
    pip install -r requirements.txt
    python cot_vs_price.py

Output:
    ./output/<commodity>_cot_vs_price.png   (chart)
    ./output/<commodity>_merged.csv         (the raw merged weekly data)
    Console printout with the lead/lag analysis
"""

import os
import time
import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf

# ----------------------------------------------------------------------
# 1. CONFIG - edit this section if you want different commodities/dates
# ----------------------------------------------------------------------

START_DATE = "2015-01-01"

# The CFTC Socrata dataset ID for the "Legacy Futures Only" report.
# (This is the classic Commercial / Non-Commercial / Non-Reportable split.)
CFTC_LEGACY_DATASET = "6dca-aqww"
CFTC_BASE_URL = f"https://publicreporting.cftc.gov/resource/{CFTC_LEGACY_DATASET}.json"

# market_name_contains: a fragment that appears in CFTC's official
# "market_and_exchange_names" field for that contract. CFTC's naming is
# specific, so if a commodity below returns no data, use
# list_available_markets() (see bottom of file) to find the exact string.
COMMODITIES = {
    "WTI Crude Oil": {
        "market_name_contains": "CRUDE OIL, LIGHT SWEET",
        "yf_ticker": "CL=F",
    },
    "Henry Hub Natural Gas": {
        "market_name_contains": "NATURAL GAS",
        "yf_ticker": "NG=F",
    },
    "Copper": {
        "market_name_contains": "COPPER",
        "yf_ticker": "HG=F",
    },
    "Gold": {
        "market_name_contains": "GOLD",
        "yf_ticker": "GC=F",
    },
    "Silver": {
        "market_name_contains": "SILVER",
        "yf_ticker": "SI=F",
    },
    "Platinum": {
        "market_name_contains": "PLATINUM",
        "yf_ticker": "PL=F",
    },
    "Palladium": {
        "market_name_contains": "PALLADIUM",
        "yf_ticker": "PA=F",
    },
    "Corn": {
        "market_name_contains": "CORN",
        "yf_ticker": "ZC=F",
    },
    "Wheat": {
        "market_name_contains": "WHEAT",
        "yf_ticker": "ZW=F",
    },
    "Soybeans": {
        "market_name_contains": "SOYBEANS",
        "yf_ticker": "ZS=F",
    },
}

# Red/Yellow/Green thresholds for the smart-money-vs-dumb-money differential,
# based on where today's value falls in its OWN full history (percentile rank).
# <=10th or >=90th percentile = red (extreme, rare).
# 10-25th or 75-90th = yellow (elevated).
# 25-75th = green (normal/typical).
BAND_RED_PCT = 10
BAND_YELLOW_PCT = 25

OUTPUT_DIR = "docs/cot"
ROLLING_WINDOW_WEEKS = 104   # ~2 years, used for the z-score of positioning
EXTREME_Z = 2.0              # |z| above this = "extreme" positioning
FORWARD_WEEKS = [4, 8, 12]   # look this many weeks ahead for the lead/lag study


# ----------------------------------------------------------------------
# 2. DATA FETCHING
# ----------------------------------------------------------------------

def list_available_markets(keyword, limit=50):
    """
    Helper: prints distinct CFTC market names containing `keyword`.
    Use this if a commodity in COMMODITIES returns no rows - it will show
    you the exact string CFTC uses so you can fix market_name_contains.
    """
    params = {
        "$select": "market_and_exchange_names",
        "$group": "market_and_exchange_names",
        "$where": f"upper(market_and_exchange_names) like '%{keyword.upper()}%'",
        "$limit": limit,
    }
    r = requests.get(CFTC_BASE_URL, params=params, timeout=30)
    r.raise_for_status()
    names = [row["market_and_exchange_names"] for row in r.json()]
    print(f"\nMarkets matching '{keyword}':")
    for n in names:
        print("  -", n)
    return names


def fetch_cot(market_name_contains, start_date):
    """
    Pulls the full weekly history for one market from the CFTC Socrata API,
    paging through results (the API caps each response at ~50k rows, but we
    page defensively anyway).
    """
    fields = [
        "report_date_as_yyyy_mm_dd",
        "open_interest_all",
        "noncomm_positions_long_all",
        "noncomm_positions_short_all",
        "comm_positions_long_all",
        "comm_positions_short_all",
        "nonrept_positions_long_all",
        "nonrept_positions_short_all",
        "market_and_exchange_names",
    ]
    all_rows = []
    offset = 0
    page_size = 5000
    while True:
        params = {
            "$select": ",".join(fields),
            "$where": (
                f"upper(market_and_exchange_names) like '%{market_name_contains.upper()}%' "
                f"AND report_date_as_yyyy_mm_dd >= '{start_date}T00:00:00.000'"
            ),
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": page_size,
            "$offset": offset,
        }
        r = requests.get(CFTC_BASE_URL, params=params, timeout=60)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        all_rows.extend(rows)
        offset += page_size
        if len(rows) < page_size:
            break
        time.sleep(0.2)  # be polite to the API

    if not all_rows:
        raise ValueError(
            f"No CFTC rows found for '{market_name_contains}'. "
            f"Try list_available_markets('{market_name_contains.split(',')[0]}') "
            f"to find the exact market name."
        )

    df = pd.DataFrame(all_rows)
    numeric_cols = [c for c in fields if c != "market_and_exchange_names" and c != "report_date_as_yyyy_mm_dd"]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["report_date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"]).dt.normalize()

    # If more than one matching market came back (e.g. ICE + NYMEX versions),
    # keep whichever single market has the most rows so series stay clean.
    if df["market_and_exchange_names"].nunique() > 1:
        top_market = df["market_and_exchange_names"].value_counts().idxmax()
        df = df[df["market_and_exchange_names"] == top_market]

    df = df.sort_values("report_date").reset_index(drop=True)
    return df


def fetch_price(ticker, start_date):
    """Daily close prices from Yahoo Finance."""
    px = yf.download(ticker, start=start_date, progress=False, auto_adjust=True)
    if px.empty:
        raise ValueError(f"No price data returned for {ticker}")
    px = px[["Close"]].reset_index()
    px.columns = ["date", "close"]
    px["date"] = pd.to_datetime(px["date"]).dt.normalize()
    return px


# ----------------------------------------------------------------------
# 3. ANALYSIS
# ----------------------------------------------------------------------

def build_dataset(cot_df, price_df):
    """
    Merges COT (weekly, Tuesday) with the daily price series, taking the
    closing price on or immediately before each COT report date.
    Adds net positioning, positioning as % of open interest, and a rolling
    z-score of net non-commercial positioning (the "speculative extreme" signal).
    """
    cot_df = cot_df.sort_values("report_date").copy()
    price_df = price_df.sort_values("date").copy()

    # Force both date columns to the exact same datetime precision.
    # Different pandas/source combos can produce datetime64[us] vs
    # datetime64[s], which merge_asof refuses to mix.
    cot_df["report_date"] = pd.to_datetime(cot_df["report_date"]).astype("datetime64[ns]")
    price_df["date"] = pd.to_datetime(price_df["date"]).astype("datetime64[ns]")

    merged = pd.merge_asof(
        cot_df, price_df, left_on="report_date", right_on="date", direction="backward"
    )

    merged["net_noncomm"] = merged["noncomm_positions_long_all"] - merged["noncomm_positions_short_all"]
    merged["net_comm"] = merged["comm_positions_long_all"] - merged["comm_positions_short_all"]
    merged["net_nonrept"] = merged["nonrept_positions_long_all"] - merged["nonrept_positions_short_all"]

    merged["net_noncomm_pct_oi"] = merged["net_noncomm"] / merged["open_interest_all"]
    merged["net_comm_pct_oi"] = merged["net_comm"] / merged["open_interest_all"]

    # The "differential between stupid money and smart money": how far apart
    # speculators (non-commercial) and hedgers (commercial) are positioned,
    # as a % of open interest. Positive = specs net long relative to hedgers
    # (bullish crowd). Negative = specs net short relative to hedgers (bearish crowd).
    merged["diff_pct_oi"] = merged["net_noncomm_pct_oi"] - merged["net_comm_pct_oi"]

    roll = merged["net_noncomm_pct_oi"].rolling(ROLLING_WINDOW_WEEKS, min_periods=26)
    merged["net_noncomm_z"] = (merged["net_noncomm_pct_oi"] - roll.mean()) / roll.std()

    return merged


def forward_returns(merged, weeks_list):
    """
    For every week flagged as an extreme (|z| > EXTREME_Z), computes what
    price did N weeks later. Prints an average summary split by
    "extreme long" (crowded bullish) vs "extreme short" (crowded bearish).
    """
    out = {}
    for w in weeks_list:
        merged[f"fwd_ret_{w}w"] = merged["close"].shift(-w) / merged["close"] - 1

    extreme_long = merged[merged["net_noncomm_z"] > EXTREME_Z]
    extreme_short = merged[merged["net_noncomm_z"] < -EXTREME_Z]

    print("\n--- Forward returns after EXTREME positioning weeks ---")
    summary = {}
    for w in weeks_list:
        col = f"fwd_ret_{w}w"
        n_long = extreme_long[col].dropna()
        n_short = extreme_short[col].dropna()
        summary[w] = {
            "long_avg_pct": n_long.mean() * 100 if len(n_long) else float("nan"),
            "long_n": len(n_long),
            "short_avg_pct": n_short.mean() * 100 if len(n_short) else float("nan"),
            "short_n": len(n_short),
        }
        print(f"  +{w} weeks after crowded-LONG speculators (z>{EXTREME_Z}): "
              f"avg return {summary[w]['long_avg_pct']:+.2f}%  (n={summary[w]['long_n']})")
        print(f"  +{w} weeks after crowded-SHORT speculators (z<-{EXTREME_Z}): "
              f"avg return {summary[w]['short_avg_pct']:+.2f}%  (n={summary[w]['short_n']})")
    return merged, summary


def classify_band(merged):
    """
    Where does TODAY's smart-money-vs-dumb-money differential sit relative
    to its own full history? Returns a red/yellow/green band plus the
    percentile and a plain-English direction.
    """
    diff_series = merged["diff_pct_oi"].dropna()
    current = diff_series.iloc[-1]
    percentile = (diff_series < current).mean() * 100

    if percentile <= BAND_RED_PCT or percentile >= (100 - BAND_RED_PCT):
        band = "red"
    elif percentile <= BAND_YELLOW_PCT or percentile >= (100 - BAND_YELLOW_PCT):
        band = "yellow"
    else:
        band = "green"

    direction = "specs crowded LONG vs. hedgers" if current > 0 else "specs crowded SHORT vs. hedgers"

    return {
        "diff_pct": current * 100,
        "percentile": percentile,
        "band": band,
        "direction": direction,
    }


def current_signal(merged):
    """Reads off the most recent week's positioning to describe today's state."""
    latest = merged.dropna(subset=["net_noncomm_z"]).iloc[-1]
    z = latest["net_noncomm_z"]
    if z > EXTREME_Z:
        state = "CROWDED LONG (extreme)"
    elif z < -EXTREME_Z:
        state = "CROWDED SHORT (extreme)"
    elif z > 1:
        state = "Leaning long"
    elif z < -1:
        state = "Leaning short"
    else:
        state = "Neutral / no extreme"
    band_info = classify_band(merged)
    return {
        "report_date": latest["report_date"].strftime("%Y-%m-%d"),
        "price": latest["close"],
        "z": z,
        "state": state,
        "diff_pct": band_info["diff_pct"],
        "percentile": band_info["percentile"],
        "band": band_info["band"],
        "direction": band_info["direction"],
    }


def plot_commodity(name, merged, outdir):
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                              gridspec_kw={"height_ratios": [2, 1.4]})

    ax0 = axes[0]
    ax0.plot(merged["report_date"], merged["close"], color="black", lw=1.2, label="Price")
    extreme_long = merged[merged["net_noncomm_z"] > EXTREME_Z]
    extreme_short = merged[merged["net_noncomm_z"] < -EXTREME_Z]
    ax0.scatter(extreme_long["report_date"], extreme_long["close"], color="green",
                marker="^", s=40, label=f"Crowded long (z>{EXTREME_Z})", zorder=5)
    ax0.scatter(extreme_short["report_date"], extreme_short["close"], color="red",
                marker="v", s=40, label=f"Crowded short (z<-{EXTREME_Z})", zorder=5)
    ax0.set_title(f"{name}: Price vs. Speculative Positioning Extremes")
    ax0.set_ylabel("Price")
    ax0.legend(loc="upper left")
    ax0.grid(alpha=0.3)

    ax1 = axes[1]
    ax1.plot(merged["report_date"], merged["net_noncomm"], color="tab:blue", lw=1, label="Net Non-Commercial (speculators)")
    ax1.plot(merged["report_date"], merged["net_comm"], color="tab:orange", lw=1, label="Net Commercial (hedgers)")
    ax1.axhline(0, color="grey", lw=0.8)
    ax1.set_ylabel("Net contracts")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)

    plt.tight_layout()
    fname = os.path.join(outdir, f"{name.replace(' ', '_')}_cot_vs_price.png")
    plt.savefig(fname, dpi=140)
    plt.close(fig)
    print(f"Saved chart: {fname}")


# ----------------------------------------------------------------------
# 4. DASHBOARD
# ----------------------------------------------------------------------

def build_dashboard(results, outdir):
    import datetime
    generated = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    band_emoji = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
    band_color = {"red": "#c62828", "yellow": "#b8860b", "green": "#2e7d32"}

    rows_html = ""
    charts_html = ""
    for name, info in results.items():
        sig = info["signal"]
        fwd = info["forward_summary"][FORWARD_WEEKS[0]]  # nearest horizon for the summary row
        badge = band_emoji[sig["band"]]
        badge_color = band_color[sig["band"]]
        rows_html += f"""
        <tr>
          <td>{name}</td>
          <td>{sig['report_date']}</td>
          <td>{sig['price']:.2f}</td>
          <td>{sig['diff_pct']:+.1f}%</td>
          <td style="color:{badge_color}; font-weight:600;">{badge} {sig['band'].upper()} ({sig['percentile']:.0f}th pct)</td>
          <td>{sig['direction']}</td>
          <td>+{FORWARD_WEEKS[0]}w after crowded-long: {fwd['long_avg_pct']:+.1f}% (n={fwd['long_n']}) &middot;
              after crowded-short: {fwd['short_avg_pct']:+.1f}% (n={fwd['short_n']})</td>
        </tr>"""
        chart_rel = os.path.basename(info["chart_path"])
        charts_html += f"""
        <h3>{name}</h3>
        <img src="{chart_rel}" alt="{name} COT chart" style="max-width:100%;">
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Smart Money / Dumb Money Dashboard</title>
<style>
  body {{ font-family: -apple-system, Arial, sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 16px; color: #1a1a1a; }}
  h1 {{ margin-bottom: 4px; }}
  .meta {{ color: #666; font-size: 0.9em; margin-bottom: 24px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 32px; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; font-size: 0.9em; }}
  th {{ background: #f4f4f4; }}
  img {{ border: 1px solid #eee; border-radius: 4px; margin-bottom: 28px; }}
  .note {{ color: #666; font-size: 0.85em; }}
</style>
</head>
<body>
  <h1>Commitments of Traders Dashboard</h1>
  <div class="meta">Commercial ("smart money" hedgers) vs. Non-Commercial (speculators) &middot; Last updated: {generated}</div>

  <h2>Current positioning</h2>
  <table>
    <tr><th>Market</th><th>As of</th><th>Price</th><th>Spec-Hedger diff (% OI)</th><th>Range</th><th>Direction</th><th>Historical follow-through</th></tr>
    {rows_html}
  </table>
  <p class="note">"Diff" is the gap between speculator and hedger positioning as a % of open interest.
  "Range" shows where today's value sits versus its own full history:
  🟢 green = typical (25th-75th percentile), 🟡 yellow = elevated (10th-25th or 75th-90th), 🔴 red = extreme (top/bottom 10%).
  This is a contrarian tilt, not a timing signal - markets can stay crowded for a long time before reversing.</p>

  <h2>Full charts</h2>
  {charts_html}
</body>
</html>"""

    with open(os.path.join(outdir, "index.html"), "w") as f:
        f.write(html)


# ----------------------------------------------------------------------
# 5. MAIN
# ----------------------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    results = {}

    for name, cfg in COMMODITIES.items():
        print(f"\n=== {name} ===")
        try:
            cot_df = fetch_cot(cfg["market_name_contains"], START_DATE)
            price_df = fetch_price(cfg["yf_ticker"], START_DATE)
        except Exception as e:
            print(f"  Skipping {name}: {e}")
            continue

        merged = build_dataset(cot_df, price_df)
        merged, fwd_summary = forward_returns(merged, FORWARD_WEEKS)
        plot_commodity(name, merged, OUTPUT_DIR)
        sig = current_signal(merged)
        print(f"  {sig['band'].upper()} ({sig['percentile']:.0f}th pct) - {sig['direction']} - diff {sig['diff_pct']:+.1f}% of OI")

        csv_path = os.path.join(OUTPUT_DIR, f"{name.replace(' ', '_')}_merged.csv")
        merged.to_csv(csv_path, index=False)
        print(f"Saved data: {csv_path}")

        results[name] = {
            "chart_path": os.path.join(OUTPUT_DIR, f"{name.replace(' ', '_')}_cot_vs_price.png"),
            "signal": sig,
            "forward_summary": fwd_summary,
        }

    build_dashboard(results, OUTPUT_DIR)
    print(f"\nDashboard written to {os.path.join(OUTPUT_DIR, 'index.html')}")

    # Write a small JSON summary that telegram_alert.py reads - keeps the
    # alert script simple and avoids re-fetching all the data a second time.
    import json
    summary_for_alert = {
        name: {
            "report_date": info["signal"]["report_date"],
            "price": info["signal"]["price"],
            "diff_pct": info["signal"]["diff_pct"],
            "percentile": info["signal"]["percentile"],
            "band": info["signal"]["band"],
            "direction": info["signal"]["direction"],
        }
        for name, info in results.items()
    }
    with open(os.path.join(OUTPUT_DIR, "latest_signal.json"), "w") as f:
        json.dump(summary_for_alert, f, indent=2)
    print(f"Alert summary written to {os.path.join(OUTPUT_DIR, 'latest_signal.json')}")


if __name__ == "__main__":
    main()

# ----------------------------------------------------------------------
# Not sure of the exact CFTC market name for a commodity you want to add?
# Uncomment the two lines below and run this file - it will print every
# market name CFTC has that contains your keyword.
# ----------------------------------------------------------------------
# list_available_markets("COPPER")
# list_available_markets("SOYBEAN")
