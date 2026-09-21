"""Spawn both fighters and keep them alive.

Laya hosts (it loads MLX, which wants to be the first thing up), Jev joins.
Both engines advance the same number of tics, so neither gets free frames.
"""

import multiprocessing as mp
import time

from . import fighter

READY_TIMEOUT = 180.0


class Arena:
    def __init__(self, mode="equal", wad="freedoom2.wad", mapname="map01",
                 seed=7, model=None, api_key=None, shield=True, seconds=100):
        self.ctx = mp.get_context("spawn")
        self.mode = mode
        self.cfg = {"wad": wad, "map": mapname, "seed": seed, "model": model,
                    "api_key": api_key, "shield": shield, "seconds": seconds}
        self.q = self.ctx.Queue(maxsize=64)
        self.stop = self.ctx.Event()
        self.procs = {}
        self.fatal = {}

    def start(self):
        # Host first, then give the engine a moment to open its socket.
        self.procs["laya"] = self.ctx.Process(
            target=fighter.run, args=("host", "laya", self.mode, self.q, self.stop, self.cfg),
            daemon=True, name="laya")
        self.procs["laya"].start()
        time.sleep(2.0)
        self.procs["jev"] = self.ctx.Process(
            target=fighter.run, args=("join", "jev", self.mode, self.q, self.stop, self.cfg),
            daemon=True, name="jev")
        self.procs["jev"].start()

    def wait_ready(self, on_progress=None):
        """Block until both fighters report ready, or something dies."""
        ready, t0 = set(), time.perf_counter()
        pending = []
        while len(ready) < 2 and time.perf_counter() - t0 < READY_TIMEOUT:
            try:
                m = self.q.get(timeout=0.5)
            except Exception:
                if any(not p.is_alive() for p in self.procs.values()):
                    break
                if on_progress:
                    on_progress(ready, time.perf_counter() - t0)
                continue
            if m.get("fatal"):
                self.fatal[m["kind"]] = m["fatal"]
                return False, pending
            if m.get("ready"):
                ready.add(m["kind"])
                if on_progress:
                    on_progress(ready, time.perf_counter() - t0)
            else:
                pending.append(m)
        return len(ready) == 2, pending

    def drain(self, budget=0.010):
        """Pull whatever telemetry is waiting without blocking the UI."""
        out, t0 = [], time.perf_counter()
        while time.perf_counter() - t0 < budget:
            try:
                m = self.q.get_nowait()
            except Exception:
                break
            if m.get("fatal"):
                self.fatal[m["kind"]] = m["fatal"]
                continue
            if m.get("ready"):
                continue
            out.append(m)
        return out

    def alive(self):
        return all(p.is_alive() for p in self.procs.values())

    def shutdown(self):
        self.stop.set()
        for p in self.procs.values():
            p.join(timeout=4.0)
            if p.is_alive():
                p.terminate()
        for p in self.procs.values():
            p.join(timeout=2.0)
