import re

# ---------------------------------------------------------------------------
# ASR Model
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")


def looks_like_context_echo(text, context):
    """True when an ASR output is just the biasing context regurgitated.

    Context-biased models (Qwen3-ASR here) hallucinate their context prompt
    back verbatim when the audio is silent or unintelligible. Our context is a
    comma-separated vocabulary of jargon with almost none of the function words
    ("the", "to", "is") that pervade real speech, so an output that reproduces
    the context's term sequence — or is saturated with its terms — is a
    hallucination, not a transcription. Kept strict to avoid discarding genuine
    speech that merely mentions a few project terms.
    """
    ctx_tokens = _WORD.findall(context.lower())
    out_tokens = _WORD.findall(text.lower())
    if not ctx_tokens or not out_tokens:
        return False

    # Contiguous echo: the output reproduces a run of the context's terms.
    if " ".join(out_tokens) in " ".join(ctx_tokens):
        return True

    # Saturated echo: a long output that is almost entirely context terms, with
    # the ordinary connective words of real speech absent.
    ctx_set = set(ctx_tokens)
    in_ctx = sum(1 for t in out_tokens if t in ctx_set)
    return len(out_tokens) >= 8 and in_ctx / len(out_tokens) >= 0.85


class ASRModel:
    def __init__(self, model_path):
        self.model_path = model_path
        self.model = None

    def load(self):
        import torch
        from qwen_asr import Qwen3ASRModel
        print(f"Loading Qwen3-ASR from {self.model_path}...")
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.model = Qwen3ASRModel.from_pretrained(
            self.model_path, dtype=torch.bfloat16,
            device_map=device, max_new_tokens=4096,
        )
        print(f"Model loaded on {device}.")

    def transcribe(self, audio, sr, context=""):
        if self.model is None:
            raise RuntimeError("Model not loaded")
        results = self.model.transcribe(
            audio=(audio, sr), context=context, language="English")
        if not results:
            return "", ""
        return results[0].text, results[0].language
