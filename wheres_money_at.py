"""
WHERE MONEY'S AT - dashboard builder
======================================

Goal: spot when the finance crowd has moved into an asset, and when an
asset is overcrowded (long or short) so you can get out.

Three modes, each for Asset classes and Commodities:

  CURRENT     - share of the money now, 1m/3m change, red/yellow/green band
  HISTORICAL  - stacked share chart + monthly table (pick 3y / 5y / 10y / all)
  TRENDS      - one section per asset: price chart, own-trend line chart,
                percentile-band chart, a "does it predict?" test, and the
                monthly percentage table underneath

THE PREDICTIVE-POWER TEST (what it does and does not prove)
  For each month we compute where the reading sat in its own history using
  ONLY data available at that date (an "expanding" percentile - no hindsight).
  Then we compare that with the price move over the NEXT 3, 6 and 12 months.
  We report the average forward return when the reading was in its top 10%,
  middle, and bottom 10%, plus a rank correlation. Months overlap, so the
  true sample is far smaller than the row counts. Treat results as hints.

What is measured
  * Asset classes: share of an approximate pie (stocks, Treasuries, cash,
    gold, bitcoin). Share is built partly from price, so a link between share
    and price is partly mechanical.
  * Commodities: share of dollar-notional open interest, AND the
    speculator-minus-hedger positioning gap (CFTC) - the gap is the crowding
    measure that gets banded and tested.

Run:
    pip install -r requirements.txt
    python wheres_money_at.py
Output:
    docs/money/index.html (open this), PNG charts, CSVs, latest.json
"""

import os
import io
import re
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

START = "2006-01-01"          # as far back as the sources reasonably go
OUT_DIR = "docs/money"
HORIZONS = (3, 6, 12)         # months ahead used in the predictive test
MIN_PCT_MONTHS = 36           # need 3 years of history before a percentile counts
DEFAULT_YEARS = "5"           # history shown in tables by default

# FRED values are millions of USD. Gold/Bitcoin = price x approximate amount in
# existence (gold ~6.8bn troy oz above ground, bitcoin ~19.9M coins).
# price_ticker = the market price used to test predictive power for that class.
ASSET_CLASSES = {
    "US stocks":          {"kind": "fred",  "id": "NCBEILQ027S", "scale": 1e6, "price_ticker": "^GSPC"},
    "US Treasuries":      {"kind": "fred",  "id": "GFDEBTN",     "scale": 1e6, "price_ticker": "TLT"},
    "Cash (money funds)": {"kind": "fred",  "id": "MMMFFAQ027S", "scale": 1e6, "price_ticker": None},
    "Gold":               {"kind": "price", "ticker": "GC=F",    "quantity": 6.8e9},
    "Bitcoin":            {"kind": "price", "ticker": "BTC-USD", "quantity": 19.9e6},
}

# (contract size in units, divisor to turn the quoted price into $ per unit).
COMMODITY_SPECS = {
    "WTI Crude Oil":         (1000, 1),
    "Henry Hub Natural Gas": (10000, 1),
    "Copper":                (25000, 1),
    "Gold":                  (100, 1),
    "Silver":                (5000, 1),
    "Platinum":              (50, 1),
    "Palladium":             (100, 1),
    "Corn":                  (5000, 100),   # quoted in cents per bushel
    "Wheat":                 (5000, 100),
    "Soybeans":              (5000, 100),
}

# Validated categorical palette (fixed order) + neutrals.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
OTHER_GREY = "#9a9990"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BAND_FILL = {"red": "#c62828", "yellow": "#e0a800", "green": "#2e8b57"}

BAND_LABEL = {"red": "RED - extreme", "yellow": "YELLOW - elevated", "green": "GREEN - typical"}
BAND_SYMBOL = {"red": "▲", "yellow": "■", "green": "●"}   # shapes, not colour alone


# ----------------------------------------------------------------------
# SMALL HELPERS
# ----------------------------------------------------------------------

def ordinal(n):
    n = int(round(n))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fmt_chg(x):
    if pd.isna(x):
        return "-"
    return "0.0" if abs(x) < 0.05 else f"{x:+.1f}"


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def key(scope, name):
    """Unique id per scope + name (Gold exists as BOTH an asset class and a commodity)."""
    return f"{scope}-{slug(name)}"


