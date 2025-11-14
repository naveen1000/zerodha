r"""
selenium_auto_login.py

Automates Kite/Zerodha login using Selenium and reads OTP from Gmail via IMAP.

Usage (PowerShell on Windows):

 $env:ZERODHA_USER = 'your_kite_user'
 $env:ZERODHA_PASSWORD = 'your_password'
 $env:GMAIL_EMAIL = 'youremail@gmail.com'
 # Prefer Gmail API OAuth: set $env:GMAIL_CREDENTIALS to the OAuth client JSON path (or place credentials.json in repo root)
 python .\selenium_auto_login.py

Notes:
- This script uses IMAP to read Gmail. For Gmail you will typically need an App Password
  (or OAuth flow). Set `GMAIL_APP_PASSWORD` to your app password.
- It uses `webdriver-manager` to auto-download chromedriver. Ensure Chrome is installed.
- Adjust timeouts and selectors below if the login page structure changes.
"""
import os
import re
import time
import json
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from kiteconnect import KiteConnect

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager


def get_gmail_service(credentials_path='credentials.json', token_path='token.json', scopes=None):
    """Create a Gmail API service using OAuth2 installed-app flow.

    - `credentials_path` should point to the OAuth client JSON you download from Google Cloud Console.
    - `token_path` will store the user's credentials after authorization.
    - `scopes` is an iterable of scopes. Default uses readonly mail scope.
    """
    if scopes is None:
        scopes = ['https://www.googleapis.com/auth/gmail.readonly']

    creds = None
    token_file = Path(token_path)
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    service = build('gmail', 'v1', credentials=creds)
    return service


def read_otp_from_gmail_api(gmail_email, credentials_path='credentials.json', token_path='token.json', sender=None, subject_keyword=None, timeout=120, poll_interval=5):
    """Poll Gmail via Gmail API for an OTP code (4-8 digits). Returns code string or None.

    Requires OAuth client credentials file at `credentials_path`. This function will open a browser
    to authorize the account on first run and store tokens in `token_path`.
    """
    service = get_gmail_service(credentials_path=credentials_path, token_path=token_path)
    query_parts = []
    if sender:
        query_parts.append(f'from:{sender}')
    if subject_keyword:
        query_parts.append(f'subject:{subject_keyword}')

    # only unread messages to be safe; we'll also restrict by recent age below
    query_parts.append('is:unread')

    end_time = time.time() + timeout
    otp_re = re.compile(r"\b(\d{4,8})\b")

    # allow caller to override how recent messages must be (minutes)
    newer_than_minutes = int(os.environ.get('GMAIL_OTP_WINDOW_MINS', '15'))
    newer_clause = f'newer_than:{newer_than_minutes}m'

    while time.time() < end_time:
        try:
            query = ' '.join(query_parts + [newer_clause])
            results = service.users().messages().list(userId='me', q=query, maxResults=25).execute()
            messages = results.get('messages', [])

            # Fetch each message (full) and sort by internalDate descending so we check newest first
            detailed = []
            for m in messages:
                try:
                    msg = service.users().messages().get(userId='me', id=m['id'], format='full').execute()
                    internal = int(msg.get('internalDate', '0'))
                    detailed.append((internal, msg))
                except Exception:
                    continue

            detailed.sort(key=lambda x: x[0], reverse=True)

            cutoff_ms = int((time.time() - newer_than_minutes * 60) * 1000)

            for internal, msg in detailed:
                # skip messages older than our cutoff (extra safety)
                if internal < cutoff_ms:
                    continue

                snippet = msg.get('snippet', '') or ''
                headers = {h['name'].lower(): h['value'] for h in msg.get('payload', {}).get('headers', [])}
                subject = headers.get('subject', '')
                text = subject + '\n' + snippet

                mobj = otp_re.search(text)
                if mobj:
                    code = mobj.group(1)
                    # log what we matched for debugging
                    try:
                        from datetime import datetime
                        dt_str = datetime.utcfromtimestamp(internal / 1000).isoformat() + 'Z'
                    except Exception:
                        dt_str = str(internal)
                    print(f'Gmail OTP matched message id={msg.get("id")} subject="{subject}" date={dt_str}')

                    # mark message as read (remove UNREAD)
                    try:
                        service.users().messages().modify(userId='me', id=msg['id'], body={'removeLabelIds': ['UNREAD']}).execute()
                    except Exception:
                        pass
                    return code
        except Exception as e:
            # If API auth failed, raise helpful message
            raise RuntimeError('Gmail API read failed: %s' % e)

        time.sleep(poll_interval)

    return None


