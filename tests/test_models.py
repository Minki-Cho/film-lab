import json
import pytest
from film_lab.models import default_settings, validate_settings, validate_export_options, validate_recipe, atomic_json


@pytest.mark.parametrize("patch", [{"exposure_ev": 3.1}, {"strength": -1}, {"warmth": 101},
                                   {"tint": float('nan')}, {"strength": True}, {"style_id": "unknown"},
                                   {"grain": {"seed": -1}}, {"exposure_ev": 0.15}])
def test_bad_settings_rejected(patch):
    with pytest.raises(ValueError):
        validate_settings(patch)


def test_defaults_are_independent_and_wb_is_relative():
    a, b = default_settings(), default_settings()
    a['grain']['amount'] = 80
    assert b['grain']['amount'] == 0
    assert validate_settings({'warmth': 20})['wb_mode'] == 'relative'


def test_schema_and_export_contract():
    with pytest.raises(ValueError):
        validate_recipe({'schema_version': 99})
    with pytest.raises(ValueError):
        validate_export_options({'output_dir': '.', 'quality': 79})
    with pytest.raises(ValueError):
        validate_export_options({'output_dir': '.', 'long_edge': 8192})


def test_atomic_json_unicode(tmp_path):
    path = tmp_path / '한글 프로젝트.json'
    atomic_json(path, {'이름': '필름', 'settings': default_settings()})
    assert json.loads(path.read_text('utf-8'))['이름'] == '필름'
    assert list(tmp_path.iterdir()) == [path]
