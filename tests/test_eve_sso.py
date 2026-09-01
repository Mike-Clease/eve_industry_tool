import base64
import hashlib
from unittest.mock import MagicMock, patch

import httpx
import pytest

import eve_sso


@pytest.fixture(autouse=True)
def _neutralise_keyring_backend(monkeypatch):
    """Tests mock keyring calls directly, so skip real backend selection."""
    monkeypatch.setattr(eve_sso, "_ensure_keyring", lambda: None)


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


class TestPkce:
    def test_challenge_is_s256_of_verifier(self):
        verifier, challenge = eve_sso.generate_pkce()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected

    def test_no_padding_in_either(self):
        verifier, challenge = eve_sso.generate_pkce()
        assert "=" not in verifier and "=" not in challenge

    def test_pairs_are_unique(self):
        assert eve_sso.generate_pkce()[0] != eve_sso.generate_pkce()[0]


# ---------------------------------------------------------------------------
# authorize URL
# ---------------------------------------------------------------------------


class TestAuthorizeUrl:
    def test_contains_required_params(self):
        with patch.object(eve_sso, "CLIENT_ID", "abc123"):
            url = eve_sso.build_authorize_url("st8", "chal")
        assert url.startswith(eve_sso.AUTHORIZE_URL)
        for fragment in (
            "response_type=code",
            "client_id=abc123",
            "code_challenge=chal",
            "code_challenge_method=S256",
            "state=st8",
        ):
            assert fragment in url

    def test_scopes_space_joined_and_encoded(self):
        url = eve_sso.build_authorize_url("s", "c")
        # spaces between scopes are URL-encoded to '+' or '%20'
        assert "esi-markets.read_character_orders.v1" in url
        assert ("+esi-wallet" in url) or ("%20esi-wallet" in url)


# ---------------------------------------------------------------------------
# parse_callback
# ---------------------------------------------------------------------------


class TestParseCallback:
    def test_real_callback_parsed(self):
        assert eve_sso.parse_callback("/callback?code=abc&state=xyz") == {
            "code": "abc",
            "state": "xyz",
            "error": None,
        }

    def test_error_callback_parsed(self):
        result = eve_sso.parse_callback("/callback?error=access_denied")
        assert result["error"] == "access_denied"

    def test_bare_root_ignored(self):
        # VSCode "open in browser" hits bare '/', which must NOT be treated as the callback
        assert eve_sso.parse_callback("/") is None

    def test_favicon_ignored(self):
        assert eve_sso.parse_callback("/favicon.ico") is None

    def test_callback_without_params_ignored(self):
        assert eve_sso.parse_callback("/callback") is None


# ---------------------------------------------------------------------------
# character_id_from_claims
# ---------------------------------------------------------------------------


class TestCharacterIdFromClaims:
    def test_parses_sub(self):
        assert (
            eve_sso.character_id_from_claims({"sub": "CHARACTER:EVE:2112625428"})
            == 2112625428
        )

    def test_returns_int(self):
        assert isinstance(
            eve_sso.character_id_from_claims({"sub": "CHARACTER:EVE:1"}), int
        )

    def test_bad_sub_raises(self):
        with pytest.raises(ValueError):
            eve_sso.character_id_from_claims({"sub": "CORPORATION:EVE:999"})


# ---------------------------------------------------------------------------
# token endpoint requests
# ---------------------------------------------------------------------------


def _token_resp(json_data):
    r = MagicMock()
    r.json.return_value = json_data
    return r


class TestTokenRequests:
    def test_exchange_code_body(self):
        with (
            patch.object(eve_sso, "CLIENT_ID", "cid"),
            patch(
                "eve_sso.httpx.post", return_value=_token_resp({"access_token": "a"})
            ) as mock_post,
        ):
            eve_sso.exchange_code("thecode", "theverifier")
        data = mock_post.call_args.kwargs["data"]
        assert data == {
            "grant_type": "authorization_code",
            "code": "thecode",
            "client_id": "cid",
            "code_verifier": "theverifier",
        }

    def test_refresh_body(self):
        with (
            patch.object(eve_sso, "CLIENT_ID", "cid"),
            patch(
                "eve_sso.httpx.post", return_value=_token_resp({"access_token": "a"})
            ) as mock_post,
        ):
            eve_sso.refresh("rt")
        data = mock_post.call_args.kwargs["data"]
        assert data == {
            "grant_type": "refresh_token",
            "refresh_token": "rt",
            "client_id": "cid",
        }

    def test_form_encoded_content_type(self):
        with patch("eve_sso.httpx.post", return_value=_token_resp({})) as mock_post:
            eve_sso._post_token({"x": "y"})
        assert (
            mock_post.call_args.kwargs["headers"]["Content-Type"]
            == "application/x-www-form-urlencoded"
        )

    def test_http_error_propagates(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "bad", request=MagicMock(), response=resp
        )
        with patch("eve_sso.httpx.post", return_value=resp):
            with pytest.raises(httpx.HTTPStatusError):
                eve_sso.refresh("rt")


