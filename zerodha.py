# zerodha_pnl_telegram.py
import datetime as dt
import asyncio
import gspread
from kiteconnect import KiteConnect
from telegram import Bot

# ========== CONFIG - EDIT ==========
API_KEY = "d6k2z8ev4jj41bm1"           # same API_KEY you used above
GSHEET_SERVICE_KEY = "gsheet_service_key.json"
GSHEET_NAME = "Apps Associates"
CONFIG_SHEET = "config"     # tab where access token is stored
ACCESS_TOKEN_CELL = "G2"
JOURNAL_SHEET = "Zerodha_PnL"   # this sheet will be created if missing

TELEGRAM_BOT_TOKEN = "1228033872:AAHsI3oFOQLKVC7mmnVH0bNyQuPGitiBEXQ"
TELEGRAM_CHAT_ID = "582942300"
# ===================================

def get_access_token_from_sheet():
    gc = gspread.service_account(filename=GSHEET_SERVICE_KEY)
    sh = gc.open(GSHEET_NAME)
    try:
        ws = sh.worksheet(CONFIG_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        raise Exception(f"Config sheet '{CONFIG_SHEET}' not found in {GSHEET_NAME}")
    token = ws.acell(ACCESS_TOKEN_CELL).value
    if not token:
        raise Exception(f"No access token found in sheet cell {ACCESS_TOKEN_CELL}. Run zerodha_auth_server first.")
    return token.strip(), sh

def get_positions(access_token):
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(access_token)
    try:
        pos = kite.positions()
        return pos
    except Exception as e:
        raise

def compute_pnl_from_positions(pos):
    # Kite returns 'day' and 'net'. Use 'net' for overall P&L
    realized = 0.0
    unrealized = 0.0
    net_positions = pos.get("net", [])
    for p in net_positions:
        # keys: 'realised' and 'unrealised' (note spelling)
        realized += float(p.get("realised", 0) or 0)
        unrealized += float(p.get("unrealised", 0) or 0)
    return realized, unrealized

def ensure_journal_sheet(sh):
    try:
        ws = sh.worksheet(JOURNAL_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=JOURNAL_SHEET, rows=2000, cols=6)
        ws.update(range_name="A1:F1", values=[["Date", "Realized", "Unrealized", "Total", "WeekNum", "Month"]])
    return ws

def log_pnl_to_sheet(sh, realized, unrealized, total):
    ws = ensure_journal_sheet(sh)
    today = dt.date.today().strftime("%Y-%m-%d")
    records = ws.get_all_records()
    dates = [r["Date"] for r in records] if records else []
    week_num = dt.date.today().isocalendar()[1]
    month_name = dt.date.today().strftime("%B")

    if today not in dates:
        ws.append_row([today, realized, unrealized, total, week_num, month_name])
    else:
        idx = dates.index(today) + 2
        ws.update(range_name=f"B{idx}:D{idx}", values=[[realized, unrealized, total]])
    print("✅ Logged:", today)

def compute_period_pnl(ws, week_start, month_start):
    data = ws.get_all_records()
    if not data:
        return 0.0, 0.0, 0.0
    today = dt.date.today()
    today_total = week_total = month_total = 0.0
    for row in data:
        try:
            rdate = dt.datetime.strptime(row["Date"], "%Y-%m-%d").date()
            total = float(row.get("Total", 0) or 0)
            realized = float(row.get("Realized", 0) or 0)
            if rdate == today:
                today_total += total
            if rdate >= week_start:
                week_total += realized
            if rdate >= month_start:
                month_total += realized
        except Exception:
            continue
    return today_total, week_total, month_total

async def send_to_telegram(msg):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="HTML")

def main():
    access_token, sh = get_access_token_from_sheet()
    pos = get_positions(access_token)
    realized, unrealized = compute_pnl_from_positions(pos)
    total = realized + unrealized

    # log to sheet
    log_pnl_to_sheet(sh, realized, unrealized, total)

    # compute week/month totals (week starts Monday)
    today = dt.date.today()
    week_start = today - dt.timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    ws = sh.worksheet(JOURNAL_SHEET)
    t_today, t_week, t_month = compute_period_pnl(ws, week_start, month_start)

    msg = f"""
📊 <b>Zerodha P&L Report</b>

🗓️ <b>Today:</b> ₹{round(t_today,2)}
📅 <b>Week (Mon→Today):</b> ₹{round(t_week,2)}
📆 <b>Month (1st→Today):</b> ₹{round(t_month,2)}

💰 <b>Realized:</b> ₹{round(realized,2)}
💤 <b>Unrealized:</b> ₹{round(unrealized,2)}

🧾 Logged on: {today.strftime('%d %b %Y')}
"""
    asyncio.run(send_to_telegram(msg.strip()))
    print("✅ Sent to Telegram")

if __name__ == "__main__":
    main()
