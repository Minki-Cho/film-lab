"""Reproduce the bundled, original 33³ initial style profiles.

These original starting-point profiles create a film-like look.
No vendor LUT, firmware, or proprietary transform is included. Asset license: MIT.
Run from the project root with: python tools/generate_profiles.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1] / "film_lab" / "data" / "profiles"
VERSION = "1.0.0"
N = 33
LUMA = np.array([0.2126, 0.7152, 0.0722])


def tone(rgb, x, y):
    return np.stack([np.interp(rgb[..., c], x, y) for c in range(3)], axis=-1)


def authored_style(rgb, style):
    y = (rgb @ LUMA)[..., None]
    chroma = rgb - y
    shadow = (1 - y) ** 2 * y * 2
    light = y ** 2 * (1 - y) * 2
    if style == "documentary":
        # Documentary palette: strong lower-mid tone contrast, restrained blues
        # and greens, warm red/skin sector, cool shadows, gentle white shoulder.
        out = tone(rgb, [0, .12, .35, .6, .82, 1], [0, .08, .31, .60, .84, 1])
        oy = (out @ LUMA)[..., None]
        out = oy + (out - oy) * np.array([.84, .70, .67])
        out += shadow * np.array([-.030, .008, .046])
        red = np.maximum(rgb[..., 0:1] - np.maximum(rgb[..., 1:2], rgb[..., 2:3]), 0)
        out += red * np.array([.022, .008, -.010])
        out += light * np.array([.015, .006, -.020])
    elif style == "negative":
        # Negative-film interpretation: pronounced toe, colour crossover with
        # luminance, darker cyanish foliage and warm lights. Hue-dependent edits.
        out = tone(rgb, [0, .08, .25, .5, .76, 1], [.008, .035, .17, .50, .84, .985])
        oy = (out @ LUMA)[..., None]
        out = oy + (out - oy) * .91
        out += shadow * np.array([-.046, .030, .014])
        out += light * np.array([.045, -.012, -.052])
        green = np.maximum(rgb[..., 1:2] - (rgb[..., 0:1] + rgb[..., 2:3]) * .5, 0)
        out += green * np.array([-.055, -.025, .040])
        blue = np.maximum(rgb[..., 2:3] - rgb[..., 0:1], 0)
        out += blue * np.array([-.014, .020, -.008])
    elif style == "cinema":
        # A soft cinema curve with lifted toe and restrained highlight shoulder.
        out = tone(rgb, [0, .1, .3, .55, .8, 1], [.022, .108, .315, .535, .748, .955])
        oy = (out @ LUMA)[..., None]
        out = oy + (out - oy) * .62
        out += shadow * np.array([-.016, .009, .026])
        out += light * np.array([.022, .005, -.026])
        # Muted yellow-green rather than a uniformly reduced colour palette.
        yellow = np.maximum(np.minimum(rgb[..., 0:1], rgb[..., 1:2]) - rgb[..., 2:3], 0)
        out += yellow * np.array([-.008, -.012, .012])
    elif style == "vivid":
        # Landscape palette: a deeper black, vivid blue/green sectors, a milder
        # saturation boost in the warm red sector to protect skin and sunsets.
        out = tone(rgb, [0, .15, .4, .65, .85, 1], [0, .095, .38, .715, .91, 1])
        oy = (out @ LUMA)[..., None]
        bluegreen = np.maximum(np.maximum(rgb[..., 1:2], rgb[..., 2:3]) - rgb[..., 0:1], 0)
        factor = 1.10 + .18 * bluegreen
        out = oy + (out - oy) * factor
        out += np.maximum(rgb[..., 2:3] - rgb[..., 0:1], 0) * np.array([-.014, -.006, .020])
        out += np.maximum(rgb[..., 1:2] - rgb[..., 2:3], 0) * np.array([-.008, .018, -.010])
        out += light * np.array([.012, .008, -.018])
    else:
        raise ValueError(style)
    return np.clip(out, 0, 1)


def save_profile(profile_id, kind, asset_name, data, description):
    folder = ROOT / profile_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / asset_name).write_bytes(data)
    manifest = {
        "id": profile_id, "version": VERSION,
        "input_space": "sRGB_D65_encoded", "output_space": "sRGB_D65_encoded",
        "grid_size": N if kind == "cube" else 0,
        "sha256": hashlib.sha256(data).hexdigest(), "baseline_version": VERSION,
        "kind": kind, "asset": asset_name,
        "source": {"creator": "Film Lab project", "method": "Original mathematical transform authored in tools/generate_profiles.py", "reference_camera": None, "calibration_images": 0, "validation_images": 0},
        "license": {"spdx": "MIT", "redistribution": "Use, modification and redistribution permitted under the bundled MIT license."},
        "validation_status": "initial_style", "description": description,
        "notice": "필름 느낌을 위한 자체 제작 초기 스타일입니다. 참고 사진으로 정밀 보정하기 전 단계입니다.",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main():
    # Mesh indexing [blue, green, red] yields the .cube red-fastest convention.
    b, g, r = np.meshgrid(*([np.linspace(0, 1, N)] * 3), indexing="ij")
    rgb = np.stack([r, g, b], axis=-1)
    descriptions = {
        "documentary": "절제된 색과 차가운 그림자, 다큐멘터리 사진의 명암",
        "negative": "밝기에 따른 색 변화, 깊은 대비와 녹색 표현",
        "cinema": "낮은 채도와 부드러운 명암, 완만한 하이라이트",
        "vivid": "풍경의 선명한 녹색과 파랑, 강한 대비",
    }
    for profile_id, description in descriptions.items():
        values = authored_style(rgb, profile_id).reshape(-1, 3)
        lines = [f'TITLE "Film Lab {profile_id} {VERSION}"', "# Original initial style, MIT; encoded sRGB D65",
                 f"LUT_3D_SIZE {N}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
        lines.extend("{:.8f} {:.8f} {:.8f}".format(*pixel) for pixel in values)
        save_profile(profile_id, "cube", "style.cube", ("\n".join(lines) + "\n").encode("utf-8"), description)
    baseline = {"version": VERSION, "input": "linear ProPhoto RGB D50", "output": "encoded sRGB D65", "adaptation": "Bradford D50 to D65", "tone": "T(Y)=min(1,1.25*Y/(1+0.25*Y))", "gamut": "equal-luminance chroma compression; 0.8 knee; exponential shoulder", "implementation": "film_lab/color.py:baseline_render"}
    monochrome = {"version": VERSION, "weights": [.25, .65, .10], "weight_domain": "linear sRGB", "curve_domain": "encoded sRGB", "knots": [[0, 0], [.06, .025], [.16, .10], [.35, .30], [.55, .56], [.78, .85], [.92, .96], [1, 1]], "grain": "Separate source-coordinate noise, with luminance-dependent envelope"}
    save_profile("baseline", "baseline", "curve.json", (json.dumps(baseline, indent=2) + "\n").encode(), "카메라 색 변환과 고정된 기본 명암 곡선")
    save_profile("monochrome", "monochrome", "curve.json", (json.dumps(monochrome, indent=2) + "\n").encode(), "색상별 명도 기여도와 전용 곡선으로 만든 흑백 계조")
    (ROOT / "LICENSE.txt").write_text("MIT License\n\nCopyright (c) 2026 Film Lab contributors\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the Software), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\nSOFTWARE.\n", encoding="utf-8", newline="\n")
    print(f"Generated 6 profiles in {ROOT}")


if __name__ == "__main__":
    main()
