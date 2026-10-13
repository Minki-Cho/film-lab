"""Refresh Android parity fixtures from the original desktop processing code.

Run from the repository root with its Python environment:
    .venv/Scripts/python.exe android/tests/generate_references.py
No Android libraries are needed by the corresponding JVM test.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
from film_lab.raster import adjust_developed
from film_lab.profiles import apply_style, PROFILE_NAMES
from film_lab.color import apply_adjustments, apply_grain


def packed(rgb):
    rgb = np.floor(np.clip(rgb, 0, 1) * np.float32(255) + np.float32(.5)).astype(np.uint32).reshape(-1, 3)
    pixels = (255 << 24) | (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]
    return ",".join(f"{int(p):08x}" for p in pixels)


def main():
    rng = np.random.default_rng(3904)
    pixels = rng.integers(0, 256, size=(512, 3), dtype=np.uint8)
    pixels[:256] = np.arange(256, dtype=np.uint8)[:, None]
    pixels[256:264] = np.array([[r, g, b] for r in (0, 255) for g in (0, 255) for b in (0, 255)])
    # Explicit tetrahedron regions, equal fractional coordinates, transfer knees.
    pixels[264:270] = [[10, 11, 12], [11, 10, 12], [12, 11, 10], [12, 10, 11], [11, 12, 10], [10, 12, 11]]
    image = pixels.reshape(16, 32, 3).astype(np.float32) / np.float32(255)
    cases = []
    defaults = dict(exposure_ev=0., strength=1., warmth=0, tint=0, contrast=0,
                    saturation=0, highlights=0, shadows=0, amount=0, scale=1,
                    size="small", source_hash="android")
    for style in PROFILE_NAMES:
        cases.append((f"{style}_identity_controls", style, {}))
        cases.append((f"{style}_strength_zero", style, dict(strength=0)))
        cases.append((f"{style}_partial", style, dict(strength=.38)))
        cases.append((f"{style}_combined", style,
                      dict(exposure_ev=.7, warmth=37, tint=-29, contrast=42,
                           saturation=63, highlights=-75, shadows=68, strength=.83)))
        cases.append((f"{style}_grain", style, dict(amount=100, scale=3)))
    for setting in ("exposure_ev", "warmth", "tint", "contrast", "saturation", "highlights", "shadows"):
        for sign in (-1, 1):
            cases.append((f"boundary_{setting}_{sign}", "negative", {setting: sign * (5 if setting == "exposure_ev" else 100)}))
    cases.extend([
        ("large_grain", "monochrome", dict(amount=100, scale=1, size="large", source_hash="c0ffee")),
        ("reduced_grain", "vivid", dict(amount=57, scale=19, source_hash="c0ffee")),
        ("combined_grain", "cinema", dict(amount=82, scale=1, exposure_ev=-1.3, warmth=-80, tint=70,
                                          contrast=-67, saturation=100, highlights=100, shadows=-100)),
    ])
    destination = Path(__file__).with_name("references.tsv")
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("name\tstyle\texposure\tstrength\twarmth\ttint\tcontrast\tsaturation\thighlights\tshadows\tgrain\twidth\theight\toriginalWidth\toriginalHeight\tlargeGrain\tsourceHash\tinputARGB\texpectedARGB\n")
        for name, style, changes in cases:
            s = {**defaults, **changes, "style_id": style}
            s["grain"] = dict(amount=s["amount"], seed=12345, size=s["size"])
            result, _ = adjust_developed(image.copy(), s)
            result = apply_style(result, style, s["strength"])
            result = apply_adjustments(result, s)
            result = apply_grain(result, s, s["source_hash"], original_size=(32 * s["scale"], 16 * s["scale"]))
            fields = [name, style, s["exposure_ev"], s["strength"], s["warmth"], s["tint"], s["contrast"],
                      s["saturation"], s["highlights"], s["shadows"], s["amount"], 32, 16,
                      32 * s["scale"], 16 * s["scale"], str(s["size"] == "large").lower(), s["source_hash"],
                      packed(image), packed(result)]
            stream.write("\t".join(map(str, fields)) + "\n")
    print(f"Wrote {len(cases)} desktop reference cases to {destination}")


if __name__ == "__main__":
    main()
