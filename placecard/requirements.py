"""Organiser requirements: split free-text notes into sentences and (very roughly) map one onto apply_constraint
arguments. The MOCK agent uses this as its whole "language understanding"; the server's fallback (ToolBox.auto_plan)
uses it to apply requirements itself when the LLM is unavailable. A real model does this translation on its own.
"""
import re

_SENTENCE_END = re.compile(r"(?<=[.!?;])(?<!\bDr\.)(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bProf\.)(?<!\bSt\.)"
                           r"(?<!\bJr\.)(?<!\bSr\.)\s+")


def split_requirements(text):
    """One requirement per non-empty line, and per sentence within a line ("Dr. Amara" is not a sentence end)."""
    out = []
    for line in str(text or "").splitlines():
        for part in _SENTENCE_END.split(line.strip()):
            if part.strip(" ."):
                out.append(part.strip())
    return out


def _names(text):
    text = re.sub(r"\(.*?\)", " ", text)
    parts = re.split(r"\s*(?:,|&|\band\b)\s*", text.strip(" ."))
    return [p.strip() for p in parts if p.strip()]


def parse_constraint(line):
    """One organiser sentence -> apply_constraint arguments, or None. A few regular expressions only."""
    raw = line.strip()
    text = re.sub(r"\(.*?\)", " ", raw).strip(" .;")
    low = text.lower()
    m = re.match(r"(?:put |seat |place )?(.+?)\s+(?:at|on)\s+the\s+head\s+table", text, re.I)
    if m:
        return {"text": raw, "kind": "head", "guests": _names(m.group(1))}
    m = re.match(r"(?:put |seat |place )?(.+?)\s+(?:at|on)\s+table\s+(\d+)", text, re.I)
    if m:
        return {"text": raw, "kind": "fixed", "guests": _names(m.group(1)), "table": int(m.group(2))}
    if re.search(r"\bspread\b|\bdistribute\b|\bacross (?:all |the )?tables\b|\bmix\b.*\btables\b", low):
        who = re.sub(r"^(?:please\s+)?(?:spread|distribute|split|mix)(?:\s+out)?\s+(?:the\s+)?", "", text, flags=re.I)
        who = re.split(r"\s+(?:should|must|needs?|to be|be|across|over|between|among)\b", who, flags=re.I)[0]
        who = re.sub(r"^the\s+", "", who.strip(), flags=re.I)
        if not who:
            return None
        if re.search(r"\band\b|,|&", who):
            return {"text": raw, "kind": "spread", "guests": _names(who)}
        return {"text": raw, "kind": "spread", "group": who}
    if re.search(r"\bapart\b|\bseparate", low):
        body = re.sub(r"^(?:keep|seat|put|separate)\s+", "", text, flags=re.I)
        body = re.split(r"\s+(?:apart|separate)", body, flags=re.I)[0]
        return {"text": raw, "kind": "apart", "guests": _names(re.sub(r"^the\s+", "", body, flags=re.I))}
    m = re.match(r"(?:keep |seat |put )?(?:the )?(.+?)\s+together", text, re.I)
    if m:
        who = m.group(1)
        if re.search(r"\band\b|,|&", who):
            return {"text": raw, "kind": "together", "guests": _names(who)}
        return {"text": raw, "kind": "together", "group": who.strip()}
    return None
