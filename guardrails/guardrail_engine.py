"""
RecoverAI — Guardrail Engine (Step 6)

Deterministic authorization layer between the Decision Engine (Step 5) and
future execution (Step 7). Takes the case, the Step 3 diagnosis, and the
Step 5 recommendation, and returns EXACTLY ONE outcome:

    AUTO_EXECUTE | APPROVAL_REQUIRED | STOP

This engine NEVER:
    - calls Razorpay
    - executes a recovery action
    - sends a communication
    - uses any post-action ground-truth field (amount_recovered,
      ground_truth_recovery_outcome, ground_truth_recoverable,
      recovery_observed, recovery_reason)

It ONLY reads: case facts (leakage_category, amount_at_risk, retry_count,
previous_attempt_count, customer_opt_out, suspicious_flag,
communication_allowed), Step 3's diagnosis_confidence, and Step 5's
recommended_action metadata (action_type, money_movement,
customer_communication, requires_merchant_approval).

==================================================
NOTE ON attempt-history source (documented limitation)
==================================================
Step 8 (Audit Trail) does not exist yet, so there is no live log of past
autonomous touches to query. This engine uses the case's own pre-decision
counters — `retry_count` (system-level payment/mandate retries already
attempted) and `previous_attempt_count` (broader recovery-touch count
already tried, any channel) — which are exactly the fields the Step 2
dataset generates for this purpose. Once Step 8 exists, these checks should
be re-pointed at the real audit trail instead of the dataset snapshot.

==================================================
NOTE ON retry_limit vs. autonomous_attempt_cap (documented design choice)
==================================================
Step 1 §6 describes `autonomous_attempt_cap` as a cap "before mandatory
escalation" — language that implies forcing human review, not a hard stop.
`retry_limit` (specifically for payment/mandate retries) is described more
strictly. This engine therefore treats:
    - retry_limit exceeded on a retry-type action  -> STOP
    - autonomous_attempt_cap exceeded (any action)  -> APPROVAL_REQUIRED
      (forces human review rather than dropping the case entirely)
This is a deliberate interpretation of the Step 1 wording, documented here
so it isn't mistaken for an oversight.

==================================================
NOTE ON contact_window (documented simplification)
==================================================
Step 1 lists "outside communication hours -> schedule later" as an example.
This engine's output is strictly tri-state (no SCHEDULED_LATER outcome), so
an out-of-window communication action is mapped to STOP ("not authorized
right now"); real rescheduling belongs to a future orchestrator (Step 9/12).
"""

import math
from datetime import datetime
from typing import Optional

from guardrail_config import GuardrailConfig, DEFAULT_GUARDRAIL_CONFIG
from guardrail_models import AuthorizationOutcome, GuardrailDecision, TriggeredRule

MONEY_MOVEMENT_RETRY_ACTIONS = {"payment_retry", "mandate_retry"}


def _get(obj, field_name):
    if hasattr(obj, "get"):
        return obj.get(field_name)
    return getattr(obj, field_name, None)


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def _within_contact_window(current_time: datetime, config: GuardrailConfig) -> bool:
    return config.contact_window_start_hour <= current_time.hour < config.contact_window_end_hour


