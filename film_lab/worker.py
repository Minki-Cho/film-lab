"""A single spawned process performs RAW IO and processing outside the Qt thread."""
from __future__ import annotations

from pathlib import Path
import multiprocessing as mp
import os
import queue
import traceback
import uuid


def _verify_jobs(jobs):
    """Recover completed files conservatively; never automatically repeat exports."""
    import json
    from PIL import Image
    from .models import sha256_file, validate_recipe
    for job in jobs:
        for result in job.get("results", []):
            if result.get("status") not in ("completed", "partial"):
                continue
            try:
                with Image.open(result["path"]) as im:
                    im.verify()
                recipe_path = result.get("recipe_path")
                if not recipe_path or not Path(recipe_path).is_file():
                    raise ValueError("레시피 파일을 찾을 수 없습니다.")
                recipe = validate_recipe(json.loads(Path(recipe_path).read_text(encoding="utf-8")))
                if recipe.get("output", {}).get("sha256") != sha256_file(result["path"]):
                    raise ValueError("결과 이미지 SHA-256이 레시피와 다릅니다.")
                if recipe.get("export", {}).get("format") == "tiff":
                    import tifffile
                    with tifffile.TiffFile(result["path"]) as image:
                        if image.pages[0].bitspersample != 16:
                            raise ValueError("TIFF의 비트 심도가 16비트가 아닙니다.")
            except Exception as exc:
                result.update(status="partial", error=f"복구 검사: {exc}")
        if job.get("status") == "completed" and any(r.get("status") != "completed" for r in job.get("results", [])):
            job["status"] = "partial"
    return jobs


def _style_thumbnails(baseline_path, directory, token):
    from PIL import Image
    import numpy as np
    from .profiles import list_profiles, apply_style
    from .cache import cache_key, DiskCache
    results = {}
    with Image.open(baseline_path) as im:
        im = im.convert("RGB")
        im.thumbnail((240, 150), Image.Resampling.LANCZOS)
        rgb = np.asarray(im, dtype=np.float32) / 255.0
    for profile in list_profiles():
        if not profile.get("available", True):
            continue
        try:
            key = cache_key({"kind": "style_thumbnail", "baseline": Path(baseline_path).stem,
                             "style": profile["id"], "sha256": profile.get("sha256"), "size": [240, 150]})
            output = Path(directory) / (key + ".png")
            if not output.is_file():
                styled = apply_style(rgb, profile["id"], 1.0)
                Image.fromarray(np.round(np.clip(styled, 0, 1) * 255).astype("uint8")).save(output)
            os.utime(output, None)
            results[profile["id"]] = str(output)
        except Exception:
            # A style failure is independently visible from its manifest in the UI.
            continue
    DiskCache(directory).prune([baseline_path, *results.values()])
    return results


def _render_tiles(result, directory, cancellation, tile_size=512):
    """Decode final-resolution PNGs here; the GUI opens only visible small tiles."""
    from PIL import Image
    from .cache import cache_key, DiskCache
    cache = DiskCache(directory)
    rendered = {}
    protected = [result["path"], result["baseline_path"]]
    layout = {"size": tile_size, "width": result["width"], "height": result["height"]}
    for name, source in (("after", result["path"]), ("before", result["baseline_path"])):
        if source in rendered:
            layout[name] = rendered[source]
            continue
        tiles = []
        with Image.open(source) as image:
            for y in range(0, image.height, tile_size):
                for x in range(0, image.width, tile_size):
                    if cancellation.is_set():
                        raise RuntimeError("상세 미리보기 준비가 취소되었습니다.")
                    key = cache_key({"kind": "detail_tile", "render": Path(source).stem,
                                     "x": x, "y": y, "size": tile_size})
                    target = cache.get(key, ".png")
                    if target is None:
                        tile = image.crop((x, y, min(x + tile_size, image.width), min(y + tile_size, image.height)))
                        temporary = cache.temporary(".png")
                        try:
                            tile.save(temporary, format="PNG")
                            target = cache.commit(temporary, key, ".png")
                        finally:
                            temporary.unlink(missing_ok=True)
                    protected.append(target)
                    tiles.append(str(target))
            if name == "after":
                key = cache_key({"kind": "list_thumbnail", "render": Path(source).stem})
                thumbnail = cache.get(key, ".png")
                if thumbnail is None:
                    image.thumbnail((160, 100), Image.Resampling.LANCZOS)
                    temporary = cache.temporary(".png")
                    try:
                        image.save(temporary, format="PNG")
                        thumbnail = cache.commit(temporary, key, ".png")
                    finally:
                        temporary.unlink(missing_ok=True)
                protected.append(thumbnail)
                layout["thumbnail"] = str(thumbnail)
        rendered[source] = tiles
        layout[name] = tiles
    cache.prune(protected)
    return layout


