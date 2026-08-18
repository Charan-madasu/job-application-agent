"""Optional semantic matching stage.

Lexical overlap misses "Werkstudent Datenanalyse" matching a profile that says
"data analysis student assistant". Embeddings catch that.

This runs locally via sentence-transformers if it is installed — no API cost, no
data leaving the machine. If it is not installed, ranking falls back to lexical
overlap and says so out loud, because the difference matters when you describe
this system to somebody else.
"""
from __future__ import annotations

import math

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_MODEL_CACHE: dict[str, object] = {}


class Embedder:
    """Thin wrapper. `available` is the only thing callers need to branch on."""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self.model = None
        self.error = ""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self.error = ("sentence-transformers not installed — "
                          "pip install sentence-transformers")
            return
        try:
            if model_name in _MODEL_CACHE:
                self.model = _MODEL_CACHE[model_name]
            else:
                self.model = SentenceTransformer(model_name)
                _MODEL_CACHE[model_name] = self.model
        except Exception as e:  # noqa: BLE001 - model download can fail offline
            self.error = f"could not load {model_name}: {e}"

    @property
    def available(self) -> bool:
        return self.model is not None

    def encode(self, texts: list[str]):
        return self.model.encode(texts, normalize_embeddings=True,
                                 show_progress_bar=False)

    def similarities(self, query: str, docs: list[str]) -> list[float]:
        """Cosine similarity of one query against many docs, rescaled to 0–1."""
        if not self.available or not docs:
            return [0.0] * len(docs)
        vecs = self.encode([query] + docs)
        q, rest = vecs[0], vecs[1:]
        sims = [float(sum(a * b for a, b in zip(q, d))) for d in rest]
        # Cosine on normalized vectors is [-1, 1]; map to [0, 1].
        return [max(0.0, min(1.0, (s + 1.0) / 2.0)) for s in sims]


def cosine(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0
