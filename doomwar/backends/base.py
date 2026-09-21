"""Backend protocol. Both fighters answer the same schema; only this differs."""

import time
from dataclasses import dataclass


@dataclass
class Verdict:
    """One completed decision."""
    answers: dict
    latency_ms: float       # wall clock, everything included
    model_ms: float         # the model call alone
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None = None

    def choice(self, key, default=None):
        a = self.answers.get(key)
        if not a:
            return default
        return a.get("choice", default)

    def probs(self, key):
        a = self.answers.get(key)
        return a.get("probabilities", {}) if a else {}

    def conf(self, key, default=0.0):
        a = self.answers.get(key)
        return float(a.get("confidence", default)) if a else default

    def noul(self, key, default=0.0):
        a = self.answers.get(key)
        return float(a.get("noul", default)) if a else default


class Backend:
    name = "backend"
    label = "backend"
    colour = (255, 255, 255)

    def decide(self, state: dict, questions: dict) -> Verdict:
        raise NotImplementedError

    def warmup(self, state: dict, questions: dict, n: int = 2):
        for _ in range(n):
            try:
                self.decide(state, questions)
            except Exception:
                pass

    def close(self):
        pass


def timed(fn):
    """Wrap a call so latency is measured the same way for every backend."""
    t0 = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t0) * 1000.0
