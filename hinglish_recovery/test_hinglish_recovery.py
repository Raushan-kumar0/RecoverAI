"""
RecoverAI — Hinglish Recovery: Tests

Run:
    python3 -m pytest test_hinglish_recovery.py -v
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from script_generator import generate_script, HinglishScript
from script_catalog import TEMPLATES, DEFAULT_TEMPLATE


def _case(case_id="CASE001", leakage_category="checkout_abandonment", amount_at_risk=500.0):
    return {"case_id": case_id, "leakage_category": leakage_category, "amount_at_risk": amount_at_risk}


# ---- is_simulated is ALWAYS True ----

def test_is_simulated_always_true():
    for category in list(TEMPLATES.keys()) + ["unknown_category", None]:
        script = generate_script(_case(leakage_category=category))
        assert script.is_simulated is True


# ---- Determinism ----

def test_same_input_produces_same_script():
    case = _case()
    s1 = generate_script(case)
    s2 = generate_script(case)
    assert s1.script_text == s2.script_text


# ---- Real amount is embedded, never fabricated ----

def test_amount_appears_in_generated_text():
    script = generate_script(_case(amount_at_risk=1234.56))
    assert "1,234.56" in script.script_text


def test_different_amounts_produce_different_text():
    s1 = generate_script(_case(amount_at_risk=100.0))
    s2 = generate_script(_case(amount_at_risk=999.0))
    assert s1.script_text != s2.script_text


# ---- Every real leakage_category has both channels ----

@pytest.mark.parametrize("category", ["checkout_abandonment", "failed_payment", "failed_subscription", "overdue_receivable"])
def test_every_real_category_has_whatsapp_and_voice_templates(category):
    whatsapp = generate_script(_case(leakage_category=category), channel="whatsapp")
    voice = generate_script(_case(leakage_category=category), channel="voice_script")
    assert whatsapp.script_text != ""
    assert voice.script_text != ""
    assert whatsapp.script_text != voice.script_text  # channels must actually differ


# ---- Unknown category falls back gracefully ----

def test_unknown_category_uses_default_template():
    script = generate_script(_case(leakage_category="some_new_category_not_in_catalog"))
    assert script.script_text != ""
    assert "pending" in script.script_text.lower()


def test_none_category_uses_default_template():
    script = generate_script(_case(leakage_category=None))
    assert script.script_text != ""


# ---- Payment link substitution ----

def test_real_payment_link_gets_embedded():
    script = generate_script(_case(), payment_link_url="https://rzp.io/rzp/ABC123")
    assert "https://rzp.io/rzp/ABC123" in script.script_text


def test_missing_payment_link_shows_readable_placeholder():
    script = generate_script(_case(), payment_link_url=None)
    assert "[payment link would go here]" in script.script_text
    assert script.script_text  # still readable/non-empty without a real link


# ---- Invalid channel falls back rather than crashing ----

def test_invalid_channel_falls_back_to_whatsapp_shape():
    script = generate_script(_case(), channel="carrier_pigeon")
    assert script.channel == "whatsapp"
    assert script.script_text != ""


# ---- to_dict / JSON safety ----

def test_to_dict_is_json_safe():
    import json
    script = generate_script(_case())
    json.dumps(script.to_dict())  # must not raise


def test_to_dict_includes_is_simulated_flag():
    script = generate_script(_case())
    d = script.to_dict()
    assert d["is_simulated"] is True


# ---- Accepts real dataset row shape ----

def test_accepts_dict_like_case_row():
    class FakeRow(dict):
        pass
    row = FakeRow(case_id="CASE_ROW", leakage_category="overdue_receivable", amount_at_risk=750.0)
    script = generate_script(row)
    assert script.case_id == "CASE_ROW"
    assert "750.00" in script.script_text


# ---- No ground-truth leakage / no fabricated customer PII ----

def test_hinglish_module_never_reads_ground_truth_columns():
    for fname in ("script_generator.py", "script_catalog.py"):
        src = open(os.path.join(os.path.dirname(__file__), fname), encoding="utf-8").read()
        for forbidden in ("ground_truth_recoverable", "ground_truth_recovery_outcome",
                           "customer_id", "customer_name"):
            assert forbidden not in src, f"{forbidden} referenced in {fname}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
