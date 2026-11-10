"""
Seasonality Dashboard - "What to expect this month"
=======================================================

One clean chart per instrument: average historical return for each
calendar month, with a confidence band (std dev) and a hit-rate label
(what % of years that month was positive). The current calendar month
is highlighted so you can see, at a glance, what history says about
right now.

Also builds a single HTML dashboard (docs/seasonality/index.html) you
can publish with GitHub Pages, showing every instrument's chart plus a
quick summary table of "this month, historically."

Run:
    pip install -r requirements.txt
    python seasonality_analysis.py

Output (in ./docs/seasonality/):
    index.html                 <- open this, or publish via GitHub Pages
    charts/<name>.png          <- one bar chart per instrument
    monthly_stats.csv          <- the underlying numbers
"""

import os
import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf

# ----------------------------------------------------------------------
# CONFIG - edit this list to add/remove instruments
# ----------------------------------------------------------------------

TICKERS = {
    "Natural Gas (Henry Hub)": "NG=F",
    "Corn": "ZC=F",
    "Heating Oil": "HO=F",
    "WTI Crude Oil": "CL=F",
    "Copper": "HG=F",
    "US 10Y Treasury Yield": "^TNX",
}

START_DATE = "2010-01-01"
OUTPUT_DIR = "docs/seasonality"
CHARTS_DIR = os.path.join(OUTPUT_DIR, "charts")
MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


# ----------------------------------------------------------------------
# DATA
# ----------------------------------------------------------------------

def fetch_daily(ticker, start_date):
    px = yf.download(ticker, start=start_date, progress=False, auto_adjust=True)
    if px.empty:
        raise ValueError(f"No data for {ticker}")
    px = px[["Close"]].reset_index()
    px.columns = ["date", "close"]
    px["date"] = pd.to_datetime(px["date"])
    return px


def build_monthly_summary(px):
    """
    One row per calendar month (Jan..Dec):
      avg_return   - average % return in that month, across all years
      std_return   - how much that varies year to year
      hit_rate_pct - % of years that month was positive
      n_years      - sample size
    """
    df = px.copy()
    monthly = df.set_index("date").resample("ME")["close"].last().to_frame()
    monthly["ret"] = monthly["close"].pct_change() * 100
    monthly["month"] = monthly.index.month

    rows = []
    for m in range(1, 13):
        vals = monthly.loc[monthly["month"] == m, "ret"].dropna()
        rows.append({
            "month": m,
            "month_name": MONTH_NAMES[m - 1],
            "avg_return": vals.mean() if len(vals) else np.nan,
            "std_return": vals.std() if len(vals) else np.nan,
            "hit_rate_pct": (vals > 0).mean() * 100 if len(vals) else np.nan,
            "n_years": len(vals),
        })
    return pd.DataFrame(rows).set_index("month")


# ----------------------------------------------------------------------
# CHART - the single "what to expect" chart per instrument
# ----------------------------------------------------------------------

def plot_monthly_outlook(name, summary, current_month, outdir):
    fig, ax = plt.subplots(figsize=(11, 5.5))

    colors = ["#2e7d32" if v >= 0 else "#c62828" for v in summary["avg_return"]]
    edge_colors = ["black" if m == current_month else "none" for m in summary.index]
    edge_widths = [2.2 if m == current_month else 0 for m in summary.index]

    bars = ax.bar(
        summary["month_name"], summary["avg_return"],
        yerr=summary["std_return"], capsize=3,
        color=colors, edgecolor=edge_colors, linewidth=edge_widths,
        alpha=0.85, error_kw={"alpha": 0.4},
    )

    for i, (m, row) in enumerate(summary.iterrows()):
        if pd.isna(row["avg_return"]):
            continue
        label_y = row["avg_return"] + (row["std_return"] if row["avg_return"] >= 0 else -row["std_return"])
        offset = 0.6 if row["avg_return"] >= 0 else -0.6
        ax.text(i, label_y + offset, f"{row['hit_rate_pct']:.0f}% pos",
                ha="center", va="bottom" if row["avg_return"] >= 0 else "top",
                fontsize=8, color="dimgray")

    ax.axhline(0, color="grey", lw=0.8)
    ax.set_ylabel("Average return (%)")
    ax.set_title(f"{name}: What to Expect Each Month  (bars = avg return, whiskers = variability,\n"
                 f"label = % of years positive, black outline = current month)")
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()

    safe_name = name.replace(" ", "_").replace("(", "").replace(")", "")
    fname = os.path.join(outdir, f"{safe_name}.png")
    plt.savefig(fname, dpi=140)
    plt.close(fig)
    return fname


