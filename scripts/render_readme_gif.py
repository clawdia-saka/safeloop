#!/usr/bin/env python3
"""Render the small SafeLoop README demo GIF.

The GIF is intentionally generated from source so the README visual is easy to
refresh without screen-recording tools. It illustrates the public five-command
flow and the boundary between covered local rollback and external review.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "safeloop-readme-demo.gif"
W, H = 960, 540
BG = (8, 13, 28)
PANEL = (14, 22, 42)
GREEN = (88, 222, 139)
CYAN = (118, 204, 255)
YELLOW = (255, 204, 94)
RED = (255, 118, 118)
TEXT = (235, 243, 255)
MUTED = (150, 166, 190)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


F_TITLE = font(36, True)
F_H2 = font(24, True)
F_BODY = font(18)
F_MONO = font(17)
F_SMALL = font(14)

COMMANDS = [
    "safeloop watch-run",
    "safeloop timeline",
    "safeloop verify-artifacts",
    "safeloop rollback plan",
    "safeloop rollback apply",
]


def rounded(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], fill, outline=None, width=1, r=18) -> None:
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


def draw_base(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle((0, 0, W, H), fill=BG)
    # subtle grid
    for x in range(0, W, 48):
        draw.line((x, 0, x, H), fill=(12, 20, 38), width=1)
    for y in range(0, H, 48):
        draw.line((0, y, W, y), fill=(12, 20, 38), width=1)
    draw.text((42, 30), "SafeLoop", fill=TEXT, font=F_TITLE)
    draw.text((42, 78), "Recoverability-first runtime for long-running agents", fill=MUTED, font=F_BODY)
    draw.text((42, 112), "Rollback covered local files · Review/compensate external effects · Audit everything", fill=CYAN, font=F_SMALL)


def draw_steps(draw: ImageDraw.ImageDraw, active: int) -> None:
    x0, y0 = 42, 155
    bw, bh, gap = 166, 72, 12
    for i, cmd in enumerate(COMMANDS):
        x = x0 + i * (bw + gap)
        color = CYAN if i <= active else (54, 68, 92)
        fill = (18, 34, 58) if i <= active else PANEL
        rounded(draw, (x, y0, x + bw, y0 + bh), fill, color, 2, 16)
        draw.text((x + 14, y0 + 13), f"{i+1}", fill=color, font=F_H2)
        parts = cmd.split(" ", 1)
        draw.text((x + 48, y0 + 14), parts[0], fill=TEXT, font=F_SMALL)
        draw.text((x + 48, y0 + 36), parts[1], fill=color, font=F_SMALL)


def draw_panels(draw: ImageDraw.ImageDraw, phase: int) -> None:
    rounded(draw, (42, 260, 450, 475), PANEL, (58, 82, 122), 1, 20)
    rounded(draw, (510, 260, 918, 475), PANEL, (58, 82, 122), 1, 20)
    draw.text((66, 282), "Covered local file", fill=GREEN, font=F_H2)
    draw.text((534, 282), "External side effect", fill=YELLOW, font=F_H2)

    local_lines_by_phase = [
        ["note.txt", "base"],
        ["note.txt", "changed by agent"],
        ["rollback-plan.json", "modified: note.txt", "status: reviewable"],
        ["rollback-result.json", "status: applied", "note.txt -> base"],
        ["verify-artifacts-result.json", "status: valid", "note.txt: base"],
    ]
    ext_lines_by_phase = [
        ["fake-api-call.log", "not created yet"],
        ["fake-api-call.log", "sent outside repo"],
        ["side effect status", "manual_review"],
        ["exact_rollback", "false"],
        ["external_review_required", "compensate or review"],
    ]
    for idx, line in enumerate(local_lines_by_phase[min(phase, 4)]):
        draw.text((68, 330 + idx * 30), line, fill=TEXT if idx == 0 else GREEN, font=F_MONO)
    for idx, line in enumerate(ext_lines_by_phase[min(phase, 4)]):
        draw.text((536, 330 + idx * 30), line, fill=TEXT if idx == 0 else YELLOW, font=F_MONO)

    if phase >= 3:
        draw.text((66, 430), "✓ local rollback succeeded", fill=GREEN, font=F_BODY)
        draw.text((534, 430), "! external review remains", fill=YELLOW, font=F_BODY)


def draw_footer(draw: ImageDraw.ImageDraw, phase: int) -> None:
    messages = [
        "Start: wrap an agent command with watch-run.",
        "Timeline: see what changed and when.",
        "Verify: check tamper-evident local artifacts.",
        "Plan: review rollback before applying it.",
        "Apply: restore covered local files; never pretend external effects vanished.",
    ]
    draw.text((42, 500), messages[min(phase, 4)], fill=TEXT, font=F_BODY)


def frame(phase: int, pulse: int) -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(im)
    draw_base(draw)
    draw_steps(draw, phase)
    draw_panels(draw, phase)
    draw_footer(draw, phase)
    if pulse:
        x = 42 + phase * (166 + 12)
        draw.rounded_rectangle((x - 3, 152, x + 169, 230), radius=18, outline=(255, 255, 255), width=2)
    return im


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames: list[Image.Image] = []
    durations: list[int] = []
    for phase in range(5):
        for pulse in [0, 1, 0, 0]:
            frames.append(frame(phase, pulse))
            durations.append(260 if pulse else 520)
    # Palette-optimized GIF for GitHub README display.
    paletted = [im.convert("P", palette=Image.Palette.ADAPTIVE, colors=96) for im in frames]
    paletted[0].save(
        OUT,
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(OUT)
    print(f"size={OUT.stat().st_size} bytes frames={len(frames)}")


if __name__ == "__main__":
    main()
