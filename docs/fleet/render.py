#!/usr/bin/env python3
"""Render the fleet risk answer from a fleet.json file (standard library only).

    python3 docs/fleet/render.py [path/to/fleet.json] [--no-png]

Default input: docs/demo/data/fleet.json, falling back to docs/fleet/sample/fleet.json.
Writes, next to this file: story.md, explainer.html, fleet-diagram.svg,
fleet-diagram.html, and (unless --no-png) explainer.png and fleet-diagram.png.

Every number is read from the JSON. A JSON with "sample": true gets a visible
"Illustrative sample" tag on every output. Contract: docs/fleet-risk.md.
"""
import argparse
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
REAL = os.path.join(DOCS, "demo", "data", "fleet.json")
SAMPLE = os.path.join(HERE, "sample", "fleet.json")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TAG = "Illustrative sample"

NAVY, COPPER, INK = "#0A2942", "#A8481B", "#14212F"
PAPER, SURFACE, LINE, LINE_STRONG, MUTED = "#F5F7FA", "#FFFFFF", "#D5DCE5", "#77859A", "#4C5A6B"
TINT_NAVY, TINT_COPPER, CAUTION, TINT_CAUTION = "#E6ECF3", "#FBEEE3", "#8A5B00", "#FBF2DC"
FONT = "'Public Sans','Benton Sans','Helvetica Neue',system-ui,-apple-system,'Segoe UI',Arial,sans-serif"


# ---------------------------------------------------------------- data helpers
def esc(s):
    return html.escape(str(s), quote=True)


def pct(x):
    """Fraction to a percent string: whole numbers, one decimal only when needed below 10%."""
    v = float(x) * 100.0
    if abs(v - round(v)) < 0.05 or v >= 10:
        return "%d%%" % round(v)
    return "%.1f%%" % v


def plural(n, one, many=None):
    return one if n == 1 else (many or one + "s")


class Fleet:
    def __init__(self, data, src_path):
        self.d = data
        self.src = src_path
        self.sample = bool(data.get("sample"))
        self.budget = float(data.get("budget", 0.05))
        self.rule = data.get("rule", "")
        self.fleet = data.get("fleet", {})
        self.agents_n = int(self.fleet.get("agents", len(data.get("agents", []))))
        self.risky_n = int(self.fleet.get("risky", 0))
        self.risky_share = float(self.fleet.get("risky_share", 0.0))
        self.cats = data.get("categories", [])
        self.agents = data.get("agents", [])
        self.by_id = {a["agent_id"]: a for a in self.agents}
        stand = set(data.get("stand_in_categories", []))
        self.stand = {c["agent_type"] for c in self.cats
                      if c.get("source") == "stand_in" or c["agent_type"] in stand}
        self.reveal = data.get("reveal", {})
        self.code = data.get("code", [])
        w = data.get("worst") or {}
        self.worst = w if w.get("agent_id") else None
        self.worst_agent = self.by_id.get(self.worst["agent_id"]) if self.worst else None

    def is_stand(self, t):
        return t in self.stand

    def is_missing(self, c):
        return c.get("source") == "missing" or int(c.get("agents", 0)) == 0

    def agents_of(self, t):
        return [a for a in self.agents if a["agent_type"] == t]

    def top_cat(self):
        live = [c for c in self.cats if not self.is_missing(c) and int(c.get("risky", 0)) > 0]
        if not live:
            return None
        return max(live, key=lambda c: (float(c.get("risky_share", 0)), int(c.get("risky", 0))))

    def answer(self):
        return "%s of our agents are risky: %d of %d." % (pct(self.risky_share), self.risky_n, self.agents_n)

    def worst_paused_phrase(self):
        a = self.worst_agent
        if not a:
            return ""
        return "paused in %d of %d sessions, against a %s budget" % (a["paused"], a["sessions"], pct(self.budget))

    def nonrisky_near(self):
        """The agent that is not risky but is paused most often: how a normal agent looks."""
        pool = [a for a in self.agents if not a.get("risky")]
        if not pool:
            return None
        return max(pool, key=lambda a: (a.get("paused_share", 0), -a.get("sessions", 0)))

    def stand_names(self):
        return [c["agent_type"] for c in self.cats if self.is_stand(c["agent_type"])]

    def missing_names(self):
        return [c["agent_type"] for c in self.cats if self.is_missing(c)]


def wrap(text, width_chars, max_lines=3):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width_chars:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;") + "..."
    return lines


