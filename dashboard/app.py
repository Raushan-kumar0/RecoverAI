"""
RecoverAI — Dashboard (Step 12)

PURE PRESENTATION LAYER. Every number shown here comes from dashboard_data.py,
which itself only calls existing, already-tested Step 3-11 functions. This
file contains no diagnosis, decision, guardrail, execution, recovery, or
measurement logic of its own.

Run:
    streamlit run app.py
(from this directory), or:
    streamlit run dashboard/app.py
(from the project root).
"""

import sys
import os
from pathlib import Path
from datetime import datetime
import re

sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import pandas as pd

import dashboard_data as dd
from audit_store import AuditStore
from promise_store import PromiseStore

st.set_page_config(page_title="RecoverAI — Revenue Recovery Agent", layout="wide", page_icon="💰")
st.markdown("""
<style>
    [data-testid="stMetric"] {
        background-color: #161B22;
        border: 1px solid #262B36;
        border-left: 4px solid #0B5FFF;
        border-radius: 8px;
        padding: 14px 16px 10px 16px;
        transition: border-color 0.25s ease, transform 0.15s ease, box-shadow 0.2s ease;
        animation: fadeSlideIn 0.35s ease-out;
    }
    [data-testid="stMetric"]:hover {
        border-left-color: #58A6FF;
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(11, 95, 255, 0.15);
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.85rem;
        opacity: 0.85;
    }
    [data-testid="stExpander"] {
        transition: border-color 0.25s ease, box-shadow 0.2s ease;
        animation: fadeSlideIn 0.4s ease-out;
    }
    [data-testid="stExpander"]:hover {
        border-color: #3d4551;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.25);
    }
    @keyframes fadeSlideIn {
        from { opacity: 0; transform: translateY(6px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    span[style*="border-radius:12px"] {
        transition: background-color 0.3s ease, color 0.3s ease;
        animation: fadeSlideIn 0.3s ease-out;
    }
    .stButton > button {
        transition: transform 0.15s ease, box-shadow 0.2s ease;
    }
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 3px 10px rgba(11, 95, 255, 0.25);
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        transition: color 0.2s ease;
    }
    .stSpinner > div > div {
        border-top-color: #0B5FFF !important;
    }
    hr {
        border-color: #262B36;
    }
</style>
""", unsafe_allow_html=True)

RAZORPAY_TEST_MODE_BADGE = "🧪 **RAZORPAY TEST MODE** — never production revenue"


def _money(x):
    if x is None:
        return "—"
    return f"₹{x:,.2f}"


def _pct(x):
    if x is None:
        return "—"
    return f"{x:.1%}"

def humanize_reason(reason: str, recovery_status: str = None) -> str:
    """
    Translates known technical recovery-reason strings into plain language.
    Never invents information — only rewords patterns we know the real
    recovery_checker.py can produce (see recovery/recovery_checker.py).
    Falls back to the original, real reason text for anything unrecognized,
    so nothing is ever silently hidden or guessed at.
    """
    if not reason:
        return reason

    m = re.search(r"status as 'paid' with amount_paid=(\d+\.?\d*)", reason)
    if m:
        return f"✅ The customer paid the full amount (₹{float(m.group(1)):,.2f}), confirmed directly by Razorpay."

    m = re.search(r"with a partial amount_paid=(\d+\.?\d*)", reason)
    if m:
        return f"🟡 The customer paid part of the amount (₹{float(m.group(1)):,.2f} so far), confirmed by Razorpay — the link isn't fully paid yet."

    if "amount_paid=0" in reason and "not yet paid" in reason:
        m = re.search(r"status as '(\w+)'", reason)
        status_word = m.group(1) if m else "created"
        return f"⏳ Razorpay confirms this Payment Link is still '{status_word}' — nobody has paid it yet."

    m = re.search(r"Action type '(\w+)' never creates a Payment Link", reason)
    if m:
        return f"❔ This case used a simulated action ('{m.group(1)}') that has no real payment link — there's nothing for Razorpay to check."

    if "no real Payment Link exists to check" in reason:
        return "❔ No real payment link was ever created for this case (it was a dry-run, simulated, or failed attempt) — so there's nothing to check."

    if "Status-check call failed" in reason:
        return "⚠️ We tried to check with Razorpay but the request itself failed (network/API issue) — we genuinely don't know the payment status, we're not saying it's unpaid."

    if "Unexpected status-check response shape" in reason:
        return "⚠️ Razorpay sent back an unexpected response — rather than guess, we're marking this as unobserved."

    if "DRY_RUN is active" in reason:
        return "🧪 Dry-run mode — no real check was made against Razorpay."

    return reason



# ------------------------------------------------------------------ #
# Session state: audit store + last batch results persist across reruns
# ------------------------------------------------------------------ #
if "audit_store" not in st.session_state:
    st.session_state.audit_store = AuditStore(":memory:")
if "case_results" not in st.session_state:
    st.session_state.case_results = []
if "sample_df" not in st.session_state:
    st.session_state.sample_df = None

st.title("RecoverAI — Autonomous Revenue Recovery Agent")
st.caption("Razorpay Hackathon — Track 03 · 🧪 Test Mode only")

diag_engine, dec_engine, guard_engine, razorpay_client, credential_error = dd.get_engines()

