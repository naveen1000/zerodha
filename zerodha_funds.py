# zerodha_funds.py
import datetime as dt
import asyncio
import gspread
from kiteconnect import KiteConnect
from telegram import Bot

# ========== CONFIG - EDIT ==========
API_KEY = "d6k2z8ev4jj41bm1"           # same API_KEY used elsewhere
GSHEET_SERVICE_KEY = "/home/ubuntu/Desktop/zerodha/gsheet_service_key.json"
GSHEET_NAME = "Apps Associates"
CONFIG_SHEET = "config"     # tab where access token is stored
ACCESS_TOKEN_CELL = "G2"
TELEGRAM_BOT_TOKEN_CELL = "8332447645:AAFMiAN6nYCzAWf0U6mDhlbC1Tl2_oPLi2A" # replace / move to sheet if needed
TELEGRAM_CHAT_ID_CELL = "582942300" # replace / move to sheet if needed
# ===================================


def get_config_from_sheet():
    gc = gspread.service_account(filename=GSHEET_SERVICE_KEY)
    sh = gc.open(GSHEET_NAME)
    try:
        ws = sh.worksheet(CONFIG_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        raise Exception(f"Config sheet '{CONFIG_SHEET}' not found in {GSHEET_NAME}")

    # Get access token
    token = ws.acell(ACCESS_TOKEN_CELL).value
    if not token:
        raise Exception(f"No access token found in sheet cell {ACCESS_TOKEN_CELL}. Run zerodha_auth_server first.")

    # Get Telegram bot token
    bot_token = TELEGRAM_BOT_TOKEN_CELL #ws.acell(TELEGRAM_BOT_TOKEN_CELL).value
    if not bot_token:
        raise Exception(f"No Telegram bot token found in sheet cell {TELEGRAM_BOT_TOKEN_CELL}. Please add it to the config sheet.")

    # Get Telegram chat ID
    chat_id = TELEGRAM_CHAT_ID_CELL #ws.acell(TELEGRAM_CHAT_ID_CELL).value
    if not chat_id:
        raise Exception(f"No Telegram chat ID found in sheet cell {TELEGRAM_CHAT_ID_CELL}. Please add it to the config sheet.")

    return token.strip(), bot_token.strip(), chat_id.strip(), sh


def get_funds(access_token):
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(access_token)
    try:
        funds = kite.margins()  # returns dict e.g. {'equity': {...}, 'commodity': {...}}
        # Prefer equity section only for this script
        return funds.get("equity") if isinstance(funds, dict) else funds
    except Exception as e:
        raise


def format_funds_message(funds):
    # Build readable message for Telegram. Include ALL equity segment details.
    lines = []
    now = dt.datetime.now().strftime('%d %b %Y %H:%M')
    lines.append(f"💵 <b>Zerodha Funds Summary</b>\n")

    if not funds:
        lines.append("No funds data returned by API.")
        return '\n'.join(lines)

    # funds is the 'equity' dict
    equity = funds

    def to_num(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    def fmt(v):
        return f"₹{v:,.2f}"

    # Separate top-level scalars and nested dictionaries
    scalars = {}
    dicts = {}

    for k, v in equity.items():
        if isinstance(v, dict):
            dicts[k] = v
        else:
            scalars[k] = v

    # 1. Print Top-level scalars (e.g. net, enabled)
    for k, v in scalars.items():
        label = k.replace('_', ' ').capitalize()
        if isinstance(v, bool):
            lines.append(f"<b>{label}:</b> {v}")
        elif isinstance(v, (int, float)) or (isinstance(v, str) and v.replace('.', '', 1).isdigit()):
            val = to_num(v)
            if val != 0:
                lines.append(f"<b>{label}:</b> {fmt(val)}")
        else:
            lines.append(f"<b>{label}:</b> {v}")

    # 2. Print nested dictionaries (e.g. Available, Utilised)
    for section_name, section_data in dicts.items():
        section_lines = []
        for sub_k, sub_v in section_data.items():
            val = to_num(sub_v)
            if val != 0:
                label = sub_k.replace('_', ' ').capitalize()
                # Assuming most values in sub-dicts are monetary
                section_lines.append(f"- {label}: {fmt(val)}")
        
        if section_lines:
            lines.append(f"\n<b>{section_name.replace('_', ' ').capitalize()}:</b>")
            lines.extend(section_lines)

    return '\n'.join(lines).strip()


async def send_to_telegram(msg, bot_token, chat_id):
    bot = Bot(token=bot_token)
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")


def main():
    access_token, bot_token, chat_id, sh = get_config_from_sheet()
    funds = get_funds(access_token)
    msg = format_funds_message(funds)
    asyncio.run(send_to_telegram(msg, bot_token, chat_id))
    print("✅ Funds snapshot sent to Telegram")


if __name__ == "__main__":
    main()