# ---------------------------------------------------------------- story.md
def build_story(f):
    L = []
    a = L.append
    a("# Fleet risk: %s" % f.answer())
    a("")
    if f.sample:
        a("> **%s.** Made-up values in the contract's schema, for development only. Real runs replace them." % TAG)
        a("")
    a("Synthetic data only. The system flags agents and actions worth a human look; a person decides.")
    a("")
    a("## 1. The answer")
    a("")
    a("**%s** Risky means: %s." % (f.answer(), f.rule or "paused share above the budget by more than chance"))
    a("")
    a("## 2. Where")
    a("")
    top = f.top_cat()
    if top:
        a("Risky share is highest in **%s**: %s (%d of %d agents)." % (
            top["agent_type"], pct(top["risky_share"]), top["risky"], top["agents"]))
    else:
        a("No category has a risky agent.")
    a("")
    a("| Category | Agents | Risky | Risky share | Sessions scored | Source |")
    a("|---|---:|---:|---:|---:|---|")
    for c in f.cats:
        t = c["agent_type"]
        if f.is_missing(c):
            a("| %s | 0 | 0 | n/a | 0 | no logs yet |" % t)
            continue
        src = "stand-in (simulated)" if f.is_stand(t) else str(c.get("source", "")).replace("_", " ")
        a("| %s | %d | %d | %s | %d | %s |" % (t, c["agents"], c["risky"], pct(c["risky_share"]),
                                                c.get("sessions_scored", 0), src))
    a("")
    if f.stand_names():
        a("Stand-in categories (%s) come from a simulator built from each category's actions, not from the team's logs. "
          "The team's real logs replace them without code changes." % ", ".join(f.stand_names()))
        a("")
    if f.missing_names():
        a("Not built yet, shown empty: %s." % ", ".join(f.missing_names()))
        a("")
    a("## 3. The worst agent")
    a("")
    if f.worst_agent:
        w = f.worst_agent
        mark = " (a stand-in category)" if f.is_stand(w["agent_type"]) else ""
        a("**%s**, in the %s category%s, was %s." % (w["agent_id"], w["agent_type"], mark, f.worst_paused_phrase()))
        a("")
        a("That is %s of its sessions paused (p-value %s)." % (pct(w["paused_share"]), "%.2g" % float(w.get("p_value", 0))))
    else:
        a("No agent met the risky rule, so there is no worst agent.")
    a("")
    a("## 4. Why")
    a("")
    if f.worst:
        for i, r in enumerate(f.worst.get("why", []), 1):
            a("%d. %s." % (i, str(r).rstrip(".")))
        a("")
        w = f.worst_agent
        if w:
            acts = ", ".join("`%s` (%d)" % (n, c) for n, c in w.get("top_paused_actions", []) if c > 0)
            if acts:
                a("Actions most often paused: %s." % acts)
                a("")
            fl = ", ".join("`%s` (%d)" % (n, c) for n, c in w.get("flags", []))
            if fl:
                a("Flags that fired: %s." % fl)
                a("")
            a("Policy checks skipped: %d." % w.get("policy_skips", 0))
            a("")
            ex = w.get("examples", [])[:3]
            if ex:
                a("Three example paused steps (no customer message text is ever shown):")
                a("")
                a("| Session | Step | Action | Risk | Reason |")
                a("|---|---:|---|---:|---|")
                for e in ex:
                    a("| %s | %s | `%s` | %s | %s |" % (e.get("session_id"), e.get("step"), e.get("action"),
                                                       e.get("risk"), e.get("reason")))
                a("")
    a("## 5. So what")
    a("")
    if f.worst_agent:
        w = f.worst_agent
        acts = ", ".join(["`%s`" % n for n, c in w.get("top_paused_actions", []) if c > 0][:3]) or "its privileged actions"
        rest = f.risky_n - 1
        tail = (" The same steps apply to the other %d risky %s, starting in %s." % (
            rest, plural(rest, "agent"), top["agent_type"])) if rest > 0 and top else ""
        a("Hold %s's privileged actions (%s) for review, send its %d paused %s to a person, and retest it after a fix. "
          "The goal is to catch the risky move before the money moves.%s" % (
              w["agent_id"], acts, w["paused"], plural(w["paused"], "session"), tail))
    else:
        a("Nothing needs holding. Keep the pause budget at %s and keep scoring new sessions." % pct(f.budget))
    a("")
    a("## 6. The check")
    a("")
    r = f.reveal
    if r:
        a("We opened the sealed answers: %d of %d flagged agents really were the weaker ones, and %s of the weaker agents "
          "were caught (%d of %d)." % (r.get("flagged_and_weaker", 0), r.get("flagged", 0), pct(r.get("recall", 0)),
                                       r.get("flagged_and_weaker", 0), r.get("weaker_total", 0)))
        a("")
        a("Precision %s, recall %s. The labels were opened only after every agent was scored." % (
            pct(r.get("precision", 0)), pct(r.get("recall", 0))))
    else:
        a("The sealed answers have not been opened for this run.")
    a("")
    a("## 7. The code")
    a("")
    if not f.code:
        a("No code snippets in this data file.")
    for c in f.code:
        a("**%s**: `%s`, lines %s to %s" % (c.get("what"), c.get("file"), c.get("start"), c.get("end")))
        a("")
        a("```python")
        start = int(c.get("start", 1))
        for i, line in enumerate(str(c.get("snippet", "")).splitlines()):
            a("%4d  %s" % (start + i, line))
        a("```")
        a("")
    a("---")
    a("")
    a("Generated by `docs/fleet/render.py` from `%s`. Every number above is read from that file." % (
        os.path.relpath(f.src, os.path.dirname(DOCS))))
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------- SVG diagram
def svg_text(x, y, s, size=28, weight=500, fill=INK, anchor="start", extra=""):
    return ('<text x="%s" y="%s" font-size="%s" font-weight="%s" fill="%s" text-anchor="%s"%s>%s</text>'
            % (x, y, size, weight, fill, anchor, extra, esc(s)))