with st.sidebar:
    st.header("Run controls")

    if credential_error:
        st.error(f"No live run available: {credential_error}")
    elif razorpay_client is None:
        st.warning(
            "Razorpay credentials not configured — running without live execution."
        )
    else:
        mode_label = (
            "DRY RUN (no network call)"
            if dd.is_dry_run()
            else "LIVE (real network call attempted)"
        )
        st.success(
            f"Razorpay is connected and ready. Current mode: {mode_label}."
        )

    n_cases = st.slider("Number of cases to run", 1, 15, 5)

    if st.button("Run batch through full pipeline", type="primary"):
        sample = dd.load_dataset_sample(
            n=n_cases,
            leakage_only=True,
            comm_allowed_only=True
        )

        with st.spinner(
            "Running DETECT → DIAGNOSE → DECIDE → GUARDRAIL → EXECUTE → RECOVER..."
        ):
            results = dd.run_batch(
                sample,
                diag_engine,
                dec_engine,
                guard_engine,
                razorpay_client,
                st.session_state.audit_store,
                current_time=datetime.now()
            )

        st.session_state.case_results = results
        st.session_state.sample_df = sample

        st.success(f"Ran {len(results)} case(s).")

    # --------------------------------------------------------------
    # Manual Razorpay Payment Link checker
    # --------------------------------------------------------------

    st.divider()

    st.subheader("🔎 Check Payment Status")

    st.caption(
        "Check whether a Razorpay Payment Link has been paid."
        
    )

    link_id = st.text_input(
        "Payment Link ID",
        placeholder="plink_..."
    )

    sidebar_case_id = st.text_input(
        "Case ID (optional — enter to permanently record this recovery)",
        key="sidebar_case_id_input",
    )

    if st.button("Check status"):

        if razorpay_client is None:
            st.error("No Razorpay client configured.")

        elif not link_id.strip():
            st.error("Enter a Payment Link ID.")

        else:
            with st.spinner("Checking Razorpay..."):
                result = dd.check_payment_link(
                    link_id.strip(),
                    razorpay_client,
                    case_id=sidebar_case_id.strip() or None,
                )

            st.session_state.manual_check_result = result

    # --------------------------------------------------------------
    # Show manual check result INSIDE SIDEBAR
    # --------------------------------------------------------------

    if "manual_check_result" in st.session_state:

        rec = st.session_state.manual_check_result

        st.divider()
        st.subheader("Manual Check Result")

        if rec["recovery_status"] == "recovered":

            st.success(
                f"✅ RECOVERED\n\n"
                f"Observed amount: {_money(rec['amount_recovered'])}"
            )

        elif rec["recovery_status"] == "pending":

            st.warning("⏳ PAYMENT PENDING")

        elif rec["recovery_status"] == "partially_recovered":

            st.warning(
                f"🟡 PARTIALLY RECOVERED\n\n"
                f"Observed amount: {_money(rec['amount_recovered'])}"
            )

        else:

            st.info(
                f"❔ {rec['recovery_status'].upper()}\n\n"
                f"{rec['reason']}"
            )

        st.caption(
            f"Payment Link: {rec.get('payment_link_id')}"
        )

        st.caption(
            f"Source: {rec['observation_source']}"
        )


has_results = len(st.session_state.case_results) > 0

