import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr

import requests

ALLOWED_NOTIFICATION_CATEGORIES = frozenset({
    "critical_system_error",
    "high_value_opportunity",
    "investor_reply",
    "daily_admin_summary",
})

SAFE_MAIL_ERROR_CODES = frozenset({
    "missing_env",
    "resend_auth_failed",
    "resend_connect_failed",
    "resend_send_failed",
})

DEFAULT_HIGH_VALUE_MIN_SCORE = 85.0
DEFAULT_HIGH_VALUE_MIN_CONFIDENCE = 80.0
DEFAULT_HIGH_VALUE_MIN_MARGIN_EUR = 5000.0
DEFAULT_NOTIFICATION_DEBOUNCE_SECONDS = 21600


@dataclass(frozen=True)
class ResendSettings:
    api_key: str
    from_value: str
    from_address: str
    admin_email: str


@dataclass(frozen=True)
class OpportunityThresholds:
    min_score: float
    min_confidence: float
    min_margin_eur: float


class MailConfigurationError(Exception):
    pass


class MailDeliveryError(Exception):
    def __init__(self, code):
        self.code = code if code in SAFE_MAIL_ERROR_CODES else "resend_send_failed"
        super().__init__(self.code)


class NotificationGate:
    """Process-local duplicate suppression for the low-volume pilot.

    This intentionally does not create a scheduler or external state dependency.
    A later production phase can move the state to the database if multi-instance
    delivery is enabled.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sent_at = {}
        self._daily_summary_dates = set()

    def reset(self):
        with self._lock:
            self._sent_at.clear()
            self._daily_summary_dates.clear()

    def allow(self, category, fingerprint, now, debounce_seconds):
        with self._lock:
            if category == "daily_admin_summary":
                day_key = now.date().isoformat()
                if day_key in self._daily_summary_dates:
                    return False
                self._daily_summary_dates.add(day_key)
                return True

            previous = self._sent_at.get(fingerprint)
            if previous is not None and (now - previous).total_seconds() < debounce_seconds:
                return False
            self._sent_at[fingerprint] = now
            return True


_NOTIFICATION_GATE = NotificationGate()


def _read_float_env(name, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _read_int_env(name, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def load_resend_settings():
    api_key = os.getenv("RESEND_API_KEY", "")
    from_value = os.getenv("MAIL_FROM", "").strip()
    admin_email_value = os.getenv("ADMIN_REPORT_EMAIL", "").strip()
    if not api_key or not from_value or not admin_email_value:
        raise MailConfigurationError

    from_address = parseaddr(from_value)[1].lower()
    admin_email = parseaddr(admin_email_value)[1]
    if from_address != "info@lotrank.ai" or not admin_email:
        raise MailConfigurationError

    return ResendSettings(
        api_key=api_key,
        from_value=from_value,
        from_address=from_address,
        admin_email=admin_email,
    )


def load_opportunity_thresholds():
    return OpportunityThresholds(
        min_score=_read_float_env("HIGH_VALUE_MIN_SCORE", DEFAULT_HIGH_VALUE_MIN_SCORE),
        min_confidence=_read_float_env(
            "HIGH_VALUE_MIN_CONFIDENCE", DEFAULT_HIGH_VALUE_MIN_CONFIDENCE
        ),
        min_margin_eur=_read_float_env(
            "HIGH_VALUE_MIN_MARGIN_EUR", DEFAULT_HIGH_VALUE_MIN_MARGIN_EUR
        ),
    )


def is_high_value_opportunity(score, confidence, margin_eur):
    thresholds = load_opportunity_thresholds()
    return (
        float(score) >= thresholds.min_score
        and float(confidence) >= thresholds.min_confidence
        and float(margin_eur) >= thresholds.min_margin_eur
    )


def _fingerprint(category, event_key, subject):
    canonical = json.dumps(
        {"category": category, "event_key": str(event_key), "subject": subject},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _send_resend_email(subject, text):
    settings = load_resend_settings()
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Accept": "application/json",
            },
            json={
                "from": settings.from_value,
                "to": [settings.admin_email],
                "subject": subject,
                "text": text,
            },
            timeout=15,
        )
    except (requests.Timeout, requests.ConnectionError):
        raise MailDeliveryError("resend_connect_failed") from None
    except requests.RequestException:
        raise MailDeliveryError("resend_send_failed") from None

    if response.status_code in (401, 403):
        raise MailDeliveryError("resend_auth_failed")
    if not 200 <= response.status_code < 300:
        raise MailDeliveryError("resend_send_failed")


def send_pilot_notification(category, event_key, subject, text, *, now=None):
    """Send one approved pilot notification.

    Routine/unknown categories are rejected without touching Resend. The caller
    must explicitly qualify high-value opportunities before calling this helper.
    """
    if category not in ALLOWED_NOTIFICATION_CATEGORIES:
        return {"status": "suppressed", "reason": "category_not_allowed"}

    current_time = now or datetime.now(timezone.utc)
    debounce_seconds = _read_int_env(
        "NOTIFICATION_DEBOUNCE_SECONDS", DEFAULT_NOTIFICATION_DEBOUNCE_SECONDS
    )
    fingerprint = _fingerprint(category, event_key, subject)
    if not _NOTIFICATION_GATE.allow(
        category, fingerprint, current_time, debounce_seconds
    ):
        return {"status": "suppressed", "reason": "duplicate_or_rate_limited"}

    _send_resend_email(subject, text)
    return {"status": "sent", "category": category}
