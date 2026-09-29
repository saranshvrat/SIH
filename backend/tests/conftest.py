import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures.make_fixtures import (  # noqa: E402
    make_band_identifiable, make_invalid, make_paired_lr_hr, make_scene,
)


@pytest.fixture
def scene_tif(tmp_path):
    return make_scene(tmp_path / "scene.tif", size=256)


@pytest.fixture
def small_scene_tif(tmp_path):
    return make_scene(tmp_path / "small.tif", size=64)


@pytest.fixture
def band_id_tif(tmp_path):
    return make_band_identifiable(tmp_path / "bands.tif")


@pytest.fixture
def paired_lr_hr(tmp_path):
    return make_paired_lr_hr(tmp_path / "lr.tif", tmp_path / "hr.tif", lr_size=64, sf=4)


@pytest.fixture
def invalid_factory(tmp_path):
    def _make(kind):
        return make_invalid(kind, tmp_path / f"invalid_{kind}.tif")
    return _make
