"""
RecoverAI — Razorpay Test-Mode Integration: Credential Configuration (Step 7)

Loads Razorpay credentials EXCLUSIVELY from environment variables — never
from source code, config files committed to the repo, or hardcoded strings.

Required environment variables:
    RAZORPAY_KEY_ID       e.g. rzp_test_XXXXXXXXXXXXXX
    RAZORPAY_KEY_SECRET   the matching test-mode secret

Optional:
    RECOVERAI_RAZORPAY_DRY_RUN   "true" (default) | "false"
                                   Defaults to DRY-RUN: no network call is
                                   ever made unless this is explicitly set
                                   to "false". This is a deliberate
                                   safe-by-default design, not an oversight.

HARD SAFETY RULE: a key_id starting with "rzp_live_" is refused outright,
unconditionally, regardless of any other setting. This integration will
NEVER operate against live-mode credentials.
"""

import os
from dataclasses import dataclass


class CredentialError(Exception):
    """Base class for all credential problems. Callers should catch this and
    fail safely (treat as 'cannot execute'), never crash the whole pipeline."""


class MissingCredentialsError(CredentialError):
    pass


class MalformedCredentialsError(CredentialError):
    pass


class LiveModeCredentialsError(CredentialError):
    """Raised when a live-mode key is detected. This is unconditional and
    cannot be overridden by any configuration flag."""


TEST_KEY_PREFIX = "rzp_test_"
LIVE_KEY_PREFIX = "rzp_live_"
MIN_SECRET_LENGTH = 8


@dataclass(frozen=True)
class RazorpayConfig:
    key_id: str
    key_secret: str
    dry_run: bool
    base_url: str = "https://api.razorpay.com/v1"

    def redacted(self):
        """Safe-to-log representation — NEVER include key_secret or the full
        key_id in any log line, error message, or audit record."""
        visible = self.key_id[:12] + "..." if len(self.key_id) > 12 else "***"
        return {"key_id": visible, "key_secret": "***REDACTED***", "dry_run": self.dry_run, "mode": "test"}


def _parse_dry_run_flag(raw_value) -> bool:
    if raw_value is None:
        return True  # default: safe
    return raw_value.strip().lower() != "false"


def load_config_from_env() -> RazorpayConfig:
    """
    Loads and validates Razorpay Test-Mode credentials from environment
    variables. Raises a CredentialError subclass on any problem — callers
    must catch this and fail safely (see razorpay_client.py).
    """
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    print(
    "DEBUG: RECOVERAI_RAZORPAY_DRY_RUN =",
    repr(os.environ.get("RECOVERAI_RAZORPAY_DRY_RUN"))
    )
    dry_run = _parse_dry_run_flag(os.environ.get("RECOVERAI_RAZORPAY_DRY_RUN"))

    if not key_id or not key_secret:
        raise MissingCredentialsError(
            "RAZORPAY_KEY_ID and/or RAZORPAY_KEY_SECRET environment variables are not set. "
            "See recoverai/integrations/razorpay/.env.example for setup instructions."
        )

    key_id = key_id.strip()
    key_secret = key_secret.strip()

    if key_id.startswith(LIVE_KEY_PREFIX):
        raise LiveModeCredentialsError(
            "A live-mode Razorpay key (rzp_live_...) was detected. This integration refuses to "
            "operate with live-mode credentials under any configuration. Use a rzp_test_... key."
        )

    if not key_id.startswith(TEST_KEY_PREFIX):
        raise MalformedCredentialsError(
            f"RAZORPAY_KEY_ID does not look like a valid test-mode key (expected prefix "
            f"'{TEST_KEY_PREFIX}')."
        )

    if len(key_secret) < MIN_SECRET_LENGTH:
        raise MalformedCredentialsError(
            f"RAZORPAY_KEY_SECRET is shorter than the minimum expected length ({MIN_SECRET_LENGTH})."
        )

    return RazorpayConfig(key_id=key_id, key_secret=key_secret, dry_run=dry_run)
