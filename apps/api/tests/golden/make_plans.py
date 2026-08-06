"""Regenerate the synthetic vector plans in this directory.

    ../../.venv/bin/python make_plans.py

Ground truth must be true by construction. A hand-placed caption version of these
plans put KITCHEN and STORE in the same room and left a large space unlabelled, which
is a benchmark asserting something the drawing does not show. So rooms are rectangles,
walls are drawn on their boundaries, each caption sits at its own room's centre, and
the generator asserts non-overlap and caption containment before writing anything.
"""

from reportlab.lib.pagesizes import A3
from reportlab.pdfgen import canvas

W, H = A3[1], A3[0]  # landscape — how a floor plate is actually sheeted
WALL, DOOR = 5.0, 30.0


def draw(path, title, rooms, doors, exterior_door, furniture):
    for name, (x0, y0, x1, y1) in rooms.items():
        assert x0 < (x0 + x1) / 2 < x1 and y0 < (y0 + y1) / 2 < y1, name
    names = list(rooms)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ax0, ay0, ax1, ay1 = rooms[a]
            bx0, by0, bx1, by1 = rooms[b]
            if min(ax1, bx1) - max(ax0, bx0) > 1 and min(ay1, by1) - max(ay0, by0) > 1:
                raise AssertionError(f"{a} overlaps {b}")

    c = canvas.Canvas(path, pagesize=(W, H))
    c.setLineWidth(1.1)
    for x0, y0, x1, y1 in rooms.values():          # double-line walls
        for dy in (-WALL / 2, WALL / 2):
            c.line(x0, y0 + dy, x1, y0 + dy)
            c.line(x0, y1 + dy, x1, y1 + dy)
        for dx in (-WALL / 2, WALL / 2):
            c.line(x0 + dx, y0, x0 + dx, y1)
            c.line(x1 + dx, y0, x1 + dx, y1)

    def door(x, y, horizontal):
        c.saveState(); c.setFillColorRGB(1, 1, 1); c.setStrokeColorRGB(1, 1, 1)
        if horizontal:
            c.rect(x - DOOR / 2, y - WALL, DOOR, WALL * 2, stroke=1, fill=1)
        else:
            c.rect(x - WALL, y - DOOR / 2, WALL * 2, DOOR, stroke=1, fill=1)
        c.restoreState()
        c.setLineWidth(0.7)
        if horizontal:
            c.arc(x - DOOR / 2, y, x + DOOR * 1.5, y + DOOR * 2, 180, -90)
            c.line(x - DOOR / 2, y, x - DOOR / 2, y + DOOR)
        else:
            c.arc(x, y - DOOR / 2, x + DOOR * 2, y + DOOR * 1.5, 270, 90)
            c.line(x, y - DOOR / 2, x + DOOR, y - DOOR / 2)
        c.setLineWidth(1.1)

    for a, b in doors:
        ax0, ay0, ax1, ay1 = rooms[a]
        bx0, by0, bx1, by1 = rooms[b]
        if abs(ax1 - bx0) < 1 or abs(bx1 - ax0) < 1:
            x = ax1 if abs(ax1 - bx0) < 1 else ax0
            lo, hi = max(ay0, by0), min(ay1, by1)
            assert hi - lo > DOOR + 20, f"{a}|{b} share too little wall for a door"
            door(x, (lo + hi) / 2, horizontal=False)
        else:
            y = ay1 if abs(ay1 - by0) < 1 else ay0
            lo, hi = max(ax0, bx0), min(ax1, bx1)
            assert hi - lo > DOOR + 20, f"{a}|{b} share too little wall for a door"
            door((lo + hi) / 2, y, horizontal=True)
    ex, ey, horizontal = exterior_door
    for offset in (-DOOR * 0.6, DOOR * 0.6):      # double-leaf main entrance
        door(ex + offset if horizontal else ex, ey if horizontal else ey + offset, horizontal)
    c.setLineWidth(1.4)                            # threshold line outside the doors
    if horizontal:
        c.line(ex - DOOR * 1.3, ey - WALL * 3, ex + DOOR * 1.3, ey - WALL * 3)
    else:
        c.line(ex - WALL * 3, ey - DOOR * 1.3, ex - WALL * 3, ey + DOOR * 1.3)
    c.setLineWidth(1.1)

    c.setLineWidth(0.8)
    for kind, args in furniture:
        (c.rect if kind == "rect" else c.circle)(*args)

    c.setFont("Helvetica", 13)
    for name, (x0, y0, x1, y1) in rooms.items():
        c.drawCentredString((x0 + x1) / 2, (y0 + y1) / 2, name)

    c.setLineWidth(1)                               # scale bar + overall dimension
    sx, sy = 70, H - 45
    c.line(sx, sy, sx + 120, sy)
    for i in range(4):
        c.line(sx + i * 40, sy - 4, sx + i * 40, sy + 4)
    c.setFont("Helvetica", 8)
    c.drawString(sx, sy - 14, "0     2     4     6 m")
    c.line(60, 40, W - 60, 40)
    c.line(60, 36, 60, 44); c.line(W - 60, 36, W - 60, 44)
    c.drawCentredString(W / 2, 46, "24.60")
    c.setFont("Helvetica", 9)
    c.drawString(60, H - 25, title)
    c.showPage(); c.save()
    print("wrote", path)


draw(
    "cafe_vector.pdf", "CAFE — GROUND FLOOR PLAN — 1:100",
    rooms={
        "ENTRANCE": (60, 70, 300, 330), "LOUNGE": (60, 330, 300, 700),
        "DINING": (300, 70, 780, 520), "BAR": (300, 520, 780, 700),
        "WC": (780, 70, 1130, 260), "KITCHEN": (780, 260, 1130, 520),
        "STORE": (780, 520, 1130, 700),
    },
    doors=[("ENTRANCE", "DINING"), ("ENTRANCE", "LOUNGE"), ("DINING", "BAR"),
           ("DINING", "WC"), ("DINING", "KITCHEN"), ("KITCHEN", "STORE")],
    exterior_door=(180, 70, True),
    furniture=[("rect", (340, 560, 200, 45)),
               *[("circle", (400 + i * 110, 200, 28)) for i in range(3)],
               *[("rect", (370 + i * 110, 300, 60, 60)) for i in range(3)],
               ("rect", (830, 300, 150, 55)), ("rect", (100, 110, 70, 40)),
               ("circle", (1050, 160, 15)), ("rect", (960, 190, 40, 25)),
               ("rect", (100, 400, 130, 60))],
)

draw(
    "coworking_vector.pdf", "COWORKING — LEVEL 2 PLAN — 1:100",
    rooms={
        "RECEPTION": (60, 70, 330, 330), "LOUNGE": (60, 330, 330, 700),
        "OPEN DESKS": (330, 70, 800, 700), "MEETING ROOM": (800, 380, 1130, 700),
        "PHONE BOOTH": (800, 70, 1130, 380),
    },
    doors=[("RECEPTION", "OPEN DESKS"), ("RECEPTION", "LOUNGE"),
           ("OPEN DESKS", "MEETING ROOM"), ("OPEN DESKS", "PHONE BOOTH")],
    exterior_door=(190, 70, True),
    furniture=[*[("rect", (380 + c * 95, 130 + r * 90, 65, 45))
                 for r in range(4) for c in range(4)],
               ("rect", (880, 480, 170, 90)),
               *[("circle", (900 + i * 45, 450, 14)) for i in range(4)],
               ("rect", (95, 110, 80, 45)), ("rect", (100, 420, 140, 65))],
)