def band_from_percentile(p):
    r, y = cvp.BAND_RED_PCT, cvp.BAND_YELLOW_PCT
    if p <= r or p >= 100 - r:
        return "red"
    if p <= y or p >= 100 - y:
        return "yellow"
    return "green"


def _clean(o):
    """Make nested data JSON-safe (NaN -> None, numpy -> python)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


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
    """Returns (dollar value panel, {name: monthly price}, skipped)."""
    values, prices, skipped, price_cols = {}, {}, [], []
    for name, cfg in ASSET_CLASSES.items():
        try:
            if cfg["kind"] == "fred":
                values[name] = (fred_series(cfg["id"]) * cfg["scale"]).resample("ME").last()
                if cfg.get("price_ticker"):
                    try:
                        prices[name] = monthly_close(cfg["price_ticker"])
                    except Exception as e:
                        skipped.append(f"{name} price ({cfg['price_ticker']}): {e}")
            else:
                p = monthly_close(cfg["ticker"])
                prices[name] = p
                values[name] = p * cfg["quantity"]
                price_cols.append(name)
        except Exception as e:  # keep going - report instead of crashing
            skipped.append(f"{name}: {e}")
    if not values:
        raise RuntimeError("No asset-class data could be loaded.")
    panel = pd.concat(values, axis=1).sort_index().loc[START:]
    panel = panel.ffill()
    for n in price_cols:             # e.g. bitcoin did not exist before 2014
        if n in panel:
            panel[n] = panel[n].fillna(0.0)
    return panel.dropna(), prices, skipped


def build_commodity_values():
    """Notional open interest panel, current crowding, per-commodity monthly gap + price."""
    notional, crowding, extras, skipped = {}, {}, {}, []
    for name, cfg in cvp.COMMODITIES.items():
        if name not in COMMODITY_SPECS:
            continue
        size, div = COMMODITY_SPECS[name]
        try:
            cot = cvp.fetch_cot(cfg["market_name_contains"], START)
            price = cvp.fetch_price(cfg["yf_ticker"], START)
            merged = cvp.build_dataset(cot, price)
            idx = pd.to_datetime(merged["report_date"])
            n = merged["open_interest_all"] * merged["close"] / div * size
            notional[name] = pd.Series(n.values, index=idx).resample("ME").last()
            crowding[name] = cvp.classify_band(merged)
            extras[name] = {
                "gap": pd.Series((merged["diff_pct_oi"] * 100).values, index=idx).resample("ME").last(),
                "price": pd.Series(merged["close"].values, index=idx).resample("ME").last(),
            }
        except Exception as e:
            skipped.append(f"{name}: {e}")
    if not notional:
        raise RuntimeError("No commodity data could be loaded.")
    panel = pd.concat(notional, axis=1).sort_index().ffill().dropna()
    return panel, crowding, extras, skipped


# ----------------------------------------------------------------------
# ANALYSIS
# ----------------------------------------------------------------------

def to_shares(values):
    return values.div(values.sum(axis=1), axis=0)


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
            basis = "share high vs its history" if pct >= 50 else "share low vs its history"
        rows.append({"name": name, "share": cur * 100, "d1": d1, "d3": d3,
                     "band": band, "percentile": pct, "basis": basis})
    return sorted(rows, key=lambda r: -r["share"])


def expanding_pct(s, min_periods=MIN_PCT_MONTHS):
    """Percentile of each value among values up to and including that date (no hindsight)."""
    vals = s.values.astype(float)
    out = np.full(len(vals), np.nan)
    for i in range(min_periods - 1, len(vals)):
        out[i] = (vals[: i + 1] < vals[i]).mean() * 100
    return pd.Series(out, index=s.index)


def predictive_stats(pct, price):
    """Forward-return comparison by percentile bucket, per horizon (months)."""
    p = pct.dropna()
    r = cvp.BAND_RED_PCT
    stats = []
    for h in HORIZONS:
        fwd = (price.shift(-h) / price - 1).reindex(p.index)
        df = pd.DataFrame({"pct": p, "fwd": fwd}).dropna()
        if len(df) < 24:
            stats.append({"h": h, "n": len(df)})
            continue
        hi = df[df["pct"] >= 100 - r]["fwd"]
        lo = df[df["pct"] <= r]["fwd"]
        mid = df[(df["pct"] > r) & (df["pct"] < 100 - r)]["fwd"]
        stats.append({
            "h": h, "n": len(df),
            "rho": df["pct"].rank().corr(df["fwd"].rank()),
            "hi_avg": hi.mean() * 100 if len(hi) else np.nan, "hi_n": len(hi),
            "mid_avg": mid.mean() * 100 if len(mid) else np.nan, "mid_n": len(mid),
            "lo_avg": lo.mean() * 100 if len(lo) else np.nan, "lo_n": len(lo),
        })
    return stats


def verdict(stats):
    s6 = next((s for s in stats if s["h"] == 6 and "rho" in s), None)
    if not s6:
        return "Not enough history to test."
    rho, a = s6["rho"], abs(s6["rho"])
    if a < 0.1:
        text = f"No meaningful link found between this reading and the next 6 months of price (rank correlation {rho:+.2f})."
    else:
        strength = "very weak" if a < 0.2 else "weak" if a < 0.3 else "moderate"
        direction = "higher" if rho > 0 else "lower"
        text = (f"A {strength} tendency: when this reading was high, the next 6 months of price "
                f"were {direction} on average (rank correlation {rho:+.2f}).")
    if min(s6["hi_n"], s6["lo_n"]) < 12:
        text += " Few extreme months, so the extreme-reading averages are shaky."
    return text


def make_trend(name, share, metric, price, metric_label):
    """Bundle everything needed to draw and describe one instrument's trend."""
    metric = metric.dropna()
    pct = expanding_pct(metric)
    band = pct.map(lambda p: band_from_percentile(p) if pd.notna(p) else None)
    info = {"name": name, "share": share.dropna(), "metric": metric, "metric_label": metric_label,
            "price": price, "pct": pct, "band": band, "stats": None, "verdict": None}
    if price is not None and price.dropna().shape[0] > 24:
        info["stats"] = predictive_stats(pct, price)
        info["verdict"] = verdict(info["stats"])
    else:
        info["price"] = None
        info["verdict"] = "No price series for this item, so predictive power cannot be tested."
    return info