tabs = st.tabs([
    "A. Overview",
    "B. Diagnosis",
    "C. Decision + Guardrail",
    "D. Execution",
    "E. Recovery",
    "F. Measurement",
    "G. Audit Trail",
    "H. Promises",
])
# ---------------------------------------------------------------- A ---
with tabs[0]:
    with st.container(border=True):
         st.write("#### 🗺️ How to navigate this dashboard")
         st.caption("A. Overview → Choose no. of cases to run and Scroll down for Batch Overview and Historical Verified Recoveries.")
         st.caption("B. Diagnosis → See why each case failed and how likely it is to recover.")
         st.caption("C. Decision + Guardrail → See what action was recommended and whether it was approved.")
         st.caption("D. Execution →  what actually happened (real API calls or honest simulations), plus the Retry Sequencer, Payment Routing Optimizer, and Hinglish Script Generator.")
         st.caption("E. Recovery →  Independently verified payment status — the ONLY tab that can say RECOVERED.")
         st.caption("F. Measurement → See four separated metrics (model performance, synthetic backtest, live execution, observed recovery).")
         st.caption("G. Audit Trail → Follow the complete history of any case from start to finish.")
         st.caption("H. Promises → Track customer payment promises and check whether they were fulfilled.")

    st.divider()
    st.subheader("📊 Batch Overview")
    st.markdown(
    '<a href="#verified-recoveries" style="text-decoration:none;">'
    '<button style="padding:8px 16px;border-radius:8px;border:1px solid #3FB950;'
    'background:#161B22;color:#3FB950;cursor:pointer;">'
    'Jump to verified evidence ↓'
    '</button></a>',
    unsafe_allow_html=True
)
    if not has_results:
        st.info("No live run yet. Use the sidebar to run a batch — nothing is shown until real data exists.")
    else:
        overview = dd.get_overview_metrics(st.session_state.case_results, st.session_state.sample_df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Revenue at risk", _money(overview["revenue_at_risk"]))
        c2.metric("Cases analyzed", overview["cases_analyzed"])
        c3.metric("Actions recommended", overview["recommended_actions"])
        c4.metric("Guardrail approved actions", overview["guardrail_approved_actions"])
        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Actions executed", overview["actions_executed"])
        c6.metric("Cases needing approval", overview["approval_required_cases"])
        c7.metric("Stopped cases", overview["stopped_cases"])
        c8.metric("Recovered revenue", _money(overview["observed_recovered_revenue"]),
                   help="ONLY from Razorpay-observed paid status. Never inferred from execution or prediction.")
        st.metric("Recovery rate (observed ÷ total at risk)", _pct(overview["recovery_rate"]))
        if overview["observed_recovered_revenue"] == 0.0:
            st.caption("ℹ️ ₹0.00 Recovered revenue means either nothing has been paid yet, "
                       "or genuine payment confirmation isn't available in this environment (Check the Recovery tab for payment status).")
            
        st.divider()
        st.markdown('<div id="verified-recoveries"></div>', unsafe_allow_html=True)

        with st.container(border=True):
            st.write("#### 🏆 Historical Verified Recoveries (all sessions, all-time)")
            st.caption("Verified payments confirmed by Razorpay across all previous runs."
                       "This number is kept separate from the current batch. ")
                       
            hc1, hc2 = st.columns(2)
            hc1.metric("Total verified recovered (all-time)", _money(overview["historical_verified_recovered_revenue"]))
            hc2.metric("Cases with confirmed payment (all-time)", overview["historical_verified_case_count"])

# ---------------------------------------------------------------- B ---
with tabs[1]:
    st.subheader("🔍 AI Diagnosis")
    if not has_results:
        st.info("No live run yet.")
    else:
        for r in st.session_state.case_results:
            with st.expander(f"{r['case_id']} — {r['leakage_category']}"):
                diagnosis = r["diagnosis"]
                if diagnosis.get("predicted_recovery_likelihood") is None:
                    st.write("Not applicable — no revenue at risk (successful transaction).")
                else:
                    st.write(f"**Root cause:** {diagnosis.get('root_cause')}")
                    col1, col2 = st.columns(2)
                    likelihood = diagnosis.get("predicted_recovery_likelihood")
                    confidence = diagnosis.get("diagnosis_confidence")

                    def _badge(value, high_threshold, low_threshold):
                        if value is None:
                            return ""
                        if value >= high_threshold:
                            return (
                                '<span style="background-color:#1a3a2a;color:#3FB950;'
                                'font-size:0.8rem;padding:3px 10px;border-radius:12px;'
                                'display:inline-block;">↑ High</span>'
                            )
                        elif value < low_threshold:
                            return (
                                '<span style="background-color:#3a1a1a;color:#F85149;'
                                'font-size:0.8rem;padding:3px 10px;border-radius:12px;'
                                'display:inline-block;">↓ Low</span>'
                            )
                        return ""

                    col1.metric("Chance of recovery", _pct(likelihood))
                    col1.markdown(_badge(likelihood, 0.7, 0.4), unsafe_allow_html=True)
                    col2.metric("Model confidence", _pct(confidence))
                    col2.markdown(_badge(confidence, 0.6, 0.4), unsafe_allow_html=True)
                    st.caption("⚠️ This is an AI prediction — it does not mean payment was recovered.")
                    if diagnosis.get("risk_factors"):
                        st.write("**Risk factors:**")
                        for factor in diagnosis["risk_factors"]:
                            st.markdown(f"- {factor}")
                    if diagnosis.get("positive_recovery_signals"):
                        st.write("**Positive signals:**")
                        for signal in diagnosis["positive_recovery_signals"]:
                            st.markdown(f"- {signal}")

# ---------------------------------------------------------------- C ---
with tabs[2]:
    st.subheader("⚖️ Decision + Guardrail")
    if not has_results:
        st.info("No live run yet.")
    else:
        for r in st.session_state.case_results:
            decision, guardrail = r.get("decision"), r.get("guardrail")
            with st.expander(f"{r['case_id']}"):
                if decision is None:
                    st.write("Not applicable — no decision was made (no revenue at risk).")
                    continue
                st.markdown(
                    f'**Recommended action:** <span style="background-color:#1a2e1a;color:#3FB950;'
                    f'font-size:0.85rem;padding:3px 10px;border-radius:12px;font-family:monospace;">'
                    f'{decision.recommended_action_type}</span>',
                    unsafe_allow_html=True,
                )

                st.write("**Why this action?**")
                likelihood = decision.predicted_recovery_likelihood
                confidence = decision.diagnosis_confidence
                tier = decision.likelihood_tier.value if hasattr(decision.likelihood_tier, "value") else decision.likelihood_tier
                st.write(f"- Recovery likelihood: {_pct(likelihood)}")
                st.write(f"- Model confidence: {_pct(confidence)}")
                st.write(f"- Risk tier: {str(tier).title()}")
                st.caption("The system recommends the most direct recovery option available for this tier and category.")
                st.caption("↑ This is a RECOMMENDATION, not an authorization.")

                if guardrail:
                    outcome = guardrail.outcome.value
                    badge_style = {
                        "auto_execute": ("#1a2e1a", "#3FB950", "✅ Auto-approved"),
                        "approval_required": ("#3a2e0a", "#E3B341", "🟡 Approval required"),
                        "stop": ("#3a1a1a", "#F85149", "🛑 Stopped"),
                    }.get(outcome, ("#262B36", "#E6E6E6", outcome))
                    bg, color, label = badge_style
                    st.markdown(
                        f'<span style="background-color:{bg};color:{color};'
                        f'font-size:0.9rem;padding:4px 12px;border-radius:12px;">{label}</span>',
                        unsafe_allow_html=True,
                    )
                    st.write("**Why?**")
                    if "confidence_threshold" in guardrail.reason and confidence is not None:
                        from guardrail_config import GuardrailConfig
                        threshold = GuardrailConfig().confidence_threshold
                        st.write(f"Model confidence ({_pct(confidence)}) is below the {_pct(threshold)} threshold "
                                 f"— requires merchant approval.")
                    else:
                        st.write(guardrail.reason)  # any other real rule (contact hours, amount limits, etc.) — shown as-is
                    st.write(f"**Execution authorized:** {'Yes' if outcome == 'auto_execute' else 'No'}")

# ---------------------------------------------------------------- D ---
CARD_STYLE = (
    "background:#15181f;border:1px solid #262b36;border-radius:10px;"
    "padding:12px 16px;display:flex;align-items:center;gap:12px;margin-bottom:10px;"
)
ICON_BOX_STYLE = (
    "background:#20242e;border-radius:6px;width:28px;height:28px;"
    "display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:14px;"
)
BADGE_STYLE = (
    "background:#20242e;border-radius:8px;padding:3px 10px;font-size:12px;"
    "color:#c9cdd6;display:inline-flex;align-items:center;gap:5px;"
)

EXEC_ICON = {
    "executed": "✅", "dry_run": "🧪", "simulated": "🔁",
    "api_error": "⚠️", "error": "⚠️", "not_executed": "⛔",
}
EXEC_LABEL = {
    "executed": "Executed", "dry_run": "Dry run", "simulated": "Simulated",
    "api_error": "API error", "error": "Error", "not_executed": "Not executed",
}
RECOVERY_ICON = {
    "recovered": "💰", "partially_recovered": "💰", "pending": "⏳",
    "not_observed": "❔", "observation_failed": "⚠️",
}
RECOVERY_LABEL = {
    "recovered": "Recovered", "partially_recovered": "Partially recovered",
    "pending": "Pending recovery", "not_observed": "Not observed",
    "observation_failed": "Observation failed",
}


def badge_html(icon: str, label: str) -> str:
    return f'<span style="{BADGE_STYLE}">{icon} {label}</span>'


with tabs[3]:
    st.subheader("⚙️ Execution")
    st.markdown(
    '<a href="#automation-tools" style="text-decoration:none;">'
    '<button style="padding:8px 16px;border-radius:8px;border:1px solid #3FB950;'
    'background:#161B22;color:#3FB950;cursor:pointer;">'
    'Jump to Automation tools ↓'
    '</button></a>',
    unsafe_allow_html=True
)
    st.caption(RAZORPAY_TEST_MODE_BADGE)

    if not has_results:
        st.info("No live run yet.")
    else:
        st.markdown("**Cases**")
        for r in st.session_state.case_results:
            fhr = r.get("failure_handling_result")
            exec_status = None
            recovery_status = None
            if fhr and not r.get("no_client_reason"):
                exec_status = fhr["primary_execution"].get("execution_status")
                rec = fhr.get("fallback_recovery_result")
                if rec:
                    recovery_status = rec.get("recovery_status")

            badges_html = ""
            if exec_status:
                badges_html += badge_html(EXEC_ICON.get(exec_status, "•"), EXEC_LABEL.get(exec_status, exec_status))
            if recovery_status:
                badges_html += badge_html(RECOVERY_ICON.get(recovery_status, "•"), RECOVERY_LABEL.get(recovery_status, recovery_status))

            row_cols = st.columns([5, 4])
            with row_cols[0]:
                st.markdown(f"`{r['case_id']}`")
            with row_cols[1]:
                if badges_html:
                    st.markdown(badges_html, unsafe_allow_html=True)

            with st.expander("Details", expanded=False):
                if r.get("no_client_reason"):
                    st.warning(r["no_client_reason"])
                    continue
                if fhr is None:
                    st.write("Not applicable.")
                    continue
                primary = fhr["primary_execution"]

                st.markdown(f"**Action type:** `{primary.get('action_type')}`")

                if r.get("reused_prior_execution"):
                    st.caption(
                        "♻️ This case already had a real Payment Link from a previous run — "
                        "reusing it and re-checking its current status, instead of creating a duplicate."
                    )

                st.markdown(
                    f"**Execution status:** {badge_html(EXEC_ICON.get(exec_status, '•'), EXEC_LABEL.get(exec_status, exec_status))}",
                    unsafe_allow_html=True,
                )

                razorpay_result = primary.get("razorpay_result")
                if razorpay_result:
                    with st.expander("View raw Razorpay result"):
                        st.json(razorpay_result)

                st.write(f"**Execution source:** {primary.get('result_source')}")
                rz = primary.get("razorpay_result", {})
                if rz.get("razorpay_payment_link_id"):
                    st.write(f"**Payment Link ID:** `{rz['razorpay_payment_link_id']}`")
                if rz.get("razorpay_short_url"):
                    st.write(f"**Payment Link URL:** {rz['razorpay_short_url']}")
                st.info("Creating a Payment Link = EXECUTED. It does NOT mean revenue was recovered — see below.")

                if fhr.get("fallback_attempted"):
                    st.write(
                        f"**Fallback attempted:** `{fhr.get('fallback_action_type')}` → "
                        f"{fhr['fallback_execution'].get('execution_status') if fhr.get('fallback_execution') else '—'}"
                    )
                if fhr.get("escalated"):
                    st.write("**Escalated:** Yes — routed to merchant for human review.")

                if recovery_status:
                    rec = fhr["fallback_recovery_result"]
                    st.markdown(
                        f"**Recovery status:** {badge_html(RECOVERY_ICON.get(recovery_status, '•'), RECOVERY_LABEL.get(recovery_status, recovery_status))}",
                        unsafe_allow_html=True,
                    )
                    st.write(f"**Observed recovered amount:** {_money(rec.get('amount_recovered', 0.0))}")
                    st.caption(rec.get("reason", ""))

    st.divider()
    st.markdown('<div id="automation-tools"></div>', unsafe_allow_html=True)
    st.markdown("**Automations**")

    # Style native expander headers so the whole clickable bar IS the card —
    # no separate styled div sitting on top of an empty expander.
    st.markdown(
        """
        <style>
        div[data-testid="stExpander"] {
            border: 1px solid #262b36;
            border-radius: 10px;
            background-color: #15181f;
            margin-bottom: 10px;
            overflow: hidden;
        }
        div[data-testid="stExpander"] summary {
            padding: 12px 16px;
            font-weight: 600;
            font-size: 0.95rem;
            color: #e6e6e6;
        }
        div[data-testid="stExpander"] summary:hover {
            background-color: #1b1f28;
        }
        div[data-testid="stExpander"] summary svg {
            fill: #8b8f99;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("🔄  Retry sequencer", expanded=False):
        st.caption(
            "Automatically retries failed payments up to 3 times. If all retries fail, "
            "it falls back to a Payment Link. This helps recover involuntary churn and failed EMI payments."
        )

        seq_case_id = st.text_input("Case ID", key="sequencer_case_id_input")
        st.caption("A failed_subscription or failed_payment case, e.g. CASE00002")

        seq_days_elapsed = st.number_input(
            "Simulated days elapsed (since sequence start)",
            min_value=0, max_value=30, value=0, step=1,
            help="Real 3/7-day waits aren't feasible in a live demo — this "
                 "simulates time passing, the same way the pipeline's own tests do.",
        )
        st.caption("Retry attempts: Day 0–2 → Attempt 1 | Day 3–6 → Attempt 2 | Day 7+ → Attempt 3")

        if st.button("Advance sequencer", key="advance_sequencer_button"):
            if not seq_case_id.strip():
                st.error("Enter a Case ID.")
            elif razorpay_client is None:
                st.error("No Razorpay client configured.")
            else:
                try:
                    state_key = "sequencer_started_at_" + seq_case_id.strip()
                    if state_key not in st.session_state:
                        st.session_state[state_key] = datetime.now()
                    base_time = st.session_state[state_key]
                    sim_time = base_time + pd.Timedelta(days=int(seq_days_elapsed))
                    with st.spinner("Advancing sequence..."):
                        seq = dd.advance_mandate_sequence(
                            seq_case_id.strip(), diag_engine, guard_engine, razorpay_client, sim_time
                        )
                    st.session_state["last_sequence_result"] = seq
                    st.success("Sequence advanced.")
                except ValueError as e:
                    st.error(str(e))

        display_seq_id = seq_case_id.strip() if seq_case_id.strip() else None
        seq_to_show = st.session_state.get("last_sequence_result")
        if seq_to_show is None and display_seq_id:
            seq_to_show = dd.get_mandate_sequence(display_seq_id)

        seq_status_icon = {
            "pending": "⏳", "attempt_scheduled": "🔁", "exhausted": "⛔",
            "fallback_triggered": "↪️", "fallback_recovered": "✅",
        }
        seq_status_label = {
            "pending": "Pending", "attempt_scheduled": "Active", "exhausted": "Exhausted",
            "fallback_triggered": "Fallback active", "fallback_recovered": "Recovered via fallback",
        }

        if seq_to_show is not None:
            status_value = seq_to_show.status.value if hasattr(seq_to_show.status, "value") else seq_to_show.status
            st.write(f"**{seq_to_show.case_id}** — {seq_to_show.leakage_category}")
            st.write(f"Amount at Risk: {_money(seq_to_show.amount_at_risk)}")
            st.markdown(
                f"**Sequence status:** {badge_html(seq_status_icon.get(status_value, '•'), seq_status_label.get(status_value, status_value))}",
                unsafe_allow_html=True,
            )
            completed = sum(1 for a in seq_to_show.attempts if a.executed_at is not None)
            st.write(f"**Attempt:** {completed} / {seq_to_show.max_attempts}")

            for a in seq_to_show.attempts:
                mark = "🔁 Simulated — completed" if a.executed_at is not None else "○ Scheduled"
                st.write(f"Attempt {a.attempt_number}    {mark}")

            if status_value in ("pending", "attempt_scheduled"):
                st.caption("Next action: recommended retry action  |  Fallback (if exhausted): `recovery_payment_link`")

            if seq_to_show.fallback_execution_record:
                st.write("---")
                fer = seq_to_show.fallback_execution_record
                if fer.get("not_executed_reason"):
                    st.warning(f"⚠️ Fallback not yet authorized: {fer['not_executed_reason']}")
                else:
                    st.write("**All retry attempts exhausted**")
                    st.write("**Fallback:** `recovery_payment_link`")
                    fstatus = fer.get("execution_status")
                    st.markdown(
                        f"**Execution status:** {badge_html(EXEC_ICON.get(fstatus, '•'), EXEC_LABEL.get(fstatus, fstatus))}",
                        unsafe_allow_html=True,
                    )
                    link_id_seq = (fer.get("razorpay_result") or {}).get("razorpay_payment_link_id")
                    link_url_seq = (fer.get("razorpay_result") or {}).get("razorpay_short_url")
                    if link_id_seq:
                        st.write(f"**Payment Link ID:** `{link_id_seq}`")
                    if link_url_seq:
                        st.write(f"**Payment Link URL:** {link_url_seq}")

            if seq_to_show.fallback_recovery_result:
                st.write("---")
                rec = seq_to_show.fallback_recovery_result
                rstatus = rec.get("recovery_status")
                st.markdown(
                    f"**Fallback recovery status:** {badge_html(RECOVERY_ICON.get(rstatus, '•'), RECOVERY_LABEL.get(rstatus, rstatus))}",
                    unsafe_allow_html=True,
                )
                st.write(f"**Observed recovered amount:** {_money(rec.get('amount_recovered', 0.0))}")
                st.caption(rec.get("reason", ""))

    with st.expander("🔀  Payment routing optimizer", expanded=False):
        st.caption(
            "Recommends which payment route to try next, based on the case's real "
            "failure_reason. Only `direct_payment_link` is genuinely real/executable — "
            "every other route is honestly SIMULATED (see routing_optimizer/README.md)."
        )

        route_case_id = st.text_input("Case ID", key="route_case_id_input")
        st.caption("Any failed_payment, failed_subscription, checkout_abandonment, "
                   "or overdue_receivable case with a failure_reason")

        if st.button("Get routing recommendation", key="get_routing_button"):
            if not route_case_id.strip():
                st.error("Enter a Case ID.")
            else:
                try:
                    routing_decision = dd.get_routing_decision(route_case_id.strip())
                    st.session_state["last_routing_decision"] = routing_decision
                except ValueError as e:
                    st.error(str(e))

        routing_decision = st.session_state.get("last_routing_decision")
        if routing_decision is not None:
            st.write(
                f"**{routing_decision.case_id}** — original method: `{routing_decision.original_payment_method}`, "
                f"failure_reason: `{routing_decision.failure_reason or 'none recorded'}`"
            )
            route_badge = badge_html("✅", "Real & executable") if routing_decision.is_real_executable \
                else badge_html("🔁", "Simulated if executed")
            st.markdown(
                f"**Recommended route:** `{routing_decision.recommended_route.value}`  {route_badge}",
                unsafe_allow_html=True,
            )
            st.write(f"**Score:** {routing_decision.recommended_route_score:.2f}")
            st.caption(routing_decision.rationale)

            if routing_decision.alternatives_considered:
                with st.expander("Alternatives considered"):
                    for alt in routing_decision.alternatives_considered:
                        st.write(f"- `{alt['route']}` (score {alt['score']:.2f}): {alt['rationale']}")

            if st.button("Execute this routing decision", key="execute_routing_button"):
                if razorpay_client is None:
                    st.error("No Razorpay client configured.")
                else:
                    with st.spinner("Executing..."):
                        exec_result = dd.execute_routing_decision(
                            routing_decision, diag_engine, guard_engine, razorpay_client, current_time=datetime.now()
                        )
                    st.session_state["last_routing_execution"] = exec_result

            exec_result = st.session_state.get("last_routing_execution")
            if exec_result is not None:
                status = exec_result.get("execution_status")
                st.markdown(
                    f"**Execution status:** {badge_html(EXEC_ICON.get(status, '•'), EXEC_LABEL.get(status, status))}",
                    unsafe_allow_html=True,
                )
                st.write(f"**Reason:** {exec_result.get('reason')}")
                rz2 = exec_result.get("razorpay_result", {})
                if rz2.get("razorpay_payment_link_id"):
                    st.write(f"**Payment Link ID:** `{rz2['razorpay_payment_link_id']}`")
                if rz2.get("razorpay_short_url"):
                    st.write(f"**Payment Link URL:** {rz2['razorpay_short_url']}")
                if exec_result.get("_initial_recovery_check"):
                    rc = exec_result["_initial_recovery_check"]
                    st.info(f"Initial recovery check: {rc.get('recovery_status')} — {rc.get('reason')}")

    with st.expander("🌐  Hinglish recovery script generator", expanded=False):
        st.caption(
            "🔁 SIMULATED — TEXT ONLY. Creates a Hinglish payment-recovery message or voice script. "
            "It does not send messages or make calls."
        )

        hg_case_id = st.text_input("Case ID", key="hinglish_case_id_input")
        hg_channel = st.selectbox("Channel", ["whatsapp", "voice_script"], key="hinglish_channel_input")
        hg_link_url = st.text_input("Payment Link URL (optional)", key="hinglish_link_input")
        st.caption("Paste a real one, e.g. from the sections above, to embed it in the script")

        if st.button("Generate script", key="generate_hinglish_button"):
            if not hg_case_id.strip():
                st.error("Enter a Case ID.")
            else:
                try:
                    script = dd.generate_hinglish_script(
                        hg_case_id.strip(), channel=hg_channel, payment_link_url=hg_link_url.strip() or None,
                    )
                    st.session_state["last_hinglish_script"] = script
                except ValueError as e:
                    st.error(str(e))

        hg_script = st.session_state.get("last_hinglish_script")
        if hg_script is not None:
            st.write(
                f"**{hg_script.case_id}** — {hg_script.leakage_category or 'unknown category'} — "
                f"channel: `{hg_script.channel}`"
            )
            st.warning("🔁 SIMULATED — CONTENT ONLY. Not sent anywhere.")
            st.text_area("Generated script", hg_script.script_text, height=150, disabled=True)
# ---------------------------------------------------------------- E ---
# ---------------------------------------------------------------- E ---
with tabs[4]:
    st.subheader("💰 Recovery — Independently Observed Only")
    st.caption(RAZORPAY_TEST_MODE_BADGE)

    recovery_status_badges = {
        "recovered": "✅ RECOVERED",
        "partially_recovered": "🟡 PARTIALLY RECOVERED",
        "pending": "⏳ PENDING",
        "not_observed": "❔ NOT OBSERVED",
        "observation_failed": "⚠️ OBSERVATION FAILED",
    }

    if not has_results:
        st.info("No live run yet.")
    else:
        for idx, r in enumerate(st.session_state.case_results):
            rec = r.get("recovery_result")

            with st.expander(f"{r['case_id']}"):
                if rec is None:
                    st.write("Not applicable.")
                    continue

                status_key = rec["recovery_status"]
                status_style = {
                    "recovered": ("#1a2e1a", "#3FB950", "✅ RECOVERED"),
                    "partially_recovered": ("#3a2e0a", "#E3B341", "🟡 PARTIALLY RECOVERED"),
                    "pending": ("#0a2a3a", "#58A6FF", "⏳ PENDING"),
                    "not_observed": ("#262B36", "#8B949E", "❔ NOT OBSERVED"),
                    "observation_failed": ("#3a1a1a", "#F85149", "⚠️ OBSERVATION FAILED"),
                }.get(status_key, ("#262B36", "#E6E6E6", status_key))
                rbg, rcolor, rlabel = status_style

                st.markdown(
                    f'**Recovery status:** <span style="background-color:{rbg};color:{rcolor};'
                    f'font-size:0.9rem;padding:4px 12px;border-radius:12px;">{rlabel}</span>',
                    unsafe_allow_html=True,
                )

                amount = rec['amount_recovered']
                amount_color = "#3FB950" if amount and amount > 0 else "#8B949E"
                st.markdown(
                    f'**Observed recovered amount:** <span style="color:{amount_color};'
                    f'font-size:1.05rem;font-weight:600;">{_money(amount)}</span>',
                    unsafe_allow_html=True,
                )

                st.write(f"**Observation source:** `{rec['observation_source']}`")
                st.info(rec['reason'])
                _sequence_for_case = dd.get_mandate_sequence(r["case_id"])
                if _sequence_for_case is not None and _sequence_for_case.fallback_recovery_result:
                    st.write("---")
                    action_name = _sequence_for_case.action_type
                    _seq_action_type = getattr(_sequence_for_case, "action_type", "mandate_retry")
                    st.info(
                        f"ℹ️ This case's original `{_seq_action_type}` attempt above genuinely had "
                        "nothing to observe (no link ever existed). It later went through the "
                        "**Retry Sequencer**, which exhausted retries and created a "
                        "real fallback Payment Link — shown below."
                    )
                    seq_rec = _sequence_for_case.fallback_recovery_result
                    seq_badge = recovery_status_badges.get(
                        seq_rec.get("recovery_status"), seq_rec.get("recovery_status")
                    )
                    st.write(f"**Sequencer fallback status:** {seq_badge}")
                    st.write(f"**Observed recovered amount:** {_money(seq_rec.get('amount_recovered', 0.0))}")
                    st.caption(seq_rec.get("reason", ""))

                if rec.get("payment_link_id"):
                    st.write(
                        f"**Payment Link ID:** "
                        f"`{rec['payment_link_id']}`"
                    )
                    if razorpay_client is not None:
                        if st.button("🔄 Recheck this case", key=f"recheck_{r['case_id']}"):
                            with st.spinner("Checking Razorpay..."):
                                fresh_result = dd.check_payment_link(rec["payment_link_id"], razorpay_client)
                            # Update this case's stored result in place with the
                            # LIVE answer — same observe_recovery() call the pipeline
                            # itself uses, just triggered on demand instead of only
                            # once at batch-run time.
                            st.session_state.case_results[idx]["recovery_result"] = fresh_result
                            st.rerun()

# ---------------------------------------------------------------- F ---
with tabs[5]:
    st.subheader("📈 Measurement — Four Categories, Never Merged")
    if st.button("Build full evaluation report"):
        with st.spinner("Running Category A (model reference) + B (synthetic backtest, held-out set)..."):
            report = dd.build_evaluation_report(
                st.session_state.case_results, st.session_state.sample_df if st.session_state.sample_df is not None else pd.DataFrame(columns=["case_id", "amount_at_risk"]),
                diag_engine, dec_engine, guard_engine, current_time=datetime.now(),
            )
        st.session_state.evaluation_report = report.to_dict()

    if "evaluation_report" not in st.session_state:
        st.info("Click the button to build the evaluation report.")
    else:
        d = st.session_state.evaluation_report
        colA, colB = st.columns(2)
        with colA:
            st.write("#### A. ML / Evaluation Metrics")
            st.caption("Step 3 model performance — referenced, not recomputed.")
            m = d["A_ml_model"]
            st.write(f"Precision: {m['test_precision']:.3f}  |  Recall: {m['test_recall']:.3f}  |  F1: {m['test_f1']:.3f}")
        with colB:
            st.write("#### B. Synthetic / Backtest Metrics")
            st.caption("⚠️ SYNTHETIC — Step 2 ground truth, held-out split. NEVER live revenue.")
            b = d["B_synthetic_backtest"]
            st.write(f"Agent precision (AUTO_EXECUTE): {b['precision_at_auto_execute']}")
            st.write(f"Agent recall (AUTO_EXECUTE): {b['recall_at_auto_execute']}")
            st.write(f"Backtest 'recoverable if ground truth trusted': "
                     f"{_money(b['backtest_amount_recoverable_if_ground_truth_trusted'])}")

        colC, colD = st.columns(2)
        with colC:
            st.write("#### C. Live / Test Mode Execution Metrics")
            c = d["C_live_execution"]
            if c is None:
                st.info("No live run in this session.")
            else:
                st.write(f"Cases analyzed: {c['cases_analyzed']}  |  Actions attempted: {c['actions_attempted']}")
                st.write(f"Successful executions: {c['successful_executions']}  |  Failed: {c['failed_executions']}")
                st.write(f"Amount processed: {_money(c['total_amount_processed'])}")
        with colD:
            st.write("#### D. Observed Recovery Metrics")
            st.caption(RAZORPAY_TEST_MODE_BADGE)
            dd_ = d["D_observed_recovery"]
            if dd_ is None:
                st.info("No live run in this session.")
            else:
                st.write(f"Observed recovered: {_money(dd_['total_amount_recovered'])}")
                st.write(f"Genuine payment verified: {'✅ Yes' if dd_['genuine_payment_verified'] else '❌ No'}")
                st.caption(dd_["limitation_note"])

        st.warning(d["category_separation_notice"])

# ---------------------------------------------------------------- G ---
with tabs[6]:
    st.subheader("📋 Audit Trail")
    if not has_results:
        st.info("No live run yet.")
    else:
        selected = st.selectbox("Case", [r["case_id"] for r in st.session_state.case_results])
        trail = dd.get_audit_timeline(st.session_state.audit_store, selected)
        if not trail:
            st.write("No audit events for this case.")
        else:
            table = pd.DataFrame([{"Sequence": e["sequence"], "Stage": e["stage"].upper(), "Summary": e["summary"]}
                                   for e in trail])
            st.table(table)

# ---------------------------------------------------------------- H ---
with tabs[7]:
    st.subheader("🤝 Promise-to-Pay Tracker")
    st.caption(
        "Tracks a customer's stated commitment to pay by a future date. "
        "'Honored' is NEVER set by inference — only by the same observe_recovery() "
        "call used everywhere else, against a real linked Payment Link."
    )

    if "promise_store" not in st.session_state:
        st.session_state.promise_store = PromiseStore(":memory:")

    with st.expander("➕ Record a new promise"):
        st.caption("*Required fields")

        p_case_id = st.text_input(
            "Case ID *",
            key="promise_case_id_input"
        )

        p_category = st.text_input(
            "Leakage category (optional)",
            key="promise_category_input"
        )

        p_amount = st.number_input(
            "Promised amount (₹) *",
            min_value=0.0,
            step=1.0,
            key="promise_amount_input"
        )

        p_date = st.date_input(
            "Promise date (customer commits to pay by) *",
            key="promise_date_input"
        )

        p_link = st.text_input(
            "Payment Link ID (optional, e.g. plink_...)",
            key="promise_link_input"
        )

        st.caption(
            "You can record a promise without a Payment Link. "
            "A promise can only reach "
            "✅ HONORED once a real, paid Payment Link is attached and verified."
        )

        if st.button("Record promise", key="record_promise_button"):

            if not p_case_id.strip():
                st.error("Case ID is required.")

            elif p_amount <= 0:
                st.error(
                    "Promised amount is required and must be greater than ₹0."
                )

            else:
                new_id = dd.record_new_promise(
                    st.session_state.promise_store,
                    p_case_id.strip(),
                    p_category.strip() or None,
                    float(p_amount),
                    p_date.isoformat(),
                    p_link.strip() or None,
                )

                st.success(
                    f"Promise #{new_id} recorded for {p_case_id.strip()}."
                )

    st.divider()

    counts = dd.get_promise_counts(
        st.session_state.promise_store
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Pending", counts.get("pending", 0))
    c2.metric("Honored", counts.get("honored", 0))
    c3.metric("Broken", counts.get("broken", 0))
    c4.metric("Escalated", counts.get("escalated", 0))

    st.divider()

    st.write("#### All promises")

    all_promises = dd.get_all_promises(
        st.session_state.promise_store
    )

    if not all_promises:
        st.info("No promises recorded yet.")

    else:
        promise_status_badges = {
            "pending": "⏳ PENDING",
            "honored": "✅ HONORED",
            "broken": "🔴 BROKEN",
            "escalated": "🟠 ESCALATED",
        }

        for p in all_promises:
            badge = promise_status_badges.get(
                p["status"],
                p["status"]
            )

            with st.expander(
                f"{p['case_id']} — {badge} — "
                f"{_money(p['promised_amount'])} by {p['promise_date']}"
            ):
                st.write(
                    f"**Promise ID:** {p['promise_id']}"
                )

                st.write(
                    f"**Leakage category:** "
                    f"{p.get('leakage_category') or '—'}"
                )

                st.write(
                    f"**Promised amount:** "
                    f"{_money(p['promised_amount'])}"
                )

                st.write(
                    f"**Promise date:** {p['promise_date']}"
                )

                st.write(
                    f"**Status:** {badge}"
                )

                if p.get("payment_link_id"):
                    st.write(
                        f"**Payment Link Id:** "
                        f"`{p['payment_link_id']}`"
                    )
                else:
                    st.warning(
                        "⚠️ No Payment Link attached yet — This promise can remain PENDING, "
                        "become BROKEN after the promise date if unpaid, or be ESCALATED for merchant follow-up. It cannot"
                        "become HONORED without a real payment to verify."
                    )
                    if p["status"] == "pending":
                        new_link = st.text_input(
                            "Attach a Payment Link ID now", key=f"attach_link_{p['promise_id']}"
                        )
                        if st.button("Click to Attach Here", key=f"attach_link_button_{p['promise_id']}"):
                            if new_link.strip():
                                dd.attach_link_to_promise(
                                    st.session_state.promise_store, p["promise_id"], new_link.strip()
                                )
                                st.rerun()
                            else:
                                st.error("Enter a Payment Link ID first.")
            st.caption(p.get("reason", ""))

            col_a, col_b = st.columns(2)
            with col_a:
                if p["status"] in ("pending",) and razorpay_client is not None:
                    if st.button("🔄 Recheck now", key=f"recheck_promise_{p['promise_id']}"):
                        with st.spinner("Checking..."):
                            dd.check_promise_status(
                                st.session_state.promise_store, p["promise_id"], razorpay_client,
                                current_time=datetime.now(),
                            )
                        st.rerun()
            with col_b:
                if p["status"] == "broken":
                    if st.button("🚩 Escalate", key=f"escalate_promise_{p['promise_id']}"):
                        dd.escalate_promise_status(
                            st.session_state.promise_store, p["promise_id"],
                            "Manually escalated by merchant for follow-up.",
                        )
                        st.rerun()