def build_svg(f):
    W, H = 1920, 1080
    o = []
    a = o.append
    n = max(1, len(f.cats))
    row_h = min(68, int(680 / n))
    y0 = 196
    rows_bottom = y0 + row_h * n
    x_row0, x_row1 = 64, 1180
    x_dots0, x_dots1 = 300, 760
    maxa = max([int(c.get("agents", 0)) for c in f.cats] + [1])
    spacing = min(48, (x_dots1 - x_dots0) / maxa)
    r_dot = min(17, int(spacing * 0.36))

    title = "Fleet risk by category: %s" % f.answer()
    desc = ("One row per category and one dot per agent; risky agents are copper. %s. Worst agent: %s." % (
        f.answer(), (f.worst["agent_id"] if f.worst else "none")) + (" " + TAG + "." if f.sample else ""))
    a('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" '
      'aria-labelledby="fd-title fd-desc" font-family="%s">' % (W, H, W, H, FONT))
    a('<title id="fd-title">%s</title><desc id="fd-desc">%s</desc>' % (esc(title), esc(desc)))
    a('<defs><pattern id="hatch" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
      '<rect width="12" height="12" fill="%s"/><rect width="4" height="12" fill="%s"/></pattern></defs>' % (SURFACE, TINT_NAVY))
    a('<rect width="%d" height="%d" fill="%s"/>' % (W, H, PAPER))
    # header
    a('<g id="head" data-beat="0">')
    a(svg_text(64, 88, f.answer(), 52, 800, NAVY))
    top = f.top_cat()
    sub = "One row per category, one dot per agent."
    if top:
        sub += " Highest risky share: %s, %s." % (top["agent_type"], pct(top["risky_share"]))
    a(svg_text(64, 140, sub, 28, 500, MUTED))
    if f.sample:
        a('<g id="sample-tag"><rect x="1496" y="44" width="360" height="52" rx="26" fill="%s" stroke="%s" stroke-width="2"/>'
          % (TINT_CAUTION, CAUTION) + svg_text(1676, 80, TAG, 28, 700, CAUTION, "middle") + '</g>')
    a('</g>')
    # rows
    a('<g id="rows" data-beat="1" data-enter="rise">')
    worst_id = f.worst["agent_id"] if f.worst else None
    for i, c in enumerate(f.cats):
        t = c["agent_type"]
        y = y0 + i * row_h
        cy = y + row_h / 2
        missing = f.is_missing(c)
        stand = f.is_stand(t)
        ry, rh = y + 4, row_h - 8
        fill = "url(#hatch)" if stand and not missing else SURFACE
        dash = ' stroke-dasharray="8 6"' if (stand or missing) else ""
        stroke = LINE_STRONG if (stand or missing) else LINE
        a('<g id="row-%s">' % esc(t))
        a('<rect x="%d" y="%s" width="%d" height="%s" rx="10" fill="%s" stroke="%s" stroke-width="2"%s/>'
          % (x_row0, ry, x_row1 - x_row0, rh, fill, stroke, dash))
        a(svg_text(x_row0 + 20, cy + 10, t, 28, 700, NAVY))
        if missing:
            a(svg_text(x_dots0, cy + 10, "no logs yet", 28, 500, MUTED, extra=' font-style="italic"'))
            a(svg_text(790, cy + 10, "n/a", 28, 700, MUTED))
            a('<rect x="1000" y="%s" width="164" height="40" rx="20" fill="%s" stroke="%s" stroke-width="2" stroke-dasharray="6 5"/>'
              % (cy - 20, SURFACE, LINE_STRONG) + svg_text(1082, cy + 10, "no logs", 28, 600, MUTED, "middle"))
        else:
            ag = f.agents_of(t)
            for j, g in enumerate(ag):
                cx = x_dots0 + r_dot + 8 + j * spacing
                risky = bool(g.get("risky"))
                col = COPPER if risky else NAVY
                if g["agent_id"] == worst_id:
                    a('<circle cx="%.1f" cy="%s" r="%d" fill="none" stroke="%s" stroke-width="4"/>' % (cx, cy, r_dot + 7, COPPER))
                a('<circle cx="%.1f" cy="%s" r="%d" fill="%s"><title>%s: paused %s of %s sessions</title></circle>'
                  % (cx, cy, r_dot, col, esc(g["agent_id"]), g.get("paused"), g.get("sessions")))
            share = pct(c.get("risky_share", 0))
            col = COPPER if int(c.get("risky", 0)) > 0 else NAVY
            a(svg_text(790, cy + 10, share, 28, 800, col))
            a(svg_text(878, cy + 10, "%d of %d" % (c.get("risky", 0), c.get("agents", 0)), 28, 500, MUTED))
            if stand:
                a('<rect x="1000" y="%s" width="164" height="40" rx="20" fill="%s" stroke="%s" stroke-width="2" stroke-dasharray="6 5"/>'
                  % (cy - 20, SURFACE, LINE_STRONG) + svg_text(1082, cy + 10, "stand-in", 28, 600, MUTED, "middle"))
        a('</g>')
    a('</g>')
    # worst card
    cx0, cx1 = 1228, 1856
    a('<g id="worst-card" data-beat="2" data-enter="pop" data-focal="true">')
    a('<rect x="%d" y="%d" width="%d" height="%d" rx="16" fill="%s" stroke="%s" stroke-width="3"/>'
      % (cx0, y0, cx1 - cx0, rows_bottom - y0, SURFACE, COPPER))
    px = cx0 + 32
    if f.worst_agent:
        w = f.worst_agent
        a(svg_text(px, y0 + 52, "Worst agent", 28, 600, MUTED))
        a(svg_text(px, y0 + 108, w["agent_id"], 44, 800, NAVY))
        catline = w["agent_type"] + (", stand-in category" if f.is_stand(w["agent_type"]) else "")
        a(svg_text(px, y0 + 148, catline, 28, 500, MUTED))
        a(svg_text(px, y0 + 244, pct(w["paused_share"]), 96, 800, COPPER))
        a(svg_text(px + 232, y0 + 218, "paused", 28, 600, INK))
        a(svg_text(px + 232, y0 + 254, "%d of %d sessions" % (w["paused"], w["sessions"]), 28, 500, MUTED))
        # bar vs budget
        bx0, bx1 = px, cx1 - 32
        scale = max(float(w["paused_share"]), f.budget * 2) * 1.15
        by = y0 + 330
        bw = bx1 - bx0
        a('<rect x="%d" y="%d" width="%d" height="36" rx="8" fill="%s"/>' % (bx0, by, bw, TINT_NAVY))
        fw = max(6, bw * float(w["paused_share"]) / scale)
        a('<rect x="%d" y="%d" width="%.1f" height="36" rx="8" fill="%s"/>' % (bx0, by, fw, COPPER))
        mx = bx0 + bw * f.budget / scale
        a('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="4" stroke-dasharray="8 5"/>'
          % (mx, by - 14, mx, by + 50, NAVY))
        a(svg_text(mx + 10, by + 82, "%s budget" % pct(f.budget), 28, 700, NAVY))
        # reasons
        ry_ = by + 134
        a('<g id="reasons">')
        reasons = (f.worst.get("why") or [])[1:3]
        for r in reasons:
            lines = wrap(r, 33, 3)
            a('<circle cx="%d" cy="%d" r="8" fill="%s"/>' % (px + 8, ry_ - 9, COPPER))
            for k, ln in enumerate(lines):
                a(svg_text(px + 30, ry_ + k * 34, ln, 28, 500, INK))
            ry_ += 34 * len(lines) + 18
        a('</g>')
    else:
        a(svg_text(px, y0 + 70, "No agent met the risky rule", 36, 700, NAVY))
    a('</g>')
    # legend
    ly = rows_bottom + 64
    a('<g id="legend">')
    x = 64

    def item(x, draw, label, width, dx=40):
        a(draw)
        a(svg_text(x + dx, ly + 10, label, 28, 500, INK))
        return x + dx + width + 48
    x = item(x, '<circle cx="%d" cy="%d" r="15" fill="%s"/>' % (x + 15, ly, NAVY), "normal agent", 190)
    x = item(x, '<circle cx="%d" cy="%d" r="15" fill="%s"/>' % (x + 15, ly, COPPER), "risky agent", 170)
    x = item(x, '<circle cx="%d" cy="%d" r="15" fill="%s"/><circle cx="%d" cy="%d" r="22" fill="none" stroke="%s" stroke-width="4"/>'
             % (x + 18, ly, COPPER, x + 18, ly, COPPER), "worst agent", 190, 56)
    x += 8
    x = item(x, '<rect x="%d" y="%d" width="32" height="32" rx="6" fill="url(#hatch)" stroke="%s" stroke-width="2" stroke-dasharray="6 4"/>'
             % (x, ly - 16, LINE_STRONG), "stand-in, simulated", 300)
    x = item(x, '<rect x="%d" y="%d" width="32" height="32" rx="6" fill="%s" stroke="%s" stroke-width="2" stroke-dasharray="6 4"/>'
             % (x, ly - 16, SURFACE, LINE_STRONG), "no logs yet", 180)
    a('</g>')
    note = ("Risky means paused far more often than the %s budget allows by chance. Rule: %s. Synthetic data only."
            % (pct(f.budget), f.rule.split(": ", 1)[-1] if f.rule else "see fleet.json"))
    if f.sample:
        note = TAG + ". " + note
    for k, ln in enumerate(wrap(note, 118, 2)):
        a(svg_text(64, ly + 70 + k * 36, ln, 28, 500, MUTED))
    a('</svg>')
    return "\n".join(o)


