import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import main


MAIL_ENV = {
    "MAIL_HOST": "smtp.example.com",
    "MAIL_PORT": "587",
    "MAIL_USER": "info@lotrank.ai",
    "MAIL_PASSWORD": "smtp-secret",
    "MAIL_FROM": "LotRank <info@lotrank.ai>",
    "ADMIN_REPORT_EMAIL": "admin@example.com",
    "MAIL_TEST_TOKEN": "secret-value-9384",
}


class MailTestEndpointTests(unittest.TestCase):
    def test_rejects_missing_or_invalid_token(self):
        with patch.dict(os.environ, MAIL_ENV, clear=True):
            for token in (None, "wrong-token"):
                with self.subTest(token=token):
                    with self.assertRaises(HTTPException) as raised:
                        asyncio.run(main.send_mail_test(token))
                    self.assertEqual(raised.exception.status_code, 401)
                    self.assertEqual(raised.exception.detail, {"code": "invalid_token"})

    def test_sends_only_the_fixed_test_message_to_admin(self):
        smtp = MagicMock()
        smtp_connection = smtp.return_value

        with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
            main.smtplib,
            "SMTP",
            smtp,
        ):
            main._send_smtp_test_email()

        smtp.assert_called_once_with(host="smtp.example.com", port=587, timeout=15)
        smtp_connection.starttls.assert_called_once()
        smtp_connection.login.assert_called_once_with("info@lotrank.ai", "smtp-secret")
        message = smtp_connection.send_message.call_args.args[0]
        self.assertEqual(message["From"], "LotRank <info@lotrank.ai>")
        self.assertEqual(message["To"], "admin@example.com")
        self.assertEqual(message["Subject"], "LotRank SMTP test başarılı")
        self.assertEqual(
            smtp_connection.send_message.call_args.kwargs["to_addrs"],
            ["admin@example.com"],
        )
        self.assertNotIn("smtp-secret", message.as_string())

    def test_classifies_smtp_phase_failures(self):
        cases = [
            ("connect", OSError("host not found"), "smtp_connect_failed"),
            (
                "auth",
                main.smtplib.SMTPAuthenticationError(535, b"credentials rejected"),
                "smtp_auth_failed",
            ),
            ("send", main.smtplib.SMTPDataError(554, b"message rejected"), "smtp_send_failed"),
        ]

        for phase, smtp_error, expected_code in cases:
            with self.subTest(phase=phase):
                smtp = MagicMock()
                connection = smtp.return_value
                if phase == "connect":
                    smtp.side_effect = smtp_error
                elif phase == "auth":
                    connection.login.side_effect = smtp_error
                else:
                    connection.send_message.side_effect = smtp_error

                with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
                    main.smtplib,
                    "SMTP",
                    smtp,
                ):
                    with self.assertRaises(main.MailDeliveryError) as raised:
                        main._send_smtp_test_email()

                self.assertEqual(raised.exception.code, expected_code)

    def test_endpoint_returns_only_safe_error_codes(self):
        cases = [
            (main.MailConfigurationError(), 503, "missing_env"),
            (main.MailDeliveryError("smtp_auth_failed"), 502, "smtp_auth_failed"),
            (main.MailDeliveryError("smtp_connect_failed"), 502, "smtp_connect_failed"),
            (main.MailDeliveryError("smtp_send_failed"), 502, "smtp_send_failed"),
            (main.MailDeliveryError("unsafe detail"), 502, "smtp_send_failed"),
        ]

        for error, status_code, expected_code in cases:
            with self.subTest(code=expected_code), patch.dict(
                os.environ,
                MAIL_ENV,
                clear=True,
            ), patch.object(main, "_send_smtp_test_email", side_effect=error):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(main.send_mail_test("secret-value-9384"))

                self.assertEqual(raised.exception.status_code, status_code)
                self.assertEqual(raised.exception.detail, {"code": expected_code})
                self.assertNotIn("smtp-secret", str(raised.exception.detail))

    def test_valid_token_returns_success_without_exposing_configuration(self):
        with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
            main,
            "_send_smtp_test_email",
        ):
            result = asyncio.run(main.send_mail_test("secret-value-9384"))

        self.assertEqual(result, {"status": "ok", "message": "SMTP test email sent"})
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
            "smtp_auth_failed",
            "smtp_connect_failed",
            "smtp_send_failed",
        ):
            self.assertIn(code, body)
        self.assertNotIn(MAIL_ENV["MAIL_PASSWORD"], body)
        self.assertNotIn(MAIL_ENV["MAIL_TEST_TOKEN"], body)
        self.assertNotIn("console.", body)
        self.assertNotIn("URLSearchParams", body)


if __name__ == "__main__":
    unittest.main()
