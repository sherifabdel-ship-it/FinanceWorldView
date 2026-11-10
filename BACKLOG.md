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
- [ ] **Verify FRED series IDs** after the first real run - check the "Data not loaded"
      box on the dashboard. `NCBEILQ027S` covers nonfinancial corporate equities only,
      so it understates total US equities; swap in a broader series if you find one.
- [ ] **Back-adjusted futures prices** - Yahoo continuous contracts show roll gaps.

## Earlier items still open

- [ ] Confirm CFTC market-name strings for Copper, Gold, Silver, Platinum, Palladium,
      Corn, Wheat, Soybeans on the first real COT run (use `list_available_markets`).
- [ ] Set the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` repo secrets.
