from voice_type.config import TARGET_SR


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def resample_audio(audio, orig_sr, target_sr):
    import numpy as np
    from scipy import signal as scipy_signal
    if orig_sr == target_sr:
        return audio
    n = int(len(audio) * target_sr / orig_sr)
    return scipy_signal.resample(audio, n).astype(np.float32)


class VoiceRecorder:
    def __init__(self, device=None):
        import sounddevice as sd
        import queue
        self.device = device
        dev_info = (sd.query_devices(device) if device is not None
                    else sd.query_devices(sd.default.device[0]))
        self.native_sr = int(dev_info["default_samplerate"])
        self.recording = False
        self.audio_queue = queue.Queue()
        self.stream = None

    def start(self):
        import sounddevice as sd
        if self.recording:
            return
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except Exception:
                break
        self.recording = True
        self.stream = sd.InputStream(
            samplerate=self.native_sr, channels=1, dtype="float32",
            device=self.device, callback=self._callback, blocksize=1024,
        )
        self.stream.start()

    def _callback(self, indata, frames, time_info, status):
        if self.recording:
            self.audio_queue.put(indata.copy())

    def stop(self):
        import numpy as np
        if not self.recording:
            return np.array([], dtype=np.float32)
        self.recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        chunks = []
        while not self.audio_queue.empty():
            try:
                chunks.append(self.audio_queue.get_nowait())
            except Exception:
                break
        if chunks:
            audio = np.concatenate(chunks).flatten()
            return resample_audio(audio, self.native_sr, TARGET_SR)
        return np.array([], dtype=np.float32)
