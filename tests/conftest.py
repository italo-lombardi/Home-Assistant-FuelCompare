"""Root conftest for FuelCompare.ie tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelcompare_ie.const import CONF_STATION_ID, DOMAIN

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


@pytest.fixture
def mock_station_id() -> str:
    """Return a test station ID."""
    return "12345"


@pytest.fixture
def mock_config_entry(mock_station_id: str) -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=f"Station {mock_station_id}",
        data={CONF_STATION_ID: mock_station_id},
        entry_id="test_entry_id",
        unique_id=f"{DOMAIN}_{mock_station_id}",
    )
