# RecoverAI — Hinglish Recovery Script Generator (Extension, post-Step 12)

## 1. Scope

Generates deterministic Hinglish (Hindi-English mixed, Latin script)
outreach text for a case — for WhatsApp/SMS or as a script a voice channel
would read out — filled in from real case data.

## 2. Files
```
hinglish_recovery/
  script_catalog.py         hand-written templates, keyed by leakage_category + channel
  script_generator.py        generate_script() — pure, deterministic
  test_hinglish_recovery.py  17 tests
  README.md                   this file
```

## 3. Read this before assuming more than is actually here

This module answers the "Hinglish voice recovery" example direction as a
**content generator only** — not a working voice/messaging system:

- It produces TEXT. It does not make a real phone call, does not
  synthesize real audio, and does not send any real message.
- **No telephony integration exists in this project** (no Twilio, no
  speech API, nothing). This was a deliberate scope decision — real voice
  calling needs paid infrastructure this project doesn't have, and
  building a fake integration would violate this project's core rule
  against claiming a capability that doesn't genuinely exist.
- Every generated script carries `is_simulated=True` and is labeled
  **SIMULATED — CONTENT ONLY** — this label is never dropped anywhere,
  including in the dashboard.
- Templates are hand-written and deterministic — the same category and
  amount always produce the same text. This is not an LLM call.

## 4. Example output

```
Category: checkout_abandonment, Channel: whatsapp, Amount: ₹500.00

"Namaste! 🙏 Aapने checkout start kiya tha lekin payment complete nahi
hua. Koi baat nahi — yahan click karke aap abhi bhi apna order complete
kar sakte hain: [payment link would go here]

Amount: ₹500.00. Yeh link safe hai aur Razorpay ke through secure payment
hoga."
```

## 5. What this does NOT do

- Does not call, message, or contact any real customer.
- Does not modify anything in Steps 1–12 or any other extension module.
- Does not read `customer_id`, `customer_name`, or any ground-truth field
  — only `case_id`, `leakage_category`, and `amount_at_risk`.
- Does not claim any AI-generation sophistication it doesn't have — these
  are fixed templates, not a language model call.
