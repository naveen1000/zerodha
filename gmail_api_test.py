"""Test Gmail API OAuth credentials and list recent unread messages.

Usage (PowerShell):
$env:GMAIL_CREDENTIALS='path\to\credentials.json'; python .\gmail_api_test.py
"""
import os
from pathlib import Path
from selenium_auto_login import get_gmail_service


def main():
    creds = os.environ.get('GMAIL_CREDENTIALS') or 'credentials.json'
    token = os.environ.get('GMAIL_TOKEN_PATH') or 'token.json'
    if not Path(creds).exists():
        print('Credentials file not found at', creds)
        print('Create OAuth client credentials (Installed app) in Google Cloud and download JSON to this path.')
        raise SystemExit(1)

    try:
        service = get_gmail_service(credentials_path=creds, token_path=token)
        results = service.users().messages().list(userId='me', q='is:unread', maxResults=10).execute()
        messages = results.get('messages', [])
        print('Found', len(messages), 'unread messages')
        for m in messages:
            msg = service.users().messages().get(userId='me', id=m['id'], format='metadata', metadataHeaders=['subject','from']).execute()
            headers = {h['name'].lower(): h['value'] for h in msg.get('payload', {}).get('headers', [])}
            print('-', headers.get('from'), '|', headers.get('subject'))
        print('Gmail API test completed.')
    except Exception as e:
        print('Gmail API test failed:', e)


if __name__ == '__main__':
    main()
