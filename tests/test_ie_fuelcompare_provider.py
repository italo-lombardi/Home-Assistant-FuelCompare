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
    """_parse_station copies name, tablename, county, working_hours, about fields."""
    provider = _make_provider()
    about = {"accessibility": {"Wheelchair-accessible car park": True}, "amenities": {"Toilets": True}}
    station = {
        "name": "My Station",
        "tablename": "circle_k",
        "county": "Dublin",
        "working_hours": "08:00-22:00",
        "about": about,
    }
    result = provider._parse_station(station)
    assert result["name"] == "My Station"
    assert result["tablename"] == "circle_k"
    assert result["county"] == "Dublin"
    assert result["working_hours"] == "08:00-22:00"
    assert result["about"] == about


def test_parse_station_brand_not_set_from_tablename() -> None:
    """_parse_station does not set brand; StationBrandSensor falls through to tablename formatting."""
    provider = _make_provider()
    station = {"tablename": "circle_k"}
    result = provider._parse_station(station)
    assert result.get("brand") is None


def test_capabilities_include_facility_keys() -> None:
    """CAPABILITIES includes accessibility, amenities, offerings, payments."""
    caps = IEFuelCompareProvider.CAPABILITIES
    for key in ("accessibility", "amenities", "offerings", "payments"):
        assert key in caps, f"{key} missing from CAPABILITIES"


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
    """_decrypt_with_recovery calls _fetch_page_assets(broad=True) after narrow decrypt fails."""
    provider = _make_provider("790")
    # Pre-seed a stale decrypt key so _fetch_encrypted_api skips the initial
    # "key is None" fetch and goes straight to _post_encrypted.
    provider._decrypt_key = "stale_key"

    session = _make_session()
    encrypted_str = "ENCRYPTED_BLOB"
    station_data = {"unleaded": "169.9", "diesel": "157.9", "name": "Circle K"}

    # _post_encrypted returns the ciphertext string immediately.
    post_encrypted_mock = AsyncMock(return_value=encrypted_str)

    # Track _fetch_page_assets calls so we can assert broad=True was used.
    fetch_assets_calls: list[dict] = []

    async def _mock_fetch_page_assets(
        sess: object,
        broad: bool = False,  # noqa: ARG001
    ) -> None:
        fetch_assets_calls.append({"broad": broad})
        # After any asset refresh give the provider a "good" key so the
        # third decrypt attempt (after broad scan) has something to work with.
        provider._decrypt_key = "good_key"

    # _cryptojs_decrypt: fail on attempts 1 and 2, succeed on attempt 3.
    decrypt_call_count = 0

    def _mock_cryptojs_decrypt(enc: str, key: str) -> list:  # noqa: ARG001
        nonlocal decrypt_call_count
        decrypt_call_count += 1
        if decrypt_call_count < 3:
            raise ValueError(f"bad key (attempt {decrypt_call_count})")
        return [[station_data]]

    # Also stub _fetch_nextjs so async_fetch goes straight to the encrypted API path.
    fetch_nextjs_mock = AsyncMock(return_value=None)

    with patch.object(provider, "_fetch_nextjs", fetch_nextjs_mock):
        with patch.object(provider, "_post_encrypted", post_encrypted_mock):
            with patch.object(
                provider, "_fetch_page_assets", side_effect=_mock_fetch_page_assets
            ):
                with patch(
                    "custom_components.fuelcompare_ie.providers.ie_fuelcompare._cryptojs_decrypt",
                    side_effect=_mock_cryptojs_decrypt,
                ):
                    # Call async_fetch so _parse_station is applied and we get StationData.
                    result = await provider.async_fetch(session, "790")

    # _decrypt_with_recovery must have called _fetch_page_assets at least once
    # with broad=True (the second recovery step).
    broad_calls = [c for c in fetch_assets_calls if c["broad"] is True]
    assert broad_calls, (
        "_fetch_page_assets(broad=True) was never called — retry path not exercised"
    )

    # The final result should be the parsed StationData from the successful third attempt.
    assert result is not None
    assert result["unleaded"] == pytest.approx(1.699)
    assert result["diesel"] == pytest.approx(1.579)
    assert result["name"] == "Circle K"


# ---------------------------------------------------------------------------
# ie_fuelcompare.py lines 185-188 — _fetch_nextjs except handler returns None
# ---------------------------------------------------------------------------


async def test_fetch_nextjs_returns_none_on_key_error() -> None:
    """Lines 185-188: _fetch_nextjs returns None when json() raises KeyError."""
    provider = _make_provider("790")
    provider._build_id = "test-build-id"

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(side_effect=KeyError("pageProps"))
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    session = _make_session()
    session.get = MagicMock(return_value=mock_response)

    result = await provider._fetch_nextjs(session)
    assert result is None


# ---------------------------------------------------------------------------
# ie_fuelcompare.py lines 244-245 — _post_encrypted raises ProviderError for non-numeric station ID
# ---------------------------------------------------------------------------


async def test_post_encrypted_raises_provider_error_for_non_numeric_station_id() -> (
    None
):
    """Lines 244-245: _post_encrypted raises ProviderError when station_id cannot be parsed as int."""
    provider = _make_provider("not-a-number")
    session = _make_session()

    with pytest.raises(ProviderError, match="must be numeric"):
        await provider._post_encrypted(session)


# ---------------------------------------------------------------------------
# ie_fuelcompare.py lines 263-269 — _post_encrypted ClientError returns None
# ---------------------------------------------------------------------------


async def test_post_encrypted_returns_none_on_client_error() -> None:
    """Lines 263-269: _post_encrypted returns None when the POST request raises ClientError."""
    from aiohttp import ClientError

    provider = _make_provider("790")

    mock_response = AsyncMock()
    mock_response.__aenter__ = AsyncMock(side_effect=ClientError("connection error"))
    mock_response.__aexit__ = AsyncMock(return_value=False)

    session = _make_session()
    session.post = MagicMock(return_value=mock_response)

    result = await provider._post_encrypted(session)
    assert result is None


# ---------------------------------------------------------------------------
# ie_fuelcompare.py lines 318-322 — _decrypt_with_recovery returns None after broad scan finds no key
# ---------------------------------------------------------------------------


async def test_decrypt_with_recovery_returns_none_when_key_absent_after_broad_scan() -> (
    None
):
    """Lines 318-322: _decrypt_with_recovery returns None when decrypt key is still None after broad scan."""
    provider = _make_provider("790")
    provider._decrypt_key = None  # start with no key

    session = _make_session()

    async def _mock_fetch_page_assets(sess, broad=False):  # noqa: ARG001
        # Never set a decrypt key — simulates failure to find key even after broad scan
        pass

    with patch.object(
        provider, "_fetch_page_assets", side_effect=_mock_fetch_page_assets
    ):
        result = await provider._decrypt_with_recovery(session, "ENCRYPTED_BLOB")

    assert result is None
