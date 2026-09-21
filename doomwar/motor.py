"""Turn typed answers into Doom inputs.

The model picks a macro; this layer executes it. It also runs a small,
explicit safety shield, so every correction is counted and shown on screen
rather than quietly papering over a bad call.
"""

import random

import vizdoom as vzd

BUTTONS = [
    vzd.Button.ATTACK,
    vzd.Button.MOVE_FORWARD,
    vzd.Button.MOVE_BACKWARD,
    vzd.Button.MOVE_LEFT,
    vzd.Button.MOVE_RIGHT,
    vzd.Button.USE,
    vzd.Button.TURN_LEFT_RIGHT_DELTA,
]
ATTACK, FWD, BACK, LEFT, RIGHT, USE, TURN = range(7)

MAX_TURN = 22.0     # degrees per tic, so aim sweeps instead of snapping
# ViZDoom's TURN_LEFT_RIGHT_DELTA is positive-is-right, but a bearing is
# positive-is-left. Without this flip every aim command turns away.
TURN_SIGN = -1.0
AIM_CONE = 30.0     # must be this close to on-target before the shield allows fire


class Motor:
    """Holds the between-decision state: current macro, aim, stuck detection."""

    def __init__(self, seed=0, shield=True):
        self.rng = random.Random(seed)
        self.shield = shield
        self.interventions = 0      # total corrections
        self.overridden = 0         # decisions with at least one correction
        self.decisions = 0
        self.last_reason = ""
        self._recent = []
        self._unstick = 0
        self._turn_budget = 0.0

    def plan(self, verdict, obs):
        """Resolve answers + observation into a repeatable action plan."""
        goal = verdict.choice("goal", "hunt_rival" if obs.final else "advance")
        move = verdict.choice("movement", "advance")
        turn = verdict.choice("turn", "hold_aim")
        dodge = verdict.choice("dodge", "carry_on")
        trigger = verdict.choice("trigger", "hold_fire")

        # Which contact are we pointing at? Prefer the model's pick.
        focus = None
        picked = verdict.choice("target")
        if picked and picked in obs.target_lookup:
            focus = obs.target_lookup[picked]
        if focus is None:
            live = [c for c in obs.contacts
                    if c.kind in ("monster", "rival") and c.engageable]
            # anything already lined up beats something merely on screen
            live.sort(key=lambda c: (not c.in_sights, c.dist))
            focus = live[0] if live else None

        # A goal that names a pickup steers movement toward that pickup instead.
        want = {"restore_health": "health", "stock_ammo": "ammo",
                "upgrade_weapon": "weapon"}.get(goal)
        seek = None
        if want:
            near = [c for c in obs.contacts if c.kind == want]
            seek = near[0] if near else None
        elif goal == "hunt_rival" and obs.rival is not None:
            # Committing to the hunt means steering at the rival, and shooting
            # them rather than whatever monster happens to be in frame.
            seek = obs.rival
            if obs.rival.engageable:
                focus = obs.rival
            elif focus is not None and focus.dist > 500:
                focus = None

        fire = trigger == "fire"
        reason = ""
        if self.shield:
            if fire and obs.ammo <= 0 and obs.weapon not in ("fist", "chainsaw"):
                fire, reason = False, "no ammo"
            elif fire and focus is None:
                fire, reason = False, "no target in sight"
            elif fire and abs(focus.bear) > AIM_CONE:
                fire, reason = False, "not on target"
        if reason:
            self.interventions += 1
            self.last_reason = reason

        # The turn answer decides where the gun points.
        nearest_monster = next((c for c in obs.contacts
                                if c.kind == "monster" and c.visible), None)
        if turn == "face_rival" and obs.rival is not None:
            aim = obs.rival
        elif turn == "face_threat" and nearest_monster is not None:
            aim = nearest_monster
        elif turn == "sweep":
            aim = None
        else:
            aim = focus

        # Walking face-first into a wall was the main reason both fighters
        # stalled. Clearance is now known, so refuse the move and say so.
        clear = obs.clear or {}
        wall = ""
        if move in ("advance", "close_in") and clear.get("ahead", 255) < 16:
            move = "strafe_left" if clear.get("left", 0) > clear.get("right", 0) \
                else "strafe_right"
            wall = "wall ahead"
        elif move == "strafe_left" and clear.get("left", 255) < 14:
            move, wall = "strafe_right", "wall left"
        elif move == "strafe_right" and clear.get("right", 255) < 14:
            move, wall = "strafe_left", "wall right"
        if wall and self.shield:
            self.interventions += 1
            self.last_reason = wall
            reason = reason or wall

        # One decision buys one turn, spent over the tics that follow. Applying
        # the same bearing every tic made the aim overshoot and oscillate, which
        # is why neither fighter could ever land a shot.
        point_at = aim if aim is not None else seek
        self._turn_budget = point_at.bear if point_at is not None else 0.0

        self.decisions += 1
        if reason:
            self.overridden += 1

        return {
            "goal": goal, "move": move, "dodge": dodge, "turn": turn, "aim": aim,
            "fire": fire, "raw_fire": trigger == "fire",
            "focus": focus, "seek": seek, "shield_reason": reason,
        }

    def act(self, plan, obs, tic):
        """One button vector. Called every tic while the next decision is in flight."""
        a = [0] * 6 + [0.0]

        # Aim: spend down the turn budget this decision set.
        if abs(self._turn_budget) > 0.5:
            d = max(-MAX_TURN, min(MAX_TURN, self._turn_budget))
            a[TURN] = TURN_SIGN * d
            self._turn_budget -= d
        elif plan["turn"] == "sweep" or self._unstick > 0:
            a[TURN] = MAX_TURN if (tic // 12) % 2 else -MAX_TURN

        if plan["fire"]:
            a[ATTACK] = 1

        move, dodge = plan["move"], plan["dodge"]
        if dodge == "dodge_left":
            a[LEFT] = 1
        elif dodge == "dodge_right":
            a[RIGHT] = 1

        if move == "close_in":
            a[FWD] = 1
        elif move == "back_off":
            a[BACK] = 1
        elif move == "strafe_left":
            a[LEFT] = 1
        elif move == "strafe_right":
            a[RIGHT] = 1
        elif move == "advance":
            a[FWD] = 1
        # hold_ground: no translation

        # Walking to a specific thing overrides "hold still".
        if plan["seek"] is not None and move in ("advance", "hold_ground"):
            a[FWD] = 1

        if self._unstick > 0:
            a[FWD], a[BACK] = 1, 0
            self._unstick -= 1

        # Doors and switches: cheap to press constantly, and unblocks progress.
        a[USE] = 1 if tic % 8 == 0 else 0
        return a

    def note_position(self, obs):
        """Detect a fighter grinding against geometry and break it out."""
        self._recent.append((obs.x, obs.y))
        if len(self._recent) > 12:
            self._recent.pop(0)
        if self._unstick == 0 and len(self._recent) == 12:
            xs = [p[0] for p in self._recent]
            ys = [p[1] for p in self._recent]
            if (max(xs) - min(xs)) < 40 and (max(ys) - min(ys)) < 40:
                self._unstick = self.rng.randint(8, 16)
                self.interventions += 1
                self.last_reason = "stuck, breaking out"
                self._recent.clear()
