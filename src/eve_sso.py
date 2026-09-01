# eve_sso.py
"""EVE SSO v2 authorisation (PKCE, no client secret) for multiple characters.

Stage 2 of the ROADMAP: owns the SSO / PKCE flow and the multi-character token
store. Read-only scopes only. Refresh tokens live in the OS keyring, never on
disk or in the repo.

Typical use
-----------
    # one-time, interactive: authorise as many characters as you like
    from eve_sso import authorize_accounts
    authorize_accounts()          # opens a browser per character; log in, repeat

    # later, non-interactive: get a fresh access token for any stored character
    from eve_sso import get_access_token, authorized_characters
    from eve_api import character_order_history

    for cid, name in authorized_characters().items():
        token = get_access_token(cid)          # refreshed automatically
        orders = character_order_history(cid, token)

Prerequisites
-------------
* Register an application at https://developers.eveonline.com/ as a *native*
  app (PKCE, no secret). Set the callback URL to exactly ``CALLBACK_URL`` below.
* Put the assigned Client ID in your .env as ``EVE_CLIENT_ID``.
"""

import base64
import getpass
import hashlib
import http.server
import json
import os
import pathlib
import secrets
import subprocess
import urllib.parse
import webbrowser

import httpx
import jwt
import keyring
import keyring.backend
import keyring.errors
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from dotenv import load_dotenv
from jwt import PyJWKClient

load_dotenv()

# --- SSO endpoints (v2) -----------------------------------------------------
SSO = "https://login.eveonline.com"
AUTHORIZE_URL = f"{SSO}/v2/oauth/authorize"
TOKEN_URL = f"{SSO}/v2/oauth/token"
JWKS_URL = f"{SSO}/oauth/jwks"
ISSUERS = ("login.eveonline.com", "https://login.eveonline.com")
JWT_AUDIENCE = "EVE Online"

# --- application config ------------------------------------------------------
CLIENT_ID = os.environ.get("EVE_CLIENT_ID", "")
CALLBACK_HOST, CALLBACK_PORT = "localhost", 8635
CALLBACK_URL = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"

# Read-only scopes to request. Adding one later forces re-authorising every
# character, so request everything you'll plausibly need up front.
SCOPES = [
    "esi-markets.read_character_orders.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-assets.read_assets.v1",
    "esi-industry.read_character_jobs.v1",
    # ROADMAP Stage 2 additions — uncomment to include (costs a re-auth if added later):
    # "esi-skills.read_skills.v1",
    # "esi-characters.read_blueprints.v1",
    # "esi-characters.read_standings.v1",
]

# --- keyring storage ---------------------------------------------------------
# Refresh tokens: service=KEYRING_SERVICE, username=str(character_id).
# Roster (character_id -> name): a single JSON blob under username=_ROSTER_KEY,
# so we can enumerate characters (the keyring API can't list its own entries).
KEYRING_SERVICE = "eve_industry_tool"
_ROSTER_KEY = "_roster"

_jwks_client = PyJWKClient(JWKS_URL)


# ---------------------------------------------------------------------------
# PKCE helpers (pure)
# ---------------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    """base64url without padding, per the SSO spec."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge). Challenge = base64url(SHA-256(verifier))."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(state: str, challenge: str) -> str:
    """The URL the user opens to log in and grant scopes."""
    params = {
        "response_type": "code",
        "redirect_uri": CALLBACK_URL,
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def character_id_from_claims(claims: dict) -> int:
    """Extract the integer character_id from the JWT 'sub' ('CHARACTER:EVE:<id>')."""
    sub = claims["sub"]
    if not sub.startswith("CHARACTER:EVE:"):
        raise ValueError(f"unexpected sub claim: {sub!r}")
    return int(sub.rsplit(":", 1)[1])


# ---------------------------------------------------------------------------
# Token endpoint (network)
# ---------------------------------------------------------------------------
def _post_token(data: dict) -> dict:
    """POST the token endpoint (form-encoded) and return the JSON token response."""
    r = httpx.post(
        TOKEN_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "login.eveonline.com",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def exchange_code(code: str, verifier: str) -> dict:
    """Swap an authorization code for the first access/refresh token pair."""
    return _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        }
    )


def refresh(refresh_token: str) -> dict:
    """Use a refresh token to get a fresh access token (may rotate the refresh token)."""
    return _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        }
    )


def validate_jwt(access_token: str) -> dict:
    """Verify signature (RS256 via JWKS), audience, issuer and expiry; return claims."""
    signing_key = _jwks_client.get_signing_key_from_jwt(access_token)
    claims = jwt.decode(
        access_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=CLIENT_ID,  # aud contains client_id (and "EVE Online")
        options={"require": ["exp", "sub"]},
    )
    if claims.get("iss") not in ISSUERS:
        raise jwt.InvalidIssuerError(f"unexpected issuer: {claims.get('iss')!r}")
    return claims


