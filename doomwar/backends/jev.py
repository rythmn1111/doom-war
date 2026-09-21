"""Jev: TypeSafe's hosted System One model.

Keeps one pooled connection open for the whole fight, so the measured latency
is steady-state round trip and not repeated TLS setup.
"""

import os
import pathlib
import time

import httpx

from .base import Backend, Verdict

URL = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000  # $0.042 / MTok, output free


def read_key(explicit=None):
    if explicit:
        return explicit.strip()
    env = os.environ.get("TYPESAFE_API_KEY")
    if env:
        return env.strip()
    for c in ("jev.txt", "../jev.txt"):
        p = pathlib.Path(c)
        if p.is_file():
            return p.read_text().strip()
    raise RuntimeError("No Jev API key: set TYPESAFE_API_KEY or provide jev.txt")


class JevBackend(Backend):
    name = "jev"
    label = "Jev · hosted API"
    colour = (255, 122, 60)

    def __init__(self, api_key=None, model="jev-latest", timeout=15.0):
        self.model = model
        self.client = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {read_key(api_key)}",
                "Content-Type": "application/json",
            },
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120),
        )
        self.spent = 0.0
        self.detail = "jev-1.13 · network"

    def decide(self, state, questions) -> Verdict:
        body = {"model": self.model, "state": state, "questions": questions}
        t0 = time.perf_counter()
        try:
            r = self.client.post(URL, json=body)
            r.raise_for_status()
            out = r.json()
        except Exception as e:
            return Verdict({}, (time.perf_counter() - t0) * 1000, 0.0, 0, 0, 0.0, str(e)[:120])
        ms = (time.perf_counter() - t0) * 1000.0
        u = out.get("usage", {})
        tok = int(u.get("input_tokens", 0))
        cost = tok * PRICE_PER_INPUT_TOKEN
        self.spent += cost
        served = r.elapsed.total_seconds() * 1000.0 if r.elapsed else ms
        return Verdict(
            answers=out["answers"],
            latency_ms=ms,
            model_ms=served,
            input_tokens=tok,
            output_tokens=int(u.get("output_tokens", 0)),
            cost_usd=cost,
        )

    def close(self):
        self.client.close()
