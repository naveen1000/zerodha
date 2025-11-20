# zerodha_auth_server.py
import os
import webbrowser
import threading
import time
from flask import Flask, request, redirect
import requests
from werkzeug.serving import make_server
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
server = None  # Will hold the werkzeug server instance
shutdown_event = threading.Event()  # Signal to shutdown server

def notify(msg):
    url='https://api.telegram.org/bot1193312817:AAGTRlOs3YZHFeDSO_33YTwwewrEaMbLizE/sendMessage?chat_id=582942300&parse_mode=Markdown&text='+msg
    requests.get(url)
    print("notified")
    
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
    shutdown_event.set()  # Signal server to shutdown
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

    # Start the Flask server in a background thread so we can drive a browser (selenium)
    def run_server():
        # disable reloader to avoid double-starts when threading
        global server
        server = make_server("127.0.0.1", 5000, app)
        while not shutdown_event.is_set():
            server.handle_request()
        print("Server shutting down...")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait a short moment for the server to bind
    time.sleep(0.8)

    use_selenium = os.environ.get('USE_SELENIUM_AUTOLOGIN', '').lower() in ('1', 'true', 'yes')
    if use_selenium:
        # Try to import selenium helper and run it. This keeps the original server/token
        # exchange logic intact (Flask will receive the redirect and exchange the token).
        try:
            from selenium_auto_login import automate_kite_login
        except Exception as e:
            print('Selenium autologin requested but failed to import selenium_auto_login:', e)
            print('Falling back to printing the login URL. Set up the module or run without USE_SELENIUM_AUTOLOGIN.')
            print(kite.login_url())
            # keep main thread alive
            server_thread.join()
            raise SystemExit(1)

        # Read credentials from env for the automated flow
        zk_user = os.environ.get('ZERODHA_USER')
        zk_pass = os.environ.get('ZERODHA_PASSWORD')
        gmail = os.environ.get('GMAIL_EMAIL')
        api_key = API_KEY
        redirect_uri = REDIRECT_URI

        if not (zk_user and zk_pass and gmail):
            print('Missing ZERODHA_USER / ZERODHA_PASSWORD / GMAIL_EMAIL environment variables required for selenium autologin.')
            print('Falling back to manual login URL:')
            print(kite.login_url())
            server_thread.join()
            raise SystemExit(1)

        print('Starting Selenium automated login...')
        try:
            success = automate_kite_login(zk_user, zk_pass, gmail, api_key=api_key, redirect_uri=redirect_uri, headless=False)
            print('Selenium autologin returned:', success)
            notify("Selenium autologin returned: " + str(success))
        except Exception as e:
            print('Selenium autologin failed:', e)
            print('Open the URL manually to continue:')
            print(kite.login_url())

        # Wait for token to be written and server to signal shutdown
        print('Waiting for callback to complete...')
        server_thread.join(timeout=10)  # Wait max 10 seconds
        print('✅ Token authentication complete. Exiting.')
        
        # Close any open browser windows (best effort)
        try:
            import subprocess
            subprocess.run(['pkill', '-f', 'chrome|firefox|chromium'], stderr=subprocess.DEVNULL)
        except Exception:
            pass
    else:
        # Default: print/open login URL and serve callback
        print("Open this URL in your browser to login and authorize the app:")
        print(kite.login_url())
        try:
            webbrowser.open(kite.login_url())
        except Exception:
            pass
        # Keep the background server running - don't start a second one!
        print('Server running on http://127.0.0.1:5000')
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass
