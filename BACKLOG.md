# Backlog - delayed tasks

Things we agreed to build later. Review this file whenever you open the repo.
Last updated: 2026-11-09.

## Where Money's At

- [ ] **Stocks drill-down** - click "US stocks" to see which sectors, then which stocks,
      hold the concentration. Needs a market-cap-by-sector source (not available free
      and reliable yet; candidates: S&P 500 constituents + market caps, sector ETF holdings).
- [ ] **Flows view** - new money moving in/out, separate from valuation-driven share.
      Candidates: weekly money-market fund assets (FRED WRMFNS / WIMFNS - verify IDs),
      ETF flow data.
- [ ] **Stock-market crowding inputs** - NAAIM exposure index (weekly), AAII allocation
      survey (monthly), 13F filings (quarterly, ~45 days stale). These tell you when the
      finance community is crowded into equities, which COT alone cannot.
- [ ] **Telegram crowding alerts for this dashboard** - message when any asset or commodity
      enters RED, or moves to a different band. (`docs/money/latest.json` already has what
      the alert needs.)
- [x] FRED series IDs loaded on the first real run (nothing skipped). Still open: the definitions below.
- [ ] **Tighten the asset-class definitions.** `NCBEILQ027S` covers nonfinancial corporate equities only,
      so it understates total US equities; swap in a broader series if you find one.
- [ ] **Back-adjusted futures prices** - Yahoo continuous contracts show roll gaps.

## Earlier items still open

- [x] CFTC market-name strings for all commodities - confirmed on the first real run (none skipped).
- [ ] Set the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` repo secrets.

## Added 2026-11-10
- [x] Trends view with per-asset price/share/percentile charts, predictive test, 5-year tables, history back to 2006.
- [ ] **Check the first real predictive results** - decide which readings (if any) deserve an alert.
- [ ] Add a "Telegram alert when something enters RED" using `docs/money/latest.json`.
