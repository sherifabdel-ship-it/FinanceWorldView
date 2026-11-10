"""
WHERE MONEY'S AT - dashboard builder
======================================

Goal: spot when the finance crowd has moved into an asset, and when an
asset is overcrowded (long or short) so you can get out.

Two views, each with two modes (Current / Historical):

  1. ASSET CLASSES - approximate share of the money sitting in:
        US stocks, US Treasuries, Cash (money-market funds), Gold, Bitcoin
     Shares are normalized to sum to 1 (a zero-sum approximation).
     Crowding band = where today's share sits versus its own history.

  2. COMMODITIES - share of dollar-notional futures open interest
        (open interest x price x contract size) per commodity, summed to 1.
     Crowding band = where the speculator-minus-hedger positioning gap sits
     versus its own history (same engine as cot_vs_price.py).

IMPORTANT LIMITS (also shown on the dashboard):
  * No free daily "where the money is" feed exists. FRED data is quarterly
    (carried forward month to month), prices are monthly closes here.
  * Share-of-total moves with PRICES. If stocks rally 20%, their share rises
    even if nobody added money. A separate "flows" view is on the backlog.
  * The asset-class mix mixes scopes (e.g. FRED's equity series covers
    nonfinancial corporations only). It is an approximation - use the TREND
    and the percentile band, not the absolute level.
  * Series IDs / quantities below are editable. A series that fails to
    download is skipped and reported on the dashboard rather than breaking it.

Run:
    pip install -r requirements.txt
    python wheres_money_at.py
Output:
    docs/money/index.html   (open this)
    docs/money/*.png, shares_*.csv, latest.json
"""

import os
import io
import json
import datetime

import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import cot_vs_price as cvp

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

START = "2015-01-01"
OUT_DIR = "docs/money"

# FRED values are in millions of USD. Gold/Bitcoin = price x approximate
# amount in existence (gold ~6.8bn troy oz above ground, bitcoin ~19.9M coins).
# Edit these if you want a different definition of "the pie".
ASSET_CLASSES = {
    "US stocks":           {"kind": "fred",  "id": "NCBEILQ027S", "scale": 1e6},
    "US Treasuries":       {"kind": "fred",  "id": "GFDEBTN",     "scale": 1e6},
    "Cash (money funds)":  {"kind": "fred",  "id": "MMMFFAQ027S", "scale": 1e6},
    "Gold":                {"kind": "price", "ticker": "GC=F",    "quantity": 6.8e9},
    "Bitcoin":             {"kind": "price", "ticker": "BTC-USD", "quantity": 19.9e6},
}

# Per-commodity: (contract size in units, divisor to convert the quoted price
# into dollars per unit). Grains are quoted in cents per bushel -> divide by 100.
COMMODITY_SPECS = {
    "WTI Crude Oil":         (1000, 1),     # barrels
    "Henry Hub Natural Gas": (10000, 1),    # mmBtu
    "Copper":                (25000, 1),    # pounds
    "Gold":                  (100, 1),      # troy oz
    "Silver":                (5000, 1),     # troy oz
    "Platinum":              (50, 1),       # troy oz
    "Palladium":             (100, 1),      # troy oz
    "Corn":                  (5000, 100),   # bushels
    "Wheat":                 (5000, 100),
    "Soybeans":              (5000, 100),
}

# Validated categorical palette (fixed order, never cycled) + page surface.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
OTHER_GREY = "#9a9990"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"

BAND_LABEL = {"red": "RED - extreme", "yellow": "YELLOW - elevated", "green": "GREEN - typical"}
BAND_SYMBOL = {"red": "▲", "yellow": "■", "green": "●"}   # shapes, not colour alone


# ----------------------------------------------------------------------
# DATA
# ----------------------------------------------------------------------

