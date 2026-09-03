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
    "MAIL_TEST_TOKEN": "test-token",
}


class MailTestEndpointTests(unittest.TestCase):
    def test_rejects_missing_or_invalid_token(self):
        with patch.dict(os.environ, MAIL_ENV, clear=True):
            for token in (None, "wrong-token"):
                with self.subTest(token=token):
                    with self.assertRaises(HTTPException) as raised:
                        asyncio.run(main.send_mail_test(token))
                    self.assertEqual(raised.exception.status_code, 401)

    def test_sends_only_the_fixed_test_message_to_admin(self):
        smtp = MagicMock()
        smtp_context = smtp.return_value.__enter__.return_value

        with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
            main.smtplib,
            "SMTP",
            smtp,
        ):
            main._send_smtp_test_email()

        smtp.assert_called_once_with(host="smtp.example.com", port=587, timeout=15)
        smtp_context.starttls.assert_called_once()
        smtp_context.login.assert_called_once_with("info@lotrank.ai", "smtp-secret")
        message = smtp_context.send_message.call_args.args[0]
        self.assertEqual(message["From"], "LotRank <info@lotrank.ai>")
        self.assertEqual(message["To"], "admin@example.com")
        self.assertEqual(message["Subject"], "LotRank SMTP test başarılı")
        self.assertEqual(
            smtp_context.send_message.call_args.kwargs["to_addrs"],
            ["admin@example.com"],
        )
        self.assertNotIn("smtp-secret", message.as_string())

    def test_valid_token_returns_success_without_exposing_configuration(self):
        with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
            main,
            "_send_smtp_test_email",
        ):
            result = asyncio.run(main.send_mail_test("test-token"))

        self.assertEqual(result, {"status": "ok", "message": "SMTP test email sent"})
        self.assertNotIn("smtp-secret", str(result))


if __name__ == "__main__":
    unittest.main()
