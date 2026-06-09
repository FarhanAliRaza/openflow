import re

# ---------------------------------------------------------------------------
# Filler-word cleanup
#
# Fast, local, deterministic removal of speech fillers ("um", "uh", ...) from
# raw ASR output. Runs on every utterance, independent of the claude refine
# layer. Deliberately conservative: only tokens that are essentially never
# meaningful words, so we never delete real content.
# ---------------------------------------------------------------------------

# um/umm…, uh/uhh…, uhm, er/erm, hmm…, mm… — matched as whole words, optionally
# wrapped in a comma ("fix the, um, thing" -> "fix the thing").
_FILLER_RE = re.compile(
    r"\s*,?\s*\b(?:um+|uh+|uhm|erm?|hm+|mm+)\b\s*,?",
    re.IGNORECASE,
)
_LEADING_FILLER_RE = re.compile(
    r"^\s*\b(?:um+|uh+|uhm|erm?|hm+|mm+)\b",
    re.IGNORECASE,
)


def strip_fillers(text):
    """Remove filler words and tidy up the punctuation/spacing left behind."""
    if not text or not text.strip():
        return text

    leading_filler = bool(_LEADING_FILLER_RE.match(text))

    out = _FILLER_RE.sub(" ", text)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)   # no space before punctuation
    out = re.sub(r"\s{2,}", " ", out)            # collapse runs of spaces
    out = re.sub(r"^[\s,.;:]+", "", out).strip()  # drop stray leading punctuation

    # Re-capitalise if we removed the leading filler that owned the capital.
    if out and leading_filler:
        out = out[0].upper() + out[1:]
    return out
