from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_script():
    path = Path(__file__).resolve().parents[3] / "scripts" / "init-data-lake-layout.py"
    spec = importlib.util.spec_from_file_location("init_data_lake_layout", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["init_data_lake_layout"] = module
    spec.loader.exec_module(module)
    return module


def test_init_data_lake_layout_splits_ashare_and_crypto(tmp_path):
    module = _load_script()

    layouts = module.initialize_layout(tmp_path / "DATA", ("ashare", "crypto"))

    assert [item.physical_dir for item in layouts] == ["Ashare", "crypto"]
    assert (tmp_path / "DATA" / "_registry" / "data_sources.json").exists()
    assert (tmp_path / "DATA" / "_registry" / "dataset_versions.json").exists()
    for domain in ("Ashare", "crypto"):
        root = tmp_path / "DATA" / domain
        for dirname in ("raw", "normalized", "pit", "features", "experiments", "_manifest"):
            assert (root / dirname).is_dir()
        manifest = json.loads((root / "_manifest" / "lake_manifest.json").read_text(encoding="utf-8"))
        assert manifest["layout_version"] == 1
