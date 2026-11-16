"""One-off reconnaissance: what do the real data sources actually contain?
Runs in GitHub Actions (which has internet) and writes tools/probe_result.json."""
import io, json, time, traceback
import requests, pandas as pd

OUT = {"cftc": {}, "fred": {}, "yahoo": {}, "errors": []}
CFTC = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

def cftc(params):
    r = requests.get(CFTC, params=params, timeout=90)
    r.raise_for_status()
    return r.json()

try:  # field names
    OUT["cftc"]["sample_keys"] = sorted(cftc({"$limit": 1})[0].keys())
except Exception as e:
    OUT["errors"].append("sample: " + repr(e))

try:  # every market with a report in the last ~3 months
    rows = cftc({"$select": "cftc_contract_market_code as code, market_and_exchange_names as name, "
                            "min(report_date_as_yyyy_mm_dd) as first, max(report_date_as_yyyy_mm_dd) as last, count(*) as n",
                 "$group": "code, name", "$having": "max(report_date_as_yyyy_mm_dd) > '2026-08-01T00:00:00.000'",
                 "$limit": 5000})
    OUT["cftc"]["active"] = rows
except Exception as e:
    OUT["errors"].append("active: " + repr(e))

CODES = ["067651", "023651", "022651", "111659", "088691", "084691", "085692", "076651", "075651", "002602",
         "001602", "001612", "005602", "026603", "007601", "080732", "083731", "033661", "073732", "057642",
         "054642", "13874A", "209742", "043602", "042601", "020601", "133741", "098662", "1170E1", "099741",
         "097741", "239742"]
try:  # full name history per code (shows renames)
    inlist = ",".join(f"'{c}'" for c in CODES)
    rows = cftc({"$select": "cftc_contract_market_code as code, market_and_exchange_names as name, "
                            "min(report_date_as_yyyy_mm_dd) as first, max(report_date_as_yyyy_mm_dd) as last, count(*) as n",
                 "$where": f"cftc_contract_market_code in ({inlist})", "$group": "code, name", "$limit": 5000})
    OUT["cftc"]["history"] = rows
except Exception as e:
    OUT["errors"].append("history: " + repr(e))

try:  # one full latest row for NYMEX WTI so numbers can be checked by eye
    OUT["cftc"]["wti_latest"] = cftc({"$where": "cftc_contract_market_code='067651'",
                                      "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 2})
except Exception as e:
    OUT["errors"].append("wti_latest: " + repr(e))

FRED = ["NCBEILQ027S", "FBCELLQ027S", "BOGZ1LM893064105Q", "BOGZ1FL893064105Q", "MVEONWMVBSNNCB",
        "FYGFDPUN", "GFDEBTN", "MMMFFAQ027S", "WRMFNS", "WRMFSL", "WIMFNS", "WIMFSL", "RMFSL",
        "BOGZ1FL153064105Q", "HNOCEAQ027S", "BOGZ1LM153064105Q", "TCMILBSHNO", "DGS10", "SP500",
        "BOGZ1FL893064125Q", "BOGZ1LM883164105Q", "CORPBNDS", "BOGZ1FL893163005Q", "BOGZ1FL634090005Q"]
for sid in FRED:
    try:
        r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=40)
        if r.status_code != 200 or "," not in r.text[:200]:
            OUT["fred"][sid] = {"ok": False, "status": r.status_code, "head": r.text[:80]}
            continue
        df = pd.read_csv(io.StringIO(r.text)); df.columns = ["date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce"); df = df.dropna()
        OUT["fred"][sid] = {"ok": True, "n": len(df), "first": df.date.iloc[0], "last": df.date.iloc[-1],
                            "last_value": float(df.value.iloc[-1])}
    except Exception as e:
        OUT["fred"][sid] = {"ok": False, "err": repr(e)}
    time.sleep(0.3)

import yfinance as yf
TICKERS = ["CL=F", "NG=F", "HO=F", "RB=F", "GC=F", "SI=F", "HG=F", "PL=F", "PA=F", "ZC=F", "ZW=F", "KE=F", "ZS=F",
           "SB=F", "KC=F", "CT=F", "CC=F", "LE=F", "HE=F", "ES=F", "NQ=F", "ZN=F", "ZB=F", "BTC=F", "BTC-USD", "DX=F",
           "DX-Y.NYB", "^VIX", "^GSPC", "^TNX", "TLT", "IEF", "SPY", "RSP", "QQQ", "IWM", "XLK", "XLF", "XLV", "XLY",
           "XLC", "XLI", "XLP", "XLE", "XLU", "XLRE", "XLB", "BZ=F", "EURUSD=X", "6E=F", "RTY=F"]
for t in TICKERS:
    try:
        px = yf.download(t, start="2000-01-01", progress=False, auto_adjust=True)
        if px.empty:
            OUT["yahoo"][t] = {"ok": False, "err": "empty"}; continue
        c = px["Close"]; c = c.iloc[:, 0] if hasattr(c, "columns") else c
        c = c.dropna()
        OUT["yahoo"][t] = {"ok": True, "n": len(c), "first": str(c.index[0])[:10], "last": str(c.index[-1])[:10],
                           "last_close": float(c.iloc[-1])}
    except Exception as e:
        OUT["yahoo"][t] = {"ok": False, "err": repr(e)[:200]}

try:  # can yfinance give shares outstanding / market cap cheaply? (for a stocks drill-down)
    fi = yf.Ticker("AAPL").fast_info
    OUT["yahoo"]["_fast_info_AAPL"] = {"market_cap": float(fi["market_cap"]), "shares": float(fi["shares"])}
except Exception as e:
    OUT["yahoo"]["_fast_info_AAPL"] = {"ok": False, "err": repr(e)[:300]}
try:
    h = yf.Ticker("SPY").funds_data.sector_weightings
    OUT["yahoo"]["_spy_sector_weightings"] = {str(k): float(v) for k, v in dict(h).items()}
except Exception as e:
    OUT["yahoo"]["_spy_sector_weightings"] = {"ok": False, "err": repr(e)[:300]}
try:
    th = yf.Ticker("SPY").funds_data.top_holdings
    OUT["yahoo"]["_spy_top_holdings"] = th.reset_index().head(12).to_dict(orient="records")
except Exception as e:
    OUT["yahoo"]["_spy_top_holdings"] = {"ok": False, "err": repr(e)[:300]}

json.dump(OUT, open("tools/probe_result.json", "w"), indent=1, default=str)
print(json.dumps({k: (len(v) if hasattr(v, "__len__") else v) for k, v in OUT.items()}))