# ----------------------------------------------------------------------
# CHARTS (thin marks, recessive grid, direct end label; panels share one x-axis)
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
    n_real = pct.shape[1] - (1 if "Other" in pct.columns else 0)
    colors = PALETTE[:n_real] + ([OTHER_GREY] if "Other" in pct.columns else [])
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


def chart_trend(info, share_title, pct_title, fname):
    price, share, pct = info["price"], info["share"], info["pct"]
    has_price = price is not None
    n = 3 if has_price else 2
    ratios = [1.6, 1.3, 1.5] if has_price else [1.3, 1.5]
    fig, axes = plt.subplots(n, 1, figsize=(7.2, 2.7 * n + 0.3), sharex=True,
                             gridspec_kw={"height_ratios": ratios}, facecolor=SURFACE)
    axes = np.atleast_1d(axes)
    i = 0
    if has_price:
        ax = axes[i]; i += 1
        ax.plot(price.index, price.values, color=INK, lw=1.4)
        hi = pct[pct >= 100 - cvp.BAND_RED_PCT].dropna().index.intersection(price.index)
        lo = pct[pct <= cvp.BAND_RED_PCT].dropna().index.intersection(price.index)
        if len(hi):
            ax.scatter(hi, price.loc[hi], marker="^", s=34, color=PALETTE[1], edgecolor=SURFACE,
                       linewidth=0.8, zorder=5, label="Reading in top 10% of its own history")
        if len(lo):
            ax.scatter(lo, price.loc[lo], marker="v", s=34, color=PALETTE[0], edgecolor=SURFACE,
                       linewidth=0.8, zorder=5, label="Reading in bottom 10% of its own history")
        _style(ax)
        ax.set_title("Price (monthly close)", color=INK, fontsize=10, loc="left")
        if len(hi) or len(lo):
            ax.legend(loc="upper left", frameon=False, fontsize=8.5, labelcolor=INK2)
    ax = axes[i]; i += 1
    ax.plot(share.index, share.values, color=PALETTE[0], lw=1.8)
    last = share.iloc[-1]
    ax.annotate(f"{last:.1f}%", (share.index[-1], last), xytext=(5, 0), textcoords="offset points",
                fontsize=9, color=INK, va="center")
    _style(ax)
    ax.set_title(share_title, color=INK, fontsize=10, loc="left")
    ax = axes[i]
    for lo_, hi_, band, a in ((0, cvp.BAND_RED_PCT, "red", .20), (cvp.BAND_RED_PCT, cvp.BAND_YELLOW_PCT, "yellow", .20),
                              (cvp.BAND_YELLOW_PCT, 100 - cvp.BAND_YELLOW_PCT, "green", .10),
                              (100 - cvp.BAND_YELLOW_PCT, 100 - cvp.BAND_RED_PCT, "yellow", .20),
                              (100 - cvp.BAND_RED_PCT, 100, "red", .20)):
        ax.axhspan(lo_, hi_, color=BAND_FILL[band], alpha=a, lw=0)
    p = pct.dropna()
    ax.plot(p.index, p.values, color=INK, lw=1.4)
    ax.set_ylim(0, 100)
    ax.set_yticks([10, 25, 75, 90])
    _style(ax)
    ax.grid(False)
    ax.set_title(pct_title, color=INK, fontsize=10, loc="left")
    for y, t in ((5, "extreme low"), (17.5, "elevated"), (50, "typical"), (82.5, "elevated"), (95, "extreme high")):
        ax.text(1.01, y, t, transform=ax.get_yaxis_transform(), fontsize=8, color=INK2, va="center")
    fig.subplots_adjust(left=0.09, right=0.83, top=0.965, bottom=0.05, hspace=0.42)
    fig.savefig(fname, dpi=130, facecolor=SURFACE)
    plt.close(fig)


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------