# IMAP support removed. This script uses Gmail API OAuth (Installed App) exclusively.


def find_and_type(driver, locators, value, timeout=10):
    """Try multiple locator strategies until element is found and typed into."""
    for by, sel in locators:
        try:
            el = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, sel)))
            el.clear()
            el.send_keys(value)
            return el
        except Exception:
            continue
    raise NoSuchElementException('None of the locators matched: %s' % locators)


def click_if_present(driver, locators, timeout=5):
    for by, sel in locators:
        try:
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, sel)))
            el.click()
            return True
        except Exception:
            continue
    return False


def enter_otp(driver, code, timeout=8):
    """Try multiple strategies to enter the OTP into the page.

    Strategies tried in order:
    - Single one-time-code input (`autocomplete=one-time-code`) or text/tel fields (JS set + input event)
    - Multiple digit inputs (fill each input)
    - Fallback: send keys to focused element / body
    """
    # Strategy 1: single input with common selectors
    single_selectors = [
        (By.CSS_SELECTOR, 'input[autocomplete="one-time-code"]'),
        (By.CSS_SELECTOR, 'input[type="tel"][maxlength]'),
        (By.CSS_SELECTOR, 'input[type="tel"]'),
        (By.CSS_SELECTOR, 'input[type="text"][inputmode]'),
        (By.CSS_SELECTOR, 'input[type="text"][maxlength]'),
        (By.ID, 'otp'),
        (By.NAME, 'otp'),
    ]
    # Try faster: find elements without long waits first, then fallback to waits
    for by, sel in single_selectors:
        try:
            els = driver.find_elements(by, sel)
            el = els[0] if els else None
        except Exception:
            el = None

        if el:
            start = time.time()
            try:
                # ensure focused, then set value via JS and dispatch input/change events
                try:
                    driver.execute_script("arguments[0].focus(); arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input')); arguments[0].dispatchEvent(new Event('change'));", el, code)
                    # some sites listen to key events; send them quickly
                    for ch in code:
                        try:
                            ActionChains(driver).send_keys(ch).perform()
                        except Exception:
                            pass
                except Exception:
                    # fallback to send_keys
                    try:
                        el.clear()
                        el.send_keys(code)
                    except Exception:
                        pass

                elapsed = time.time() - start
                print(f'enter_otp: single-input strategy succeeded in {elapsed:.2f}s using selector {by} {sel}')
                return True
            except Exception:
                pass

        # fallback: try with explicit wait if direct find failed
        try:
            el = WebDriverWait(driver, 1).until(EC.element_to_be_clickable((by, sel)))
            try:
                driver.execute_script("arguments[0].focus(); arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input')); arguments[0].dispatchEvent(new Event('change'));", el, code)
            except Exception:
                try:
                    el.clear()
                    el.send_keys(code)
                except Exception:
                    continue
            print(f'enter_otp: single-input (waited) strategy succeeded using selector {by} {sel}')
            return True
        except Exception:
            continue

    # Strategy 2: multiple inputs for each digit
    multi_css = 'input.otp, input[id^="pin"], input[id*="pin"], input[id*="otp"], input[class*="otp"], input[class*="pin"]'
    try:
        inputs = driver.find_elements(By.CSS_SELECTOR, multi_css)
        if inputs:
            start = time.time()
            for i, ch in enumerate(code):
                if i < len(inputs):
                    try:
                        el = inputs[i]
                        driver.execute_script("arguments[0].focus(); arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input')); arguments[0].dispatchEvent(new Event('change'));", el, ch)
                    except Exception:
                        try:
                            inputs[i].send_keys(ch)
                        except Exception:
                            pass
            elapsed = time.time() - start
            print(f'enter_otp: multi-input strategy succeeded in {elapsed:.2f}s')
            return True
    except Exception:
        pass

    # Strategy 3: send keys to active element or body as fallback
    try:
        ActionChains(driver).send_keys(code).perform()
        return True
    except Exception:
        try:
            body = driver.find_element(By.TAG_NAME, 'body')
            body.send_keys(code)
            return True
        except Exception:
            return False


