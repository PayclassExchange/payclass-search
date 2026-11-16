"""Google sign-in and Google Drive search, per user.

Each person signs in with Google. The server searches Drive with that person's
own access, so people see only what they already have access to.

The session lives in a signed cookie that holds the user's refresh token.
Nothing is written to a database.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
SCOPE = "openid email https://www.googleapis.com/auth/drive.readonly"
ALLOWED_DOMAIN = "payclassinc.com"
SESSION_COOKIE = "ps_session"
STATE_COOKIE = "ps_state"


def sign(payload: dict, secret: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def unsign(value: str | None, secret: str) -> dict | None:
    if not value or "." not in value:
        return None
    body, sig = value.rsplit(".", 1)
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(body.encode()))
    except (ValueError, json.JSONDecodeError):
        return None


def login_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )


async def exchange_code(client: httpx.AsyncClient, client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    resp = await client.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Google sign-in failed")
    return resp.json()


async def email_for_token(client: httpx.AsyncClient, access_token: str) -> str:
    resp = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Could not read Google profile")
    info = resp.json()
    email = info.get("email", "")
    if not info.get("email_verified") or not email.lower().endswith("@" + ALLOWED_DOMAIN):
        raise HTTPException(status_code=403, detail=f"Sign in with a @{ALLOWED_DOMAIN} account")
    return email


async def access_token_for(client: httpx.AsyncClient, client_id: str, client_secret: str, refresh_token: str) -> str:
    resp = await client.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Sign in again")
    return resp.json()["access_token"]


def _escape_drive_query(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


async def drive_search(client: httpx.AsyncClient, access_token: str, query: str, limit: int) -> list[dict]:
    params = {
        "q": f"fullText contains '{_escape_drive_query(query)}' and trashed = false",
        "pageSize": min(limit, 50),
        "fields": "files(id,name,webViewLink,modifiedTime,mimeType)",
        "corpora": "allDrives",
        "includeItemsFromAllDrives": "true",
        "supportsAllDrives": "true",
    }
    resp = await client.get(DRIVE_FILES_URL, params=params, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Google Drive search failed")
    return [
        {
            "title": f.get("name"),
            "url": f.get("webViewLink"),
            "date": f.get("modifiedTime"),
            "snippet": f.get("mimeType"),
            "source": "google_drive",
        }
        for f in resp.json().get("files", [])
    ]