CSS = """
:root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3df;--card:#f4f3ef;--accent:#2a78d6;
 --red:#c62828;--yellow:#8a6400;--green:#1b6e2e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
 --line:#34332f;--card:#242422;--accent:#3987e5;--red:#e66767;--yellow:#e0b030;--green:#4cc36b}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:-apple-system,Segoe UI,Arial,sans-serif;
 max-width:1000px;margin:24px auto;padding:0 14px}
h1{margin:0 0 4px;font-size:1.6em}h2{margin:24px 0 8px;font-size:1.25em}h3{margin:28px 0 6px;font-size:1.1em}
.meta,.note{color:var(--ink2);font-size:.86em;line-height:1.45}
.tabs{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 6px;align-items:center}
.tabs button,.tabs select{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:8px 14px;
 border-radius:8px;cursor:pointer;font-size:.92em}
.tabs button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.sep{flex-basis:100%;height:0}
.hide{display:none}
.scroll{overflow:auto;max-height:430px;border:1px solid var(--line);border-radius:8px;margin:8px 0 14px}
.scroll.full{max-height:none}
table{border-collapse:collapse;width:100%;font-size:.84em}
th,td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
thead th{position:sticky;top:0;background:var(--card);color:var(--ink2);z-index:2;font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td:first-child,th:first-child{position:sticky;left:0;background:var(--bg);z-index:1}
thead th:first-child{background:var(--card);z-index:3}
.red{color:var(--red);font-weight:600}.yellow{color:var(--yellow);font-weight:600}.green{color:var(--green);font-weight:600}
.watch,.stat{background:var(--card);border-radius:10px;padding:12px 14px;margin:12px 0;font-size:.92em;line-height:1.45}
.watch ul{margin:6px 0 0;padding-left:18px}
.chart{background:#fcfcfb;border-radius:10px;padding:6px;margin:8px 0}
.chart img{max-width:100%;height:auto;display:block}
a.jump{color:var(--accent);text-decoration:none;border-bottom:1px dotted var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 12px}
.chips a{border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:.84em;background:var(--card)}
@media (max-width:520px){th,td{padding:6px 6px}body{padding:0 10px}}
"""

JS = """
const MODES=['current','historical','trends'], SCOPES=['assets','commodities'];
window._m='current'; window._s='assets';
function show(mode, scope){
  for (const m of MODES) for (const s of SCOPES)
    document.getElementById(m+'-'+s).classList.toggle('hide', !(m===mode && s===scope));
  for (const b of document.querySelectorAll('[data-mode]')) b.classList.toggle('on', b.dataset.mode===mode);
  for (const b of document.querySelectorAll('[data-scope]')) b.classList.toggle('on', b.dataset.scope===scope);
  window._m = mode; window._s = scope;
}
function jump(scope, slug){
  show('trends', scope);
  const el = document.getElementById('t-'+slug);
  if (el) el.scrollIntoView({behavior:'smooth', block:'start'});
}
function setYears(v){
  let cutoff = '0000-00-00';
  if (v !== 'all'){ const d = new Date(); d.setFullYear(d.getFullYear() - parseInt(v)); cutoff = d.toISOString().slice(0,10); }
  for (const tr of document.querySelectorAll('tr[data-date]')) tr.classList.toggle('hide', tr.dataset.date < cutoff);
}
window.addEventListener('load', () => setYears(document.getElementById('yrs').value));
"""


