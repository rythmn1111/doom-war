"""One fighter: a ViZDoom client, a model backend, and the loop between them.

Runs in its own process because ViZDoom multiplayer needs one engine instance
per player. Pushes frames and telemetry to the dashboard over a queue.

Two timing modes, both of which keep the two engines in lockstep on game tics:

  equal     decide synchronously, then advance TICS_PER_DECISION tics.
            Both fighters get the same number of decisions. Measures judgment.

  realtime  a worker thread decides continuously while the main loop advances
            the game in small steps using the freshest plan available. Both
            advance identical tics, but a faster model refreshes its plan more
            often and a slower one acts on stale information. Measures speed.
"""

import os
import queue
import threading
import time

import numpy as np
import vizdoom as vzd

from . import observe, schema
from .motor import BUTTONS, Motor

TICS_PER_DECISION = 12    # equal mode: game tics executed per decision
STALE_SUDDEN = 10.0   # seconds without a frag before goals narrow
STALE_FINAL = 18.0    # seconds without a frag before the objective is fixed
REALTIME_STEP = 2         # realtime mode: tics advanced per engine step
DOOM_TICRATE = 35.0       # realtime mode paces the engine to this

GAME_VARS = [
    vzd.GameVariable.HEALTH, vzd.GameVariable.ARMOR,
    vzd.GameVariable.SELECTED_WEAPON, vzd.GameVariable.SELECTED_WEAPON_AMMO,
    vzd.GameVariable.FRAGCOUNT, vzd.GameVariable.KILLCOUNT, vzd.GameVariable.DEATHCOUNT,
    vzd.GameVariable.POSITION_X, vzd.GameVariable.POSITION_Y, vzd.GameVariable.ANGLE,
]


def build_game(role, wad, mapname, seed, visible=False):
    pkg = os.path.dirname(vzd.__file__)
    g = vzd.DoomGame()
    # a local file (our generated arena) wins over a bundled scenario name
    path = wad if os.path.exists(wad) else os.path.join(pkg, wad)
    g.set_doom_game_path(os.path.join(pkg, "freedoom2.wad"))
    g.set_doom_scenario_path(path)
    g.set_doom_map(mapname)
    g.set_window_visible(visible)
    g.set_mode(vzd.Mode.PLAYER)
    g.set_screen_resolution(vzd.ScreenResolution.RES_320X240)
    g.set_screen_format(vzd.ScreenFormat.RGB24)
    g.set_objects_info_enabled(True)
    g.set_labels_buffer_enabled(True)
    g.set_depth_buffer_enabled(True)   # spatial senses for the observation
    g.set_render_hud(True)
    g.set_render_weapon(True)
    g.set_available_game_variables(GAME_VARS)
    g.set_available_buttons(BUTTONS)
    g.set_episode_timeout(0)
    if role == "host":
        g.add_game_args(
            "-host 2 -deathmatch +timelimit 0 +sv_forcerespawn 1 "
            "+sv_nocrouch 1 "
            "+viz_respawn_delay 2 +viz_nocheat 0"
        )
        g.add_game_args("+name LAYA +colorset 3")
    else:
        g.add_game_args("-join 127.0.0.1")
        g.add_game_args("+name JEV +colorset 6")
    g.set_seed(seed)
    return g


def make_backend(kind, model, api_key):
    if kind == "laya":
        from .backends.laya import LayaBackend
        return LayaBackend(model=model)
    from .backends.jev import JevBackend
    return JevBackend(api_key=api_key)


class Stats:
    def __init__(self):
        self.lat, self.model_lat = [], []
        self.decisions = self.tokens = self.errors = 0
        self.cost = 0.0

    def add(self, v):
        self.decisions += 1
        self.tokens += v.input_tokens
        self.cost += v.cost_usd
        if v.error:
            self.errors += 1
        else:
            self.lat.append(v.latency_ms)
            self.model_lat.append(v.model_ms)

    def p50(self):
        return float(np.median(self.lat)) if self.lat else 0.0

    def p95(self):
        return float(np.percentile(self.lat, 95)) if self.lat else 0.0


