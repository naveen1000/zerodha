# zerodha_auth_server.py
import os
import webbrowser
from flask import Flask, request, redirect
from kiteconnect import KiteConnect
import gspread
import datetime as dt

# ========== CONFIG - EDIT THESE ==========
API_KEY = "d6k2z8ev4jj41bm1"
API_SECRET = "abt33jd44g55pmfkwua89dia26utj131"
REDIRECT_URI = "http://127.0.0.1:5000/zerodha_callback"  # must match Kite app Redirect URL
GSHEET_SERVICE_KEY = "gsheet_service_key.json"
GSHEET_NAME = "Apps Associates"
CONFIG_SHEET = "config"   # config sheet/tab in the Google Sheet
ACCESS_TOKEN_CELL = "G2"  # cell where access_token will be written
# =========================================

app = Flask(__name__)

def save_access_token_to_sheet(token_response):
    """
    token_response: dict returned by KiteConnect.generate_session(...)
    writes access_token to G2
    """
    gc = gspread.service_account(filename=GSHEET_SERVICE_KEY)
    sh = gc.open(GSHEET_NAME)
    try:
        ws = sh.worksheet(CONFIG_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=CONFIG_SHEET, rows=10, cols=10)

    access_token = token_response.get("access_token")
    if not access_token:
        print("No access_token in response:", token_response)
        return False

    # write token and timestamp
    ws.update(range_name=ACCESS_TOKEN_CELL, values=[[access_token]])
    ws.update(range_name="G3", values=[[str(dt.datetime.now())]])
    print("✅ Access token written to sheet.")
    return True

@app.route("/")
def index():
    kite = KiteConnect(api_key=API_KEY)
    kite.redirect_uri = REDIRECT_URI
    login_url = kite.login_url()
    return f"""
    <h3>Zerodha Auth Server</h3>
    <p>Open this URL to login to Kite (or click):</p>
    <a href="{login_url}" target="_blank">{login_url}</a>
    <p>After login, Kite will redirect to this server and the token will be exchanged and saved to the Google Sheet.</p>
    """


@app.route("/zerodha_callback")
def zerodha_callback():
    # Kite redirects here with ?request_token=xxx&action=login
    request_token = request.args.get("request_token")
    status = request.args.get("status") or request.args.get("action")
    if not request_token:
        return "<p>Request token not found in URL. Copy the full redirect URL and paste it into the script if needed.</p>"

    kite = KiteConnect(api_key=API_KEY)
    try:
        # exchange request_token for access_token
        data = kite.generate_session(request_token, api_secret=API_SECRET)
        # data contains 'access_token' and 'public_token' etc.
        saved = save_access_token_to_sheet(data)
        if saved:
            return "<p>Access token generated and saved to Google Sheet. You can close this tab.</p>"
        else:
            return "<p>Token exchange succeeded but failed to save to sheet. Check console.</p>"
    except Exception as e:
        return f"<p>Token exchange failed: {e}</p>"

if __name__ == "__main__":
    kite = KiteConnect(api_key=API_KEY)
    kite.redirect_uri = REDIRECT_URI
    print("Open this URL in your browser to login and authorize the app:")
    print(kite.login_url())
    try:
        webbrowser.open(kite.login_url())
    except Exception:
        pass
    app.run(host="127.0.0.1", port=5000, debug=False)