def fred_series(series_id):
    """Free FRED CSV download (no API key needed)."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().set_index("date")["value"]


def monthly_close(ticker):
    px = cvp.fetch_price(ticker, START)
    s = px.set_index("date")["close"]
    s.index = pd.to_datetime(s.index)
    return s.resample("ME").last()


def build_asset_values():
    """Dollar value per asset class, month-end index. Returns (DataFrame, skipped)."""
    series, skipped = {}, []
    for name, cfg in ASSET_CLASSES.items():
        try:
            if cfg["kind"] == "fred":
                s = fred_series(cfg["id"]) * cfg["scale"]
                s = s.resample("ME").last()
            else:
                s = monthly_close(cfg["ticker"]) * cfg["quantity"]
            series[name] = s
        except Exception as e:  # keep going - report instead of crashing
            skipped.append(f"{name}: {e}")
    if not series:
        raise RuntimeError("No asset-class data could be loaded.")
    panel = pd.concat(series, axis=1).sort_index()
    panel = panel.loc[START:].ffill().dropna()
    return panel, skipped


def build_commodity_values():
    """Dollar-notional open interest per commodity + COT crowding band."""
    notional, crowding, skipped = {}, {}, []
    for name, cfg in cvp.COMMODITIES.items():
        if name not in COMMODITY_SPECS:
            continue
        size, div = COMMODITY_SPECS[name]
        try:
            cot = cvp.fetch_cot(cfg["market_name_contains"], START)
            price = cvp.fetch_price(cfg["yf_ticker"], START)
            merged = cvp.build_dataset(cot, price)
            n = merged["open_interest_all"] * merged["close"] / div * size
            s = pd.Series(n.values, index=pd.to_datetime(merged["report_date"]))
            notional[name] = s.resample("ME").last()
            crowding[name] = cvp.classify_band(merged)
        except Exception as e:
            skipped.append(f"{name}: {e}")
    if not notional:
        raise RuntimeError("No commodity data could be loaded.")
    panel = pd.concat(notional, axis=1).sort_index().ffill().dropna()
    return panel, crowding, skipped


# ----------------------------------------------------------------------
# ANALYSIS
# ----------------------------------------------------------------------

def to_shares(values):
    return values.div(values.sum(axis=1), axis=0)


def band_from_percentile(p):
    r, y = cvp.BAND_RED_PCT, cvp.BAND_YELLOW_PCT
    if p <= r or p >= 100 - r:
        return "red"
    if p <= y or p >= 100 - y:
        return "yellow"
    return "green"


def snapshot_rows(shares, crowding=None):
    """One row per asset: current share, 1m/3m change (pct points), band."""
    rows = []
    for name in shares.columns:
        s = shares[name].dropna()
        cur = s.iloc[-1]
        d1 = (cur - s.iloc[-2]) * 100 if len(s) > 1 else float("nan")
        d3 = (cur - s.iloc[-4]) * 100 if len(s) > 3 else float("nan")
        if crowding is not None and name in crowding:
            c = crowding[name]
            band, pct, basis = c["band"], c["percentile"], c["direction"]
        else:
            pct = (s < cur).mean() * 100
            band = band_from_percentile(pct)
            basis = "share is high vs its history" if pct >= 50 else "share is low vs its history"
        rows.append({"name": name, "share": cur * 100, "d1": d1, "d3": d3,
                     "band": band, "percentile": pct, "basis": basis})
    return sorted(rows, key=lambda r: -r["share"])


# ----------------------------------------------------------------------
# CHARTS (light surface; thin marks, direct labels, recessive grid)
# ----------------------------------------------------------------------

def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def chart_current(rows, title, fname):
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(rows) + 1.4), facecolor=SURFACE)
    names = [r["name"] for r in rows][::-1]
    vals = [r["share"] for r in rows][::-1]
    ax.barh(names, vals, color=PALETTE[0], height=0.55)
    for i, v in enumerate(vals):
        ax.text(v + max(vals) * 0.01, i, f"{v:.1f}%", va="center", color=INK, fontsize=9)
    _style(ax)
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Share of total (%)", color=INK2, fontsize=9)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(fname, dpi=140, facecolor=SURFACE)
    plt.close(fig)


def chart_history(shares, title, fname, max_series=6):
    pct = shares * 100
    if pct.shape[1] > max_series:   # fold the tail into "Other" (never cycle colours)
        order = pct.iloc[-1].sort_values(ascending=False).index
        keep = list(order[: max_series - 1])
        pct = pct[keep].assign(Other=pct.drop(columns=keep).sum(axis=1))
    colors = PALETTE[: pct.shape[1] - (1 if "Other" in pct.columns else 0)]
    if "Other" in pct.columns:
        colors = colors + [OTHER_GREY]
    fig, ax = plt.subplots(figsize=(10, 5.2), facecolor=SURFACE)
    ax.stackplot(pct.index, pct.T.values, colors=colors, labels=list(pct.columns),
                 edgecolor=SURFACE, linewidth=0.8)
    _style(ax)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Share of total (%)", color=INK2, fontsize=9)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=min(len(pct.columns), 6),
              frameon=False, fontsize=9, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(fname, dpi=140, facecolor=SURFACE)
    plt.close(fig)


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------

CSS = """
:root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3df;--card:#f4f3ef;--accent:#2a78d6;
 --red:#c62828;--yellow:#8a6400;--green:#1b6e2e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
 --line:#34332f;--card:#242422;--accent:#3987e5;--red:#e66767;--yellow:#e0b030;--green:#4cc36b}}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,Segoe UI,Arial,sans-serif;
 max-width:1000px;margin:32px auto;padding:0 16px}
