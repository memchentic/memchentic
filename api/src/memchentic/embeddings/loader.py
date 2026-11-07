from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import struct
from pathlib import Path

import numpy as np

from memchentic.settings import get_settings

logger = logging.getLogger(__name__)

_embedder: Embedder | None = None


class Embedder:
    """Local embedding. Uses ONNX when model files exist; otherwise a stable hash fallback.

    Fallback keeps hybrid search functional in CI / first boot before model download.
    Production Compose should populate EMBEDDING_MODEL_PATH with all-MiniLM-L6-v2 ONNX.
    """

    def __init__(self, model_path: str, dim: int = 384) -> None:
        self.model_path = Path(model_path)
        self.dim = dim
        self._session = None
        self._tokenizer = None
        self._backend = "hash"
        self._try_load_onnx()

    def _try_load_onnx(self) -> None:
        model_file = self.model_path / "model.onnx"
        if not model_file.exists():
            logger.warning(
                "ONNX model not found at %s; using deterministic hash embeddings",
                model_file,
            )
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tok_path = self.model_path / "tokenizer.json"
            if not tok_path.exists():
                logger.warning("tokenizer.json missing; using hash embeddings")
                return
            self._tokenizer = Tokenizer.from_file(str(tok_path))
            self._session = ort.InferenceSession(
                str(model_file), providers=["CPUExecutionProvider"]
            )
            self._backend = "onnx"
            logger.info("Loaded ONNX embedder from %s", model_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load ONNX embedder: %s; using hash fallback", exc)

    @property
    def backend(self) -> str:
        return self._backend

    def embed(self, text: str) -> list[float]:
        text = (text or "").strip()
        if not text:
            return [0.0] * self.dim
        if self._backend == "onnx" and self._session and self._tokenizer:
            return self._embed_onnx(text)
        return self._embed_hash(text)

    def _embed_hash(self, text: str) -> list[float]:
        """Deterministic pseudo-embedding for offline/dev without the model."""
        vec = [0.0] * self.dim
        tokens = text.lower().split()
        if not tokens:
            tokens = [text.lower()]
        for tok in tokens:
            digest = hashlib.sha256(tok.encode()).digest()
            for i in range(0, min(len(digest) - 4, self.dim * 4), 4):
                idx = struct.unpack_from("<I", digest, i)[0] % self.dim
                vec[idx] += 1.0
        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _embed_onnx(self, text: str) -> list[float]:
        assert self._tokenizer is not None and self._session is not None
        encoded = self._tokenizer.encode(text)
        ids = encoded.ids[:256]
        mask = [1] * len(ids)
        # pad
        while len(ids) < 8:
            ids.append(0)
            mask.append(0)
        input_ids = np.array([ids], dtype=np.int64)
        attention_mask = np.array([mask], dtype=np.int64)
        feeds = {}
        for inp in self._session.get_inputs():
            if "input_ids" in inp.name:
                feeds[inp.name] = input_ids
            elif "attention_mask" in inp.name:
                feeds[inp.name] = attention_mask
            elif "token_type" in inp.name:
                feeds[inp.name] = np.zeros_like(input_ids)
        outputs = self._session.run(None, feeds)
        arr = np.array(outputs[0])
        # mean pool if token embeddings
        if arr.ndim == 3:
            mask_exp = attention_mask[:, :, None]
            summed = (arr * mask_exp).sum(axis=1)
            counts = np.clip(mask_exp.sum(axis=1), 1, None)
            pooled = summed / counts
            vec = pooled[0]
        else:
            vec = arr[0]
        vec = vec.astype(np.float64)
        if vec.shape[0] != self.dim:
            # truncate or pad
            out = np.zeros(self.dim, dtype=np.float64)
            n = min(self.dim, vec.shape[0])
            out[:n] = vec[:n]
            vec = out
        norm = np.linalg.norm(vec) or 1.0
        return (vec / norm).tolist()

    def embed_memory_text(self, title: str, body: str) -> list[float]:
        return self.embed(f"{title}\n{body}")


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        settings = get_settings()
        _embedder = Embedder(settings.embedding_model_path, settings.embedding_dim)
    return _embedder


def download_model_hint() -> str:
    return json.dumps(
        {
            "model": "sentence-transformers/all-MiniLM-L6-v2",
            "path": os.environ.get("EMBEDDING_MODEL_PATH", "/opt/memchentic/models/all-MiniLM-L6-v2"),
            "files": ["model.onnx", "tokenizer.json"],
        }
    )
