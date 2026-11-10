"""Checks on the question before any model or tool runs, and redaction before anything is logged.

Bedrock Guardrails sits in front of the model on AWS (infra/template.yaml). These checks run
first anyway, for three reasons: they work in Nepali and romanised Nepali, which I could not
confirm the guardrail's topic filters handle; they cost nothing; and they run offline, so they
are tested on every commit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import MAX_QUESTION_CHARS

# Forecasts are the request that does the most harm when answered from history. Words that point
# at the future, in English, Nepali and romanised Nepali.
_FORECAST = [
    r"\btomorrow\b",
    r"\btonight\b",
    r"\bnext (?:week|day|few days|month)\b",
    r"\bwill (?:it|there) (?:be )?rain",
    r"\bgoing to rain\b",
    r"\bforecast",
    r"\bpredict",
    r"\bthis weekend\b",
    r"\bupcoming\b",
    r"\bwill .* flood",
    r"भोलि",
    r"पर्सि",
    r"आउँदो",
    r"आगामी",
    r"पूर्वानुमान",
    r"पर्नेछ",
    r"पर्ला",
    r"पर्छ कि",
    r"हुनेछ",
    r"हुन्छ कि",
    r"\bbholi\b",
    r"\bparsi\b",
    r"\baaudo\b",
    r"\baaune\b",
    r"\bparla\b",
    r"\bparchha ki\b",
    r"\bparcha ki\b",
]
# Questions Sodhnus cannot answer from rainfall data, and where a wrong answer hurts someone.
_OUT_OF_SCOPE = {
    "impact": [
        r"\bdeaths?\b",
        r"\bdied\b",
        r"\bkilled\b",
        r"\bcasualt",
        r"\bdamage",
        r"\bmissing people\b",
        r"\bhow many (?:people|houses)\b",
        r"मृत्यु",
        r"मरे",
        r"मारिए",
        r"क्षति",
        r"बेपत्ता",
        r"घाइते",
    ],
    "safety": [
        r"\bis it safe\b",
        r"\bsafe to (?:travel|drive|go|trek)\b",
        r"\bshould i (?:travel|go|drive|evacuate)\b",
        r"सुरक्षित छ",
        r"जान हुन्छ",
        r"\bjana hunchha\b",
        r"\bsurakshit\b",
    ],
}
_INJECTION = [
    r"ignore (?:all |the |any )?(?:previous|prior|above|earlier) (?:instructions|rules|messages)",
    r"\bsystem prompt\b",
    r"\byou are now\b",
    r"\bdeveloper mode\b",
    r"\bjailbreak\b",
    r"\bpretend (?:to be|you are)\b",
    r"\bact as\b",
    r"disregard (?:your|the) (?:rules|instructions)",
    r"पहिलेका निर्देशन",
    r"निर्देशन बिर्स",
]

# Digits as people type them: ASCII or Devanagari (Nepali keyboards produce ९८४१…).
_D = "[0-9०-९]"
_NINE, _SEVEN, _ZERO = "[9९]", "[7७]", "[0०]"
_MOBILE = (
    rf"(?:(?:\+|\b){_NINE}{_SEVEN}{_SEVEN}[-\s]?|\b)"  # optional +977 / 977 prefix
    rf"{_NINE}[678६-८]{_D}"  # 96x / 97x / 98x
    rf"(?:{_D}{{7}}|[-\s]{_D}{{7}}|{_D}{{2}}[-\s]{_D}{{5}})\b"  # 9841234567, 984-1234567, 98412 34567
)
_LANDLINE = rf"\b{_ZERO}{_D}{{1,2}}-{_D}{{6,7}}\b"  # 01-4412345, 061-523456
_INTERNATIONAL = rf"\+{_D}{{1,3}}[-\s]?{_D}{{6,12}}\b"
_PHONE = re.compile(f"{_MOBILE}|{_LANDLINE}|{_INTERNATIONAL}")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None  # forecast | impact | safety | injection | too_long | empty


def check_question(question: str) -> Verdict:
    q = (question or "").strip()
    if not q:
        return Verdict(False, "empty")
    if len(q) > MAX_QUESTION_CHARS:
        return Verdict(False, "too_long")
    low = q.lower()
    if any(re.search(p, low) for p in _INJECTION):
        return Verdict(False, "injection")
    if any(re.search(p, low) for p in _FORECAST):
        return Verdict(False, "forecast")
    for reason, patterns in _OUT_OF_SCOPE.items():
        if any(re.search(p, low) for p in patterns):
            return Verdict(False, reason)
    return Verdict(True)


def redact(text: str) -> str:
    """Phone numbers and emails out of anything stored. People paste contact details when they are
    worried about a relative in a flood area; the logs don't need them."""
    return _EMAIL.sub("[email]", _PHONE.sub("[phone]", text or ""))
