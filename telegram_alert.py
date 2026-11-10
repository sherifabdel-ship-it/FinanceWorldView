"""
Telegram Alert: Smart Money / Dumb Money weekly update
==========================================================

Reads docs/cot/latest_signal.json (written by cot_vs_price.py) and sends
one Telegram message listing every instrument's current red/yellow/green
band for the speculator-vs-hedger differential.

Designed to run right after cot_vs_price.py in the same GitHub Actions
job/workflow, so it always alerts on the freshest data.

Setup (one-time):
  1. Message @BotFather on Telegram, run /newbot, follow the prompts.
     You'll get a bot token like "123456789:AAExample-Token".
  2. Message your new bot anything (e.g. "hi"), then visit
     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates in a browser -
     find "chat":{"id": ...} in the response. That number is your chat ID.
     (For a group chat, add the bot to the group first, then send a
     message in the group before checking getUpdates.)
  3. In your GitHub repo: Settings -> Secrets and variables -> Actions ->
     New repository secret. Add two secrets:
       TELEGRAM_BOT_TOKEN = the token from step 1
       TELEGRAM_CHAT_ID   = the chat id from step 2
  4. That's it - the cot.yml workflow already passes these to this script.

Run locally to test:
    export TELEGRAM_BOT_TOKEN="..."
    export TELEGRAM_CHAT_ID="..."
    python cot_vs_price.py        # must run first to produce latest_signal.json
    python telegram_alert.py
"""

import os
import json
import requests

SIGNAL_FILE = "docs/cot/latest_signal.json"
STATE_FILE = "docs/cot/.last_alert_date.txt"   # prevents duplicate alerts

BAND_EMOJI = {"red": "\U0001F534", "yellow": "\U0001F7E1", "green": "\U0001F7E2"}


def load_signals():
    if not os.path.exists(SIGNAL_FILE):
        raise FileNotFoundError(
            f"{SIGNAL_FILE} not found - run cot_vs_price.py first so it has data to alert on."
        )
    with open(SIGNAL_FILE) as f:
        return json.load(f)


def already_alerted(alert_date):
    """
    CFTC only publishes once a week. If the workflow accidentally runs
    twice for the same report date (e.g. a DST-related double schedule),
    this stops a duplicate Telegram message from going out.
    """
    if not os.path.exists(STATE_FILE):
        return False
    with open(STATE_FILE) as f:
        return f.read().strip() == alert_date


def mark_alerted(alert_date):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(alert_date)


def build_message(signals):
    # All instruments should share the same CFTC report date most weeks.
    latest_date = max(v["report_date"] for v in signals.values())

    lines = [f"*Weekly COT Update* - report date {latest_date}", ""]
    # Sort so the most extreme (red) readings appear first.
    band_order = {"red": 0, "yellow": 1, "green": 2}
    for name, info in sorted(signals.items(), key=lambda kv: band_order[kv[1]["band"]]):
        emoji = BAND_EMOJI[info["band"]]
        lines.append(
            f"{emoji} *{name}*: {info['diff_pct']:+.1f}% diff "
            f"({info['percentile']:.0f}th pct, {info['band'].upper()}) - "
            f"{info['direction']}  |  price {info['price']:.2f}"
        )
    lines.append("")
    lines.append("_Diff = speculator minus hedger positioning, as % of open interest._")
    lines.append("_Red = top/bottom 10% of history. Yellow = 10-25% or 75-90%. Green = typical range._")
    return "\n".join(lines), latest_date


def send_telegram(message, token, chat_id):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set - skipping alert.")
        print("See the setup instructions at the top of this file.")
        return

    signals = load_signals()
    message, alert_date = build_message(signals)

    if already_alerted(alert_date):
        print(f"Already sent an alert for report date {alert_date} - skipping duplicate send.")
        return

    result = send_telegram(message, token, chat_id)
    print("Telegram response:", result.get("ok"))
    mark_alerted(alert_date)
    print(f"Alert sent for report date {alert_date}.")


if __name__ == "__main__":
    main()