def automate_kite_login(username, password, gmail_email, api_key=None, redirect_uri=None, headless=False):
    """Automate Kite login flow. Returns True on success, False otherwise."""
    # Build Kite login URL using KiteConnect if api_key supplied, else ask user to set KITE_LOGIN_URL
    if api_key:
        kite = KiteConnect(api_key=api_key)
        if redirect_uri:
            kite.redirect_uri = redirect_uri
        login_url = kite.login_url()
    else:
        raise ValueError('api_key is required to build Kite login URL')

    chrome_opts = Options()
    if headless:
        chrome_opts.add_argument('--headless=new')
    chrome_opts.add_argument('--no-sandbox')
    chrome_opts.add_argument('--disable-dev-shm-usage')
    CHROME_DATA_PATH = "user-data-dir=C:\\Users\\naveen.simma\\AppData\\Local\\Google\\Chrome\\User Data\\Default"
    ser = Service("D:\\projects\\Wapp\\chromedriver.exe")
    chrome_opts.add_argument(CHROME_DATA_PATH)
    chrome_opts.add_argument('--profile-directory=Profile 1')

    driver = webdriver.Chrome(service=ser, options=chrome_opts)
    try:
        driver.get(login_url)


        # Try common username locators
        username_locators = [
            (By.ID, 'userid'),
            (By.ID, 'user_id'),
            (By.NAME, 'userid'),
            (By.NAME, 'user_id'),
            (By.CSS_SELECTOR, 'input[type="text"]'),
        ]
        
        # Check if username is already auto-populated; if so, skip entering it
        username_already_filled = False
        for by, sel in username_locators:
            try:
                el = driver.find_element(by, sel)
                val = el.get_attribute('value')
                if val:
                    print(f"Username already auto-populated: {val}")
                    username_already_filled = True
                    break
            except Exception:
                continue
        
        # Only try to fill username if it wasn't already populated
        if not username_already_filled:
            try:
                find_and_type(driver, username_locators, username)
            except NoSuchElementException:
                # Username field not found - likely auto-populated on page
                print("Username field not found (likely already displayed on page)")
        
        time.sleep(2)  # small pause for page to settle

        # Try to check the 'Login to Kite Web also?' checkbox if present so the session
        # is also valid for Kite Web. The checkbox is often inside a label or clickable container.
        try:
            # Strategy 1: Click the label that contains the checkbox text
            labels = driver.find_elements(By.XPATH, "//label[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'kite web')]")
            if labels:
                lbl = labels[0]
                # Click the label to toggle the checkbox
                driver.execute_script("arguments[0].click();", lbl)
                print("Clicked label for 'Login to Kite Web also?'")
                time.sleep(0.5)
            
            # Strategy 2: Find checkbox directly and use JS to set it
            cbs = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
            for cb in cbs:
                try:
                    if cb.is_displayed() and not cb.is_selected():
                        driver.execute_script("arguments[0].checked = true; arguments[0].dispatchEvent(new Event('change')); arguments[0].dispatchEvent(new Event('click'));", cb)
                        print("Checked checkbox via JS")
                        break
                except Exception:
                    continue
        except Exception as e:
            print(f"Could not check checkbox: {e}")
        
        time.sleep(1)  # small pause before typing password


        # password
        password_locators = [
            (By.ID, 'password'),
            (By.NAME, 'password'),
            (By.CSS_SELECTOR, 'input[type="password"]'),
        ]
        find_and_type(driver, password_locators, password)

 

        # click login button - try a few strategies
        login_button_locators = [
            (By.XPATH, "//button[contains(., 'Login') or contains(., 'Log in')]"),
            (By.CSS_SELECTOR, 'button[type="submit"]'),
            (By.XPATH, "//button[contains(@class,'login')]")
        ]

        clicked = click_if_present(driver, login_button_locators)
        if not clicked:
            # try pressing Enter on password field
            WebDriverWait(driver, 2).until(EC.presence_of_element_located((By.XPATH, "//body"))).send_keys('\n')

        # After logging in, there is a 2FA screen. Choose 'Email/SMS' option instead of app.
        # Try to click any element that contains 'Email' or 'SMS' or 'Use Email' or 'Use SMS/Email'
        # If the screen asks for Mobile App Code, a link 'Problem with Mobile App Code?'
        # must be clicked to reveal alternate methods (SMS/Email). Try that first.
        problem_xpaths = [
            "//a[contains(., 'Problem with Mobile App')]",
            "//button[contains(., 'Problem with Mobile App')]",
            "//a[contains(., 'Problem with Mobile App Code')]",
            "//button[contains(., 'Problem with Mobile App Code')]",
            "//a[contains(., 'Problem') and contains(., 'Mobile App')]",
        ]
        for xp in problem_xpaths:
            try:
                el = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((By.XPATH, xp)))
                print("Clicking 'Problem with Mobile App Code' to reveal alternate 2FA options")
                el.click()
                # small pause to allow the UI to update
                time.sleep(1)
                break
            except Exception:
                continue

        email_sms_xpaths = [
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'email')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'sms')]",
            "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'email')]",
            "//div[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'email')]",
            "//button[contains(., 'Use Email') or contains(., 'Use SMS') or contains(., 'Use SMS/Email')]",
        ]
        clicked = False
        for xp in email_sms_xpaths:
            try:
                el = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, xp)))
                el.click()
                clicked = True
                break
            except Exception:
                continue

        # If clicking didn't work, try a fallback: look for radio/links with labels
        if not clicked:
            try:
                # a common flow has a link text 'Email' or 'SMS'
                el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.PARTIAL_LINK_TEXT, 'Email')))
                el.click()
                clicked = True
            except Exception:
                pass

        # Now wait a little for OTP input to appear and for message to be delivered
        otp_locators = [
            (By.ID, 'otp'),
            (By.NAME, 'otp'),
            (By.CSS_SELECTOR, 'input[type="tel"]'),
            (By.CSS_SELECTOR, 'input[type="text"][maxlength]'),
        ]

        # Poll Gmail for the code. Prefer Gmail API OAuth if credentials are provided,
        # otherwise fall back to IMAP with app-password.
        print('Polling Gmail for OTP...')
        time.sleep(5)  # initial wait before polling
        credentials_path = os.environ.get('GMAIL_CREDENTIALS') or os.environ.get('GMAIL_CREDENTIALS_PATH') or 'credentials.json'
        token_path = os.environ.get('GMAIL_TOKEN_PATH') or 'token.json'
        if not Path(credentials_path).exists():
            print('Gmail API credentials not found. Set $env:GMAIL_CREDENTIALS or place a credentials.json in the repo root.')
            return False
        try:
            print(f'Using Gmail API with credentials: {credentials_path}')
            code = read_otp_from_gmail_api(gmail_email, credentials_path=credentials_path, token_path=token_path, subject_keyword='Kite', timeout=120)
            if not code:
                code = read_otp_from_gmail_api(gmail_email, credentials_path=credentials_path, token_path=token_path, subject_keyword=None, timeout=120)
        except Exception as e:
            print('Error while reading OTP from Gmail API:', e)
            return False

        if not code:
            print('OTP not found in mailbox within timeout.')
            return False

        print('OTP found:', code)

        # Enter the OTP using robust helper
        entered = enter_otp(driver, code)
        if not entered:
            print('Failed to enter OTP into page.')
            return False

        # Submit OTP - try common submit buttons
        # try to submit after entering OTP
        if True:
            try:
                submit_click = click_if_present(driver, [
                    (By.XPATH, "//button[contains(., 'Continue') or contains(., 'Verify') or contains(., 'Submit') or contains(., 'Login')]")
                ], timeout=5)
                if not submit_click:
                    # press Enter from OTP element
                    try:
                        ActionChains(driver).send_keys('\n').perform()
                    except Exception:
                        pass
            except Exception:
                pass

        # Wait for redirect or presence of an element that indicates success
        try:
            WebDriverWait(driver, 15).until(EC.url_contains('kite.trade') )
        except Exception:
            # still may have succeeded — return True if no errors thrown
            pass
        # check page for Kite API error messages (helpful when app is not enabled for user)
        try:
            page_src = driver.page_source
            if 'The user is not enabled for the app' in page_src or 'user is not enabled for the app' in page_src:
                print('Kite API error detected in page: The user is not enabled for the app')
                # try to extract a JSON blob if present
                start = page_src.find('{"status"')
                if start != -1:
                    snippet = page_src[start:start+1000]
                    print('Page JSON snippet:', snippet)
                print('Current URL:', driver.current_url)
                return False
        except Exception:
            pass

        print('Login flow completed (check browser for success).')
        # short pause so user can see result; remove or shorten if you prefer
        time.sleep(30)
        return True
    finally:
        # keep browser open for user inspection by default. If headless, quit.
        if headless:
            driver.quit()


