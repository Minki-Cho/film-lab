"""Project state, bounded per-photo history, and durable export snapshots."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import uuid

from .models import atomic_json, default_settings, validate_settings


@dataclass
class PhotoState:
    source: dict
    settings: dict = field(default_factory=default_settings)
    status: str = "대기"
    error: str = ""
    preview: str = ""
    undo_stack: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)

    @property
    def id(self):
        return self.source["id"]

    @property
    def path(self):
        return self.source["path"]

    def update(self, values, remember=True):
        updated = validate_settings(deepcopy(values))
        if updated == self.settings:
            return False
        if remember:
            self.undo_stack.append(deepcopy(self.settings))
            self.undo_stack = self.undo_stack[-20:]
            self.redo_stack.clear()
        self.settings = updated
        return True

    def undo(self):
        if not self.undo_stack:
            return False
        self.redo_stack.append(deepcopy(self.settings))
        self.settings = self.undo_stack.pop()
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        self.undo_stack.append(deepcopy(self.settings))
        self.settings = self.redo_stack.pop()
        return True

    def serialize(self):
        return {"source": deepcopy(self.source), "settings": deepcopy(self.settings),
                "status": self.status, "error": self.error,
                "undo": deepcopy(self.undo_stack[-20:]), "redo": deepcopy(self.redo_stack[-20:])}


class Session:
    """Project state and immutable export settings for all supported inputs."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.photos: list[PhotoState] = []
        self.jobs: list[dict] = []
        self.export_options = {"format": "jpeg", "quality": 95, "color_space": "sRGB",
                               "long_edge": None, "output_dir": ""}

    def find(self, photo_id):
        return next((photo for photo in self.photos if photo.id == photo_id), None)

    def add(self, source):
        key = str(Path(source["path"]).resolve()).casefold()
        if any(str(Path(photo.path).resolve()).casefold() == key for photo in self.photos):
            return None
        if len(self.photos) >= 100:
            raise ValueError("사진은 프로젝트당 최대 100장까지 추가할 수 있습니다.")
        source = deepcopy(source)
        source.setdefault("id", uuid.uuid4().hex)
        photo = PhotoState(source)
        self.photos.append(photo)
        return photo

    def snapshot_job(self, photos, options):
        from .profiles import list_profiles
        profiles = {profile["id"]: profile for profile in list_profiles()}
        def recipe(photo):
            profile = profiles.get(photo.settings["style_id"], {})
            return {"schema_version": 1, "engine_version": "1.0.0",
                    "source": deepcopy(photo.source), "settings": deepcopy(photo.settings),
                    "profile": {key: profile[key] for key in ("id", "version", "sha256", "baseline_version") if key in profile}}
        job = {"id": uuid.uuid4().hex, "status": "waiting", "options": deepcopy(options),
               "items": [{"photo_id": photo.id, "source": photo.path,
                          "recipe": recipe(photo)}
                         for photo in photos], "results": []}
        self.jobs.append(job)
        return job

    def save(self, path=None):
        if path:
            self.path = Path(path)
        if self.path is None:
            raise ValueError("프로젝트 저장 경로가 없습니다.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(self.path, {"schema_version": 1, "engine_version": "1.0.0",
                               "photos": [photo.serialize() for photo in self.photos],
                               "jobs": self.jobs, "export_options": self.export_options})

    @classmethod
    def load(cls, path):
        import json
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("지원하지 않는 프로젝트 버전입니다.")
        if not isinstance(data.get("photos"), list) or len(data["photos"]) > 100:
            raise ValueError("프로젝트 사진 목록이 올바르지 않습니다.")
        session = cls(path)
        for entry in data["photos"]:
            photo = session.add(entry["source"])
            if photo is None:
                continue
            photo.settings = validate_settings(entry["settings"])
            photo.undo_stack = [validate_settings(s) for s in entry.get("undo", [])[-20:]]
            photo.redo_stack = [validate_settings(s) for s in entry.get("redo", [])[-20:]]
            photo.status = entry.get("status", "대기")
            photo.error = entry.get("error", "")
            source = Path(photo.path)
            try:
                stat = source.stat()
                if stat.st_size != photo.source.get("size_bytes") or stat.st_mtime_ns != photo.source.get("mtime_ns"):
                    photo.status, photo.error = "재연결 필요", "원본이 변경되었거나 이동했습니다. 크기와 해시 확인이 필요합니다."
                elif photo.status in ("처리", "검사 중"):
                    photo.status = "대기"
            except OSError:
                photo.status, photo.error = "재연결 필요", "원본 파일을 찾을 수 없습니다."
        session.jobs = deepcopy(data.get("jobs", []))
        for job in session.jobs:
            if job.get("status") in ("processing", "waiting"):
                job["status"] = "interrupted"
        session.export_options.update(data.get("export_options", {}))
        return session
