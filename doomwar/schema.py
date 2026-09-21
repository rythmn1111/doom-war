"""The question schema both fighters answer.

Identical for Laya and Jev: same instructions, same option text, same order.
Only the model behind them differs. Options are built from the live
observation so the model only ever chooses among things that exist.

Budget note: Laya re-encodes the state for every question row, and the
multilingual checkpoint caps at 1024 tokens per row. Keep option text short.
"""

from .observe import Observation

TURNS = {
    "face_rival": "turn to point at the rival fighter",
    "face_threat": "turn to point at the nearest monster",
    "hold_aim": "keep pointing where the fighter already is",
    "sweep": "turn away and look somewhere new",
}

GOALS = {
    "hunt_rival": "go after the rival fighter and kill them",
    "fight_monsters": "shoot the monsters that are attacking right now",
    "restore_health": "pick up health before taking more damage",
    "stock_ammo": "collect ammo, the current weapon is running dry",
    "upgrade_weapon": "grab a better weapon that is nearby",
    "advance": "move through the level to find what is not yet seen",
}

MOVES = {
    "close_in": "move toward the target to get in range",
    "hold_ground": "stay put and keep shooting from here",
    "back_off": "retreat, this position is losing",
    "strafe_left": "sidestep left, good when space on the left is open",
    "strafe_right": "sidestep right, good when space on the right is open",
    "advance": "push straight ahead, only sensible when the space ahead is open",
}

DODGES = {
    "carry_on": "no incoming fire worth reacting to",
    "dodge_left": "break left now to shed incoming fire",
    "dodge_right": "break right now to shed incoming fire",
}


def build(obs: Observation) -> dict:
    """Return the question dict for this observation."""
    q: dict = {}

    # Which goal, out of the ones that actually make sense right now. In the
    # final phase the objective is fixed, so there is nothing to ask.
    goals = {k: v for k, v in GOALS.items() if k in obs.available_goals}
    if len(goals) >= 2:
        q["goal"] = {
            "type": "choice",
            "instructions": "What should the fighter do next?",
            "criteria": goals,
        }

    # Who to shoot at. Only ever offered when something is visible.
    if obs.target_options:
        q["target"] = {
            "type": "choice",
            "instructions": "Which one is the most dangerous right now?",
            "criteria": obs.target_options,
        }

    q["trigger"] = {
        "type": "choice",
        "instructions": "Should the fighter hold the trigger down right now?",
        "criteria": {
            "fire": "a target is lined up and in range",
            "hold_fire": "nothing lined up, or the shot would be wasted",
        },
    }

    q["movement"] = {
        "type": "choice",
        "instructions": "How should the fighter move right now?",
        "criteria": MOVES,
    }

    # Where to point. Aiming was pure motor logic before, which left the model
    # no way out of a wall it was facing.
    turns = dict(TURNS)
    if obs.rival is None:
        turns.pop("face_rival")
    if not any(c.kind == "monster" and c.visible for c in obs.contacts):
        turns.pop("face_threat")
    q["turn"] = {
        "type": "choice",
        "instructions": "Where should the fighter point? Looking and aiming are one act.",
        "criteria": turns,
    }

    q["dodge"] = {
        "type": "choice",
        "instructions": "Is the fighter being shot at, and which way should it break?",
        "criteria": DODGES,
    }

    q["danger"] = {
        "type": "noul",
        "instructions": "Is the fighter about to take serious damage?",
    }

    return q


# Answer keys the motor layer depends on, so a backend can be validated early.
REQUIRED = ("goal", "trigger", "movement", "turn", "dodge", "danger")
