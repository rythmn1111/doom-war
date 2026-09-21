"""Live 1920x1080 dashboard. Two Doom viewports, two probability rails,
a shared arena map in the middle so you can watch them converge.

Everything drawn here comes from the fighters' own telemetry. Nothing is
simulated, smoothed or replayed.
"""

import time
from collections import deque

import numpy as np
import pygame

W, H = 1920, 1080
BG = (8, 10, 12)
PANEL = (17, 20, 23)
EDGE = (34, 40, 44)
TEXT = (228, 234, 232)
DIM = (112, 124, 122)
FAINT = (58, 66, 68)
WARN = (255, 96, 96)
LAYA_C = (64, 224, 168)
JEV_C = (255, 122, 60)

VW, VH = 512, 384          # viewport, 1.6x of 320x240
VY = 166
LX, RX = 40, W - 40 - VW
CX, CW = 576, W - 40 - VW - 24 - 576   # centre column fills the gap

# (answer key, heading, how many options to draw). Kept short so the rails
# always fit above the scoreboard.
RAILS = [
    ("goal", "GOAL", 3),
    ("turn", "AIM", 3),
    ("movement", "MOVEMENT", 3),
    ("target", "TARGET", 2),
    ("trigger", "TRIGGER", 2),
]


def font(size, bold=False):
    f = pygame.font.SysFont("Menlo,Monaco,Courier New", size, bold=bold)
    return f


