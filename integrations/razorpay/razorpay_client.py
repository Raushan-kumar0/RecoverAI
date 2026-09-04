"""
RecoverAI — Razorpay Test-Mode Integration: HTTP Client (Step 7)

Thin, auditable wrapper around the Razorpay REST API using `requests` and
Basic Auth, exactly as documented at https://razorpay.com/docs/api/ (base
URL https://api.razorpay.com/v1, Basic Auth with key_id:key_secret).

Two real operations are implemented: creating a Payment Link
(POST /v1/payment_links) and fetching a Payment Link's status
(GET /v1/payment_links/:id) — both genuinely supported, safe, test-mode
APIs documented at https://razorpay.com/docs/payments/payment-links/apis/
and https://razorpay.com/docs/api/payments/payment-links/fetch-id-standard/.
Creating a link proves EXECUTE succeeded (an action was taken); fetching its
status is what can prove RECOVER succeeded (the customer actually paid) —
see recovery/recovery_checker.py, which is the only caller of the fetch
method. These are deliberately kept as two distinct methods on this one
client, never conflated.

payment_retry and mandate_retry are NOT implemented as real API calls here.
Razorpay's public API does not expose a merchant-triggered "retry this
failed charge now" endpoint: one-off payment retries require the customer
to re-attempt at checkout, and subscription charge retries are automatic
and server-scheduled (T+1/T+2/T+3 days), not API-triggerable on demand
(see https://razorpay.com/docs/subscriptions/payment-retries/). Per the
Step 1 rule "if a particular real-world money action cannot safely or
realistically be performed in test mode, design an honest bounded
simulation... rather than pretending it happened," these two actions are
handled by `simulate_retry_operation()` below: a clearly labeled SIMULATED
result, never a real network call, never claimed as a real API outcome.

DRY-RUN SAFETY: by default (RazorpayConfig.dry_run == True), NO network call
is ever made — `create_payment_link` returns a structured preview of exactly
what request would have been sent, with `status="dry_run"`. A real call only
happens if dry_run is explicitly False.
"""

import time
import uuid
from typing import Optional, Dict, Any

import requests

from razorpay_config import RazorpayConfig


