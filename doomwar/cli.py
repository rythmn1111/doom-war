"""doom-war — run the match.

One continuous deathmatch on a small arena. Every frag is a round; first to
`--rounds` wins. Monsters keep both fighters honest in between. If nobody
scores for a while the objective narrows for both sides at the same moment,
so a stale fight always resolves.
"""

import argparse
import json
import pathlib
import sys
import time


def ensure_arena(path):
    """Generate the arena on first run so the demo works from a clean clone."""
    if path == "arena.wad" and not pathlib.Path(path).exists():
        from .mapgen import build
        _, things, walls = build(out=path)
        print(f"  generated {path}: {things} things, {walls} walls")


def summarise(dash, mode, elapsed):
    la = sum(1 for r in dash.rounds if r == "laya")
    je = sum(1 for r in dash.rounds if r == "jev")
    out = {
        "mode": mode, "wall_seconds": round(elapsed, 1),
        "rounds_won": {"laya": la, "jev": je},
        "round_order": dash.rounds,
        "winner": "laya" if la > je else "jev" if je > la else None,
        "fighters": {},
    }
    for k, m in dash.last.items():
        if not m:
            continue
        out["fighters"][k] = {
            "rounds_won": la if k == "laya" else je,
            "monster_kills": int(m["kills"]), "deaths": int(m["deaths"]),
            "decisions": m["decisions"],
            "latency_p50_ms": round(m["p50"], 1), "latency_p95_ms": round(m["p95"], 1),
            "overridden_pct": round(100 * m.get("overridden", 0)
                                    / max(1, m.get("planned", 1)), 1),
            "input_tokens": m["tokens"], "cost_usd": round(m["cost"], 6),
            "errors": m["errors"], "game_tics": m["tic"],
        }
    return out


def banner(res):
    a = res["fighters"].get("laya", {})
    b = res["fighters"].get("jev", {})
    w = res.get("winner")
    print(f"\n  {'':18}{'LAYA':>12}{'JEV':>12}")
    print(f"  {'-' * 42}")
    for label, key, fmt in [
        ("rounds won", "rounds_won", "{}"),
        ("monsters killed*", "monster_kills", "{}"),
        ("deaths", "deaths", "{}"),
        ("decisions", "decisions", "{}"),
        ("latency p50", "latency_p50_ms", "{} ms"),
        ("overridden", "overridden_pct", "{}%"),
        ("tokens in", "input_tokens", "{}"),
        ("cost", "cost_usd", "${}"),
    ]:
        print(f"  {label:18}{fmt.format(a.get(key, 0)):>12}{fmt.format(b.get(key, 0)):>12}")
    print(f"  {'-' * 42}")
    print(f"  WINNER: {(w or 'DRAW').upper()}   "
          f"{res['rounds_won']['laya']} - {res['rounds_won']['jev']}")
    print("  * level kill counter is shared in a netgame, not per fighter\n")


def run_match(args):
    from .arena import Arena
    from .ui import Dashboard

    label = "REAL TIME" if args.mode == "realtime" else "EQUAL DECISIONS"
    print(f"\n=== DOOM WAR · first to {args.rounds} · {label} ===")
    arena = Arena(mode=args.mode, wad=args.wad, mapname=args.map, seed=args.seed,
                  model=args.model, shield=not args.no_shield, seconds=args.seconds)
    arena.start()
    print("  starting engines and loading models…", flush=True)
    ok, _ = arena.wait_ready()
    if not ok:
        print(f"  FAILED: {arena.fatal}", file=sys.stderr)
        arena.shutdown()
        return None
    print("  both fighters ready — fight!\n", flush=True)

    frames = None
    if args.save_frames:
        p = pathlib.Path(args.save_frames)
        p.mkdir(parents=True, exist_ok=True)
        frames = str(p)
    dash = Dashboard(args.mode, 1, label, save_frames=frames, fps=args.fps)
    dash.target = args.rounds
    rec = open(args.record, "a") if args.record else None

    def won(side):
        return sum(1 for r in dash.rounds if r == side)

    t0, done_at = time.perf_counter(), None
    try:
        while True:
            if not dash.pump():
                break
            now = time.perf_counter()
            for m in arena.drain(0.008):
                dash.update(m)
                if rec:
                    rec.write(json.dumps({k: v for k, v in m.items() if k != "frame"}) + "\n")

            if done_at is None and (won("laya") >= args.rounds or won("jev") >= args.rounds
                                    or now - t0 > args.seconds):
                dash.finished = ("laya" if won("laya") > won("jev")
                                 else "jev" if won("jev") > won("laya") else None)
                done_at = now
            if done_at is not None and now - done_at > args.hold:
                break      # let the end card breathe, then stop
            if not arena.alive() or arena.fatal:
                break
            dash.draw()
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.perf_counter() - t0
        arena.shutdown()
        if rec:
            rec.close()
    if arena.fatal:
        print(f"  fighter error: {arena.fatal}", file=sys.stderr)
    res = summarise(dash, args.mode, elapsed)
    banner(res)
    return res


def main(argv=None):
    p = argparse.ArgumentParser("doom-war", description="LAYA vs JEV, Doom deathmatch")
    p.add_argument("--mode", choices=("equal", "realtime"), default="realtime",
                   help="realtime: the Doom clock runs at 1x and latency matters. "
                        "equal: the engine waits for both, measuring judgment only.")
    p.add_argument("--rounds", type=int, default=5, help="frags needed to win the match")
    p.add_argument("--seconds", type=float, default=240, help="hard cap on match length")
    p.add_argument("--hold", type=float, default=8, help="seconds to hold the end card")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--map", default="MAP01")
    p.add_argument("--wad", default="arena.wad",
                   help="defaults to the generated DOOM WAR arena")
    p.add_argument("--model", default=None, help="Laya checkpoint dir")
    p.add_argument("--no-shield", action="store_true", help="execute raw model calls")
    p.add_argument("--record", default=None, help="append telemetry JSONL here")
    p.add_argument("--results", default="results.json")
    p.add_argument("--save-frames", default=None,
                   help="write true 1920x1080 PNG frames here for a clean render")
    p.add_argument("--fps", type=int, default=30, help="frame capture rate")
    a = p.parse_args(argv)

    ensure_arena(a.wad)
    res = run_match(a)
    if res:
        pathlib.Path(a.results).write_text(json.dumps(res, indent=2))
        print(f"  results -> {a.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
