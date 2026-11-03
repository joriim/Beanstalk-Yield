from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def example_config_path() -> Path:
    return ROOT / "examples" / "field-2026.yaml"