# ---------------------------------------------------------------------------
# Keyring store
# ---------------------------------------------------------------------------
_keyring_ready = False


def _ensure_keyring() -> None:
    """Make sure a usable keyring backend is active before we touch it.

    Native Windows / macOS / a Linux desktop with a keychain provide a secure
    OS backend automatically. A bare WSL or headless box has none, so fall back
    to an AES-encrypted file backend unlocked by a master password (set
    EVE_KEYRING_PASSWORD to avoid an interactive prompt).
    """
    global _keyring_ready
    if _keyring_ready:
        return

    from keyring.backends.fail import Keyring as _FailKeyring

    if not isinstance(keyring.get_keyring(), _FailKeyring):
        _keyring_ready = True  # a real OS backend is available
        return

    keyring.set_keyring(_EncryptedFileKeyring())
    _keyring_ready = True


class _EncryptedFileKeyring(keyring.backend.KeyringBackend):
    """WSL/headless fallback: an AES-encrypted (Fernet) JSON file store.

    Secrets live in a master-password-encrypted file under the user config dir,
    outside the repo. The password comes from EVE_KEYRING_PASSWORD, or an
    interactive prompt if that's unset. Uses only `cryptography`, which
    pyjwt[crypto] already requires — no extra dependency.
    """

    priority = 0.6  # only ever selected explicitly via keyring.set_keyring()
    _PATH = pathlib.Path.home() / ".config" / "eve_industry_tool" / "tokens.enc"
    _KDF_ITERATIONS = 390_000

    def __init__(self):
        super().__init__()
        self._fernet = None
        self._salt = None
        self._cache = None

    def _make_fernet(self, salt: bytes) -> Fernet:
        password = os.environ.get("EVE_KEYRING_PASSWORD") or getpass.getpass(
            "Master password for the EVE token store: "
        )
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self._KDF_ITERATIONS,
        )
        return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode())))

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        if not self._PATH.exists():
            self._salt = os.urandom(16)
            self._fernet = self._make_fernet(self._salt)
            self._cache = {}
            return self._cache
        blob = json.loads(self._PATH.read_text())
        self._salt = base64.b64decode(blob["salt"])
        self._fernet = self._make_fernet(self._salt)
        try:
            self._cache = json.loads(self._fernet.decrypt(blob["data"].encode()))
        except InvalidToken as e:
            raise RuntimeError("wrong master password for the EVE token store") from e
        return self._cache

    def _flush(self) -> None:
        self._PATH.parent.mkdir(parents=True, exist_ok=True)
        blob = {
            "salt": base64.b64encode(self._salt).decode(),
            "data": self._fernet.encrypt(json.dumps(self._cache).encode()).decode(),
        }
        self._PATH.write_text(json.dumps(blob))
        try:
            os.chmod(self._PATH, 0o600)
        except OSError:
            pass

    @staticmethod
    def _key(service: str, username: str) -> str:
        return f"{service}\x00{username}"

    def get_password(self, service, username):
        return self._load().get(self._key(service, username))

    def set_password(self, service, username, password):
        self._load()[self._key(service, username)] = password
        self._flush()

    def delete_password(self, service, username):
        data = self._load()
        key = self._key(service, username)
        if key not in data:
            raise keyring.errors.PasswordDeleteError("not found")
        del data[key]
        self._flush()


def _load_roster() -> dict[int, str]:
    _ensure_keyring()
    raw = keyring.get_password(KEYRING_SERVICE, _ROSTER_KEY)
    if not raw:
        return {}
    return {int(k): v for k, v in json.loads(raw).items()}


def _save_roster(roster: dict[int, str]) -> None:
    _ensure_keyring()
    keyring.set_password(
        KEYRING_SERVICE, _ROSTER_KEY, json.dumps({str(k): v for k, v in roster.items()})
    )


def _store_character(character_id: int, name: str, refresh_token: str) -> None:
    _ensure_keyring()
    keyring.set_password(KEYRING_SERVICE, str(character_id), refresh_token)
    roster = _load_roster()
    roster[character_id] = name
    _save_roster(roster)


def authorized_characters() -> dict[int, str]:
    """{character_id: name} for every character you've authorised."""
    return _load_roster()


def forget_character(character_id: int) -> None:
    """Remove one character's stored refresh token and roster entry."""
    _ensure_keyring()
    try:
        keyring.delete_password(KEYRING_SERVICE, str(character_id))
    except keyring.errors.PasswordDeleteError:
        pass
    roster = _load_roster()
    roster.pop(character_id, None)
    _save_roster(roster)