class RazorpayTestModeClient:
    def __init__(self, config: RazorpayConfig):
        # Belt-and-suspenders: even if a caller somehow constructs a config
        # around a live key without going through load_config_from_env's
        # validation, refuse to proceed.
        if config.key_id.startswith("rzp_live_"):
            raise ValueError("Refusing to initialize client with a live-mode key.")
        self.config = config

    # ------------------------------------------------------------------ #
    # REAL operation: Payment Links API (POST /v1/payment_links)
    # ------------------------------------------------------------------ #
    def create_payment_link(self, amount_rupees: float, description: str,
                             reference_id: str, customer: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        amount_rupees: positive amount in INR (converted to paise for the API,
                        per Razorpay's "smallest currency unit" requirement).
        description:   shown to the customer on the hosted payment page.
        reference_id:  merchant-side unique reference (we use the case_id).
        customer:      optional {"name":..., "email":..., "contact":...}.
        """
        if amount_rupees is None or amount_rupees <= 0:
            raise ValueError(f"amount_rupees must be positive, got {amount_rupees}")
        if not reference_id:
            raise ValueError("reference_id is required")

        payload = {
            "amount": int(round(amount_rupees * 100)),  # paise
            "currency": "INR",
            "description": description[:2048],
            "reference_id": str(reference_id)[:40],
            "notify": {"sms": False, "email": False},  # never auto-notify in this integration; Step 9/12 concern
        }
        if customer:
            payload["customer"] = customer

        idempotency_key = str(uuid.uuid4())

        if self.config.dry_run:
            return {
                "status": "dry_run",
                "mode": "test",
                "would_call": {
                    "method": "POST",
                    "url": f"{self.config.base_url}/payment_links",
                    "payload": payload,
                    "auth": self.config.redacted(),
                    "idempotency_key": idempotency_key,
                },
                "note": "DRY_RUN is active — no network call was made. Set RECOVERAI_RAZORPAY_DRY_RUN=false to make a real test-mode call.",
                "timestamp": time.time(),
            }

        try:
            response = requests.post(
                f"{self.config.base_url}/payment_links",
                json=payload,
                auth=(self.config.key_id, self.config.key_secret),
                headers={"X-Razorpay-Idempotency-Key": idempotency_key},
                timeout=15,
            )
        except requests.exceptions.RequestException as e:
            # Never leak the secret in an exception message.
            return {
                "status": "error",
                "mode": "test",
                "error_type": type(e).__name__,
                "error": "network_or_connection_error",
                "timestamp": time.time(),
            }

        result = {
            "status": "executed" if response.status_code in (200, 201) else "api_error",
            "mode": "test",
            "http_status": response.status_code,
            "timestamp": time.time(),
        }
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text_truncated": response.text[:500]}

        if response.status_code in (200, 201):
            result["razorpay_payment_link_id"] = body.get("id")
            result["razorpay_short_url"] = body.get("short_url")
            result["razorpay_status"] = body.get("status")
        else:
            # Surface the error body but never the auth header.
            result["razorpay_error"] = body.get("error", body)

        return result

    # ------------------------------------------------------------------ #
    # REAL operation: fetch Payment Link status (GET /v1/payment_links/:id)
    # This is the RECOVER-stage signal — it observes whether the customer
    # actually paid. Creating a link (above) only proves EXECUTE succeeded;
    # THIS call is what can prove RECOVER succeeded. Documented at
    # https://razorpay.com/docs/api/payments/payment-links/fetch-id-standard/
    # ------------------------------------------------------------------ #
    def fetch_payment_link_status(self, payment_link_id: str) -> Dict[str, Any]:
        """
        Returns the current status of a previously created Payment Link.
        This is a read-only observation call — it never creates, modifies,
        or cancels anything. Callers (see recovery/recovery_checker.py) are
        responsible for interpreting `razorpay_status`/`amount_paid` into a
        recovery outcome; this method only reports what Razorpay returned.
        """
        if not payment_link_id:
            raise ValueError("payment_link_id is required")

        if self.config.dry_run:
            return {
                "status": "dry_run",
                "mode": "test",
                "would_call": {
                    "method": "GET",
                    "url": f"{self.config.base_url}/payment_links/{payment_link_id}",
                    "auth": self.config.redacted(),
                },
                "note": "DRY_RUN is active — no network call was made, so no payment status was observed.",
                "timestamp": time.time(),
            }

        try:
            response = requests.get(
                f"{self.config.base_url}/payment_links/{payment_link_id}",
                auth=(self.config.key_id, self.config.key_secret),
                timeout=15,
            )
        except requests.exceptions.RequestException as e:
            return {
                "status": "error",
                "mode": "test",
                "error_type": type(e).__name__,
                "error": "network_or_connection_error",
                "timestamp": time.time(),
            }

        result = {
            "status": "observed" if response.status_code == 200 else "api_error",
            "mode": "test",
            "http_status": response.status_code,
            "timestamp": time.time(),
        }
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text_truncated": response.text[:500]}

        if response.status_code == 200:
            result["razorpay_payment_link_id"] = body.get("id")
            result["razorpay_status"] = body.get("status")          # e.g. created/paid/partially_paid/expired/cancelled
            result["amount"] = body.get("amount")                    # paise, total link amount
            result["amount_paid"] = body.get("amount_paid")            # paise, actually paid so far — the real signal
        else:
            result["razorpay_error"] = body.get("error", body)

        return result

    # ------------------------------------------------------------------ #
    # SIMULATED operation: payment_retry / mandate_retry
    # ------------------------------------------------------------------ #
    def simulate_retry_operation(self, action_type: str, case_id: str, amount_rupees: float) -> Dict[str, Any]:
        """
        Returns a clearly labeled SIMULATION — never a real API call, never
        claimed as a genuine Razorpay result. See module docstring for why
        a real endpoint doesn't exist for this operation.
        """
        return {
            "status": "simulated",
            "mode": "test",
            "action_type": action_type,
            "case_id": case_id,
            "amount_rupees": amount_rupees,
            "note": (
                f"'{action_type}' has no merchant-triggerable Razorpay API endpoint for retrying "
                f"an existing failed charge on demand. This is a documented, honest simulation, "
                f"not a real Razorpay test-mode result. See module docstring."
            ),
            "timestamp": time.time(),
        }
