from copy import deepcopy
from pathlib import Path
import json

import pytest

from film_lab.models import default_settings, sha256_file
from film_lab.session import Session


def source(path, photo_id="photo"):
    path.write_bytes(b"test source identity")
    stat = path.stat()
    return {"id": photo_id, "path": str(path), "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "sha256": sha256_file(path), "supported": True}


def test_project_duplicate_limit_and_history(tmp_path):
    session = Session(tmp_path / "project.json")
    original = source(tmp_path / "사진.ARW")
    photo = session.add(original)
    assert session.add(original) is None
    for contrast in range(1, 31):
        photo.update({**photo.settings, "contrast": contrast})
    assert len(photo.undo_stack) == 20
    for _ in range(20):
        assert photo.undo()
    assert photo.settings["contrast"] == 10
    assert not photo.undo()
    assert photo.redo()
    photo.update({**photo.settings, "contrast": 5})
    assert not photo.redo()
    for i in range(99):
        session.add({"id": str(i), "path": str(tmp_path / f"{i}.ARW")})
    with pytest.raises(ValueError, match="100"):
        session.add({"id": "overflow", "path": str(tmp_path / "overflow.ARW")})


def test_job_snapshot_survives_edit_and_restart(tmp_path):
    session = Session(tmp_path / "project.json")
    photo = session.add(source(tmp_path / "사진.ARW"))
    photo.update({**photo.settings, "style_id": "documentary", "contrast": 15})
    options = {"format": "jpeg", "quality": 95, "output_dir": str(tmp_path / "output")}
    job = session.snapshot_job([photo], options)
    assert job["items"][0]["recipe"]["profile"]["sha256"]
    photo.update({**photo.settings, "contrast": -30})
    options["quality"] = 80
    assert job["items"][0]["recipe"]["settings"]["contrast"] == 15
    assert job["options"]["quality"] == 95
    session.save()
    restored = Session.load(session.path)
    assert restored.photos[0].settings["contrast"] == -30
    assert restored.photos[0].undo()
    assert restored.photos[0].settings["contrast"] == 15
    assert restored.jobs[0]["status"] == "interrupted"
    assert not list(tmp_path.glob("*.tmp"))
    assert len(list(tmp_path.glob("*.ARW"))) == 1


def test_source_changes_require_reconnection(tmp_path):
    session = Session(tmp_path / "project.json")
    photo = session.add(source(tmp_path / "사진.ARW"))
    session.save()
    Path(photo.path).write_bytes(b"changed")
    loaded = Session.load(session.path)
    assert loaded.photos[0].status == "재연결 필요"
    Path(photo.path).unlink()
    assert Session.load(session.path).photos[0].status == "재연결 필요"


def test_reject_unknown_project_schema(tmp_path):
    project = tmp_path / "project.json"
    project.write_text(json.dumps({"schema_version": 100, "photos": []}))
    with pytest.raises(ValueError, match="버전"):
        Session.load(project)


def test_recovery_rejects_tampered_output(tmp_path):
    from PIL import Image
    from film_lab.worker import _verify_jobs
    image_path = tmp_path / "export.jpg"
    Image.new("RGB", (12, 8), "red").save(image_path)
    recipe_path = tmp_path / "export.recipe.json"
    recipe_path.write_text(json.dumps({"schema_version": 1, "engine_version": "1.0.0", "settings": default_settings(),
                                     "output": {"sha256": sha256_file(image_path)}}))
    jobs = [{"status": "completed", "results": [{"status": "completed", "path": str(image_path), "recipe_path": str(recipe_path)}]}]
    assert _verify_jobs(deepcopy(jobs))[0]["status"] == "completed"
    Image.new("RGB", (12, 8), "blue").save(image_path)
    restored = _verify_jobs(jobs)
    assert restored[0]["status"] == "partial"
    assert "SHA-256" in restored[0]["results"][0]["error"]