# ---------------------------------------------------------------------------
# Access-token retrieval (the function the API layer actually uses)
# ---------------------------------------------------------------------------
def get_access_token(character_id: int) -> str:
    """Return a valid access token for a stored character, refreshing as needed.

    Raises KeyError if the character hasn't been authorised (run authorize_accounts).
    """
    _ensure_keyring()
    stored = keyring.get_password(KEYRING_SERVICE, str(character_id))
    if stored is None:
        raise KeyError(
            f"character {character_id} not authorised — run authorize_accounts()"
        )
    token = refresh(stored)
    # EVE rotates refresh tokens; persist the new one if it changed.
    new_refresh = token.get("refresh_token")
    if new_refresh and new_refresh != stored:
        keyring.set_password(KEYRING_SERVICE, str(character_id), new_refresh)
    return token["access_token"]


# ---------------------------------------------------------------------------
# Interactive authorisation (loopback callback)
# ---------------------------------------------------------------------------
def parse_callback(path: str) -> dict | None:
    """Parse an SSO callback request path into {code, state, error}.

    Returns None for requests that aren't the OAuth callback — favicon fetches,
    port probes, and VSCode's "open in browser" hitting bare '/' — so the server
    ignores them and keeps waiting for the real redirect instead of aborting.
    """
    query = urllib.parse.urlparse(path).query
    params = urllib.parse.parse_qs(query)
    if "code" not in params and "error" not in params:
        return None
    return {
        "code": params.get("code", [None])[0],
        "state": params.get("state", [None])[0],
        "error": params.get("error", [None])[0],
    }


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures ?code=&state= from the SSO redirect; ignores stray requests."""

    result: dict = {}

    def do_GET(self):  # noqa: N802 (http.server naming)
        parsed = parse_callback(self.path)
        if parsed is None:
            self.send_response(204)  # ignore favicon / port probes / bare '/'
            self.end_headers()
            return
        _CallbackHandler.result = parsed
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h3>Authorisation received.</h3>"
            b"You can close this tab and return to the terminal.</body></html>"
        )

    def log_message(self, *args):  # silence the default stderr logging
        pass


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _open_browser(url: str) -> bool:
    """Best-effort open the URL in a browser. Never raises.

    Under WSL the Linux backends (gio/xdg-open) can't reach the Windows browser,
    so hand off to the Windows side via wslview or PowerShell.
    """
    if _is_wsl():
        for cmd in (
            ["wslview", url],
            ["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{url}'"],
        ):
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
                return True
            except Exception:
                continue
        return False
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def authorize_character() -> tuple[int, str]:
    """Authorise ONE character interactively; store its refresh token; return (id, name).

    Opens the browser to the SSO login. Whichever character you select on
    whichever account is the one that gets stored.
    """
    if not CLIENT_ID:
        raise RuntimeError("EVE_CLIENT_ID is not set (add it to your .env)")

    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    url = build_authorize_url(state, challenge)

    # Bind the loopback server BEFORE opening the browser so the redirect can't
    # race ahead of it, then serve requests until the real callback arrives.
    _CallbackHandler.result = {}
    server = http.server.HTTPServer((CALLBACK_HOST, CALLBACK_PORT), _CallbackHandler)

    opened = _open_browser(url)
    if opened:
        print(f"Opened your browser to log in.\nIf it didn't open, click:\n{url}\n")
    else:
        print(f"Couldn't auto-open a browser. Click this link to log in:\n{url}\n")

    try:
        while not _CallbackHandler.result:
            server.handle_request()  # blocks; stray requests are ignored (204)
    finally:
        server.server_close()

    cb = _CallbackHandler.result
    if cb.get("error"):
        raise RuntimeError(f"SSO returned an error: {cb['error']}")
    if cb.get("state") != state:
        raise RuntimeError("state mismatch — possible CSRF, aborting")
    if not cb.get("code"):
        raise RuntimeError("no authorization code returned")

    token = exchange_code(cb["code"], verifier)
    claims = validate_jwt(token["access_token"])
    character_id = character_id_from_claims(claims)
    name = claims.get("name", str(character_id))

    _store_character(character_id, name, token["refresh_token"])
    return character_id, name


def authorize_accounts() -> dict[int, str]:
    """Authorise characters in a loop until you're done. Returns {id: name} added.

    To authorise characters on a *different* account, log out of the EVE SSO
    session in your browser first (https://login.eveonline.com/account/logoff),
    otherwise the browser reuses your current login.
    """
    added: dict[int, str] = {}
    while True:
        cid, name = authorize_character()
        added[cid] = name
        print(f"  ✓ authorised {name} ({cid})\n")
        again = input("Authorise another character? [y/N] ").strip().lower()
        if again != "y":
            break
        input(
            "For a DIFFERENT account, log out at "
            "https://login.eveonline.com/account/logoff first, then press Enter..."
        )
    print(f"\nDone. Authorised {len(added)} character(s) this run.")
    print(f"Total stored: {authorized_characters()}")
    return added


if __name__ == "__main__":
    authorize_accounts()
