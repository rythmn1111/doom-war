"""Turn a ViZDoom game state into compact prose both models can read.

The model never sees pixels or raw coordinates. Deterministic code does the
geometry and hands over short descriptions; the model only ranks them. This
is the same split the Snake demo in laya-mlx uses, and the same split the
TypeSafe docs recommend ("offload computation, filter data").
"""

import math
from dataclasses import dataclass, field

import vizdoom as vzd

MONSTERS = {
    "Zombieman": "zombieman", "ShotgunGuy": "shotgunner", "ChaingunGuy": "chaingunner",
    "DoomImp": "imp", "Demon": "pinky", "Spectre": "spectre", "Cacodemon": "cacodemon",
    "LostSoul": "lost soul", "HellKnight": "hell knight", "BaronOfHell": "baron",
    "Arachnotron": "arachnotron", "PainElemental": "pain elemental", "Revenant": "revenant",
    "Fatso": "mancubus", "Archvile": "archvile",
}
HEALTH_ITEMS = {"Medikit": "medikit", "Stimpack": "stimpack", "HealthBonus": "health bonus",
                "Soulsphere": "soulsphere", "Megasphere": "megasphere"}
AMMO_ITEMS = {"Clip": "clip", "ClipBox": "box of bullets", "Shell": "shells",
              "ShellBox": "box of shells", "RocketAmmo": "rocket", "RocketBox": "box of rockets",
              "Cell": "cell", "CellPack": "cell pack"}
WEAPON_ITEMS = {"Shotgun": "shotgun", "SuperShotgun": "super shotgun", "Chaingun": "chaingun",
                "RocketLauncher": "rocket launcher", "PlasmaRifle": "plasma rifle",
                "BFG9000": "BFG", "Chainsaw": "chainsaw"}
ARMOR_ITEMS = {"GreenArmor": "armor vest", "BlueArmor": "combat armor", "ArmorBonus": "armor shard"}

WEAPON_NAMES = {0: "fist", 1: "pistol", 2: "shotgun", 3: "chaingun",
                4: "rocket launcher", 5: "plasma rifle", 6: "BFG", 7: "chainsaw",
                8: "super shotgun"}


def bearing(px, py, pangle, tx, ty):
    """Signed degrees from where the fighter is looking to the target. + is left."""
    want = math.degrees(math.atan2(ty - py, tx - px))
    return (want - pangle + 180.0) % 360.0 - 180.0


def side(b):
    if abs(b) <= 12:
        return "dead ahead"
    if abs(b) <= 60:
        return "slightly left" if b > 0 else "slightly right"
    if abs(b) <= 120:
        return "to the left" if b > 0 else "to the right"
    return "behind"


def span(d):
    if d < 150:
        return "right on top of the fighter"
    if d < 400:
        return "close"
    if d < 900:
        return "mid range"
    return "far off"


AIM_CONE = 25.0      # degrees either side of centre that counts as lined up
ENGAGE_RANGE = 1200.0


@dataclass
class Contact:
    """One thing in the world the fighter could act on."""
    name: str
    kind: str          # monster | rival | health | ammo | weapon | armor
    dist: float
    bear: float
    visible: bool
    x: float
    y: float

    @property
    def in_sights(self):
        """Lined up and in range.

        The renderer's label list misses actors that are very close or partly
        off-frame, which left both fighters standing next to each other with
        nothing in the state saying so. Geometry is the reliable signal.
        """
        return abs(self.bear) <= AIM_CONE and self.dist <= ENGAGE_RANGE

    @property
    def engageable(self):
        return self.visible or self.in_sights

    def describe(self):
        if self.in_sights:
            return (f"{self.name}, {span(self.dist)} dead ahead, "
                    f"LINED UP IN THE SIGHTS, {int(self.dist)} units")
        sight = "in sight" if self.visible else "not in sight"
        return f"{self.name}, {span(self.dist)} {side(self.bear)}, {sight}"


def clearance(depth):
    """Rough free distance ahead / left / right, sampled from the depth buffer.

    Without this both fighters walk into walls and stay there: nothing in the
    state told them a wall was in the way.
    """
    if depth is None:
        return {}
    h, w = depth.shape
    band = depth[int(h * .42):int(h * .62)]          # eye level, ignore floor/ceiling
    third = w // 5
    return {
        "ahead": int(band[:, 2 * third:3 * third].min()),
        "left": int(band[:, :third].min()),
        "right": int(band[:, 4 * third:].min()),
    }


def room(v):
    if v < 16:
        return "blocked"
    if v < 40:
        return "tight"
    if v < 100:
        return "some room"
    return "wide open"


