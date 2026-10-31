#!/usr/bin/env python3
"""Generate the enten review-path figure as an Excalidraw scene.

The geometry is declared once so the checked-in scene can be rebuilt instead of
hand-edited. Render with the packaged Excalidraw CLI:

    excalidraw render docs/figures/architecture.excalidraw \
      --out docs/figures/architecture.png --scale 2 --max-width 3200
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from pathlib import Path

SCENE_WIDTH = 1520
SCENE_HEIGHT = 940
UPDATED_AT = 1_761_915_600_000
INK = "#1e1e1e"
ARROW_INK = "#495057"
BACKGROUND = "#ffffff"

PALETTE = {
    "input": ("#1971c2", "#d0ebff"),
    "analysis": ("#f08c00", "#fff3bf"),
    "risk": ("#e03131", "#ffe3e3"),
    "model": ("#0c8599", "#c5f6fa"),
    "runtime": ("#9c36b5", "#f3d9fa"),
    "neutral": ("#495057", "#f1f3f5"),
}


@dataclass(frozen=True)
class Box:
    key: str
    x: int
    y: int
    width: int
    height: int
    role: str
    label: str


BOXES = (
    Box("github", 70, 180, 280, 100, "input", "GitHub pull request\nsigned webhook"),
    Box("edge", 470, 180, 320, 100, "runtime", "Authenticated edge\nHMAC and body cap"),
    Box("worker", 960, 180, 380, 100, "runtime", "Worker\nfenced claim"),
    Box("queue", 470, 350, 320, 110, "analysis", "Postgres queue\none job per SHA"),
    Box("checkout", 960, 350, 380, 110, "risk", "Public checkout\nexact head SHA"),
    Box("findings", 470, 530, 320, 100, "model", "Findings\nadvisory only"),
    Box("verifier", 960, 530, 380, 110, "input", "Docker verifier\nno network, read-only"),
    Box("arbiter", 470, 710, 320, 100, "analysis", "Exit arbiter\nthree fresh runs"),
    Box("report", 70, 710, 280, 100, "neutral", "GitHub check\nand comment"),
)


def stable_number(value: str) -> int:
    return zlib.crc32(value.encode("utf-8"))


def element(
    element_id: str,
    element_type: str,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    stroke: str = ARROW_INK,
    fill: str = "transparent",
    roundness: dict[str, int] | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": element_id,
        "type": element_type,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "angle": 0,
        "strokeColor": stroke,
        "backgroundColor": fill,
        "fillStyle": "hachure",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 2,
        "fontFamily": 1,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": roundness,
        "seed": stable_number(element_id),
        "version": 1,
        "versionNonce": stable_number(f"{element_id}:nonce"),
        "isDeleted": False,
        "boundElements": None,
        "updated": UPDATED_AT,
        "link": None,
        "locked": False,
    }
    if extra is not None:
        value.update(extra)
    return value


def box_element(box: Box) -> tuple[dict[str, object], dict[str, object]]:
    stroke, fill = PALETTE[box.role]
    text_id = f"{box.key}-label"
    shape = element(
        box.key,
        "rectangle",
        box.x,
        box.y,
        box.width,
        box.height,
        stroke=stroke,
        fill=fill,
        roundness={"type": 3},
        extra={"boundElements": [{"type": "text", "id": text_id}]},
    )
    label = element(
        text_id,
        "text",
        box.x,
        box.y,
        box.width,
        box.height,
        stroke=INK,
        extra={
            "text": box.label,
            "fontSize": 22,
            "fontFamily": 1,
            "textAlign": "center",
            "verticalAlign": "middle",
            "containerId": box.key,
            "originalText": box.label,
            "lineHeight": 1.25,
            "autoResize": True,
            "boundElements": None,
        },
    )
    return shape, label


def arrow_element(
    element_id: str,
    start: tuple[int, int],
    end: tuple[int, int],
) -> dict[str, object]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    return element(
        element_id,
        "arrow",
        start[0],
        start[1],
        abs(dx),
        abs(dy),
        extra={
            "points": [[0, 0], [dx, dy]],
            "lastCommittedPoint": None,
            "startBinding": None,
            "endBinding": None,
            "startArrowhead": None,
            "endArrowhead": "arrow",
            "elbowed": False,
            "boundElements": None,
        },
    )


def label_element(
    element_id: str,
    center_x: int,
    y: int,
    width: int,
    text: str,
) -> dict[str, object]:
    return element(
        element_id,
        "text",
        center_x - width // 2,
        y,
        width,
        24,
        stroke=INK,
        extra={
            "text": text,
            "fontSize": 18,
            "fontFamily": 1,
            "textAlign": "center",
            "verticalAlign": "top",
            "containerId": None,
            "originalText": text,
            "lineHeight": 1.25,
            "autoResize": True,
            "boundElements": None,
        },
    )


def build_scene() -> dict[str, object]:
    elements: list[dict[str, object]] = []

    title = "The verifier exit code decides the verdict"
    elements.append(
        element(
            "title",
            "text",
            70,
            48,
            1100,
            44,
            stroke=INK,
            extra={
                "text": title,
                "fontSize": 32,
                "fontFamily": 1,
                "textAlign": "left",
                "verticalAlign": "top",
                "containerId": None,
                "originalText": title,
                "lineHeight": 1.25,
                "autoResize": True,
                "boundElements": None,
            },
        )
    )

    for box in BOXES:
        shape, label = box_element(box)
        elements.extend((shape, label))

    arrows = (
        ("signed-webhook", (350, 230), (470, 230)),
        ("enqueue", (630, 280), (630, 350)),
        ("claim", (790, 405), (960, 230)),
        ("checkout", (1150, 280), (1150, 350)),
        ("verify", (1150, 460), (1150, 530)),
        ("exit-codes", (960, 585), (790, 760)),
        ("verdict", (470, 760), (350, 760)),
        ("advisory", (630, 630), (630, 710)),
    )
    for element_id, start, end in arrows:
        elements.append(arrow_element(element_id, start, end))

    labels = (
        ("signed-webhook-label", 410, 196, 120, "signed POST"),
        ("enqueue-label", 560, 300, 110, "enqueue v1"),
        ("claim-label", 790, 300, 170, "claim generation"),
        ("checkout-label", 1260, 302, 140, "checked out SHA"),
        ("verify-label", 1260, 482, 140, "argv command"),
        ("exit-codes-label", 940, 670, 170, "three exit codes"),
        ("verdict-label", 410, 724, 90, "verdict"),
        ("advisory-label", 720, 650, 160, "cannot override"),
    )
    for element_id, center_x, y, width, text in labels:
        elements.append(label_element(element_id, center_x, y, width, text))

    mapping = "3/3 pass, 2/3 flaky, 0-1/3 fail"
    elements.append(
        element(
            "verdict-mapping",
            "text",
            70,
            846,
            520,
            26,
            stroke=INK,
            extra={
                "text": mapping,
                "fontSize": 18,
                "fontFamily": 1,
                "textAlign": "left",
                "verticalAlign": "top",
                "containerId": None,
                "originalText": mapping,
                "lineHeight": 1.25,
                "autoResize": True,
                "boundElements": None,
            },
        )
    )

    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {
            "viewBackgroundColor": BACKGROUND,
            "gridSize": None,
            "exportBackground": True,
            "exportWithDarkMode": False,
        },
        "files": {},
    }


def main() -> None:
    output = Path(__file__).with_suffix(".excalidraw")
    output.write_text(
        json.dumps(build_scene(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