class Dashboard:
    def __init__(self, mode, round_no, round_label, save_frames=None, fps=30):
        pygame.init()
        pygame.display.set_caption("LAYA vs JEV — DOOM WAR")
        # Always compose at true 1080p, then scale to whatever the display can
        # show. Recorded frames stay pristine regardless of screen size.
        dw, dh = pygame.display.get_desktop_sizes()[0]
        self.scale = min(1.0, (dw - 40) / W, (dh - 90) / H)
        self.window = pygame.display.set_mode((int(W * self.scale), int(H * self.scale)))
        self.canvas = pygame.Surface((W, H))
        self.screen = self.canvas
        self.save_frames = save_frames
        self.frame_every = 1.0 / fps
        self._next_frame = 0.0
        self._frame_no = 0
        self.f_title = font(40, True)
        self.f_sub = font(17)
        self.f_h = font(21, True)
        self.f = font(16)
        self.f_sm = font(13)
        self.f_num = font(38, True)
        self.f_lab = font(12)
        self.f_name = font(34, True)
        self.f_score = font(92, True)
        self.f_big = font(56, True)
        self.f_mid = font(26, True)
        self.mode, self.round_no, self.round_label = mode, round_no, round_label
        self.last = {}
        self.surf = {}
        self.lat_hist = {"laya": deque(maxlen=260), "jev": deque(maxlen=260)}
        self.trail = {"laya": deque(maxlen=400), "jev": deque(maxlen=400)}
        self.bounds = None
        self.t0 = time.perf_counter()
        self.clock = pygame.time.Clock()
        self.banner = None
        self.banner_until = 0.0
        self.target = 5
        self.rounds = []      # winner of each frag
        self.finished = None

    # ---------- ingest ----------
    def update(self, msg):
        k = msg["kind"]
        prev = self.last.get(k)
        if prev and msg.get("frags", 0) > prev.get("frags", 0):
            who = "LAYA" if k == "laya" else "JEV"
            self.rounds.append(k)
            n = sum(1 for r in self.rounds if r == k)
            self.flash(f"ROUND {len(self.rounds)}  ·  {who} TAKES IT   ({n}/{self.target})",
                       LAYA_C if k == "laya" else JEV_C)
        self.last[k] = msg
        if msg.get("frame") is not None:
            a = np.asarray(msg["frame"])
            if a.ndim == 3 and a.shape[0] == 3:      # CHW -> HWC
                a = np.transpose(a, (1, 2, 0))
            self.surf[k] = pygame.transform.scale(
                pygame.surfarray.make_surface(np.transpose(a, (1, 0, 2))), (VW, VH))
        if msg.get("latency"):
            self.lat_hist[k].append(msg["latency"])
        self.trail[k].append((msg["x"], msg["y"]))
        xs = [p[0] for t in self.trail.values() for p in t]
        ys = [p[1] for t in self.trail.values() for p in t]
        if xs:
            pad = 260
            self.bounds = (min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad)

    def flash(self, text, colour):
        self.banner = (text, colour)
        self.banner_until = time.perf_counter() + 2.5

    # ---------- primitives ----------
    def txt(self, s, x, y, f=None, c=TEXT, right=False, center=False):
        img = (f or self.f).render(str(s), True, c)
        r = img.get_rect()
        if right:
            r.topright = (x, y)
        elif center:
            r.midtop = (x, y)
        else:
            r.topleft = (x, y)
        self.screen.blit(img, r)
        return r

    def panel(self, x, y, w, h, fill=PANEL):
        pygame.draw.rect(self.screen, fill, (x, y, w, h), border_radius=6)
        pygame.draw.rect(self.screen, EDGE, (x, y, w, h), 1, border_radius=6)

    def bar(self, x, y, w, frac, colour, h=9, bg=(28, 33, 36)):
        pygame.draw.rect(self.screen, bg, (x, y, w, h), border_radius=3)
        fw = int(max(0.0, min(1.0, frac)) * w)
        if fw > 1:
            pygame.draw.rect(self.screen, colour, (x, y, fw, h), border_radius=3)

    # ---------- sections ----------
    def header(self):
        el = time.perf_counter() - self.t0
        self.txt("LAYA", 40, 26, self.f_title, LAYA_C)
        self.txt("vs", 40 + 118, 36, self.f_h, DIM)
        self.txt("JEV", 40 + 170, 26, self.f_title, JEV_C)
        self.txt("/  DOOM WAR", 40 + 258, 30, self.f_title, TEXT)
        self.txt("Same state. Same questions. Same shield. Only the model differs.",
                 42, 76, self.f_sub, DIM)
        self.txt(f"ROUND {self.round_no} · {self.round_label}", W - 40, 24,
                 self.f_h, TEXT, right=True)
        self.txt(f"LIVE · {int(el)//60:02d}:{int(el) % 60:02d}.{int(el * 10) % 10}",
                 W - 40, 52, self.f_sub, DIM, right=True)
        tics = max((m.get("tic", 0) for m in self.last.values()), default=0)
        rt = (tics / 35.0) / el if el > 0.5 else 0.0
        self.txt(f"game clock {rt:.2f}x real time", W - 40, 76, self.f_sm, FAINT, right=True)
        if any(m.get("sudden_death") for m in self.last.values()):
            tag = ("FINAL · KILL THE RIVAL"
                   if any(m.get("final") for m in self.last.values())
                   else "SUDDEN DEATH · HUNT THE RIVAL")
            r = pygame.Rect(0, 0, self.f_h.size(tag)[0] + 28, 30)
            r.center = (W // 2, 46)
            pygame.draw.rect(self.screen, (52, 14, 14), r, border_radius=5)
            pygame.draw.rect(self.screen, (226, 70, 70), r, 1, border_radius=5)
            self.txt(tag, W // 2, 50, self.f_h, (255, 132, 132), center=True)
        pygame.draw.line(self.screen, EDGE, (0, 104), (W, 104))

    def viewport(self, k, x, colour, name, sub):
        m = self.last.get(k)
        r = self.txt(name, x, VY - 54, self.f_name, colour)
        self.txt(sub, r.right + 14, VY - 44, self.f_sub, DIM)
        self.panel(x - 6, VY - 6, VW + 12, VH + 12, (12, 14, 16))
        s = self.surf.get(k)
        if s is not None:
            self.screen.blit(s, (x, VY))
        else:
            self.txt("waiting for first frame…", x + VW // 2, VY + VH // 2, self.f, DIM,
                     center=True)
        if m and m.get("shield_reason"):
            tag = f"SHIELD · {m['shield_reason']}"
            r = pygame.Rect(x + 8, VY + VH - 30, self.f.size(tag)[0] + 16, 22)
            pygame.draw.rect(self.screen, (40, 28, 12), r, border_radius=4)
            pygame.draw.rect(self.screen, (200, 150, 60), r, 1, border_radius=4)
            self.txt(tag, x + 16, VY + VH - 27, self.f_sm, (240, 196, 110))

    def rails(self, k, x, colour):
        m = self.last.get(k)
        y = VY + VH + 26
        if not m:
            return
        ans = m.get("answers", {})
        self.txt("MODEL PROBABILITIES", x, y, self.f_lab, DIM)
        y += 22
        for key, title, cap in RAILS:
            a = ans.get(key)
            if not a:
                continue
            probs = a.get("probabilities", {})
            if not probs:
                continue
            top = sorted(probs.items(), key=lambda kv: -kv[1])[:cap]
            pick = a.get("choice")
            self.txt(title, x, y, self.f_lab, FAINT)
            self.txt(f"conf {a.get('confidence', 0):.2f}", x + VW, y, self.f_lab,
                     FAINT, right=True)
            y += 17
            if y > 856:
                break
            for label, p in top:
                chosen = label == pick
                self.txt(label[:22], x + 6, y - 2, self.f_sm,
                         TEXT if chosen else DIM)
                self.bar(x + 168, y + 1, VW - 226, p, colour if chosen else (62, 72, 76), 8)
                self.txt(f"{p * 100:4.0f}%", x + VW, y - 2, self.f_sm,
                         TEXT if chosen else DIM, right=True)
                y += 17
            y += 8
        d = ans.get("danger")
        if d:
            v = float(d.get("noul", 0))
            self.txt("DANGER", x, y, self.f_lab, FAINT)
            self.bar(x + 168, y + 1, VW - 226, v, WARN if v > .5 else colour, 8)
            self.txt(f"{v:.2f}", x + VW, y - 2, self.f_sm, TEXT, right=True)

    def minimap(self, y, h):
        self.panel(CX, y, CW, h, (12, 14, 16))
        self.txt("ARENA", CX + 12, y + 8, self.f_lab, DIM)
        if not self.bounds:
            return
        x0, x1, y0, y1 = self.bounds
        pad = 30
        iw, ih = CW - pad * 2, h - pad * 2 - 14
        sx = iw / max(1.0, x1 - x0)
        sy = ih / max(1.0, y1 - y0)
        s = min(sx, sy)
        ox = CX + pad + (iw - (x1 - x0) * s) / 2
        oy = y + pad + 14 + (ih - (y1 - y0) * s) / 2

        def px(p):
            return (int(ox + (p[0] - x0) * s), int(oy + (y1 - p[1]) * s))

        for k, colour in (("laya", LAYA_C), ("jev", JEV_C)):
            pts = list(self.trail[k])
            if len(pts) > 1:
                pygame.draw.lines(self.screen, tuple(int(c * .32) for c in colour),
                                  False, [px(p) for p in pts], 2)
            if pts:
                pygame.draw.circle(self.screen, colour, px(pts[-1]), 8)
                pygame.draw.circle(self.screen, BG, px(pts[-1]), 3)
        a, b = self.last.get("laya"), self.last.get("jev")
        if a and b:
            pa, pb = px((a["x"], a["y"])), px((b["x"], b["y"]))
            dist = ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** .5
            close = dist < 600
            pygame.draw.line(self.screen, (220, 80, 80) if close else FAINT, pa, pb,
                             2 if close else 1)
            mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2)
            self.txt(f"{dist:.0f}", mid[0], mid[1] - 20, self.f_sm,
                     (240, 120, 120) if close else DIM, center=True)
            self.txt("SEPARATION", CX + CW - 12, y + 8, self.f_lab, DIM, right=True)

    def scoreboard(self, y, h):
        self.panel(CX, y, CW, h, (12, 14, 16))
        la = sum(1 for r in self.rounds if r == "laya")
        je = sum(1 for r in self.rounds if r == "jev")
        self.txt(f"FIRST TO {self.target}", CX + 12, y + 8, self.f_lab, DIM)
        self.txt(f"ROUND {len(self.rounds) + 1}", CX + CW - 12, y + 8, self.f_lab, DIM,
                 right=True)
        mid = CX + CW // 2
        self.txt("LAYA", mid - 108, y + 34, self.f_mid, LAYA_C, right=True)
        self.txt("JEV", mid + 108, y + 34, self.f_mid, JEV_C)
        self.txt(str(la), mid - 42, y + 20, self.f_score, LAYA_C, right=True)
        self.txt("—", mid, y + 46, self.f_big, FAINT, center=True)
        self.txt(str(je), mid + 42, y + 20, self.f_score, JEV_C)
        # round pips
        pw, gap = 22, 8
        total = self.target * 2 - 1
        x0 = mid - (total * (pw + gap) - gap) // 2
        for i in range(total):
            r = pygame.Rect(x0 + i * (pw + gap), y + h - 22, pw, 8)
            if i < len(self.rounds):
                pygame.draw.rect(self.screen, LAYA_C if self.rounds[i] == "laya" else JEV_C,
                                 r, border_radius=3)
            else:
                pygame.draw.rect(self.screen, (34, 40, 44), r, border_radius=3)

    def endcard(self):
        w = self.finished
        a, b = self.last.get("laya", {}), self.last.get("jev", {})
        la = sum(1 for r in self.rounds if r == "laya")
        je = sum(1 for r in self.rounds if r == "jev")
        s = pygame.Surface((W, H), pygame.SRCALPHA)
        s.fill((0, 0, 0, 232))
        self.screen.blit(s, (0, 0))
        colour = LAYA_C if w == "laya" else JEV_C if w == "jev" else TEXT
        self.txt("MATCH OVER", W // 2, 210, self.f_mid, DIM, center=True)
        self.txt((w or "draw").upper() + (" WINS" if w else ""), W // 2, 250,
                 self.f_score, colour, center=True)
        self.txt(f"{la}  —  {je}", W // 2, 372, self.f_big, TEXT, center=True)
        rows = [
            ("rounds won", la, je),
            ("monsters killed", int(a.get("kills", 0)), int(b.get("kills", 0))),
            ("deaths", int(a.get("deaths", 0)), int(b.get("deaths", 0))),
            ("decisions", a.get("decisions", 0), b.get("decisions", 0)),
            ("latency p50", f"{a.get('p50', 0):.0f} ms", f"{b.get('p50', 0):.0f} ms"),
            ("overridden", f"{100 * a.get('overridden', 0) / max(1, a.get('planned', 1)):.0f}%",
             f"{100 * b.get('overridden', 0) / max(1, b.get('planned', 1)):.0f}%"),
            ("tokens in", f"{a.get('tokens', 0) / 1000:.0f}k", f"{b.get('tokens', 0) / 1000:.0f}k"),
            ("cost", "free", f"${b.get('cost', 0):.4f}"),
        ]
        y = 470
        self.txt("LAYA", W // 2 - 190, y - 34, self.f_mid, LAYA_C, right=True)
        self.txt("JEV", W // 2 + 190, y - 34, self.f_mid, JEV_C)
        for label, l, r in rows:
            self.txt(label, W // 2, y, self.f, DIM, center=True)
            self.txt(str(l), W // 2 - 190, y - 2, self.f_h, TEXT, right=True)
            self.txt(str(r), W // 2 + 190, y - 2, self.f_h, TEXT)
            y += 42
        self.txt("same state · same questions · same shield · only the model differs",
                 W // 2, y + 24, self.f_sub, FAINT, center=True)

    def scoreline(self, k, x, colour, y):
        m = self.last.get(k)
        if not m:
            return
        cells = [
            ("FRAGS", f"{int(m['frags'])}", colour),
            ("KILLS", f"{int(m['kills'])}", TEXT),
            ("HEALTH", f"{int(m['health'])}", WARN if m["health"] < 40 else TEXT),
            ("ARMOR", f"{int(m['armor'])}", TEXT),
            ("DEATHS", f"{int(m['deaths'])}", TEXT),
        ]
        cw = VW // len(cells)
        for i, (lab, val, c) in enumerate(cells):
            self.txt(lab, x + i * cw, y, self.f_lab, DIM)
            self.txt(val, x + i * cw, y + 16, self.f_num, c)
        y2 = y + 66
        p50 = m.get("p50", 0)
        cost = "free" if k == "laya" else f"${m['cost']:.4f}"
        pct = 100.0 * m.get("overridden", 0) / max(1, m.get("planned", 1))
        row = [
            ("DECISIONS", f"{m['decisions']}"),
            ("LATENCY p50", f"{p50:.0f} ms"),
            ("OVERRIDDEN", f"{min(pct, 999):.0f}%"),
            ("TOKENS", f"{m['tokens'] / 1000:.1f}k"),
            ("COST", cost),
        ]
        for i, (lab, val) in enumerate(row):
            self.txt(lab, x + i * cw, y2, self.f_lab, FAINT)
            self.txt(val, x + i * cw, y2 + 15, self.f, TEXT)

    def latency_plot(self, y, h):
        self.panel(CX, y, CW, h, (12, 14, 16))
        self.txt("DECISION LATENCY", CX + 12, y + 8, self.f_lab, DIM)
        allv = [v for d in self.lat_hist.values() for v in d]
        if not allv:
            return
        top = max(120.0, max(allv) * 1.1)
        x0, y0 = CX + 14, y + 28
        pw, ph = CW - 28, h - 42
        for k, colour in (("laya", LAYA_C), ("jev", JEV_C)):
            d = list(self.lat_hist[k])
            if len(d) < 2:
                continue
            step = pw / max(1, len(d) - 1)
            pts = [(x0 + i * step, y0 + ph - (v / top) * ph) for i, v in enumerate(d)]
            pygame.draw.lines(self.screen, colour, False, pts, 2)
            self.txt(f"{d[-1]:.0f} ms", CX + CW - 12,
                     y + 8 + (0 if k == "laya" else 16), self.f_sm, colour, right=True)

    def overlay(self):
        if self.banner and time.perf_counter() < self.banner_until:
            text, colour = self.banner
            img = self.f_title.render(text, True, colour)
            r = img.get_rect(center=(W // 2, 640))
            bg = r.inflate(56, 28)
            s = pygame.Surface(bg.size, pygame.SRCALPHA)
            s.fill((0, 0, 0, 205))
            self.screen.blit(s, bg.topleft)
            pygame.draw.rect(self.screen, colour, bg, 2, border_radius=6)
            self.screen.blit(img, r)

    def draw(self):
        self.screen.fill(BG)
        self.header()
        self.viewport("laya", LX, LAYA_C, "LAYA", "local MLX · M4 · offline · free")
        self.viewport("jev", RX, JEV_C, "JEV", "hosted API · jev-1.13 · network")
        self.rails("laya", LX, LAYA_C)
        self.rails("jev", RX, JEV_C)
        self.minimap(VY - 6, 372)
        self.latency_plot(VY + 382, 188)
        self.scoreboard(748, 154)
        self.scoreline("laya", LX, LAYA_C, 916)
        self.scoreline("jev", RX, JEV_C, 916)
        pygame.draw.line(self.screen, EDGE, (40, 900), (W - 40, 900))
        self.txt("real decisions · real Doom · vizdoom + laya-mlx + typesafe jev",
                 CX + CW // 2, 1046, self.f_sm, FAINT, center=True)
        self.overlay()
        if self.finished is not None:
            self.endcard()

        now = time.perf_counter()
        if self.save_frames and now >= self._next_frame:
            self._next_frame = max(now, self._next_frame + self.frame_every)
            pygame.image.save(self.canvas,
                              f"{self.save_frames}/f_{self._frame_no:06d}.png")
            self._frame_no += 1

        if self.scale == 1.0:
            self.window.blit(self.canvas, (0, 0))
        else:
            pygame.transform.smoothscale(self.canvas, self.window.get_size(), self.window)
        pygame.display.flip()
        self.clock.tick(60)

    def pump(self):
        """Return False when the user closes the window."""
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                return False
            if e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q):
                return False
        return True