def _chip(band):
    return f'<span class="{band}">{BAND_SYMBOL[band]} {BAND_LABEL[band]}</span>'


def _chip_short(band, pct):
    return f'<span class="{band}">{BAND_SYMBOL[band]} {band.upper()}</span> <span class="note">{ordinal(pct)}</span>'


def _name_link(name, scope):
    k = key(scope, name)
    return f'<a class="jump" href="#t-{k}" onclick="jump(\'{scope}\',\'{k}\');return false">{name}</a>'


def _current_table(rows, basis_header, scope):
    body = ""
    for r in rows:
        body += (f"<tr><td>{_name_link(r['name'], scope)}</td><td class='n'>{r['share']:.1f}%</td>"
                 f"<td class='n'>{_chip_short(r['band'], r['percentile'])}</td>"
                 f"<td class='n'>{fmt_chg(r['d1'])}</td><td class='n'>{fmt_chg(r['d3'])}</td>"
                 f"<td>{r['basis']}</td></tr>")
    return ("<div class='scroll full'><table><thead><tr><th>Asset</th><th class='n'>Share</th><th class='n'>Crowding</th>"
            f"<th class='n'>1m</th><th class='n'>3m</th><th>{basis_header}</th></tr></thead>{body}</table></div>"
            "<p class='note'>1m / 3m = change in share, in percentage points. Crowding shows the band and where today's "
            "reading sits in its own history (e.g. 92nd = higher than 92% of past readings).</p>")


def _history_table(shares):
    """All months, newest first; the year selector hides older rows."""
    head = "".join(f"<th class='n'>{c}</th>" for c in shares.columns)
    body = ""
    for d in shares.index[::-1]:
        cells = "".join(f"<td class='n'>{v * 100:.1f}</td>" for v in shares.loc[d])
        body += f"<tr data-date='{d.strftime('%Y-%m-%d')}'><td>{d.strftime('%b %Y')}</td>{cells}</tr>"
    return f"<div class='scroll'><table><thead><tr><th>Share %</th>{head}</tr></thead>{body}</table></div>"


def _stats_table(stats):
    rows = ""
    for s in stats:
        if "rho" not in s:
            rows += f"<tr><td>{s['h']}m</td><td colspan='5'>not enough data</td></tr>"
            continue
        def cell(avg, n):
            return "-" if pd.isna(avg) else f"{avg:+.1f}% <span class='note'>(n={n})</span>"
        rows += (f"<tr><td>{s['h']}m</td><td class='n'>{cell(s['hi_avg'], s['hi_n'])}</td>"
                 f"<td class='n'>{cell(s['mid_avg'], s['mid_n'])}</td><td class='n'>{cell(s['lo_avg'], s['lo_n'])}</td>"
                 f"<td class='n'>{s['rho']:+.2f}</td></tr>")
    return ("<div class='scroll full'><table><thead><tr><th>Next</th>"
            "<th class='n'>After top 10%</th><th class='n'>After middle</th>"
            "<th class='n'>After bottom 10%</th><th class='n'>Corr.</th></tr></thead>"
            f"{rows}</table></div>")


def _trend_table(info, is_commodity):
    dates = info["share"].index
    gap_header = "<th class='n'>Spec-hedger gap (% OI)</th>" if is_commodity else ""
    body = ""
    for d in dates[::-1]:
        share = info["share"].get(d, np.nan)
        pct = info["pct"].get(d, np.nan)
        band = info["band"].get(d, None)
        price = info["price"].get(d, np.nan) if info["price"] is not None else np.nan
        prev = info["share"].shift(1).get(d, np.nan)
        chg = share - prev if pd.notna(prev) else np.nan
        band_html = _chip(band) if isinstance(band, str) else "-"
        gap = ""
        if is_commodity:
            g = info["metric"].get(d, np.nan)
            gap = f"<td class='n'>{'-' if pd.isna(g) else f'{g:+.1f}'}</td>"
        body += (f"<tr data-date='{d.strftime('%Y-%m-%d')}'><td>{d.strftime('%b %Y')}</td>"
                 f"<td class='n'>{share:.1f}%</td><td class='n'>{fmt_chg(chg)}</td>{gap}"
                 f"<td class='n'>{'-' if pd.isna(pct) else ordinal(pct)}</td><td>{band_html}</td>"
                 f"<td class='n'>{'-' if pd.isna(price) else f'{price:,.2f}'}</td></tr>")
    return (f"<div class='scroll'><table><thead><tr><th>Month</th><th class='n'>Share</th><th class='n'>1m chg (pts)</th>"
            f"{gap_header}<th class='n'>Percentile</th><th>Band</th><th class='n'>Price</th></tr></thead>{body}</table></div>")