# ---------------------------------------------------------------- shared CSS
BASE_CSS = """
:root{--navy:#0A2942;--copper:#A8481B;--ink:#14212F;--paper:#F5F7FA;--surface:#FFFFFF;--line:#D5DCE5;--line-strong:#77859A;
--muted:#4C5A6B;--tint-navy:#E6ECF3;--tint-copper:#FBEEE3;--caution:#8A5B00;--tint-caution:#FBF2DC;--hero:#0A2942;--hero-ink:#F2F6FA;--hero-muted:#B9C8DA;--hero-accent:#F0A27A;
--font:"Public Sans","Benton Sans","Helvetica Neue",system-ui,-apple-system,"Segoe UI",Arial,sans-serif;
--mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--navy:#A9C2E3;--copper:#EE9264;--ink:#E8EDF3;--paper:#081420;--surface:#0E2238;--line:#22384F;
--line-strong:#6A82A0;--muted:#9DAEC0;--tint-navy:#13304F;--tint-copper:#33231A;--caution:#E3B341;--tint-caution:#2F2610;--hero:#0E2238}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;font-size:18px}
@media (min-width:1400px){html{font-size:22px}}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--font);line-height:1.5;overflow-x:hidden}
.wrap{width:100%;max-width:1760px;margin:0 auto;padding:0 16px}
@media (min-width:700px){.wrap{padding:0 32px}}
h1,h2,h3{line-height:1.12;letter-spacing:-.015em;margin:0}
p{margin:0 0 .6em}
.kicker{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;font-weight:700;color:var(--copper);margin:0 0 .5rem}
.sample-tag{display:inline-block;font-weight:700;font-size:.8rem;letter-spacing:.03em;color:var(--caution);background:var(--tint-caution);
border:2px solid var(--caution);border-radius:999px;padding:.2rem .8rem;margin:0 0 .8rem}
.small{font-size:.82rem;color:var(--muted)}
.id{overflow-wrap:anywhere;font-weight:700}
"""


# ---------------------------------------------------------------- diagram html
def dot_html(cls=""):
    return '<span class="dot %s"></span>' % cls


