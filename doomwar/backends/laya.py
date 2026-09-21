"""Laya: open-weight typed decisions, running locally on Apple silicon via MLX.

Free, offline, no API. Latency scales roughly linearly with question count
because every question row gets its own encoder pass.
"""

import os
import time

from .base import Backend, Verdict

DEFAULT_MODEL = "models/laya-multilingual-mlx"


class LayaBackend(Backend):
    name = "laya"
    label = "Laya · local MLX"
    colour = (64, 224, 168)

    def __init__(self, model=None, dtype="float16", batch_size=8, optimize=True):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        import laya_mlx

        path = model or DEFAULT_MODEL
        self.agent = laya_mlx.Agent(
            path,
            dtype=dtype,
            device="gpu",
            batch_size=batch_size,
            compile=optimize,
            pad_to_multiple=16 if optimize else None,
            cache_prompts=optimize,
        )
        self.model_path = str(path)
        self.detail = "M4 · FP16 · offline"

    def decide(self, state, questions) -> Verdict:
        t0 = time.perf_counter()
        try:
            out = self.agent.predict(state, questions)
        except Exception as e:  # keep the fight alive; the motor falls back
            return Verdict({}, (time.perf_counter() - t0) * 1000, 0.0, 0, 0, 0.0, str(e))
        ms = (time.perf_counter() - t0) * 1000.0
        u = out.get("usage", {})
        return Verdict(
            answers=out["answers"],
            latency_ms=ms,
            model_ms=ms,          # nothing else in the path; it is all local compute
            input_tokens=int(u.get("input_tokens", 0)),
            output_tokens=int(u.get("output_tokens", 0)),
            cost_usd=0.0,
        )