if __name__ == '__main__':
    USER = os.environ.get('ZERODHA_USER') or os.environ.get('ZERODHA_USERNAME')
    PASS = os.environ.get('ZERODHA_PASSWORD')
    GMAIL = os.environ.get('GMAIL_EMAIL')
    GMAIL_CREDENTIALS = os.environ.get('GMAIL_CREDENTIALS') or os.environ.get('GMAIL_CREDENTIALS_PATH')
    API_KEY = os.environ.get('ZERODHA_API_KEY') or None
    REDIRECT_URI = os.environ.get('ZERODHA_REDIRECT_URI') or None
    missing = []
    if not USER:
        missing.append('ZERODHA_USER')
    if not PASS:
        missing.append('ZERODHA_PASSWORD')
    if not GMAIL:
        missing.append('GMAIL_EMAIL')
    # Require Gmail API OAuth credentials (preferred)
    if not GMAIL_CREDENTIALS and Path('credentials.json').exists():
        GMAIL_CREDENTIALS = 'credentials.json'

    if not GMAIL_CREDENTIALS:
        missing.append('GMAIL_CREDENTIALS (path to OAuth client JSON) or place credentials.json in repo root')

    if not API_KEY:
        # try reading from your existing file if available
        try:
            from zerodha_auth_server import API_KEY as AUTH_API_KEY, REDIRECT_URI as AUTH_REDIRECT
            API_KEY = AUTH_API_KEY
            REDIRECT_URI = REDIRECT_URI or AUTH_REDIRECT
        except Exception:
            missing.append('ZERODHA_API_KEY (or set ZERODHA_API_KEY env)')

    if missing:
        print('Missing environment variables:', ', '.join(missing))
        print('Set them and re-run. Example (PowerShell):')
        print("$env:ZERODHA_USER='youruser'; $env:ZERODHA_PASSWORD='pw'; $env:GMAIL_EMAIL='you@gmail.com'; $env:GMAIL_CREDENTIALS='path\\to\\credentials.json'; python .\\selenium_auto_login.py")
        raise SystemExit(1)

    # run automation (not headless by default so you can see the browser)
    success = automate_kite_login(USER, PASS, GMAIL, api_key=API_KEY, redirect_uri=REDIRECT_URI, headless=False)
    print('Success' if success else 'Failed')
