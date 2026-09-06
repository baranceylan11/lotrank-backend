import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import notifications


MAIL_ENV = {
    "RESEND_API_KEY": "re_test_secret_api_key",
    "MAIL_FROM": "LotRank <info@lotrank.ai>",
    "ADMIN_REPORT_EMAIL": "admin@example.com",
    "HIGH_VALUE_MIN_SCORE": "85",
    "HIGH_VALUE_MIN_CONFIDENCE": "80",
    "HIGH_VALUE_MIN_MARGIN_EUR": "5000",
    "NOTIFICATION_DEBOUNCE_SECONDS": "3600",
}


class PilotNotificationTests(unittest.TestCase):
    def setUp(self):
        notifications._NOTIFICATION_GATE.reset()

    def test_only_four_categories_are_allowed(self):
        self.assertEqual(
            notifications.ALLOWED_NOTIFICATION_CATEGORIES,
            {
                "critical_system_error",
                "high_value_opportunity",
                "investor_reply",
                "weekly_admin_summary",
            },
        )
        with patch.object(notifications, "_send_resend_email") as send:
            result = notifications.send_pilot_notification(
                "routine_scan", "scan-1", "Routine scan", "Nothing important"
            )
        self.assertEqual(result["status"], "suppressed")
        self.assertEqual(result["reason"], "category_not_allowed")
        send.assert_not_called()

    def test_duplicate_event_is_suppressed_within_debounce_window(self):
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
        with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
            notifications, "_send_resend_email"
        ) as send:
            first = notifications.send_pilot_notification(
                "critical_system_error", "error-42", "Critical error", "Details", now=now
            )
            second = notifications.send_pilot_notification(
                "critical_system_error",
                "error-42",
                "Critical error",
                "Details",
                now=now + timedelta(minutes=30),
            )
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "suppressed")
        send.assert_called_once()

    def test_weekly_summary_is_limited_to_once_per_iso_week(self):
        thursday = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
        with patch.object(notifications, "_send_resend_email") as send:
            first = notifications.send_pilot_notification(
                "weekly_admin_summary", "summary-a", "Weekly summary", "A", now=thursday
            )
            second = notifications.send_pilot_notification(
                "weekly_admin_summary",
                "summary-b",
                "Weekly summary updated",
                "B",
                now=thursday + timedelta(days=2),
            )
            third = notifications.send_pilot_notification(
                "weekly_admin_summary",
                "summary-c",
                "Next weekly summary",
                "C",
                now=thursday + timedelta(days=7),
            )
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "suppressed")
        self.assertEqual(third["status"], "sent")
        self.assertEqual(send.call_count, 2)

    def test_high_value_thresholds_are_configurable(self):
        with patch.dict(os.environ, MAIL_ENV, clear=True):
            self.assertTrue(notifications.is_high_value_opportunity(90, 90, 8000))
            self.assertFalse(notifications.is_high_value_opportunity(84.9, 90, 8000))
            self.assertFalse(notifications.is_high_value_opportunity(90, 79.9, 8000))
            self.assertFalse(notifications.is_high_value_opportunity(90, 90, 4999))

    def test_invalid_threshold_values_fall_back_safely(self):
        env = dict(MAIL_ENV)
        env.update({
            "HIGH_VALUE_MIN_SCORE": "invalid",
            "HIGH_VALUE_MIN_CONFIDENCE": "-1",
            "HIGH_VALUE_MIN_MARGIN_EUR": "bad",
        })
        with patch.dict(os.environ, env, clear=True):
            thresholds = notifications.load_opportunity_thresholds()
        self.assertEqual(thresholds.min_score, notifications.DEFAULT_HIGH_VALUE_MIN_SCORE)
        self.assertEqual(
            thresholds.min_confidence, notifications.DEFAULT_HIGH_VALUE_MIN_CONFIDENCE
        )
        self.assertEqual(
            thresholds.min_margin_eur, notifications.DEFAULT_HIGH_VALUE_MIN_MARGIN_EUR
        )

    def test_missing_resend_configuration_fails_safely(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(notifications.MailConfigurationError):
                notifications.load_resend_settings()

    def test_resend_failures_are_safely_classified(self):
        cases = [
            (notifications.requests.ConnectionError("network"), None, "resend_connect_failed"),
            (None, 401, "resend_auth_failed"),
            (None, 403, "resend_auth_failed"),
            (None, 422, "resend_send_failed"),
        ]
        for request_error, status, code in cases:
            with self.subTest(code=code), patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
                notifications.requests, "post"
            ) as post:
                if request_error:
                    post.side_effect = request_error
                else:
                    post.return_value = MagicMock(status_code=status)
                with self.assertRaises(notifications.MailDeliveryError) as raised:
                    notifications._send_resend_email("Subject", "Body")
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn(MAIL_ENV["RESEND_API_KEY"], str(raised.exception))
                self.assertNotIn(MAIL_ENV["ADMIN_REPORT_EMAIL"], str(raised.exception))

    def test_successful_resend_request_does_not_return_secrets(self):
        response = MagicMock(status_code=200)
        with patch.dict(os.environ, MAIL_ENV, clear=True), patch.object(
            notifications.requests, "post", return_value=response
        ) as post:
            notifications._send_resend_email("Critical alert", "Safe body")
        call = post.call_args
        self.assertEqual(call.args[0], "https://api.resend.com/emails")
        self.assertEqual(call.kwargs["timeout"], 15)
        self.assertNotIn(MAIL_ENV["RESEND_API_KEY"], str(response))


if __name__ == "__main__":
    unittest.main()
