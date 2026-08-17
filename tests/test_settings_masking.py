"""Hardening #4 — API credentials are write-only on the settings page.

Decrypted tokens must never be echoed into settings HTML (they linger in
browser cache/history/DOM). Masked fields post empty, which keeps the stored
value; explicit clear checkboxes remove it; test buttons fall back to the
stored credential server-side.
"""

from unittest.mock import AsyncMock, patch

from app.database import get_db, get_setting
from app.crypto import is_fernet_token


def _save_abs(admin_client, token="super-secret-abs-token"):
    resp = admin_client.post(
        "/api/settings",
        data={"abs_url": "https://abs.example", "abs_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return token


class TestNoEcho:
    def test_google_key_never_rendered(self, admin_client):
        sentinel = "sentinel-google-secret"
        admin_client.post("/api/settings", data={"google_books_api_key": sentinel},
                          follow_redirects=False)
        assert sentinel not in admin_client.get("/settings").text

    def test_environment_managed_google_state_never_renders_key(self, admin_client, monkeypatch):
        sentinel = "sentinel-env-google-secret"
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", sentinel)
        html = admin_client.get("/settings").text
        assert sentinel not in html
        assert "Environment-managed" in html
        assert "Managed by GOOGLE_BOOKS_API_KEY" in html
        assert "clear_google_books_api_key" not in html

    def test_saved_secrets_not_in_settings_html(self, admin_client):
        token = _save_abs(admin_client)
        admin_client.post(
            "/api/settings",
            data={"hardcover_token": "hc-secret-xyz", "isbndb_api_key": "isbndb-secret-xyz"},
            follow_redirects=False,
        )
        html = admin_client.get("/settings").text
        assert token not in html
        assert "hc-secret-xyz" not in html
        assert "isbndb-secret-xyz" not in html
        # Saved state is still communicated
        assert "Saved — leave blank to keep" in html

    def test_unsaved_fields_show_normal_placeholder(self, admin_client):
        html = admin_client.get("/settings").text
        assert "Saved — leave blank to keep" not in html


class TestWriteOnlySemantics:
    def test_google_key_encrypted_keep_and_clear(self, admin_client):
        sentinel = "sentinel-google-secret"
        admin_client.post("/api/settings", data={"google_books_api_key": sentinel},
                          follow_redirects=False)
        with get_db() as db:
            raw = db.execute("SELECT value FROM settings WHERE key = 'google_books_api_key'").fetchone()["value"]
            assert raw != sentinel and is_fernet_token(raw)
        admin_client.post("/api/settings", data={"google_books_api_key": ""},
                          follow_redirects=False)
        with get_db() as db:
            assert get_setting(db, "google_books_api_key") == sentinel
        admin_client.post("/api/settings", data={"google_books_api_key": "",
                          "clear_google_books_api_key": "on"}, follow_redirects=False)
        with get_db() as db:
            assert get_setting(db, "google_books_api_key") == ""

    def test_blank_sensitive_field_keeps_stored_value(self, admin_client):
        token = _save_abs(admin_client)
        # Re-save the ABS form with a blank token (what the masked field posts)
        resp = admin_client.post(
            "/api/settings",
            data={"abs_url": "https://abs.example", "abs_token": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        with get_db() as db:
            assert get_setting(db, "abs_token") == token

    def test_new_value_overwrites(self, admin_client):
        _save_abs(admin_client)
        admin_client.post(
            "/api/settings",
            data={"abs_url": "https://abs.example", "abs_token": "rotated-token"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "abs_token") == "rotated-token"

    def test_clear_checkbox_removes_value(self, admin_client):
        _save_abs(admin_client)
        admin_client.post(
            "/api/settings",
            data={"abs_url": "https://abs.example", "abs_token": "", "clear_abs_token": "on"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "abs_token") == ""

    def test_forms_do_not_blank_other_sections(self, admin_client):
        _save_abs(admin_client)
        # Hardcover form posts only its own field — ABS settings must survive
        admin_client.post(
            "/api/settings",
            data={"hardcover_token": "hc-token"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "abs_url") == "https://abs.example"
            assert get_setting(db, "abs_token") == "super-secret-abs-token"
            assert get_setting(db, "hardcover_token") == "hc-token"

    def test_non_sensitive_blank_still_clears(self, admin_client):
        _save_abs(admin_client)
        admin_client.post(
            "/api/settings",
            data={"abs_url": "", "abs_token": ""},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "abs_url") == ""
            assert get_setting(db, "abs_token") == "super-secret-abs-token"

    def test_vision_key_keep_and_clear(self, admin_client):
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "anthropic", "anthropic_api_key": "sk-ant-secret"},
            follow_redirects=False,
        )
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "anthropic", "anthropic_api_key": ""},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "anthropic_api_key") == "sk-ant-secret"
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "anthropic", "anthropic_api_key": "",
                  "clear_anthropic_api_key": "on"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "anthropic_api_key") == ""

    def test_openai_key_keep_and_clear(self, admin_client):
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "openai", "openai_api_key": "sk-openai-secret"},
            follow_redirects=False,
        )
        # Blank submit keeps the stored key (masked field posts empty)...
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "openai", "openai_api_key": ""},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "openai_api_key") == "sk-openai-secret"
        # ...and the OpenAI clear checkbox only clears the OpenAI key.
        admin_client.post(
            "/api/settings/vision",
            data={"vision_provider": "openai", "openai_api_key": "",
                  "clear_openai_api_key": "on"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "openai_api_key") == ""

    def test_notify_url_keep_and_clear(self, admin_client):
        admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "28", "notify_url": "https://ntfy.example/t",
                  "notify_format": "ntfy"},
            follow_redirects=False,
        )
        admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "28", "notify_url": "", "notify_format": "ntfy"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "notify_url") == "https://ntfy.example/t"
        admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "28", "notify_url": "", "notify_format": "ntfy",
                  "clear_notify_url": "on"},
            follow_redirects=False,
        )
        with get_db() as db:
            assert get_setting(db, "notify_url") == ""


