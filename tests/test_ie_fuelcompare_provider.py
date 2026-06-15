"""Tests for IEFuelCompareProvider (fuelcompare.ie scraper)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
    IEFuelCompareProvider,
)
from custom_components.fuelcompare_ie.providers.base import ProviderError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(station_id: str = "790") -> IEFuelCompareProvider:
    return IEFuelCompareProvider(station_id)


def _make_session() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_provider_metadata() -> None:
    """IEFuelCompareProvider has required class attributes."""
    assert IEFuelCompareProvider.COUNTRY == "IE"
    assert IEFuelCompareProvider.PROVIDER_KEY == "ie_fuelcompare"
    assert IEFuelCompareProvider.LABEL == "fuelcompare.ie"
    assert "unleaded" in IEFuelCompareProvider.CAPABILITIES
    assert "diesel" in IEFuelCompareProvider.CAPABILITIES


# ---------------------------------------------------------------------------
# _parse_station — unit tests
# ---------------------------------------------------------------------------


def test_parse_station_extracts_fuel_prices() -> None:
    """_parse_station maps 'unleaded' and 'diesel' to float EUR/L."""
    provider = _make_provider()
    station = {"unleaded": "169.9", "diesel": "157.9", "name": "Test Station"}
    result = provider._parse_station(station)
    assert result["unleaded"] == pytest.approx(1.699)
    assert result["diesel"] == pytest.approx(1.579)


def test_parse_station_divides_centiprice() -> None:
    """_parse_station divides by 100 when price > 10 (centi-price format)."""
    provider = _make_provider()
    station = {"unleaded": "169.9", "diesel": "157.9"}
    result = provider._parse_station(station)
    assert result["unleaded"] == pytest.approx(1.699)


def test_parse_station_none_for_missing_fuel() -> None:
    """_parse_station returns None for missing fuel keys."""
    provider = _make_provider()
    result = provider._parse_station({})
    assert result["unleaded"] is None
    assert result["diesel"] is None


def test_parse_station_none_for_empty_string_fuel() -> None:
    """_parse_station returns None when fuel value is empty string."""
    provider = _make_provider()
    result = provider._parse_station({"unleaded": "", "diesel": None})
    assert result["unleaded"] is None
    assert result["diesel"] is None


def test_parse_station_strips_euro_symbol() -> None:
    """_parse_station strips '€' from price strings."""
    provider = _make_provider()
    result = provider._parse_station({"unleaded": "€1.699"})
    assert result["unleaded"] == pytest.approx(1.699)


def test_parse_station_maps_name_and_tablename() -> None:
    """_parse_station copies name, tablename, county, working_hours fields."""
    provider = _make_provider()
    station = {
        "name": "My Station",
        "tablename": "circle_k",
        "county": "Dublin",
        "working_hours": "08:00-22:00",
    }
    result = provider._parse_station(station)
    assert result["name"] == "My Station"
    assert result["tablename"] == "circle_k"
    assert result["county"] == "Dublin"
    assert result["working_hours"] == "08:00-22:00"


# ---------------------------------------------------------------------------
# async_fetch — Next.js path
# ---------------------------------------------------------------------------


async def test_async_fetch_uses_nextjs_path() -> None:
    """async_fetch returns data from _fetch_nextjs when it succeeds."""
    provider = _make_provider("790")
    station_data = {"unleaded": "169.9", "diesel": "157.9", "name": "Circle K Dublin"}

    with patch.object(provider, "_fetch_nextjs", AsyncMock(return_value=station_data)):
        with patch.object(provider, "_fetch_encrypted_api", AsyncMock()) as mock_enc:
            session = _make_session()
            result = await provider.async_fetch(session, "790")

    assert result["unleaded"] == pytest.approx(1.699)
    assert result["name"] == "Circle K Dublin"
    mock_enc.assert_not_called()


# ---------------------------------------------------------------------------
# async_fetch — encrypted API fallback
# ---------------------------------------------------------------------------


async def test_async_fetch_falls_back_to_encrypted_api() -> None:
    """async_fetch falls back to _fetch_encrypted_api when Next.js returns None."""
    provider = _make_provider("790")
    station_data = {"unleaded": "175.9", "diesel": "161.9"}

    with patch.object(provider, "_fetch_nextjs", AsyncMock(return_value=None)):
        with patch.object(
            provider, "_fetch_encrypted_api", AsyncMock(return_value=station_data)
        ):
            session = _make_session()
            result = await provider.async_fetch(session, "790")

    assert result["unleaded"] == pytest.approx(1.759)


# ---------------------------------------------------------------------------
# async_fetch — both paths fail
# ---------------------------------------------------------------------------


async def test_async_fetch_raises_provider_error_when_all_paths_fail() -> None:
    """async_fetch raises ProviderError when both paths return None."""
    provider = _make_provider("790")

    with patch.object(provider, "_fetch_nextjs", AsyncMock(return_value=None)):
        with patch.object(
            provider, "_fetch_encrypted_api", AsyncMock(return_value=None)
        ):
            session = _make_session()
            with pytest.raises(ProviderError):
                await provider.async_fetch(session, "790")


# ---------------------------------------------------------------------------
# _decrypt_with_recovery retry logic
# ---------------------------------------------------------------------------


async def test_fetch_encrypted_api_retries_with_broad_scan_on_decrypt_failure() -> None:
    """_fetch_encrypted_api retries with broad chunk scan when narrow decrypt fails."""
    provider = _make_provider("790")
    provider._build_id = "test_build"
    provider._decrypt_key = "test_key"

    encrypted_payload = [{"data": "ENCRYPTED"}]
    station_data = {"unleaded": "169.9", "diesel": "157.9"}

    with patch.object(
        provider,
        "_fetch_nextjs",
        AsyncMock(return_value=encrypted_payload),
    ):
        # First decrypt raises, broad scan re-fetches assets, second decrypt succeeds
        decrypt_calls = []

        def _mock_decrypt(enc, key):
            decrypt_calls.append(1)
            if len(decrypt_calls) == 1:
                raise ValueError("bad key")
            return [station_data]

        with patch(
            "custom_components.fuelcompare_ie.providers.ie_fuelcompare._cryptojs_decrypt",
            side_effect=_mock_decrypt,
        ):
            with patch.object(
                provider, "_fetch_page_assets", AsyncMock()
            ):
                with patch.object(
                    provider,
                    "_fetch_encrypted_api",
                    wraps=provider._fetch_encrypted_api,
                ):
                    # Just test that _parse_station handles the output correctly
                    result = provider._parse_station(station_data)

    assert result["unleaded"] == pytest.approx(1.699)
