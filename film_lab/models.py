"""Validated, JSON-safe contracts shared by the CLI, worker and desktop UI."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

ENGINE_VERSION = "1.0.0"
SCHEMA_VERSION = 1
STYLE_IDS = ("baseline", "documentary", "negative",
             "cinema", "vivid", "monochrome")


def default_settings() -> dict:
    return dict(exposure_ev=0.0, wb_mode="as_shot", warmth=0, tint=0,
                style_id="baseline", style_version="1.0.0", strength=1.0,
                contrast=0, saturation=0, highlights=0, shadows=0,
                grain=dict(amount=0, size="small", seed=12345))


def _number(value, low, high, name, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: 숫자가 필요합니다.")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name}: {low}~{high} 범위를 벗어났습니다.")
    if integer and int(value) != value:
        raise ValueError(f"{name}: 정수가 필요합니다.")
    return int(value) if integer else float(value)


def validate_settings(values: dict | None = None) -> dict:
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError("보정 설정은 JSON 객체여야 합니다.")
    defaults = default_settings()
    unknown = set(values) - set(defaults)
    if unknown:
        raise ValueError(f"알 수 없는 보정 항목: {', '.join(sorted(unknown))}")
    settings = {**defaults, **copy.deepcopy(values)}
    settings["exposure_ev"] = _number(settings["exposure_ev"], -2, 3, "exposure_ev")
    if abs(settings["exposure_ev"] * 10 - round(settings["exposure_ev"] * 10)) > 1e-6:
        raise ValueError("노출은 0.1 EV 단위여야 합니다.")
    settings["strength"] = _number(settings["strength"], 0, 1, "strength")
    for name in ("warmth", "tint", "contrast", "saturation", "highlights", "shadows"):
        settings[name] = _number(settings[name], -100, 100, name, True)
    if settings["style_id"] not in STYLE_IDS:
        raise ValueError("지원하지 않는 스타일입니다.")
    if settings["style_version"] != "1.0.0":
        raise ValueError("지원하지 않는 스타일 버전입니다.")
    if settings["wb_mode"] not in ("as_shot", "relative"):
        raise ValueError("지원하지 않는 화이트밸런스 방식입니다.")
    settings["wb_mode"] = "relative" if settings["warmth"] or settings["tint"] else "as_shot"
    grain = settings["grain"]
    if not isinstance(grain, dict) or set(grain) - {"amount", "size", "seed"}:
        raise ValueError("잘못된 입자 설정입니다.")
    grain = {**defaults["grain"], **grain}
    grain["amount"] = _number(grain["amount"], 0, 100, "grain.amount", True)
    grain["seed"] = _number(grain["seed"], 0, 2**32 - 1, "grain.seed", True)
    if grain["size"] not in ("small", "large"):
        raise ValueError("입자 크기는 small 또는 large여야 합니다.")
    settings["grain"] = grain
    return settings


def validate_export_options(values: dict) -> dict:
    if not isinstance(values, dict):
        raise ValueError("저장 설정은 JSON 객체여야 합니다.")
    result = {"format": "jpeg", "quality": 95, "long_edge": None,
              "color_space": "sRGB", **copy.deepcopy(values)}
    if set(result) - {"format", "quality", "long_edge", "color_space", "output_dir"}:
        raise ValueError("알 수 없는 저장 설정입니다.")
    if result["format"] not in ("jpeg", "tiff"):
        raise ValueError("저장 형식은 jpeg 또는 tiff여야 합니다.")
    result["quality"] = _number(result["quality"], 80, 100, "quality", True)
    if result["long_edge"] is not None and (type(result["long_edge"]) is not int or result["long_edge"] not in (2048, 3840)):
        raise ValueError("긴 변은 원본, 2048 또는 3840만 지원합니다.")
    if result["color_space"] != "sRGB":
        raise ValueError("출력 색공간은 sRGB여야 합니다.")
    if not isinstance(result.get("output_dir"), (str, os.PathLike)) or not str(result["output_dir"]).strip():
        raise ValueError("저장 폴더를 선택하세요.")
    result["output_dir"] = str(Path(result["output_dir"]).expanduser().resolve())
    return result


def validate_recipe(recipe: dict) -> dict:
    if not isinstance(recipe, dict) or recipe.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("지원하지 않는 레시피 스키마 버전입니다.")
    if recipe.get("engine_version") != ENGINE_VERSION:
        raise ValueError("지원하지 않는 엔진 버전입니다.")
    result = copy.deepcopy(recipe)
    result["settings"] = validate_settings(recipe.get("settings", {}))
    return result


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