class TestStoredCredentialFallback:
    """Test buttons post blank fields once masked — endpoints use stored values."""

    def test_hardcover_test_uses_stored_token(self, admin_client):
        admin_client.post("/api/settings", data={"hardcover_token": "hc-stored"},
                          follow_redirects=False)
        with patch("app.services.hardcover.test_connection",
                   new=AsyncMock(return_value={"ok": True, "username": "dan"})) as tc:
            resp = admin_client.post("/api/hardcover/test", json={"token": ""})
        assert resp.json()["ok"] is True
        tc.assert_awaited_once_with("hc-stored")

    def test_google_test_uses_typed_key(self, admin_client):
        with patch("app.services.googlebooks.test_connection",
                   new=AsyncMock(return_value={"ok": True, "message": "Connected"})) as tc:
            resp = admin_client.post("/api/settings/google-books/test",
                                     json={"api_key": "typed-google-key"})
        assert resp.json()["ok"] is True
        tc.assert_awaited_once_with("typed-google-key")

    def test_google_test_uses_effective_env_key(self, admin_client, monkeypatch):
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "env-google-key")
        with patch("app.services.googlebooks.test_connection",
                   new=AsyncMock(return_value={"ok": True, "message": "Connected"})) as tc:
            admin_client.post("/api/settings/google-books/test", json={"api_key": ""})
        tc.assert_awaited_once_with("env-google-key")

    def test_google_test_rejects_non_object_json(self, admin_client):
        resp = admin_client.post("/api/settings/google-books/test", json=[])
        assert resp.json() == {"ok": False, "message": "Invalid request"}

    def test_igdb_test_uses_stored_credentials(self, admin_client):
        admin_client.post(
            "/api/settings",
            data={"igdb_client_id": "cid-stored", "igdb_client_secret": "sec-stored"},
            follow_redirects=False,
        )
        with patch("app.services.igdb.test_credentials",
                   new=AsyncMock(return_value={"ok": True, "message": "ok"})) as tc:
            resp = admin_client.post("/api/igdb/test-key",
                                     json={"client_id": "", "client_secret": ""})
        assert resp.json()["ok"] is True
        assert tc.await_args.args[0] == "cid-stored"
        assert tc.await_args.args[1] == "sec-stored"

    def test_notify_test_uses_stored_url(self, admin_client):
        admin_client.post(
            "/api/settings/lending",
            data={"lending_overdue_days": "28", "notify_url": "https://ntfy.example/stored",
                  "notify_format": "ntfy"},
            follow_redirects=False,
        )
        with patch("app.services.notify.send_notification",
                   new=AsyncMock(return_value=True)) as send:
            resp = admin_client.post("/api/settings/notify-test",
                                     json={"url": "", "format": "ntfy"})
        assert resp.json()["ok"] is True
        assert send.await_args.args[0] == "https://ntfy.example/stored"
