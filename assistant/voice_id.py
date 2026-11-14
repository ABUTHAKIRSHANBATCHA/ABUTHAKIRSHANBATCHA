"""Voice lock: recognise who is speaking from a short recording, on this PC.

Uses a speaker-embedding model run locally by sherpa-onnx (no audio leaves the
computer for this). Enrolled voices are stored as number lists, not audio.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx")
MODEL_NAME = "speaker-model.onnx"
SAMPLE_RATE = 16000
MIN_SECONDS = 1.5


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1e-9)


class VoiceID:
    def __init__(self, folder: Path, threshold: float = 0.6) -> None:
        self.folder = folder
        self.file = folder / "voices.json"
        self.model_path = folder / MODEL_NAME
        self.threshold = threshold
        self._extractor = None
        self.voices: dict[str, list[list[float]]] = {}
        if self.file.exists():
            self.voices = json.loads(self.file.read_text(encoding="utf-8"))

    @property
    def available(self) -> bool:
        """True when the model file and the sherpa-onnx package are both present."""
        if not self.model_path.exists():
            return False
        try:
            import sherpa_onnx  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def locked(self) -> bool:
        return self.available and bool(self.voices)

    def embed(self, pcm16: bytes) -> list[float]:
        """Embedding for 16 kHz mono 16-bit audio."""
        import numpy as np
        import sherpa_onnx

        samples = np.frombuffer(pcm16[:len(pcm16) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) < SAMPLE_RATE * MIN_SECONDS:
            raise ValueError("The recording is too short. Speak for at least two seconds.")
        if float(np.sqrt(np.mean(samples ** 2))) < 0.005:
            raise ValueError("The recording is silent. Check the microphone and speak clearly.")
        if self._extractor is None:
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self.model_path), num_threads=2))
        stream = self._extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        stream.input_finished()
        return [float(x) for x in self._extractor.compute(stream)]

    def enroll(self, name: str, pcm16: bytes) -> int:
        """Add one sample for `name`; returns how many samples that voice now has."""
        embedding = self.embed(pcm16)        # raises before anything is stored if the audio is unusable
        self.voices.setdefault(name, []).append(embedding)
        self._save()
        return len(self.voices[name])

    def identify(self, pcm16: bytes) -> tuple[str | None, float]:
        """Best matching enrolled voice, or None when nobody passes the threshold."""
        probe = self.embed(pcm16)
        best_name, best = None, -1.0
        for name, samples in self.voices.items():
            if not samples:
                continue
            score = max(_cosine(probe, s) for s in samples)
            if score > best:
                best_name, best = name, score
        return (best_name if best >= self.threshold else None), round(best, 3)

    def remove(self, name: str) -> bool:
        removed = self.voices.pop(name, None) is not None
        self._save()
        return removed

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(self.voices), encoding="utf-8")
