import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import main


MAIL_ENV = {
    "RESEND_API_KEY": "re_test_secret_api_key",
    "MAIL_HOST": "smtp.example.com",
    "MAIL_PORT": "587",
    "MAIL_USER": "info@lotrank.ai",
    "MAIL_PASSWORD": "smtp-secret",
    "MAIL_FROM": "LotRank <info@lotrank.ai>",
    "ADMIN_REPORT_EMAIL": "admin@example.com",
    "MAIL_TEST_TOKEN": "secret-value-9384",
}


class MailTestEndpointTests(unittest.TestCase):
    def test_requires_resend_configuration_and_ignores_legacy_smtp_values(self):
        environment = dict(MAIL_ENV)
        environment.pop("RESEND_API_KEY")

        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(main.MailConfigurationError):
                main._load_resend_settings()

    def test_rejects_missing_or_invalid_token(self):
        with patch.dict(os.environ, MAIL_ENV, clear=True):
            for token in (None, "wrong-token"):
                with self.subTest(token=token):
                    with self.assertRaises(HTTPException) as raised:
                        asyncio.run(main.send_mail_test(token))
                    self.assertEqual(raised.exception.status_code, 401)
                    self.assertEqual(raised.exception.detail, {"code": "invalid_token"})

    def test_sends_only_the_fixed_test_message_to_admin(self):
        response = MagicMock(status_code=200)
        resend_post = MagicMock(return_value=response)

        with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
            main.requests,
            "post",
            resend_post,
        ):
            main._send_resend_test_email()

        resend_post.assert_called_once()
        call = resend_post.call_args
        self.assertEqual(call.args[0], "https://api.resend.com/emails")
        self.assertEqual(call.kwargs["timeout"], 15)
        self.assertEqual(
            call.kwargs["headers"]["Authorization"],
            "Bearer re_test_secret_api_key",
        )
        self.assertEqual(call.kwargs["json"]["from"], "LotRank <info@lotrank.ai>")
        self.assertEqual(
            call.kwargs["json"]["to"],
            ["admin@example.com"],
        )
        self.assertEqual(
            call.kwargs["json"]["subject"],
            "LotRank SMTP test başarılı",
        )
        self.assertNotIn("smtp-secret", str(call))

    def test_classifies_resend_failures(self):
        cases = [
            (main.requests.ConnectionError("network unavailable"), None, "resend_connect_failed"),
            (None, 401, "resend_auth_failed"),
            (None, 403, "resend_auth_failed"),
            (None, 422, "resend_send_failed"),
        ]

        for request_error, status_code, expected_code in cases:
            with self.subTest(code=expected_code, status=status_code):
                resend_post = MagicMock()
                if request_error is not None:
                    resend_post.side_effect = request_error
                else:
                    resend_post.return_value = MagicMock(status_code=status_code)

                with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
                    main.requests,
                    "post",
                    resend_post,
                ):
                    with self.assertRaises(main.MailDeliveryError) as raised:
                        main._send_resend_test_email()

                self.assertEqual(raised.exception.code, expected_code)

    def test_endpoint_returns_only_safe_error_codes(self):
        cases = [
            (main.MailConfigurationError(), 503, "missing_env"),
            (main.MailDeliveryError("resend_auth_failed"), 502, "resend_auth_failed"),
            (main.MailDeliveryError("resend_connect_failed"), 502, "resend_connect_failed"),
            (main.MailDeliveryError("resend_send_failed"), 502, "resend_send_failed"),
            (main.MailDeliveryError("unsafe detail"), 502, "resend_send_failed"),
        ]

        for error, status_code, expected_code in cases:
            with self.subTest(code=expected_code), patch.dict(
                os.environ,
                MAIL_ENV,
                clear=True,
            ), patch.object(main, "_send_resend_test_email", side_effect=error):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(main.send_mail_test("secret-value-9384"))

                self.assertEqual(raised.exception.status_code, status_code)
                self.assertEqual(raised.exception.detail, {"code": expected_code})
                self.assertNotIn("smtp-secret", str(raised.exception.detail))

    def test_valid_token_returns_success_without_exposing_configuration(self):
        with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
            main,
            "_send_resend_test_email",
        ):
            result = asyncio.run(main.send_mail_test("secret-value-9384"))

        self.assertEqual(result, {"status": "ok", "message": "Resend test email sent"})
        self.assertNotIn("smtp-secret", str(result))

    def test_page_posts_token_in_header_without_exposing_secrets(self):
        with patch.dict(os.environ, MAIL_ENV, clear=True):
            response = main.mail_test_page()

        body = response.body.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("type=\"password\"", body)
        self.assertIn("action=\"/internal/mail/test\"", body)
        self.assertIn('method: "POST"', body)
        self.assertIn('"X-Mail-Test-Token": token', body)
        self.assertIn("Mail gönderildi", body)
        for code in (
            "invalid_token",
            "missing_env",
            "resend_auth_failed",
            "resend_connect_failed",
            "resend_send_failed",
        ):
            self.assertIn(code, body)
        self.assertNotIn(MAIL_ENV["MAIL_PASSWORD"], body)
        self.assertNotIn(MAIL_ENV["MAIL_TEST_TOKEN"], body)
        self.assertNotIn("console.", body)
        self.assertNotIn("URLSearchParams", body)


if __name__ == "__main__":
    unittest.main()