def _trend_section(info, chart_file, is_commodity):
    name = info["name"]
    scope = "commodities" if is_commodity else "assets"
    last_pct, last_band = info["pct"].dropna(), info["band"].dropna()
    now = (f"{_chip(last_band.iloc[-1])} &middot; {ordinal(last_pct.iloc[-1])} percentile of its own history"
           if len(last_pct) else "Not enough history yet for a percentile")
    caveat = ("Months overlap, so the real sample is much smaller than the row counts. A weak result is a hint, not proof, "
              "and crowds can stay crowded for a long time.")
    mech = ("" if is_commodity else " Share is built partly from price, so part of any link here is mechanical.")
    stats_html = ""
    if info["stats"]:
        stats_html = _stats_table(info["stats"])
    return f"""
<section id="t-{key(scope, name)}">
  <h3>{name}</h3>
  <div class="meta">{now}</div>
  <div class="chart"><img src="{chart_file}" alt="{name} trend, price and percentile bands"></div>
  <div class="stat"><b>Does it predict?</b> {info['verdict']}{mech}<br><span class="note">{caveat} Percentiles use only data
  available at each date, so there is no hindsight.</span></div>
  {stats_html}
  <b>Month by month</b> <span class="note">(use the history selector at the top)</span>
  {_trend_table(info, is_commodity)}
</section>"""


def build_html(asset_rows, comm_rows, asset_shares, comm_shares, asset_trends, comm_trends, skipped, outdir):
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    watch = [(r, "asset class") for r in asset_rows if r["band"] != "green"] + \
            [(r, "commodity") for r in comm_rows if r["band"] != "green"]
    watch.sort(key=lambda x: (x[0]["band"] != "red", -abs(x[0]["percentile"] - 50)))
    if watch:
        items = "".join(f"<li>{_chip(r['band'])} <b>{r['name']}</b> ({kind}) - {r['basis']}, "
                        f"{ordinal(r['percentile'])} percentile</li>" for r, kind in watch)
        watch_html = f"<div class='watch'><b>Crowding watch</b><ul>{items}</ul></div>"
    else:
        watch_html = "<div class='watch'><b>Crowding watch</b><br>Nothing is outside its normal range right now.</div>"

    skipped_html = ""
    if skipped:
        skipped_html = ("<div class='watch'><b>Data not loaded this run</b><ul>"
                        + "".join(f"<li>{s}</li>" for s in skipped) + "</ul></div>")

    def chips(trends, scope):
        return "<div class='chips'>" + "".join(
            f"<a class='jump' href='#t-{key(scope, t['name'])}' onclick=\"jump('{scope}','{key(scope, t['name'])}');return false\">{t['name']}</a>"
            for t in trends) + "</div>"

    asset_sections = "".join(_trend_section(t, f"trend_{key('assets', t['name'])}.png", False) for t in asset_trends)
    comm_sections = "".join(_trend_section(t, f"trend_{key('commodities', t['name'])}.png", True) for t in comm_trends)

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Where Money's At</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>{CSS}</style></head><body>
<h1>Where Money's At</h1>
<div class="meta">Approximate zero-sum shares &middot; updated {generated}</div>
{watch_html}{skipped_html}
<div class="tabs">
  <button data-mode="current" class="on" onclick="show('current', window._s)">Current</button>
  <button data-mode="historical" onclick="show('historical', window._s)">Historical</button>
  <button data-mode="trends" onclick="show('trends', window._s)">Trends</button>
  <span class="sep"></span>
  <button data-scope="assets" class="on" onclick="show(window._m, 'assets')">Asset classes</button>
  <button data-scope="commodities" onclick="show(window._m, 'commodities')">Commodities</button>
  <span class="sep"></span>
  <label class="note">History in tables:
    <select id="yrs" onchange="setYears(this.value)">
      <option value="3">3 years</option><option value="5" selected>5 years</option>
      <option value="10">10 years</option><option value="all">All</option></select></label>
