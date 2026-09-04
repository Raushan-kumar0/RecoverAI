"""
RecoverAI — Hinglish Recovery: Script Generator (Extension, post-Step 12)

Generates deterministic Hinglish (Hindi-English mixed) outreach text for a
case, filled in from REAL case data (amount, category) — never fabricated
customer details.

===================================================================
HONESTY STATEMENT (read this before assuming more than is actually here):
===================================================================
This module answers the "Hinglish voice recovery" example direction as a
SCRIPT/CONTENT generator ONLY:

  - It produces the TEXT a voice call, SMS, or WhatsApp message would
    contain. It does NOT make any real phone call, does NOT synthesize
    real audio (text-to-speech), and does NOT send any real message to
    any real customer.
  - There is no telephony integration (e.g. Twilio) or speech API call
    anywhere in this project. Building one was deliberately out of scope —
    real voice calling requires paid infrastructure this project doesn't
    have, and faking that integration would violate this project's core
    rule against claiming capabilities that don't genuinely exist.
  - Every generated script is labeled SIMULATED — CONTENT ONLY at the
    point of generation, and this label is never dropped, even in the
    dashboard.
  - Templates are hand-written and deterministic (same category + amount
    => same script every time) — not an LLM/AI generation call — matching
    this project's general preference for explainable, reproducible
    behavior over opaque generation.

If a channel were ever wired up for real (WhatsApp Business API, Twilio
Voice, etc.), this generator's OUTPUT could be the payload sent through
it — but that wiring does not exist in this project, and this module makes
no claim that it does.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional

from script_catalog import TEMPLATES, DEFAULT_TEMPLATE


@dataclass
class HinglishScript:
    case_id: str
    leakage_category: Optional[str]
    channel: str  # "whatsapp" or "voice_script"
    amount: float
    script_text: str
    is_simulated: bool = True  # ALWAYS True — see module honesty statement above

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def generate_script(case, channel: str = "whatsapp", payment_link_url: Optional[str] = None) -> HinglishScript:
    """
    case: dict-like with at least case_id, leakage_category, amount_at_risk.
    channel: "whatsapp" or "voice_script". Falls back to "whatsapp" template
             shape for any other value rather than raising, since this is
             display/content generation, not a safety-relevant decision.
    payment_link_url: if provided, filled into the template; otherwise a
             placeholder is shown so the generated text is still readable
             on its own.
    """
    case_id = case.get("case_id") if hasattr(case, "get") else getattr(case, "case_id", None)
    leakage_category = case.get("leakage_category") if hasattr(case, "get") else getattr(case, "leakage_category", None)
    amount = case.get("amount_at_risk") if hasattr(case, "get") else getattr(case, "amount_at_risk", None)
    amount = float(amount) if amount is not None else 0.0

    channel_key = channel if channel in ("whatsapp", "voice_script") else "whatsapp"
    category_templates = TEMPLATES.get(leakage_category, DEFAULT_TEMPLATE)
    template = category_templates.get(channel_key, category_templates.get("whatsapp", DEFAULT_TEMPLATE["whatsapp"]))

    link_display = payment_link_url or "[payment link would go here]"
    script_text = template.format(amount=f"{amount:,.2f}", link=link_display)

    return HinglishScript(
        case_id=case_id, leakage_category=leakage_category, channel=channel_key,
        amount=amount, script_text=script_text, is_simulated=True,
    )
