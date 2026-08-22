"""Real Gmail API access via OAuth (installed-app flow), used when
config/gmail_credentials.json is present (see README.md "Gmail API setup"). This is
what makes "search mail for X" return actual matching messages, instead of just
opening Gmail in the browser (integrations.py falls back to that when this isn't
configured, so the assistant still works before you've set up a Google Cloud project).

Read-only by design (gmail.readonly scope) -- listing/searching mail doesn't need
send or delete permissions, and granting less than the assistant could ever need
limits the damage if a command is ever misrouted.
"""

from pathlib import Path
from typing import List, Optional

from assistant.logger import get_logger

log = get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CREDENTIALS_FILE = CONFIG_DIR / "gmail_credentials.json"
TOKEN_FILE = CONFIG_DIR / "gmail_token.json"

_service = None


def is_configured() -> bool:
    return CREDENTIALS_FILE.exists()


def _get_service():
    global _service
    if _service is not None:
        return _service
    if not is_configured():
        return None

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # First run only: opens a browser for one-time consent, then caches
            # the refresh token to TOKEN_FILE so this doesn't happen again.
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    _service = build("gmail", "v1", credentials=creds)
    return _service


def search(query: str, max_results: int = 5) -> Optional[List[str]]:
    """Returns a list of 'From - Subject' summary strings for matching messages, or
    None if Gmail isn't configured -- callers should fall back to opening the browser.
    """
    try:
        service = _get_service()
    except Exception as e:
        log.error("Gmail auth failed: %s", e)
        return None

    if service is None:
        return None

    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    message_ids = resp.get("messages", [])
    if not message_ids:
        return []

    summaries = []
    for m in message_ids:
        msg = service.users().messages().get(
            userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject"]
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        summaries.append(f"{headers.get('From', '?')} -- {headers.get('Subject', '(no subject)')}")

    return summaries