def build_diagram_html(f, svg):
    worst_id = f.worst["agent_id"] if f.worst else None
    rows = []
    for c in f.cats:
        t = c["agent_type"]
        missing, stand = f.is_missing(c), f.is_stand(t)
        tag = '<span class="tag">no logs yet</span>' if missing else ('<span class="tag">stand-in</span>' if stand else "")
        if missing:
            dots, stat = '<span class="small"><i>no logs yet</i></span>', "n/a"
        else:
            dots = "".join(dot_html(("r" if g.get("risky") else "") + (" w" if g["agent_id"] == worst_id else ""))
                           for g in f.agents_of(t))
            stat = "%s <small>%d of %d</small>" % (pct(c.get("risky_share", 0)), c.get("risky", 0), c.get("agents", 0))
        cls = "mrow" + (" stand" if stand and not missing else "") + (" empty" if missing else "")
        rows.append('<li class="%s"><div class="mtop"><b>%s</b>%s<span class="stat">%s</span></div><div class="dots">%s</div></li>'
                    % (cls, esc(t), tag, stat, dots))
    wcard = ""
    if f.worst_agent:
        w = f.worst_agent
        scale = max(float(w["paused_share"]), f.budget * 2) * 1.15
        why = "".join("<li>%s</li>" % esc(r) for r in (f.worst.get("why") or [])[1:3])
        wcard = ('<div class="wcard"><p class="small">Worst agent</p><h2 class="id">%s</h2><p class="small">%s</p>'
                 '<p class="big">%s <small>paused: %d of %d sessions</small></p>'
                 '<div class="bar"><i style="width:%.1f%%"></i><u style="left:%.1f%%"></u></div>'
                 '<p class="bl" style="padding-left:%.1f%%">%s budget</p><ul>%s</ul></div>') % (
            esc(w["agent_id"]), esc(w["agent_type"] + (", stand-in category" if f.is_stand(w["agent_type"]) else "")),
            pct(w["paused_share"]), w["paused"], w["sessions"], 100 * float(w["paused_share"]) / scale,
            100 * f.budget / scale, 100 * f.budget / scale, pct(f.budget), why)
    tag = '<p class="sample-tag">%s</p>' % TAG if f.sample else ""
    css = BASE_CSS + """
.page{padding:28px 0 40px}
h1{font-size:clamp(1.6rem,6vw,2.4rem);color:var(--navy);font-weight:800;margin-bottom:.5rem}
.wide{display:none}
.wide svg{display:block;width:100%;height:auto;border:2px solid var(--line);border-radius:12px}
.narrow{display:block}
@media (min-width:900px){.wide{display:block}.narrow{display:none}}
.mlist{list-style:none;margin:1rem 0;padding:0;display:grid;gap:10px}
.mrow{background:var(--surface);border:2px solid var(--line);border-radius:12px;padding:12px 14px}
.mrow.stand{background-image:repeating-linear-gradient(45deg,transparent 0 6px,var(--tint-navy) 6px 9px);border:2px dashed var(--line-strong)}
.mrow.empty{border:2px dashed var(--line-strong);background:transparent}
.mtop{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.mtop b{color:var(--navy)}
.stat{margin-left:auto;font-weight:800;color:var(--ink)}.stat small{font-weight:500;color:var(--muted)}
.tag{font-size:.72rem;font-weight:600;color:var(--muted);border:2px dashed var(--line-strong);border-radius:999px;padding:0 .6rem;background:var(--surface)}
.dots{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;min-height:24px}
.dot{width:24px;height:24px;border-radius:50%;background:var(--navy);display:inline-block}
.dot.r{background:var(--copper)}.dot.w{box-shadow:0 0 0 3px var(--surface),0 0 0 6px var(--copper);margin:0 4px}
.wcard{background:var(--surface);border:3px solid var(--copper);border-radius:14px;padding:16px;margin:1rem 0}
.wcard h2{font-size:1.5rem;color:var(--navy)}
.big{font-size:2.6rem;font-weight:800;color:var(--copper);margin:.2rem 0 .6rem;line-height:1}
.big small{font-size:.9rem;font-weight:500;color:var(--muted);display:block;margin-top:.3rem}
.bar{position:relative;height:22px;border-radius:6px;background:var(--tint-navy)}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--copper);border-radius:6px;display:block}
.bar u{position:absolute;top:-6px;bottom:-6px;border-left:3px dashed var(--navy);text-decoration:none}
.bl{font-size:.82rem;font-weight:700;color:var(--navy);margin:.5rem 0}
.wcard ul{padding-left:1.1rem;margin:.6rem 0 0}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center;font-size:.85rem;margin:1rem 0}
.legend span{display:inline-flex;align-items:center;gap:8px}
"""
    legend = ('<p class="legend"><span>%s normal agent</span><span>%s risky agent</span><span>%s worst agent</span>'
              '<span><span class="tag">stand-in</span> simulated, not the team\'s logs</span></p>') % (
        dot_html(), dot_html("r"), dot_html("r w"))
    rule = "Risky means paused far more often than the %s budget allows by chance. Rule: %s." % (
        pct(f.budget), esc(f.rule.split(": ", 1)[-1] if f.rule else "see fleet.json"))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light dark">'
            '<title>Fleet risk diagram</title><script>if(location.hash==="#light")document.documentElement.setAttribute("data-theme","light")</script><style>%s</style></head><body><main class="wrap page">'
            '<p class="kicker">Sundai Bank, fleet risk</p>%s<h1>%s</h1>'
            '<div class="wide">%s</div>'
            '<div class="narrow"><ul class="mlist">%s</ul>%s%s</div>'
            '<p class="small">%s Synthetic data only. <a href="explainer.html">The same answer in five pictures</a>.</p>'
            '</main></body></html>') % (css, tag, esc(f.answer()), svg, "".join(rows), wcard, legend, rule)


# ---------------------------------------------------------------- explainer html
def meter(share, scale, budget, cls="c"):
    return ('<div class="bar %s"><i style="width:%.1f%%"></i><u style="left:%.1f%%"></u></div>'
            '<p class="bl" style="padding-left:%.1f%%">%s budget</p>') % (
        cls, max(1.5, 100 * share / scale), 100 * budget / scale, min(60, 100 * budget / scale), pct(budget))


def panel(num, title, pic, say, cls=""):
    return ('<section class="panel %s"><div class="head"><span class="num">%d</span><h2>%s</h2></div>'
            '<div class="pic">%s</div><p class="say">%s</p></section>') % (cls, num, title, pic, say)