@dataclass
class Observation:
    tick: int = 0
    health: float = 100.0
    armor: float = 0.0
    ammo: float = 0.0
    weapon: str = "pistol"
    frags: float = 0.0
    killcount: float = 0.0
    deaths: float = 0.0
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0
    contacts: list = field(default_factory=list)
    rival: Contact | None = None
    available_goals: list = field(default_factory=list)
    target_options: dict = field(default_factory=dict)
    target_lookup: dict = field(default_factory=dict)
    hurt: float = 0.0
    clear: dict = field(default_factory=dict)
    sudden: bool = False
    final: bool = False

    def state(self) -> dict:
        """The compact JSON object handed to the model. Keep this small."""
        threats = [c for c in self.contacts if c.kind in ("monster", "rival")][:4]
        s = {
            "mission": ("FINAL PHASE. Close on the rival and kill them. Nothing else counts."
                        if self.final else
                        "KILL THE RIVAL. Monsters are only in the way."
                        if self.sudden else
                        "Kill the rival fighter. Clear monsters and take kit on the way."),
            "health": int(self.health),
            "armor": int(self.armor),
            "weapon": self.weapon,
            "ammo": int(self.ammo),
            "threats": [c.describe() for c in threats] or ["nothing hostile in sight"],
        }
        if self.clear:
            s["space"] = {k: room(v) for k, v in self.clear.items()}
        if self.hurt > 0:
            s["just_took_damage"] = f"{int(self.hurt)} damage since the last decision"
        if self.rival is not None:
            r = self.rival
            s["rival"] = (
                f"LINED UP IN THE SIGHTS, {int(r.dist)} units away — SHOOT NOW"
                if r.in_sights else
                f"{span(r.dist)} {side(r.bear)}, "
                f"{'in sight' if r.visible else 'not in sight'}, {int(r.dist)} units away")
        picks = {}
        for kind in ("health", "ammo", "weapon", "armor"):
            near = [c for c in self.contacts if c.kind == kind]
            if near:
                picks[kind] = near[0].describe()
        if picks:
            s["pickups"] = picks
        s["score"] = f"{int(self.frags)} frags, {int(self.killcount)} monsters killed"
        return s


def read(game: vzd.DoomGame, tick: int, rival_name: str, prev_health: float,
         sudden_death: bool = False, final: bool = False) -> Observation | None:
    st = game.get_state()
    if st is None:
        return None

    def var(v, default=0.0):
        try:
            return game.get_game_variable(v)
        except Exception:
            return default

    o = Observation(tick=tick, sudden=sudden_death, final=final)
    o.clear = clearance(st.depth_buffer)
    o.health = var(vzd.GameVariable.HEALTH)
    o.armor = var(vzd.GameVariable.ARMOR)
    o.ammo = var(vzd.GameVariable.SELECTED_WEAPON_AMMO)
    o.weapon = WEAPON_NAMES.get(int(var(vzd.GameVariable.SELECTED_WEAPON)), "weapon")
    o.frags = var(vzd.GameVariable.FRAGCOUNT)
    o.killcount = var(vzd.GameVariable.KILLCOUNT)
    o.deaths = var(vzd.GameVariable.DEATHCOUNT)
    o.x, o.y = var(vzd.GameVariable.POSITION_X), var(vzd.GameVariable.POSITION_Y)
    o.angle = var(vzd.GameVariable.ANGLE)
    o.hurt = max(0.0, prev_health - o.health)

    # labels list exactly the actors rendered this frame; match them by id, not position.
    visible = {l.object_id for l in st.labels}

    contacts = []
    for ob in st.objects:
        if math.hypot(ob.position_x - o.x, ob.position_y - o.y) < 2.0:
            continue  # this is us
        if ob.name == "DoomPlayer":
            kind, label = "rival", rival_name
        elif ob.name in MONSTERS:
            kind, label = "monster", MONSTERS[ob.name]
        elif ob.name in HEALTH_ITEMS:
            kind, label = "health", HEALTH_ITEMS[ob.name]
        elif ob.name in AMMO_ITEMS:
            kind, label = "ammo", AMMO_ITEMS[ob.name]
        elif ob.name in WEAPON_ITEMS:
            kind, label = "weapon", WEAPON_ITEMS[ob.name]
        elif ob.name in ARMOR_ITEMS:
            kind, label = "armor", ARMOR_ITEMS[ob.name]
        else:
            continue
        d = math.hypot(ob.position_x - o.x, ob.position_y - o.y)
        contacts.append(Contact(label, kind, d, bearing(o.x, o.y, o.angle, ob.position_x,
                                                        ob.position_y), ob.id in visible,
                                ob.position_x, ob.position_y))

    contacts.sort(key=lambda c: (not c.visible, c.dist))
    o.contacts = contacts
    rivals = [c for c in contacts if c.kind == "rival"]
    o.rival = rivals[0] if rivals else None

    # Distinct, labelled shooting targets. Suffix duplicates so "imp" is unambiguous.
    seen, opts, lookup = {}, {}, {}
    for c in contacts:
        if c.kind not in ("monster", "rival") or not c.engageable:
            continue
        n = seen.get(c.name, 0) + 1
        seen[c.name] = n
        label = c.name if n == 1 else f"{c.name} {chr(64 + n)}"
        opts[label] = ("lined up, %d units" % int(c.dist)) if c.in_sights \
            else f"{span(c.dist)}, {side(c.bear)}"
        lookup[label] = c
        if len(opts) >= 5:
            break
    o.target_options, o.target_lookup = opts, lookup

    # Only offer goals that are actionable, so the model never picks a no-op.
    if final:
        # Last stretch: the objective is fixed, so the goal question is dropped
        # entirely and both fighters commit. Same rule, same moment, both sides.
        goals = []
    elif sudden_death:
        # Late in a round the arena closes: survive, or go and win it. Both
        # fighters get the same restriction at the same moment.
        goals = ["hunt_rival", "fight_monsters"]
        if o.health < 50 and any(c.kind == "health" and c.dist < 600 for c in contacts):
            goals.append("restore_health")
    else:
        goals = ["advance"]
        if any(c.kind == "monster" and c.visible for c in contacts):
            goals.append("fight_monsters")
        if o.rival is not None:
            goals.append("hunt_rival")
        if o.health < 70 and any(c.kind == "health" for c in contacts):
            goals.append("restore_health")
        if o.ammo < 15 and any(c.kind == "ammo" for c in contacts):
            goals.append("stock_ammo")
        if any(c.kind == "weapon" and c.dist < 700 for c in contacts):
            goals.append("upgrade_weapon")
    o.available_goals = goals
    return o
