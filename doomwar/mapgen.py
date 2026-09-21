"""Generate the DOOM WAR arena as a UDMF PWAD.

A small square box with a handful of cover pillars — the aim-map shape, not a
level. Both fighters spawn in opposite corners, monsters sit near each spawn
so each fighter gets its own pressure, and there is enough kit on the floor
that neither starves.

ZDoom (which ViZDoom is built on) builds BSP nodes for UDMF maps at load time,
so this only has to emit TEXTMAP — no node builder required.
"""

import struct
from pathlib import Path

# Doom thing types
DM_START = 11
ZOMBIE, SHOTGUY, IMP, PINKY = 3004, 9, 3001, 3002
SHOTGUN, CHAINGUN, SSG = 2001, 2002, 82
SHELLS, BULLETS, SHELLBOX, BULLETBOX = 2008, 2007, 2049, 2048
STIMPACK, MEDIKIT, ARMOR, ARMORBONUS = 2011, 2012, 2018, 2015

WALL, FLOOR, CEIL = "STARTAN2", "FLOOR4_8", "CEIL3_5"
LIGHT = 200


class Udmf:
    def __init__(self):
        self.v, self.side, self.line, self.thing = [], [], [], []

    def vertex(self, x, y):
        self.v.append((float(x), float(y)))
        return len(self.v) - 1

    def loop(self, pts, sector=0):
        """A closed run of one-sided walls. Winding decides which way they face."""
        ids = [self.vertex(*p) for p in pts]
        for a, b in zip(ids, ids[1:] + ids[:1]):
            self.side.append(sector)
            self.line.append((a, b, len(self.side) - 1))

    def add_thing(self, x, y, kind, angle=0):
        self.thing.append((float(x), float(y), int(kind), int(angle)))

    def render(self, w, h):
        out = ['namespace = "zdoom";\n']
        for x, y in self.v:
            out.append("vertex{x=%.1f;y=%.1f;}\n" % (x, y))
        for sec in self.side:
            out.append('sidedef{sector=%d;texturemiddle="%s";}\n' % (sec, WALL))
        for a, b, s in self.line:
            out.append("linedef{v1=%d;v2=%d;sidefront=%d;blocking=true;}\n" % (a, b, s))
        out.append('sector{heightfloor=0;heightceiling=192;texturefloor="%s";'
                   'textureceiling="%s";lightlevel=%d;}\n' % (FLOOR, CEIL, LIGHT))
        for x, y, kind, ang in self.thing:
            out.append("thing{x=%.1f;y=%.1f;angle=%d;type=%d;skill1=true;skill2=true;"
                       "skill3=true;skill4=true;skill5=true;single=true;dm=true;"
                       "coop=true;}\n" % (x, y, ang, kind))
        return "".join(out).encode("ascii")


def wad(lumps):
    """Assemble a PWAD from (name, bytes) pairs."""
    body, directory, off = b"", b"", 12
    for name, data in lumps:
        body += data
        directory += struct.pack("<ii8s", off, len(data), name.encode().ljust(8, b"\0"))
        off += len(data)
    return struct.pack("<4sii", b"PWAD", len(lumps), 12 + len(body)) + body + directory


def build(size=800, out="arena.wad"):
    """One empty square box. No cover, no corners to lose each other in.

    At 800 units the diagonal is about 1130, so both fighters are in each
    other's line of sight from the first tic. A couple of monsters sit off to
    each side for pressure without turning it into a monster fight.
    """
    m = Udmf()
    s = size
    c = s / 2

    # Outer wall only. This winding puts every front side facing into the room.
    m.loop([(0, 0), (0, s), (s, s), (s, 0)])

    edge = 90
    a = (edge, edge)                 # one corner
    b = (s - edge, s - edge)         # diagonally opposite, in plain view
    m.add_thing(*a, DM_START, 45)
    m.add_thing(*b, DM_START, 225)

    # Two monsters per fighter, tucked into the other two corners so they
    # apply pressure without standing between the two of them.
    m.add_thing(edge, s - edge, ZOMBIE, 315)
    m.add_thing(edge + 120, s - edge - 120, SHOTGUY, 315)
    m.add_thing(s - edge, edge, ZOMBIE, 135)
    m.add_thing(s - edge - 120, edge + 120, SHOTGUY, 135)

    # Just enough kit, mirrored, plus the good gun in the middle.
    for base in (a, b):
        bx, by = base
        d = 1 if base is a else -1
        m.add_thing(bx + 150 * d, by + 30 * d, SHOTGUN)
        m.add_thing(bx + 30 * d, by + 150 * d, SHELLS)
        m.add_thing(bx + 210 * d, by + 210 * d, STIMPACK)
    m.add_thing(c, c, CHAINGUN)
    m.add_thing(c - 150, c + 150, MEDIKIT)
    m.add_thing(c + 150, c - 150, SHELLBOX)

    data = wad([("MAP01", b""), ("TEXTMAP", m.render(s, s)), ("ENDMAP", b"")])
    Path(out).write_bytes(data)
    return out, len(m.thing), len(m.line)


if __name__ == "__main__":
    import sys
    path, things, lines = build(out=sys.argv[1] if len(sys.argv) > 1 else "arena.wad")
    print(f"wrote {path}: {things} things, {lines} linedefs")