def run(role, kind, mode, out_q, stop_evt, cfg):
    """Process entry point."""
    rival = "JEV" if kind == "laya" else "LAYA"
    game = build_game(role, cfg["wad"], cfg["map"], cfg["seed"])
    game.init()

    backend = make_backend(kind, cfg.get("model"), cfg.get("api_key"))
    motor = Motor(seed=cfg["seed"], shield=cfg.get("shield", True))
    stats = Stats()

    prev_health, tic = 100.0, 0
    round_len = float(cfg.get("seconds", 100))
    sudden = final = False
    last_score = 0.0
    prev_score = 0.0
    obs = observe.read(game, 0, rival, prev_health)
    if obs is None:
        out_q.put({"kind": kind, "fatal": "no initial state"})
        return

    # Warm the path so the first measured decision is steady state.
    backend.warmup(obs.state(), schema.build(obs), n=2)
    out_q.put({"kind": kind, "ready": True})

    plan = motor.plan(backend.decide(obs.state(), schema.build(obs)), obs)
    latest = {"verdict": None, "plan": plan, "obs": obs}
    lock = threading.Lock()

    def think_forever():
        while not stop_evt.is_set():
            with lock:
                o = latest["obs"]
            if o is None:
                time.sleep(0.005)
                continue
            v = backend.decide(o.state(), schema.build(o))
            with lock:
                cur = latest["obs"]
                latest["verdict"] = v
                latest["plan"] = motor.plan(v, cur if cur is not None else o)
            stats.add(v)

    worker = None
    if mode == "realtime":
        worker = threading.Thread(target=think_forever, daemon=True)
        worker.start()

    t_start = time.perf_counter()
    last_push = 0.0
    step = REALTIME_STEP if mode == "realtime" else TICS_PER_DECISION

    try:
        while not stop_evt.is_set() and not game.is_episode_finished():
            if game.is_player_dead():
                game.respawn_player()
                motor._recent.clear()

            obs = observe.read(game, tic, rival, prev_health,
                               sudden_death=sudden, final=final)
            if obs is None:
                game.make_action([0] * 6 + [0.0], step)
                tic += step
                continue

            # Any frag by either side resets the clock. A fight that goes
            # nowhere escalates until somebody has to commit. Both fighters
            # read the same scoreline, so both flip at the same moment.
            now_s = time.perf_counter()
            score = obs.frags
            if score != prev_score:
                prev_score, last_score = score, now_s
            since = now_s - (last_score or t_start)
            sudden, final = since > STALE_SUDDEN, since > STALE_FINAL
            prev_health = obs.health
            motor.note_position(obs)

            if mode == "realtime":
                with lock:
                    latest["obs"] = obs
                    plan = latest["plan"]
                    verdict = latest["verdict"]
            else:
                verdict = backend.decide(obs.state(), schema.build(obs))
                stats.add(verdict)
                plan = motor.plan(verdict, obs)

            game.make_action(motor.act(plan, obs, tic), step)
            tic += step

            # Realtime mode holds the engine to the real Doom clock, so a slow
            # model loses freshness rather than slowing the world down.
            if mode == "realtime":
                slack = (t_start + tic / DOOM_TICRATE) - time.perf_counter()
                if slack > 0:
                    time.sleep(slack)

            now = time.perf_counter()
            if now - last_push > 1 / 30 and verdict is not None and not out_q.full():
                st = game.get_state()
                frame = st.screen_buffer if st is not None else None
                out_q.put({
                    "kind": kind, "tic": tic, "t": now - t_start,
                    "frame": None if frame is None else np.ascontiguousarray(frame),
                    "health": obs.health, "armor": obs.armor, "ammo": obs.ammo,
                    "weapon": obs.weapon, "frags": obs.frags, "kills": obs.killcount,
                    "deaths": obs.deaths, "x": obs.x, "y": obs.y, "angle": obs.angle,
                    "answers": verdict.answers, "goal": plan["goal"],
                    "move": plan["move"], "fire": plan["fire"],
                    "shield_reason": plan["shield_reason"],
                    "interventions": motor.interventions,
                    "overridden": motor.overridden,
                    "planned": motor.decisions,
                    "final": final,
                    "latency": verdict.latency_ms, "model_ms": verdict.model_ms,
                    "p50": stats.p50(), "p95": stats.p95(),
                    "decisions": stats.decisions, "tokens": stats.tokens,
                    "cost": stats.cost, "errors": stats.errors,
                    "sudden_death": sudden,
                    "rival_seen": obs.rival is not None,
                    "rival_dist": obs.rival.dist if obs.rival else None,
                    "threats": [c.describe() for c in obs.contacts
                                if c.kind in ("monster", "rival")][:3],
                    "visible": sum(1 for c in obs.contacts
                                   if c.visible and c.kind in ("monster", "rival")),
                }, block=False)
                last_push = now
    except Exception as e:
        out_q.put({"kind": kind, "fatal": f"{type(e).__name__}: {e}"})
    finally:
        stop_evt.set()
        if worker is not None:
            worker.join(timeout=2.0)
        backend.close()
        try:
            game.close()
        except Exception:
            pass