# ----------------------------------------------------------------------
# DASHBOARD
# ----------------------------------------------------------------------

def month_verdict(row):
    """A one-line, plain-English read of the month's historical stats."""
    if pd.isna(row["avg_return"]):
        return "Not enough data"
    strength = "strong" if abs(row["avg_return"]) > 3 else ("mild" if abs(row["avg_return"]) > 1 else "flat")
    direction = "up" if row["avg_return"] >= 0 else "down"
    reliability = "reliable" if (row["hit_rate_pct"] >= 70 or row["hit_rate_pct"] <= 30) else "mixed/unreliable"
    return f"{strength} seasonal tilt {direction} ({row['avg_return']:+.1f}% avg, {row['hit_rate_pct']:.0f}% of years positive) - {reliability}"


def build_dashboard(results, outdir):
    current_month_name = MONTH_NAMES[datetime.date.today().month - 1]
    generated = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    rows_html = ""
    charts_html = ""
    for name, info in results.items():
        current_row = info["summary"].loc[info["month_num"]]
        rows_html += f"""
        <tr>
          <td>{name}</td>
          <td>{current_row['avg_return']:+.1f}%</td>
          <td>{current_row['hit_rate_pct']:.0f}%</td>
          <td>{current_row['n_years']}</td>
          <td>{month_verdict(current_row)}</td>
        </tr>"""
        chart_rel = f"charts/{os.path.basename(info['chart_path'])}"
        charts_html += f"""
        <h3>{name}</h3>
        <img src="{chart_rel}" alt="{name} seasonality chart" style="max-width:100%;">
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Seasonality Dashboard</title>
<style>
  body {{ font-family: -apple-system, Arial, sans-serif; max-width: 960px; margin: 40px auto; padding: 0 16px; color: #1a1a1a; }}
  h1 {{ margin-bottom: 4px; }}
  .meta {{ color: #666; font-size: 0.9em; margin-bottom: 24px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 32px; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; font-size: 0.92em; }}
  th {{ background: #f4f4f4; }}
  img {{ border: 1px solid #eee; border-radius: 4px; margin-bottom: 28px; }}
</style>
</head>
<body>
  <h1>Seasonality Dashboard</h1>
  <div class="meta">Last updated: {generated} &middot; Current month: {current_month_name}</div>

  <h2>This month, at a glance</h2>
  <table>
    <tr><th>Instrument</th><th>Avg return, {current_month_name}</th><th>Historically positive</th><th>Years of data</th><th>Read</th></tr>
    {rows_html}
  </table>

  <h2>Full-year charts</h2>
  {charts_html}
</body>
</html>"""

    with open(os.path.join(outdir, "index.html"), "w") as f:
        f.write(html)


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    os.makedirs(CHARTS_DIR, exist_ok=True)
    current_month = datetime.date.today().month
    results = {}

    for name, ticker in TICKERS.items():
        print(f"\n=== {name} ({ticker}) ===")
        try:
            px = fetch_daily(ticker, START_DATE)
        except Exception as e:
            print(f"  Skipping {name}: {e}")
            continue

        summary = build_monthly_summary(px)
        chart_path = plot_monthly_outlook(name, summary, current_month, CHARTS_DIR)
        print(f"  Saved chart: {chart_path}")

        results[name] = {"summary": summary, "chart_path": chart_path, "month_num": current_month}

    # combined CSV across all instruments
    all_rows = []
    for name, info in results.items():
        df = info["summary"].copy()
        df["instrument"] = name
        all_rows.append(df.reset_index())
    if all_rows:
        pd.concat(all_rows).to_csv(os.path.join(OUTPUT_DIR, "monthly_stats.csv"), index=False)

    build_dashboard(results, OUTPUT_DIR)
    print(f"\nDashboard written to {os.path.join(OUTPUT_DIR, 'index.html')}")


if __name__ == "__main__":
    main()
