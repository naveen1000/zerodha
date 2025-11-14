"# fyres"

## Auto-login with Selenium + Gmail OTP

This repository includes a helper script `selenium_auto_login.py` which automates logging into Kite/Zerodha, selects the Email/SMS 2FA option, reads the one-time passcode (OTP) from Gmail via the Gmail API (OAuth), and submits it automatically.

Prerequisites
- Python 3.8+ installed
- Google Chrome installed (for ChromeDriver)
 - Google Cloud OAuth client credentials (see Gmail API setup below)

Install dependencies
```powershell
python -m pip install -r .\requirements.txt
```

Environment variables
Set environment variables before running. Example (PowerShell):
```powershell
$env:ZERODHA_USER = 'your_kite_user'
$env:ZERODHA_PASSWORD = 'your_password'
# Gmail API OAuth: set `GMAIL_CREDENTIALS` to the downloaded OAuth client JSON, or place `credentials.json` in the repo root
$env:GMAIL_EMAIL = 'youremail@gmail.com'
$env:GMAIL_CREDENTIALS = 'C:\path\to\credentials.json'  # or place credentials.json in repo root
# Optional: read API key/redirect from env instead of code
$env:ZERODHA_API_KEY = 'your_kite_api_key'
$env:ZERODHA_REDIRECT_URI = 'http://127.0.0.1:5000/zerodha_callback'
python .\selenium_auto_login.py
```

Notes and tips
- Gmail: This script uses the Gmail API (OAuth) to read OTPs. Follow the Gmail API setup below and set `$env:GMAIL_CREDENTIALS` to the OAuth client JSON path (or place `credentials.json` in repo root).
- The script uses `webdriver-manager` to auto-download the appropriate ChromeDriver. Ensure your Chrome version is reasonably up-to-date.
- The script runs with a visible browser window by default so you can observe the flow. To run headless, edit the `automate_kite_login(..., headless=True)` call or set `headless=True` in the script.
- If the Zerodha/Kite login page changes, the script's locators may need adjustment in `selenium_auto_login.py`.

Gmail API (preferred) setup
 - Create OAuth client credentials (type: "Desktop / Installed app") in Google Cloud Console:
	 1. Go to https://console.cloud.google.com/apis/credentials
	 2. Create a new OAuth 2.0 Client ID, choose "Desktop app" as the application type.
	 3. Download the JSON and save it as `credentials.json` in the repo root (or set `$env:GMAIL_CREDENTIALS` to its path).
 - First run will open a browser to authorize access and store tokens in `token.json`.
 - Test the Gmail API setup with:
```powershell
$env:GMAIL_CREDENTIALS='path\to\credentials.json'
python .\gmail_api_test.py
```

Gmail API is the supported method (preferred). The script requires OAuth credentials as described above.

Security
- Keep credentials out of source control. Use environment variables or a secure secrets manager.

Troubleshooting
- If Gmail API authorization fails, ensure your `credentials.json` matches an "Installed app" OAuth client and that you completed the consent flow when `gmail_api_test.py` opened the browser.
- OTP not found: increase the timeout in `read_otp_from_gmail_api(...)` or check your spam/promotions folders.

Want changes?
- I can add a PowerShell wrapper script to set env vars and run automatically, or switch the mail reader to the Gmail API (OAuth) if you prefer. Tell me which you'd like next.