h1{margin:0 0 4px}h2{margin-top:28px}
.meta,.note{color:var(--ink2);font-size:.88em}
.tabs{display:flex;gap:8px;margin:14px 0}
.tabs button{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:8px 16px;
 border-radius:8px;cursor:pointer;font-size:.95em}
.tabs button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.hide{display:none}
table{border-collapse:collapse;width:100%;margin:10px 0 18px}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;font-size:.9em}
th{color:var(--ink2);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.red{color:var(--red);font-weight:600}.yellow{color:var(--yellow);font-weight:600}.green{color:var(--green);font-weight:600}
.watch{background:var(--card);border-radius:10px;padding:12px 16px;margin:14px 0}
.chart{background:#fcfcfb;border-radius:10px;padding:8px;margin:8px 0}
.chart img{max-width:100%;display:block}
"""

JS = """
function show(mode, scope){
  for (const m of ['current','historical']) for (const s of ['assets','commodities']) {
    document.getElementById(m+'-'+s).classList.toggle('hide', !(m===mode && s===scope));
  }
  for (const b of document.querySelectorAll('[data-mode]')) b.classList.toggle('on', b.dataset.mode===mode);
  for (const b of document.querySelectorAll('[data-scope]')) b.classList.toggle('on', b.dataset.scope===scope);
  window._m = mode; window._s = scope;
}
window._m='current'; window._s='assets';
"""


def _chip(band):
    return f'<span class="{band}">{BAND_SYMBOL[band]} {BAND_LABEL[band]}</span>'


def _current_table(rows, basis_header):
    body = ""
    for r in rows:
        body += (f"<tr><td>{r['name']}</td><td class='n'>{r['share']:.1f}%</td>"
                 f"<td class='n'>{r['d1']:+.1f}</td><td class='n'>{r['d3']:+.1f}</td>"
                 f"<td>{_chip(r['band'])} ({r['percentile']:.0f}th pct)</td><td>{r['basis']}</td></tr>")
    return ("<table><tr><th>Asset</th><th class='n'>Share</th><th class='n'>1m chg (pts)</th>"
            f"<th class='n'>3m chg (pts)</th><th>Crowding</th><th>{basis_header}</th></tr>{body}</table>")


def _history_table(shares, months=12):
    tail = shares.tail(months)
    head = "".join(f"<th class='n'>{d.strftime('%b %y')}</th>" for d in tail.index)
    body = ""
    for col in shares.columns:
        cells = "".join(f"<td class='n'>{v * 100:.1f}</td>" for v in tail[col])
        body += f"<tr><td>{col}</td>{cells}</tr>"
    return f"<div style='overflow-x:auto'><table><tr><th>Share %</th>{head}</tr>{body}</table></div>"


def build_html(asset_rows, comm_rows, asset_shares, comm_shares, skipped, outdir):
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    watch = [(r, "asset class") for r in asset_rows if r["band"] != "green"] + \
            [(r, "commodity") for r in comm_rows if r["band"] != "green"]
    watch.sort(key=lambda x: (x[0]["band"] != "red", -abs(x[0]["percentile"] - 50)))
    if watch:
        items = "".join(f"<li>{_chip(r['band'])} <b>{r['name']}</b> ({kind}) - {r['basis']}, "
                        f"{r['percentile']:.0f}th percentile</li>" for r, kind in watch)
        watch_html = f"<div class='watch'><b>Crowding watch</b><ul>{items}</ul></div>"
    else:
        watch_html = "<div class='watch'><b>Crowding watch</b><br>Nothing is outside its normal range right now.</div>"

    skipped_html = ""
    if skipped:
        skipped_html = ("<div class='watch'><b>Data not loaded this run</b><ul>"
                        + "".join(f"<li>{s}</li>" for s in skipped) + "</ul></div>")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Where Money's At</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>{CSS}</style></head><body>
<h1>Where Money's At</h1>
<div class="meta">Approximate zero-sum shares &middot; updated {generated}</div>
{watch_html}{skipped_html}
<div class="tabs">
  <button data-mode="current" class="on" onclick="show('current', window._s)">Current</button>
  <button data-mode="historical" onclick="show('historical', window._s)">Historical</button>
  <span style="width:24px"></span>
  <button data-scope="assets" class="on" onclick="show(window._m, 'assets')">Asset classes</button>
  <button data-scope="commodities" onclick="show(window._m, 'commodities')">Commodities</button>
</div>

<div id="current-assets">
  <h2>Asset classes - now</h2>
  <div class="chart"><img src="assets_current.png" alt="Asset class shares now"></div>
  {_current_table(asset_rows, "Why this band")}
</div>
<div id="current-commodities" class="hide">
  <h2>Commodities - now</h2>
  <div class="chart"><img src="commodities_current.png" alt="Commodity shares now"></div>
  {_current_table(comm_rows, "Speculators vs hedgers")}
</div>
<div id="historical-assets" class="hide">
  <h2>Asset classes - history</h2>
  <div class="chart"><img src="assets_history.png" alt="Asset class share history"></div>
  {_history_table(asset_shares)}
</div>
<div id="historical-commodities" class="hide">
  <h2>Commodities - history</h2>
  <div class="chart"><img src="commodities_history.png" alt="Commodity share history"></div>
  {_history_table(comm_shares)}
</div>

<p class="note"><b>How to read the crowding band.</b> Asset classes: where today's SHARE sits versus its own
history. Commodities: where the speculator-minus-hedger positioning gap sits versus its own history (CFTC data).
&#9679; green = typical (25th-75th percentile), &#9632; yellow = elevated (10-25th / 75-90th),
&#9650; red = extreme (top or bottom 10%). Extremes are a contrarian warning, not a timing signal.</p>
<p class="note"><b>Limits.</b> Shares move with prices, not only with money flows. Source data is quarterly or
monthly, so "current" means the latest reading available. The asset-class pie mixes scopes and is approximate -
trust the trend and the band more than the absolute level. Commodity open interest is dollar-notional
(contracts x price x contract size) and covers futures only.</p>
<script>{JS}</script></body></html>"""
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    skipped = []

    asset_values, sk = build_asset_values()
    skipped += sk
    asset_shares = to_shares(asset_values)
    asset_rows = snapshot_rows(asset_shares)

    comm_values, crowding, sk = build_commodity_values()
    skipped += sk
    comm_shares = to_shares(comm_values)
    comm_rows = snapshot_rows(comm_shares, crowding)

    chart_current(asset_rows, "Asset classes: share of total now", os.path.join(OUT_DIR, "assets_current.png"))
    chart_history(asset_shares, "Asset classes: share of total over time", os.path.join(OUT_DIR, "assets_history.png"))
    chart_current(comm_rows, "Commodities: share of notional open interest now", os.path.join(OUT_DIR, "commodities_current.png"))
    chart_history(comm_shares, "Commodities: share of notional open interest over time", os.path.join(OUT_DIR, "commodities_history.png"))

    asset_shares.to_csv(os.path.join(OUT_DIR, "shares_assets.csv"))
    comm_shares.to_csv(os.path.join(OUT_DIR, "shares_commodities.csv"))
    build_html(asset_rows, comm_rows, asset_shares, comm_shares, skipped, OUT_DIR)

    with open(os.path.join(OUT_DIR, "latest.json"), "w") as f:
        json.dump({"asset_classes": asset_rows, "commodities": comm_rows, "skipped": skipped},
                  f, indent=2, default=float)
    for r in asset_rows + comm_rows:
        print(f"{r['name']:<24} {r['share']:5.1f}%  {r['band'].upper():<6} ({r['percentile']:.0f}th pct)")
    if skipped:
        print("\nSkipped:", *skipped, sep="\n  ")
    print(f"\nDashboard written to {os.path.join(OUT_DIR, 'index.html')}")


if __name__ == "__main__":
    main()