</div>

<div id="current-assets">
  <h2>Asset classes - now</h2>
  <div class="chart"><img src="assets_current.png" alt="Asset class shares now"></div>
  {_current_table(asset_rows, "Why this band", "assets")}
  <p class="note">Tap an asset name to see its trend and predictive test.</p>
</div>
<div id="current-commodities" class="hide">
  <h2>Commodities - now</h2>
  <div class="chart"><img src="commodities_current.png" alt="Commodity shares now"></div>
  {_current_table(comm_rows, "Speculators vs hedgers", "commodities")}
  <p class="note">Tap a commodity name to see its trend and predictive test.</p>
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
<div id="trends-assets" class="hide">
  <h2>Asset classes - trends</h2>
  {chips(asset_trends, "assets")}
  {asset_sections}
</div>
<div id="trends-commodities" class="hide">
  <h2>Commodities - trends</h2>
  {chips(comm_trends, "commodities")}
  {comm_sections}
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

    asset_values, asset_prices, sk = build_asset_values()
    skipped += sk
    asset_shares = to_shares(asset_values)
    asset_rows = snapshot_rows(asset_shares)

    comm_values, crowding, extras, sk = build_commodity_values()
    skipped += sk
    comm_shares = to_shares(comm_values)
    comm_rows = snapshot_rows(comm_shares, crowding)

    # Trend bundles (ordered like the Current tables)
    asset_trends = []
    for r in asset_rows:
        n = r["name"]
        share = asset_shares[n] * 100
        t = make_trend(n, share, share, asset_prices.get(n), "Share percentile")
        asset_trends.append(t)
    comm_trends = []
    for r in comm_rows:
        n = r["name"]
        if n not in extras:
            continue
        t = make_trend(n, comm_shares[n] * 100, extras[n]["gap"], extras[n]["price"], "Spec-hedger gap percentile")
        comm_trends.append(t)

    for t in asset_trends:
        chart_trend(t, "Share of total (%)",
                    "Share: percentile in own history",
                    os.path.join(OUT_DIR, f"trend_{key('assets', t['name'])}.png"))
    for t in comm_trends:
        chart_trend(t, "Share of notional open interest (%)",
                    "Spec-minus-hedger gap: percentile in own history",
                    os.path.join(OUT_DIR, f"trend_{key('commodities', t['name'])}.png"))

    chart_current(asset_rows, "Asset classes: share of total now", os.path.join(OUT_DIR, "assets_current.png"))
    chart_history(asset_shares, "Asset classes: share of total over time", os.path.join(OUT_DIR, "assets_history.png"))
    chart_current(comm_rows, "Commodities: share of notional open interest now", os.path.join(OUT_DIR, "commodities_current.png"))
    chart_history(comm_shares, "Commodities: share of notional open interest over time", os.path.join(OUT_DIR, "commodities_history.png"))

    asset_shares.to_csv(os.path.join(OUT_DIR, "shares_assets.csv"))
    comm_shares.to_csv(os.path.join(OUT_DIR, "shares_commodities.csv"))
    build_html(asset_rows, comm_rows, asset_shares, comm_shares, asset_trends, comm_trends, skipped, OUT_DIR)

    summary = {
        "asset_classes": asset_rows, "commodities": comm_rows, "skipped": skipped,
        "predictive": {t["name"]: {"verdict": t["verdict"], "stats": t["stats"]} for t in asset_trends + comm_trends},
    }
    with open(os.path.join(OUT_DIR, "latest.json"), "w") as f:
        json.dump(_clean(summary), f, indent=2)
    for r in asset_rows + comm_rows:
        print(f"{r['name']:<24} {r['share']:5.1f}%  {r['band'].upper():<6} ({ordinal(r['percentile'])} pct)")
    print("\nPredictive-power verdicts:")
    for t in asset_trends + comm_trends:
        print(f"  {t['name']}: {t['verdict']}")
    if skipped:
        print("\nSkipped:", *skipped, sep="\n  ")
    print(f"\nDashboard written to {os.path.join(OUT_DIR, 'index.html')}")


if __name__ == "__main__":
    main()
