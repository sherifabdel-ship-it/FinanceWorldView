# FinanceWorldView - Market Dashboards: Seasonality, Smart Money / Dumb Money (COT), Where Money's At

Two independent tools, each with its own GitHub Actions workflow, each
producing a simple HTML dashboard you can view locally or publish with
GitHub Pages.

**Important:** these scripts need internet access to CFTC's public data
API and Yahoo Finance. Run them on your own computer, in GitHub Actions
(where they'll run automatically), or in Google Colab.

## Files

| File | What it is |
|---|---|
| `seasonality_analysis.py` | Builds the seasonality dashboard |
| `cot_vs_price.py` | Builds the COT ("smart money vs. dumb money") dashboard |
| `wheres_money_at.py` | Builds the "Where Money's At" dashboard (asset-class + commodity shares, crowding bands) |
| `BACKLOG.md` | Delayed tasks - check it when you open the repo |
| `.github/workflows/money.yml` | Runs the Where Money's At dashboard every Saturday |
| `telegram_alert.py` | Sends the weekly Telegram alert (reads output from `cot_vs_price.py`) |
| `requirements.txt` | Python packages both scripts need |
| `docs/index.html` | Landing page linking both dashboards |
| `.github/workflows/seasonality.yml` | Runs the seasonality script automatically every Monday |
| `.github/workflows/cot.yml` | Runs the COT script automatically every Saturday |

**Keep this exact folder structure** when you save/upload the files —
GitHub Actions and GitHub Pages both depend on files being at exact paths:

```
your-repo/
├── cot_vs_price.py
├── seasonality_analysis.py
├── telegram_alert.py
├── wheres_money_at.py
├── BACKLOG.md
├── requirements.txt
├── README.md
├── docs/
│   └── index.html
└── .github/
    └── workflows/
        ├── seasonality.yml
        ├── money.yml
        └── cot.yml
```

## One-time setup (running locally)

1. Install Python 3.10+: https://www.python.org/downloads/
2. In a terminal, in this folder:
   ```
   pip install -r requirements.txt
   ```
3. Run either script:
   ```
   python seasonality_analysis.py
   python cot_vs_price.py
   ```
4. Open the dashboard: `docs/seasonality/index.html` or `docs/cot/index.html`
   in your browser.

## Seasonality dashboard (`seasonality_analysis.py`)

**One chart per instrument** — average return for each calendar month,
with a variability band (the whiskers) and a "% of years positive" label
on every bar, so you can see at a glance both the size AND the reliability
of the seasonal pattern. The current month's bar is outlined in black.

Covers: Natural Gas, Corn, Heating Oil, WTI Crude Oil, and the 10-Year
Treasury yield (as a rates example). Edit the `TICKERS` dictionary at the
top of the file to add/remove instruments — any Yahoo Finance ticker works.

Output: `docs/seasonality/index.html` (open this), plus
`docs/seasonality/charts/*.png` and `monthly_stats.csv`.

## COT dashboard (`cot_vs_price.py`)

Pulls the CFTC Legacy Commitments of Traders report (Commercial =
hedgers/"smart money", Non-Commercial = speculators) since 2015, merges it
with weekly futures prices, and shows:

- A **current positioning table**: for every market, the differential
  between speculator and hedger positioning (as a % of open interest),
  and a **red / yellow / green range** showing where today's reading sits
  versus that market's own full history:
  - 🟢 **Green** = typical (25th-75th percentile of history)
  - 🟡 **Yellow** = elevated (10th-25th or 75th-90th percentile)
  - 🔴 **Red** = extreme (top or bottom 10% of history)
- What price has historically done after similar extremes.
- The full price + positioning chart underneath.

Covers by default: WTI Crude Oil, Henry Hub Natural Gas, Copper, Silver,
Platinum, Palladium, Corn, Wheat, and Soybeans. Edit the `COMMODITIES`
dictionary to add/remove markets (see the comment at the bottom of the
file for how to find the exact CFTC market name for a new commodity).

Output: `docs/cot/index.html` (open this), plus chart PNGs, merged CSVs,
and `docs/cot/latest_signal.json` (a compact summary the Telegram alert
script reads).

## Weekly Telegram alert (`telegram_alert.py`)

Sends one Telegram message listing every instrument's current band
(🔴/🟡/🟢) and differential, right after `cot_vs_price.py` runs in the
same GitHub Actions job — so it fires automatically a few minutes after
CFTC's weekly Friday release.

**One-time setup:**
1. On Telegram, message **@BotFather**, send `/newbot`, and follow the
   prompts. You'll get a bot token that looks like `123456789:AAExample`.
2. Send your new bot any message (e.g. "hi"), then open this URL in a
   browser (replace `<TOKEN>`):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Look for `"chat":{"id": ...}` in the response — that number is your
   chat ID. (For a group: add the bot to the group first, send a message
   in the group, then check the same URL.)
3. In your GitHub repo: **Settings → Secrets and variables → Actions →
   New repository secret**. Add two secrets:
   - `TELEGRAM_BOT_TOKEN` = the token from step 1
   - `TELEGRAM_CHAT_ID` = the chat ID from step 2
4. Done — `cot.yml` already passes these secrets to `telegram_alert.py`
   automatically. No code changes needed.

If the secrets aren't set, the script just prints a reminder and skips
sending — it won't break the workflow.

**On timing:** CFTC publishes around 3:30pm US Eastern Time. Because
Eastern Time shifts against UTC with Daylight Saving, the workflow is
scheduled at two UTC times (covering both EDT and EST) so it stays close
to "a few minutes after release" year-round. A duplicate-prevention check
means you'll only ever get ONE Telegram message per week even though the
workflow technically runs twice a week — the second run just refreshes
the dashboard silently.

## Automating both with GitHub Actions + publishing with GitHub Pages

1. Push all the files above into a GitHub repo, keeping the exact
   folder structure (including the two files inside `.github/workflows/`).
   ```
   git init
   git add .
   git commit -m "Add seasonality and COT dashboards"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```
2. **Turn on GitHub Pages** so the dashboards are viewable as a website:
   - In your repo, go to **Settings → Pages**
   - Under "Build and deployment", set **Source: Deploy from a branch**
   - Set **Branch: main**, folder: **/docs**, then **Save**
   - GitHub will give you a URL like `https://<your-username>.github.io/<your-repo>/`
3. From here on, both dashboards update themselves:
   - **Seasonality** runs every Monday (cron in `seasonality.yml`)
   - **COT** runs every Saturday, a day after CFTC's Friday release
     (cron in `cot.yml`)
   - Either can also be triggered manually any time: repo → **Actions**
     tab → pick the workflow → **Run workflow**
   - Each workflow commits its refreshed `docs/<name>/` folder back into
     the repo, so the published site updates automatically after each run.
4. To change how often either runs, edit the `cron` line in the
   corresponding workflow file (`minute hour day month weekday`, in UTC).

## Reading the results (a few honest caveats)

- **Positioning extremes are contrarian, not a timing tool.** Markets can
  stay "crowded" for months before anything reverses — the dashboard shows
  what tends to happen on average, not what will happen this time.
- **Seasonality is a historical tendency, not a guarantee.** A few years
  with big fundamental shocks (2020, 2022...) can skew the average — the
  "% of years positive" number on each bar matters more than the average
  bar height for judging how reliable a pattern actually is.
- **Futures-roll artifacts:** the underlying prices are unadjusted
  continuous front-month futures (via Yahoo Finance). Contract rolls can
  occasionally show up as a large single-day/month price jump that isn't a
  real market move — treat isolated spikes with a bit of skepticism.
- None of this is a trading system by itself — treat it as one input
  among several.

## Where Money's At (`wheres_money_at.py`)

Three modes (Current / Historical / Trends) for two views (Asset classes / Commodities).
Shares are normalized to 100% (an approximate zero-sum view). Each asset gets a red/yellow/green
crowding band, and a "Crowding watch" box at the top lists everything outside its normal range.

- **Current:** share now, 1m/3m change, band. Tap a name to jump to its trend section.
- **Historical:** stacked share chart back to 2006 plus a month-by-month table. The **History in tables**
  selector (3 / 5 / 10 years / all) controls how far back the tables go.
- **Trends:** one section per asset with (1) price chart with markers on extreme months, (2) its own share
  trend, (3) its percentile with red/yellow/green bands, (4) a **"Does it predict?"** test, (5) the monthly
  percentage table underneath.

**The predictive test:** each month's reading is ranked against its own history using only data available
at that date (no hindsight), then compared with the price move over the next 3, 6 and 12 months. It reports
average forward returns after top-10%, middle and bottom-10% readings plus a rank correlation, and says in plain
words when there is no meaningful link. Months overlap, so treat results as hints, not proof.

- **Asset classes:** US stocks, US Treasuries, cash (money funds), gold, bitcoin. FRED data is quarterly, so it
  is carried forward; prices are monthly closes. Share is built partly from price, so a share/price link is
  partly mechanical. Cash has no price series, so it is not tested.
- **Commodities:** share of dollar-notional futures open interest (contracts x price x contract size). The band
  and the predictive test use the speculator-vs-hedger gap from CFTC data.
- Series that fail to download are skipped and listed on the dashboard instead of breaking the run.

Run: `python wheres_money_at.py` -> open `docs/money/index.html`.