def build_explainer(f):
    tag = '<p class="sample-tag">%s</p>' % TAG if f.sample else ""
    top = f.top_cat()
    ncat = len([c for c in f.cats if not f.is_missing(c)])
    r = f.reveal
    w = f.worst_agent
    near = f.nonrisky_near()

    # hero strip
    chips = []
    for c in f.cats:
        t = c["agent_type"]
        if f.is_missing(c):
            chips.append('<li class="cchip empty"><b>%s</b><span>no logs yet</span></li>' % esc(t))
        else:
            cls = "cchip" + (" stand" if f.is_stand(t) else "") + (" hot" if c.get("risky", 0) else "")
            chips.append('<li class="%s"><b>%s</b><span>%d of %d risky, %s%s</span></li>' % (
                cls, esc(t), c.get("risky", 0), c.get("agents", 0), pct(c.get("risky_share", 0)),
                ", stand-in" if f.is_stand(t) else ""))

    # picture 1: what the risk team watches
    p1 = ('<div class="row"><div class="chip"><span>%d agents</span><small>in %d categories do their jobs</small></div>'
          '<span class="arrow">&rarr;</span>'
          '<div class="chip eye"><span>One risk agent</span><small>reads every step as it is logged</small></div>'
          '<span class="arrow">&rarr;</span>'
          '<div class="pills"><i>run</i><i>log</i><i>review</i><i class="p">pause</i></div></div>'
          '<div class="dots" aria-label="every agent in the fleet">%s</div>'
          '<p class="cap">Each dot is one agent. Copper dots are the %d flagged as risky.</p>') % (
        f.agents_n, ncat, "".join('<span class="dot%s"></span>' % (" r" if g.get("risky") else "") for g in f.agents), f.risky_n)
    s1 = "A separate risk agent watches each step before it runs. It flags agents and actions worth a human look. A person decides."

    # picture 2: how a session is paused
    boxes = 20
    pause_boxes = max(0, round(f.budget * boxes))
    sq = "".join('<i class="%s"></i>' % ("p" if k < pause_boxes else "") for k in range(boxes))
    p2 = ('<div class="row steps"><div class="chip"><b>1</b><span>Learn normal</span><small>from normal sessions only</small></div>'
          '<span class="arrow">&rarr;</span>'
          '<div class="chip"><b>2</b><span>Set the line</span><small>at most %s of normal sessions pause</small></div>'
          '<span class="arrow">&rarr;</span>'
          '<div class="chip warn"><b>3</b><span>Score new ones</span><small>over the line means pause</small></div></div>'
          '<div class="squares" aria-label="%d of %d normal sessions paused">%s</div>'
          '<p class="cap">Out of %d normal sessions, at most %d %s paused.</p>') % (
        pct(f.budget), pause_boxes, boxes, sq, boxes, pause_boxes, plural(pause_boxes, "is", "are"))
    s2 = "A session is paused when a step looks too unusual for the damage it could do. The line is set so normal sessions rarely hit it."

    # picture 3: what makes an agent risky
    scale = max([float(x.get("paused_share", 0)) for x in (near, w) if x] + [f.budget * 2]) * 1.15
    bars = ""
    if near:
        bars += ('<div class="mrow"><p class="mlabel"><span class="id">%s</span>: not risky<small>paused %d of %d sessions</small></p>%s</div>'
                 % (esc(near["agent_id"]), near["paused"], near["sessions"], meter(float(near["paused_share"]), scale, f.budget, "n")))
    if w:
        bars += ('<div class="mrow"><p class="mlabel"><span class="id">%s</span>: risky<small>paused %d of %d sessions</small></p>%s</div>'
                 % (esc(w["agent_id"]), w["paused"], w["sessions"], meter(float(w["paused_share"]), scale, f.budget, "c")))
    if not bars:
        bars = '<p class="cap">No agents to compare yet.</p>'
    rule = esc(f.rule.split(": ", 1)[-1]) if f.rule else "paused far above the budget"
    p3 = bars + '<div class="rulebox"><b>The rule</b><span>%s</span></div>' % rule
    s3 = "A few pauses are bad luck: even a normal agent gets paused now and then. An agent is risky only when it is paused far more than luck allows." 

    # picture 4: worst and why
    if w:
        why = (f.worst.get("why") or [])
        reasons = "".join('<li><b>%d</b><span>%s</span></li>' % (i, esc(x)) for i, x in enumerate(why, 1))
        acts = [x for x in w.get("top_paused_actions", []) if x[1] > 0][:3]
        amax = max([c for _, c in acts] + [1])
        abars = "".join('<div class="abar"><span class="mono">%s</span><i style="width:%.0f%%"></i><em>%d</em></div>'
                        % (esc(n), 100 * c / amax, c) for n, c in acts)
        stand_note = " (stand-in category)" if f.is_stand(w["agent_type"]) else ""
        p4 = ('<div class="big">%s<small>of <span class="id">%s</span> sessions paused (%d of %d)</small></div>%s'
              '<ol class="why">%s</ol>%s') % (
            pct(w["paused_share"]), esc(w["agent_id"]), w["paused"], w["sessions"],
            meter(float(w["paused_share"]), max(float(w["paused_share"]), f.budget * 2) * 1.15, f.budget, "c"),
            reasons, ('<p class="cap">Actions most often paused</p>' + abars) if abars else "")
        t4 = '<span class="id">%s</span> is the worst agent%s' % (esc(w["agent_id"]), esc(stand_note))
        s4 = ("It is in the %s category, and it is paused more than any other risky agent. These are the reasons, with counts."
              % esc(w["agent_type"]))
    else:
        p4 = '<p class="cap">No agent met the risky rule in this run.</p>'
        t4, s4 = "No agent is the worst", "Nothing crossed the rule."

    # picture 5: sealed answers
    if r:
        fl, fw, wt = int(r.get("flagged", 0)), int(r.get("flagged_and_weaker", 0)), int(r.get("weaker_total", 0))
        d1 = "".join('<span class="dot r"></span>' if k < fw else '<span class="dot hollow"></span>' for k in range(fl))
        d2 = "".join('<span class="dot r"></span>' if k < fw else '<span class="dot miss"></span>' for k in range(wt))
        p5 = ('<div class="two"><div><p class="mlabel">Flagged by us<small>%d agents</small></p><div class="dots">%s</div>'
              '<p class="cap"><b>%d of %d</b> really were the weaker ones (precision %s)</p></div>'
              '<div><p class="mlabel">Weaker agents in the fleet<small>%d agents</small></p><div class="dots">%s</div>'
              '<p class="cap"><b>%d of %d</b> were caught (recall %s)</p></div></div>'
              '<p class="small">Filled copper: flagged and really weaker. Hollow: flagged but not weaker. Dashed: weaker but missed.</p>') % (
            fl, d1, fw, fl, pct(r.get("precision", 0)), wt, d2, fw, wt, pct(r.get("recall", 0)))
    else:
        p5 = '<p class="cap">The sealed answers have not been opened for this run.</p>'
    s5 = "Only after every agent was scored did we open the sealed answers and compare. The labels never touched the scoring."

    where = ""
    if top:
        where = "Highest in <b>%s</b>: %s of its agents (%d of %d)." % (esc(top["agent_type"]), pct(top["risky_share"]), top["risky"], top["agents"])
    notes = []
    if f.stand_names():
        notes.append("Dashed categories are stand-ins from a simulator, not the team's logs: %s." % esc(", ".join(f.stand_names())))
    if f.missing_names():
        notes.append("No logs yet: %s." % esc(", ".join(f.missing_names())))
    css = BASE_CSS + """
.hero{background:var(--hero);color:var(--hero-ink);padding:28px 0 30px}
.hero .kicker{color:var(--hero-accent)}
.hero h1{font-size:clamp(1.9rem,7.4vw,3.6rem);font-weight:800;letter-spacing:-.025em;max-width:32ch}
.hero .lede{color:var(--hero-muted);font-size:1.05rem;max-width:62ch;margin:.8rem 0 0}
.hero .sample-tag{background:var(--tint-caution);}
.strip{list-style:none;margin:1.1rem 0 0;padding:0;display:flex;flex-wrap:wrap;gap:8px}.strip li{flex:1 1 9rem}@media (min-width:700px){.strip li{flex:0 1 auto}}
.cchip{background:var(--surface);color:var(--ink);border:2px solid var(--line);border-radius:10px;padding:.35rem .7rem;font-size:.8rem;display:flex;flex-direction:column;min-width:0}
.cchip b{color:var(--navy)}.cchip span{color:var(--muted)}
.cchip.hot{border-color:var(--copper)}
.cchip.stand{border-style:dashed;border-color:var(--line-strong)}.cchip.empty{background:transparent;border-style:dashed;border-color:var(--hero-muted);color:var(--hero-muted)}
.cchip.empty b,.cchip.empty span{color:var(--hero-muted)}
.grid{display:grid;gap:18px;grid-template-columns:minmax(0,1fr);padding:22px 0 8px}
@media (min-width:980px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.full{grid-column:1/-1}}
.panel{background:var(--surface);border:2px solid var(--line);border-radius:14px;padding:18px 16px 20px;min-width:0}
@media (min-width:700px){.panel{padding:24px 28px 26px}}
.panel{display:flex;flex-direction:column}
.panel.warn{border-color:var(--copper)}
.head{display:flex;align-items:center;gap:14px;margin-bottom:16px}
.num{width:2.6rem;height:2.6rem;flex:none;border-radius:50%;background:var(--navy);color:var(--surface);font-weight:800;font-size:1.3rem;display:flex;align-items:center;justify-content:center}
.panel.warn .num{background:var(--copper)}
h2{font-size:clamp(1.15rem,4.4vw,1.6rem);color:var(--navy);font-weight:800}
.pic{display:flex;flex-direction:column;gap:12px;min-height:7rem;justify-content:center;flex:1}
.say{font-size:1rem;margin:16px 0 0;color:var(--ink)}
.row{display:flex;flex-wrap:wrap;align-items:stretch;gap:10px}
.chip{flex:1 1 9rem;min-width:0;border:3px solid var(--navy);background:var(--tint-navy);border-radius:12px;padding:10px;text-align:center;
display:flex;flex-direction:column;justify-content:center;gap:2px;font-weight:700;color:var(--navy)}
.chip small{font-weight:500;color:var(--muted);font-size:.78rem;line-height:1.2}
.chip b{font-size:1.3rem}
.chip.warn{border-color:var(--copper);background:var(--tint-copper);color:var(--copper)}.chip.warn small{color:var(--copper)}
.arrow{align-self:center;font-weight:800;font-size:1.4rem;color:var(--line-strong)}
@media (max-width:640px){.arrow{display:none}}
.pills{flex:1 1 9rem;display:flex;flex-wrap:wrap;gap:6px;align-items:center;justify-content:center}
.pills i{font-style:normal;font-weight:700;font-size:.78rem;letter-spacing:.04em;text-transform:uppercase;border:2px solid var(--navy);color:var(--navy);padding:3px 10px;border-radius:999px}
.pills i.p{background:var(--copper);border-color:var(--copper);color:#fff}
.squares{display:grid;grid-template-columns:repeat(10,1fr);gap:5px;max-width:26rem}
.squares i{display:block;aspect-ratio:1;border-radius:5px;background:var(--tint-navy);border:2px solid var(--navy)}
.squares i.p{background:var(--copper);border-color:var(--copper)}
.cap{font-size:.9rem;color:var(--muted);margin:0}.cap b{color:var(--ink)}
.mrow{display:grid;gap:6px}
.mlabel{font-size:.95rem;font-weight:700;color:var(--navy);margin:0}.mlabel small{display:block;font-weight:500;color:var(--muted);font-size:.8rem}
.bar{position:relative;height:1.5rem;border-radius:6px;background:var(--tint-navy)}
.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:6px;display:block}
.bar.c i{background:var(--copper)}.bar.n i{background:var(--navy)}
.bar u{position:absolute;top:-6px;bottom:-6px;border-left:3px dashed var(--navy);text-decoration:none}
.bl{font-size:.8rem;font-weight:700;color:var(--navy);margin:2px 0 0}
.big{font-size:clamp(2.4rem,9vw,3.4rem);font-weight:800;color:var(--copper);line-height:1}
.big small{display:block;font-size:.9rem;font-weight:500;color:var(--muted);margin-top:.35rem;line-height:1.3}
.why{list-style:none;margin:6px 0 0;padding:0;display:grid;gap:8px}
.why li{display:flex;gap:10px;align-items:flex-start;border:2px solid var(--copper);background:var(--tint-copper);border-radius:10px;padding:8px 10px;font-weight:600;font-size:.92rem}
.why li b{flex:none;width:1.5rem;height:1.5rem;border-radius:50%;background:var(--copper);color:#fff;display:flex;align-items:center;justify-content:center;font-size:.85rem}
.why li span{min-width:0;overflow-wrap:anywhere}
.abar{display:grid;grid-template-columns:minmax(0,9.5rem) 1fr auto;align-items:center;gap:8px;font-size:.82rem;margin-top:4px}
.abar span{overflow-wrap:anywhere}.abar i{display:block;height:.9rem;border-radius:4px;background:var(--copper)}.abar em{font-style:normal;font-weight:700}
.mono{font-family:var(--mono)}
.rulebox{border:2px solid var(--navy);background:var(--tint-navy);border-radius:10px;padding:10px 12px;display:grid;gap:2px;font-size:.9rem}.rulebox b{color:var(--navy)}
.two{display:grid;gap:18px;grid-template-columns:minmax(0,1fr)}
@media (min-width:700px){.two{grid-template-columns:repeat(2,minmax(0,1fr))}}
.dots{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.dot{width:1.4rem;height:1.4rem;border-radius:50%;background:var(--navy);display:inline-block}
.dot.r{background:var(--copper)}.dot.hollow{background:transparent;border:3px solid var(--copper)}.dot.miss{background:transparent;border:3px dashed var(--navy)}
footer{padding:12px 0 36px}
a{color:var(--navy)}
"""
    body = ('<header class="hero"><div class="wrap"><p class="kicker">Sundai Bank &middot; fleet risk</p>%s'
            '<h1>%s</h1><p class="lede">%s Across %d categories. Synthetic data only: nothing here touches a real bank, customer or card.</p>'
            '<ul class="strip">%s</ul></div></header>'
            '<main class="wrap"><div class="grid">%s%s%s%s%s</div></main>'
            '<footer class="wrap"><p class="small">%s Every number on this page is read from <code>fleet.json</code>. '
            'The system flags agents and actions worth a human look; it does not replace one. '
            '<a href="fleet-diagram.html">See every category as dots</a>.</p></footer>') % (
        tag, esc(f.answer()), where, ncat, "".join(chips),
        panel(1, "A separate risk agent watches every step", p1, s1),
        panel(2, "A session is paused when a step looks too unusual", p2, s2, "warn"),
        panel(3, "An agent is risky when it is paused far more than luck allows", p3, s3),
        panel(4, t4, p4, s4, "warn"),
        panel(5, "We checked against the sealed answers", p5, s5, "full"),
        esc(" ".join(notes)))
    script = ("<script>addEventListener('load',function(){document.documentElement.setAttribute('data-h',"
              "String(Math.ceil(document.documentElement.scrollHeight)))})</script>")
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light dark">'
            '<title>Fleet risk in five pictures</title><script>if(location.hash==="#light")document.documentElement.setAttribute("data-theme","light")</script><style>%s</style></head><body>%s%s</body></html>') % (css, body, script)


