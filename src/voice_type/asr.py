# ---------------------------------------------------------------------------
# ASR Model
# ---------------------------------------------------------------------------

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

    def transcribe(self, audio, sr):
        if self.model is None:
            raise RuntimeError("Model not loaded")
        results = self.model.transcribe(audio=(audio, sr), language="English")
        if not results:
            return "", ""
        return results[0].text, results[0].language