# ---------------------------------------------------------------------------
# get_access_token (refresh + rotation)
# ---------------------------------------------------------------------------


class TestGetAccessToken:
    def test_unknown_character_raises_keyerror(self):
        with patch("eve_sso.keyring.get_password", return_value=None):
            with pytest.raises(KeyError):
                eve_sso.get_access_token(123)

    def test_returns_access_token(self):
        with (
            patch("eve_sso.keyring.get_password", return_value="old_rt"),
            patch(
                "eve_sso.refresh",
                return_value={"access_token": "AT", "refresh_token": "old_rt"},
            ),
            patch("eve_sso.keyring.set_password") as mock_set,
        ):
            assert eve_sso.get_access_token(123) == "AT"
        mock_set.assert_not_called()  # refresh token unchanged -> no rewrite

    def test_persists_rotated_refresh_token(self):
        with (
            patch("eve_sso.keyring.get_password", return_value="old_rt"),
            patch(
                "eve_sso.refresh",
                return_value={"access_token": "AT", "refresh_token": "new_rt"},
            ),
            patch("eve_sso.keyring.set_password") as mock_set,
        ):
            eve_sso.get_access_token(123)
        mock_set.assert_called_once_with(eve_sso.KEYRING_SERVICE, "123", "new_rt")


# ---------------------------------------------------------------------------
# roster / store
# ---------------------------------------------------------------------------


class TestEncryptedFileKeyring:
    def _backend(self, tmp_path, monkeypatch, password="pw"):
        monkeypatch.setenv("EVE_KEYRING_PASSWORD", password)
        kr = eve_sso._EncryptedFileKeyring()
        monkeypatch.setattr(kr, "_PATH", tmp_path / "tokens.enc")
        return kr

    def test_round_trip(self, tmp_path, monkeypatch):
        kr = self._backend(tmp_path, monkeypatch)
        kr.set_password("svc", "user", "secret-token")
        # a fresh instance must read it back from the encrypted file
        kr2 = self._backend(tmp_path, monkeypatch)
        assert kr2.get_password("svc", "user") == "secret-token"

    def test_file_is_encrypted_at_rest(self, tmp_path, monkeypatch):
        kr = self._backend(tmp_path, monkeypatch)
        kr.set_password("svc", "user", "secret-token")
        raw = (tmp_path / "tokens.enc").read_bytes()
        assert b"secret-token" not in raw  # plaintext must not appear on disk

    def test_wrong_password_raises(self, tmp_path, monkeypatch):
        self._backend(tmp_path, monkeypatch, password="right").set_password(
            "svc", "user", "tok"
        )
        wrong = self._backend(tmp_path, monkeypatch, password="wrong")
        with pytest.raises(RuntimeError, match="wrong master password"):
            wrong.get_password("svc", "user")

    def test_delete_missing_raises(self, tmp_path, monkeypatch):
        kr = self._backend(tmp_path, monkeypatch)
        with pytest.raises(eve_sso.keyring.errors.PasswordDeleteError):
            kr.delete_password("svc", "nope")


class TestStore:
    def test_store_writes_token_and_roster(self):
        with (
            patch("eve_sso.keyring.set_password") as mock_set,
            patch("eve_sso.keyring.get_password", return_value=None),
        ):
            eve_sso._store_character(42, "Pilot", "rt42")
        # refresh token written under the character id
        assert (eve_sso.KEYRING_SERVICE, "42", "rt42") in [
            c.args for c in mock_set.call_args_list
        ]

    def test_authorized_characters_parses_roster(self):
        with patch(
            "eve_sso.keyring.get_password", return_value='{"42": "Pilot", "7": "Alt"}'
        ):
            assert eve_sso.authorized_characters() == {42: "Pilot", 7: "Alt"}

    def test_empty_roster(self):
        with patch("eve_sso.keyring.get_password", return_value=None):
            assert eve_sso.authorized_characters() == {}