# ---------------------------------------------------------------- PNGs
def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    return struct.unpack(">II", head[16:24])


def run_chrome(args, timeout=90):
    prof = tempfile.mkdtemp(prefix="fleet-chrome-")
    try:
        return subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                               "--user-data-dir=" + prof] + args,
                              capture_output=True, text=True, timeout=timeout)
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def make_pngs():
    if not os.path.exists(CHROME):
        print("Chrome not found, skipping PNGs: " + CHROME, file=sys.stderr)
        return False
    ex_html = os.path.join(HERE, "explainer.html")
    out = run_chrome(["--window-size=1920,1080", "--dump-dom", "file://" + ex_html + "#light"]).stdout
    m = re.search(r'data-h="(\d+)"', out)
    h = (int(m.group(1)) if m else 3000) + 16
    run_chrome(["--window-size=1920,%d" % h, "--screenshot=" + os.path.join(HERE, "explainer.png"), "file://" + ex_html + "#light"])
    run_chrome(["--window-size=1920,1080", "--screenshot=" + os.path.join(HERE, "fleet-diagram.png"),
                "file://" + os.path.join(HERE, "fleet-diagram.svg")])
    for n in ("explainer.png", "fleet-diagram.png"):
        p = os.path.join(HERE, n)
        print("%s %s" % (n, "x".join(map(str, png_size(p))) if os.path.exists(p) else "MISSING"))
    return True


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fleet_json", nargs="?")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--png-only", action="store_true")
    args = ap.parse_args()
    if args.png_only:
        make_pngs()
        return
    path = args.fleet_json or (REAL if os.path.exists(REAL) else SAMPLE)
    with open(path) as fh:
        data = json.load(fh)
    f = Fleet(data, path)
    svg = build_svg(f)
    outputs = {
        "story.md": build_story(f),
        "fleet-diagram.svg": svg + "\n",
        "fleet-diagram.html": build_diagram_html(f, svg),
        "explainer.html": build_explainer(f),
    }
    for name, text in outputs.items():
        with open(os.path.join(HERE, name), "w") as fh:
            fh.write(text)
    print("rendered from %s (sample=%s)" % (os.path.relpath(path, DOCS), f.sample))
    if not args.no_png:
        make_pngs()


if __name__ == "__main__":
    main()
