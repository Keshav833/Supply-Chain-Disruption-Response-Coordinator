import json
from pathlib import Path

import pytest

from supply_chain.data.sample_data import DATA_DIR, load_dataset

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def dataset():
    return load_dataset()


@pytest.fixture(scope="session")
def expected_outcomes():
    with open(FIXTURES / "expected_outcomes.json", encoding="utf-8") as f:
        return {row["incident_id"]: row for row in json.load(f)}


@pytest.fixture
def data_copy(tmp_path):
    """Writable copy of the JSON datasets for negative (corruption) tests."""
    for name in ("catalog.json", "inventory.json", "suppliers.json", "routes.json", "incidents.json"):
        (tmp_path / name).write_text((DATA_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path
