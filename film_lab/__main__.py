"""Both the desktop launcher and a fully headless RAW command line interface."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
from pathlib import Path

from .models import ENGINE_VERSION, default_settings, validate_recipe, validate_settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="film-lab", description="Sony a6400 로컬 RAW 필름 스타일 변환")
    parser.add_argument("--version", action="version", version=ENGINE_VERSION)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help="편집 화면 실행")
    inspect_cmd = sub.add_parser("inspect", help="ARW 유효성 및 촬영 정보 확인")
    inspect_cmd.add_argument("source")
    sub.add_parser("profiles", help="사용 가능한 초기 스타일 목록")
    export_cmd = sub.add_parser("export", help="GUI 없이 한 장 또는 여러 장 변환")
    export_cmd.add_argument("sources", nargs="+")
    export_cmd.add_argument("--output", required=True)
    export_cmd.add_argument("--style", default=None)
    export_cmd.add_argument("--recipe", help="기존 .recipe.json의 보정 설정 사용")
    export_cmd.add_argument("--format", choices=("jpeg", "tiff"), default="jpeg")
    export_cmd.add_argument("--quality", type=int, default=95)
    export_cmd.add_argument("--long-edge", type=int, choices=(2048, 3840))
    export_cmd.add_argument("--exposure", type=float, default=None)
    export_cmd.add_argument("--strength", type=float, default=None, help="0~1")
    preview_cmd = sub.add_parser("render", help="기본 현상 및 편집 미리보기 PNG 생성")
    preview_cmd.add_argument("source")
    preview_cmd.add_argument("--style", default="documentary")
    preview_cmd.add_argument("--cache-dir")
    preview_cmd.add_argument("--full", action="store_true")
    smoke_cmd = sub.add_parser("self-test", help="배포본 GUI/작업 프로세스 검사 및 스크린샷")
    smoke_cmd.add_argument("--source")
    smoke_cmd.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "gui"):
            from .gui import main as gui_main
            return gui_main()
        if args.command == "self-test":
            from .gui import self_test
            return self_test(source=args.source, output=args.output)
        if args.command == "profiles":
            from .profiles import list_profiles
            result = list_profiles()
        elif args.command == "inspect":
            from .engine import inspect
            result = inspect(args.source)
        elif args.command == "render":
            from .engine import render
            settings = validate_settings({"style_id": args.style})
            result = render(args.source, settings, quality="full" if args.full else "preview",
                            cache_dir=args.cache_dir, request_id="cli")
        else:
            from .engine import export
            settings = default_settings()
            if args.recipe:
                recipe = validate_recipe(json.loads(Path(args.recipe).read_text(encoding="utf-8")))
                settings = recipe["settings"]
            for option, key in ((args.style, "style_id"), (args.exposure, "exposure_ev"),
                                (args.strength, "strength")):
                if option is not None:
                    settings[key] = option
            settings = validate_settings(settings)
            paths = []
            for value in args.sources:
                path = Path(value)
                paths.extend(sorted(path.glob("*.[aA][rR][wW]")) if path.is_dir() else [path])
            paths = list(dict.fromkeys(str(p.resolve()) for p in paths))
            if not paths or len(paths) > 100:
                raise ValueError("한 번에 1~100장의 ARW를 지정하세요.")
            result = export([{"source": path, "recipe": {"settings": settings}} for path in paths],
                            {"format": args.format, "quality": args.quality,
                             "long_edge": args.long_edge, "output_dir": args.output})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if all(item["status"] == "completed" for item in result) else 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"오류: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