class GuardrailEngine:
    def __init__(self, config: Optional[GuardrailConfig] = None):
        self.config = config or DEFAULT_GUARDRAIL_CONFIG

    def authorize(self, case, diagnosis, decision, current_time: Optional[datetime] = None) -> GuardrailDecision:
        """
        case: dict or pandas.Series (pre-decision fields only).
        diagnosis: dict from DiagnosisEngine.diagnose(case).
        decision: Decision from DecisionEngine.decide(case, ...) (Step 5).
        current_time: optional injectable clock, for deterministic testing.
                       Defaults to datetime.now().
        """
        current_time = current_time or datetime.now()
        config = self.config

        case_id = _get(case, "case_id")
        leakage_category = _get(case, "leakage_category")

        triggered_rules = []
        limits_checked = {}

        # ---------------------------------------------------------------
        # 0. Invalid / missing recommendation -> STOP
        # ---------------------------------------------------------------
        recommended_action = self._extract_recommended_action(decision)
        if recommended_action is None:
            reason = self._invalid_recommendation_reason(decision)
            return self._build_decision(
                case_id, leakage_category, None, AuthorizationOutcome.STOP, reason,
                [TriggeredRule("invalid_or_missing_recommendation", reason)],
                limits_checked, config, current_time,
            )

        action_type = recommended_action.get("action_type")
        money_movement = bool(recommended_action.get("money_movement"))
        customer_communication = bool(recommended_action.get("customer_communication"))
        requires_merchant_approval = bool(recommended_action.get("requires_merchant_approval"))

        # ---------------------------------------------------------------
        # Gather case facts (pre-decision only — never post-action fields)
        # ---------------------------------------------------------------
        suspicious_flag = bool(_get(case, "suspicious_flag"))
        customer_opt_out = bool(_get(case, "customer_opt_out"))
        communication_allowed = _get(case, "communication_allowed")
        amount_at_risk = _get(case, "amount_at_risk")
        retry_count = _get(case, "retry_count")
        previous_attempt_count = _get(case, "previous_attempt_count")
        diagnosis_confidence = diagnosis.get("diagnosis_confidence") if diagnosis else None

        limits_checked["suspicious_flag"] = suspicious_flag
        limits_checked["customer_opt_out"] = customer_opt_out
        limits_checked["communication_allowed"] = communication_allowed

        # ---------------------------------------------------------------
        # STOP conditions (checked first — most restrictive wins)
        # ---------------------------------------------------------------
        if suspicious_flag:
            rule = TriggeredRule("suspicious_flag", "Case is flagged as suspicious/high-risk; autonomous or approved action is not authorized.")
            triggered_rules.append(rule)
            return self._build_decision(case_id, leakage_category, action_type, AuthorizationOutcome.STOP,
                                         rule.description, triggered_rules, limits_checked, config, current_time)

        if customer_communication and customer_opt_out:
            rule = TriggeredRule("customer_opt_out_blocks_communication",
                                  "Recommended action requires customer communication, but the customer has opted out.")
            triggered_rules.append(rule)
            return self._build_decision(case_id, leakage_category, action_type, AuthorizationOutcome.STOP,
                                         rule.description, triggered_rules, limits_checked, config, current_time)

        if customer_communication and communication_allowed is False:
            rule = TriggeredRule("communication_not_allowed",
                                  "Recommended action requires customer communication, but communication is not permitted for this case.")
            triggered_rules.append(rule)
            return self._build_decision(case_id, leakage_category, action_type, AuthorizationOutcome.STOP,
                                         rule.description, triggered_rules, limits_checked, config, current_time)

        if customer_communication:
            within_window = _within_contact_window(current_time, config)
            limits_checked["contact_window"] = {
                "current_hour": current_time.hour,
                "window": [config.contact_window_start_hour, config.contact_window_end_hour],
                "within_window": within_window,
            }
            if not within_window:
                rule = TriggeredRule("outside_contact_window",
                                      f"Current time ({current_time.strftime('%H:%M')}) is outside the allowed contact window "
f"({config.contact_window_start_hour}:00-{config.contact_window_end_hour}:00); "
                                      f"not authorized now.")
                triggered_rules.append(rule)
                return self._build_decision(case_id, leakage_category, action_type, AuthorizationOutcome.STOP,
                                             rule.description, triggered_rules, limits_checked, config, current_time)

        if action_type in MONEY_MOVEMENT_RETRY_ACTIONS:
            retry_ok = (not _is_missing(retry_count)) and retry_count < config.retry_limit
            limits_checked["retry_count_vs_limit"] = {
                "retry_count": retry_count, "retry_limit": config.retry_limit, "passed": retry_ok,
            }
            if not retry_ok:
                rule = TriggeredRule("retry_limit_exceeded",
                                      f"retry_count ({retry_count}) has reached or exceeded the configured "
                                      f"retry_limit ({config.retry_limit}); no further automated retries authorized.")
                triggered_rules.append(rule)
                return self._build_decision(case_id, leakage_category, action_type, AuthorizationOutcome.STOP,
                                             rule.description, triggered_rules, limits_checked, config, current_time)

        # ---------------------------------------------------------------
        # APPROVAL_REQUIRED conditions (checked next)
        # ---------------------------------------------------------------
        approval_rules = []

        attempt_cap_ok = _is_missing(previous_attempt_count) or previous_attempt_count < config.autonomous_attempt_cap
        limits_checked["previous_attempt_count_vs_cap"] = {
            "previous_attempt_count": previous_attempt_count,
            "autonomous_attempt_cap": config.autonomous_attempt_cap,
            "passed": attempt_cap_ok,
        }
        if not attempt_cap_ok:
            approval_rules.append(TriggeredRule(
                "autonomous_attempt_cap_reached",
                f"previous_attempt_count ({previous_attempt_count}) has reached the configured "
                f"autonomous_attempt_cap ({config.autonomous_attempt_cap}); mandatory human review required "
                f"before any further touch (Step 1 §6)."
            ))

        confidence_ok = (diagnosis_confidence is not None) and (diagnosis_confidence >= config.confidence_threshold)
        limits_checked["diagnosis_confidence_vs_threshold"] = {
            "diagnosis_confidence": diagnosis_confidence,
            "confidence_threshold": config.confidence_threshold,
            "passed": confidence_ok,
        }
        if not confidence_ok:
            approval_rules.append(TriggeredRule(
                "confidence_below_threshold",
                f"diagnosis_confidence ({diagnosis_confidence}) is below the configured confidence_threshold "
                f"({config.confidence_threshold}); requires merchant approval rather than autonomous execution."
            ))

        monetary_ok = True
        if money_movement:
            monetary_ok = (not _is_missing(amount_at_risk)) and amount_at_risk <= config.monetary_ceiling
            limits_checked["amount_at_risk_vs_monetary_ceiling"] = {
                "amount_at_risk": amount_at_risk, "monetary_ceiling": config.monetary_ceiling, "passed": monetary_ok,
            }
            if not monetary_ok:
                approval_rules.append(TriggeredRule(
                    "monetary_ceiling_exceeded",
                    f"amount_at_risk ({amount_at_risk}) exceeds the configured monetary_ceiling "
                    f"({config.monetary_ceiling}) for a money-movement action; requires merchant approval."
                ))

        if requires_merchant_approval:
            approval_rules.append(TriggeredRule(
                "action_requires_merchant_approval_by_definition",
                f"'{action_type}' is defined (Step 4 catalog) as inherently requiring merchant approval "
                f"(e.g. escalation is a human handoff by design)."
            ))

        if approval_rules:
            triggered_rules.extend(approval_rules)
            reason = "; ".join(r.description for r in approval_rules)
            return self._build_decision(case_id, leakage_category, action_type, AuthorizationOutcome.APPROVAL_REQUIRED,
                                         reason, triggered_rules, limits_checked, config, current_time)

        # ---------------------------------------------------------------
        # All checks passed -> AUTO_EXECUTE
        # ---------------------------------------------------------------
        rule = TriggeredRule("all_guardrail_checks_passed",
                              "No stopping or approval condition was triggered; action is within all configured limits.")
        triggered_rules.append(rule)
        return self._build_decision(case_id, leakage_category, action_type, AuthorizationOutcome.AUTO_EXECUTE,
                                     rule.description, triggered_rules, limits_checked, config, current_time)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_recommended_action(decision):
        if decision is None:
            return None
        recommended_action = _get(decision, "recommended_action")
        decision_status = _get(decision, "decision_status")
        status_value = decision_status.value if hasattr(decision_status, "value") else decision_status
        if status_value != "recommended":
            return None
        if not recommended_action:
            return None
        return recommended_action

    @staticmethod
    def _invalid_recommendation_reason(decision):
        if decision is None:
            return "No decision was provided to authorize."
        decision_status = _get(decision, "decision_status")
        status_value = decision_status.value if hasattr(decision_status, "value") else decision_status
        if status_value == "not_applicable":
            return "Decision status is not_applicable (no revenue at risk); nothing to authorize."
        if status_value == "no_applicable_actions":
            return "Decision status is no_applicable_actions; there is no recommended action to authorize."
        return f"Decision has no valid recommended_action (status={status_value!r}); cannot authorize."

    @staticmethod
    def _build_decision(case_id, leakage_category, action_type, outcome, reason,
                         triggered_rules, limits_checked, config, current_time) -> GuardrailDecision:
        return GuardrailDecision(
            case_id=case_id,
            leakage_category=leakage_category,
            recommended_action_type=action_type,
            outcome=outcome,
            reason=reason,
            triggered_rules=triggered_rules,
            limits_checked=limits_checked,
            approval_required=(outcome == AuthorizationOutcome.APPROVAL_REQUIRED),
            config_used={
                "retry_limit": config.retry_limit,
                "autonomous_attempt_cap": config.autonomous_attempt_cap,
                "attempt_cap_window_days": config.attempt_cap_window_days,
                "confidence_threshold": config.confidence_threshold,
                "monetary_ceiling": config.monetary_ceiling,
                "contact_window_start_hour": config.contact_window_start_hour,
                "contact_window_end_hour": config.contact_window_end_hour,
            },
            evaluated_at=current_time.isoformat(),
        )
