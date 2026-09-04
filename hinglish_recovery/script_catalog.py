"""
RecoverAI — Hinglish Recovery: Script Catalog (Extension, post-Step 12)

Deterministic, hand-written Hinglish (Hindi-English mixed, Latin script)
message templates for customer outreach, keyed by leakage_category.

IMPORTANT: these are TEMPLATES — plain text with placeholders — not an AI
text-generation call. Same wording every time for the same category/amount,
by design (matches this project's preference for deterministic,
explainable behavior over opaque generation). See script_generator.py's
module docstring for the full honesty statement about what this module is
and is not.
"""

from typing import Dict

# {amount} and {link} are the only placeholders — filled in by
# script_generator.py from real case data, never fabricated.
TEMPLATES: Dict[str, Dict[str, str]] = {
    "checkout_abandonment": {
        "whatsapp": (
            "Namaste! 🙏 Aapने checkout start kiya tha lekin payment complete "
            "nahi hua. Koi baat nahi — yahan click karke aap abhi bhi apna order "
            "complete kar sakte hain: {link}\n\nAmount: ₹{amount}. Yeh link "
            "safe hai aur Razorpay ke through secure payment hoga."
        ),
        "voice_script": (
            "Namaste. Main RecoverAI se bol raha hoon. Aapने ek order start "
            "kiya tha jiska payment complete nahi ho paaya, amount tha "
            "{amount} rupaye ka. Agar aap chahein toh main aapko ek payment "
            "link bhej sakta hoon jisse aap apna order turant complete kar "
            "sakte hain. Kya aap interested hain?"
        ),
    },
    "failed_payment": {
        "whatsapp": (
            "Hi! Aapka payment of ₹{amount} process nahi ho paya — aisa "
            "kabhi-kabhi network ya bank ki taraf se ho jaata hai. Please is "
            "link ko use karke dobara try karein: {link}\n\nKoi problem ho toh "
            "hume batayein, hum madad karenge."
        ),
        "voice_script": (
            "Namaste. Aapka ek payment fail ho gaya tha, amount ₹{amount}. "
            "Yeh kisi technical issue ki wajah se ho sakta hai, aapki taraf "
            "se koi galti nahi hai. Main aapko ek naya payment link bhej "
            "sakta hoon taaki aap dobara try kar sakein."
        ),
    },
    "failed_subscription": {
        "whatsapp": (
            "Hi! Aapki subscription ka payment of ₹{amount} complete nahi ho "
            "paya, is wajah se aapki service pause ho sakti hai. Please is "
            "link se payment update karein: {link}\n\nHum nahi chahte ki aap "
            "service miss karein!"
        ),
        "voice_script": (
            "Namaste. Aapki subscription ka renewal payment fail ho gaya, "
            "amount tha ₹{amount}. Hum nahi chahte ki aapki service band ho "
            "jaaye. Kya main aapko ek payment link bhej sakta hoon taaki aap "
            "apni subscription continue rakh sakein?"
        ),
    },
    "overdue_receivable": {
        "whatsapp": (
            "Namaste! Aapka payment of ₹{amount} due date se overdue hai. "
            "Please jaldi se jaldi is link se payment complete karein: "
            "{link}\n\nKoi query ho toh humein reply karein, hum help karne "
            "ke liye ready hain."
        ),
        "voice_script": (
            "Namaste. Yeh ek reminder hai ki aapka payment, amount "
            "₹{amount}, due date se overdue ho chuka hai. Main aapko ek "
            "payment link bhej sakta hoon taaki aap ise turant clear kar "
            "sakein. Kya aap abhi payment karna chahenge?"
        ),
    },
}

DEFAULT_TEMPLATE = {
    "whatsapp": (
        "Namaste! Aapke account mein ₹{amount} ka payment pending hai. "
        "Please is link se complete karein: {link}"
    ),
    "voice_script": (
        "Namaste. Aapka ek payment pending hai, amount ₹{amount}. Main "
        "aapko ek payment link bhej sakta hoon."
    ),
}