def _process(inbox, outbox, cancellation, cache_dir):
    os.environ["FILM_LAB_CACHE"] = cache_dir
    from . import engine
    while True:
        job = inbox.get()
        if job["kind"] == "shutdown":
            return
        kind, token, payload = job["kind"], job["token"], job["payload"]
        try:
            if kind == "inspect":
                result = engine.inspect(payload["path"])
                expected = payload.get("expected")
                if expected and (result["size_bytes"] != expected["size_bytes"] or result["sha256"] != expected["sha256"]):
                    raise ValueError("선택한 파일은 기존 원본과 크기 또는 SHA-256이 다릅니다. 원래 사진을 선택하세요.")
            elif kind == "render":
                result = engine.render(payload["source"], payload["recipe"], quality=payload["quality"],
                                       request_id=token, cache_dir=cache_dir, cancel_file=cancellation.is_set)
                if payload.get("thumbnails", True):
                    result["thumbnails"] = _style_thumbnails(result["baseline_path"], cache_dir, token)
                if payload["quality"] == "full":
                    result["tiles"] = _render_tiles(result, cache_dir, cancellation)
            elif kind == "export":
                outbox.put({"kind": kind, "token": token, "event": "started"})
                def progress(item):
                    outbox.put({"kind": kind, "token": token, "event": "progress", "result": item})
                result = engine.export(payload["items"], payload["options"], progress=progress, cancel=cancellation.is_set)
            elif kind == "verify":
                result = _verify_jobs(payload["jobs"])
            else:
                raise ValueError(f"Unknown worker task: {kind}")
            outbox.put({"kind": kind, "token": token, "event": "done", "result": result})
        except BaseException as exc:
            outbox.put({"kind": kind, "token": token, "event": "error", "error": str(exc),
                        "traceback": traceback.format_exc()})


class Processor:
    """Poll from QTimer; pending preview requests coalesce to the latest one."""

    def __init__(self, cache_dir):
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        context = mp.get_context("spawn")
        self.inbox, self.outbox = context.Queue(), context.Queue()
        self.cancellation = context.Event()
        self.process = context.Process(target=_process, args=(self.inbox, self.outbox, self.cancellation, str(cache_dir)), daemon=True)
        self.process.start()
        self.pending = []
        self.active = None
        self.closing = False

    def submit(self, kind, payload, token=None):
        job = {"kind": kind, "payload": payload, "token": token or uuid.uuid4().hex}
        if kind == "render":
            self.pending = [j for j in self.pending if j["kind"] != "render"]
            self.pending.insert(0, job)
            if self.active and self.active["kind"] == "render":
                self.cancellation.set()
        else:
            self.pending.append(job)
        self._dispatch()
        return job["token"]

    def _dispatch(self):
        if self.active is None and self.pending and not self.closing:
            self.active = self.pending.pop(0)
            self.cancellation.clear()
            self.inbox.put(self.active)

    def poll(self):
        events = []
        while True:
            try:
                event = self.outbox.get_nowait()
            except queue.Empty:
                break
            events.append(event)
            if event["event"] in ("done", "error"):
                self.active = None
        if not self.process.is_alive() and self.active:
            events.append({"kind": self.active["kind"], "token": self.active["token"], "event": "error",
                           "error": "처리 프로세스가 종료되었습니다. 메모리와 파일 상태를 확인하고 앱을 다시 시작하세요."})
            self.active = None
            self.pending.clear()
        self._dispatch()
        return events

    def cancel(self):
        self.cancellation.set()
        cancelled = [j for j in self.pending if j["kind"] == "export"]
        self.pending = [j for j in self.pending if j["kind"] != "export"]
        return cancelled

    def shutdown(self):
        self.closing = True
        self.pending.clear()
        self.cancellation.set()
        self.inbox.put({"kind": "shutdown"})
