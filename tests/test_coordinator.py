"""Tests for FuelCompareIECoordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.fuelcompare_ie.coordinator import FuelCompareIECoordinator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    json_data: dict | None = None,
    text_data: str | None = None,
):
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    mock_resp.text = AsyncMock(return_value=text_data or "")
    mock_resp.raise_for_status = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_session(*responses):
    """Return a mock session whose .get() call cycles through *responses*.

    Each element of *responses* is either a single mock_resp (reused) or a list
    of mock_resps returned in order.
    """
    session = MagicMock()
    # session.get() is called as a context manager:  async with session.get(...) as r
    call_iter = iter(responses)

    def _get(*_args, **_kwargs):
        resp = next(call_iter)
        return resp

    session.get = MagicMock(side_effect=_get)
    return session


def _station_json(
    unleaded=185,
    diesel=175,
    tablename="circle_k",
    county="Dublin",
    working_hours='{"Monday":"6a.m.-10p.m."}',
    about='{"Accessibility":{"Wheelchair ramp":true}}',
    lastupdated="2024-01-15T10:30:00.000Z",
) -> dict:
    """Return a minimal valid Next.js JSON payload for a station."""
    station: dict = {
        "tablename": tablename,
        "county": county,
        "working_hours": working_hours,
        "about": about,
        "lastupdated": lastupdated,
    }
    if unleaded is not None:
        station["unleaded"] = unleaded
    if diesel is not None:
        station["diesel"] = diesel
    return {"pageProps": {"initialStation": station}}


# ---------------------------------------------------------------------------
# test_fetch_build_id_success
# ---------------------------------------------------------------------------


async def test_fetch_build_id_success(hass: HomeAssistant) -> None:
    """HTML containing buildId JSON fragment returns the extracted ID."""
    html = '<script id="__NEXT_DATA__">{"buildId":"abc123","page":"/station"}</script>'
    html_resp = _make_mock_response(200, text_data=html)
    session = _make_session(html_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        build_id = await coordinator._fetch_build_id(session)

    assert build_id == "abc123"


# ---------------------------------------------------------------------------
# test_fetch_build_id_not_found
# ---------------------------------------------------------------------------


async def test_fetch_build_id_not_found(hass: HomeAssistant) -> None:
    """HTML with no buildId fragment raises UpdateFailed."""
    html_resp = _make_mock_response(200, text_data="<html>no build id here</html>")
    session = _make_session(html_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")

    with pytest.raises(UpdateFailed, match="buildId not found"):
        await coordinator._fetch_build_id(session)


# ---------------------------------------------------------------------------
# test_async_update_data_happy_path
# ---------------------------------------------------------------------------


async def test_async_update_data_happy_path(hass: HomeAssistant) -> None:
    """Valid JSON with prices in cents is divided to produce euro values."""
    data_resp = _make_mock_response(
        200, json_data=_station_json(unleaded=185, diesel=175)
    )
    session = _make_session(data_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")
    coordinator._build_id = "test_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["unleaded"] == pytest.approx(1.85)
    assert data["diesel"] == pytest.approx(1.75)


# ---------------------------------------------------------------------------
# test_async_update_data_prices_already_euros
# ---------------------------------------------------------------------------


async def test_async_update_data_prices_already_euros(hass: HomeAssistant) -> None:
    """Prices already in euro range (≤10) are not divided."""
    data_resp = _make_mock_response(
        200, json_data=_station_json(unleaded=1.85, diesel=1.75)
    )
    session = _make_session(data_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")
    coordinator._build_id = "test_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["unleaded"] == pytest.approx(1.85)
    assert data["diesel"] == pytest.approx(1.75)


# ---------------------------------------------------------------------------
# test_async_update_data_missing_fuel
# ---------------------------------------------------------------------------


async def test_async_update_data_missing_fuel(hass: HomeAssistant) -> None:
    """Station data with no unleaded key stores None for that fuel type."""
    data_resp = _make_mock_response(
        200,
        json_data=_station_json(unleaded=None, diesel=175),
    )
    session = _make_session(data_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")
    coordinator._build_id = "test_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["unleaded"] is None
    assert data["diesel"] == pytest.approx(1.75)


# ---------------------------------------------------------------------------
# test_async_update_data_missing_station
# ---------------------------------------------------------------------------


async def test_async_update_data_missing_station(hass: HomeAssistant) -> None:
    """Response with no initialStation in pageProps raises UpdateFailed."""
    data_resp = _make_mock_response(200, json_data={"pageProps": {}})
    session = _make_session(data_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")
    coordinator._build_id = "test_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed, match="Station data not found"):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# test_async_update_data_stale_buildid
# ---------------------------------------------------------------------------


async def test_async_update_data_stale_buildid(hass: HomeAssistant) -> None:
    """404 on first data fetch triggers build-id refresh; retry returns data."""
    stale_resp = _make_mock_response(404)

    html = '<script>{"buildId":"fresh_build"}</script>'
    html_resp = _make_mock_response(200, text_data=html)

    fresh_resp = _make_mock_response(
        200, json_data=_station_json(unleaded=185, diesel=175)
    )

    session = _make_session(stale_resp, html_resp, fresh_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")
    coordinator._build_id = "stale_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["unleaded"] == pytest.approx(1.85)
    assert coordinator._build_id == "fresh_build"
    assert (
        session.get.call_count == 3
    )  # stale data fetch + html refresh + retry data fetch


# ---------------------------------------------------------------------------
# test_async_update_data_client_error
# ---------------------------------------------------------------------------


async def test_async_update_data_client_error(hass: HomeAssistant) -> None:
    """aiohttp ClientError propagates as UpdateFailed."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    coordinator = FuelCompareIECoordinator(hass, "12345")
    coordinator._build_id = "test_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed, match="Error communicating with API"):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# test_coordinator_stores_metadata
# ---------------------------------------------------------------------------


async def test_coordinator_stores_metadata(hass: HomeAssistant) -> None:
    """Coordinator stores tablename, county, working_hours, about in returned data."""
    data_resp = _make_mock_response(
        200,
        json_data=_station_json(
            tablename="circle_k",
            county="Dublin",
            working_hours='{"Monday":"6a.m.-10p.m."}',
            about='{"Accessibility":{"Wheelchair ramp":true}}',
        ),
    )
    session = _make_session(data_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")
    coordinator._build_id = "test_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["tablename"] == "circle_k"
    assert data["county"] == "Dublin"
    assert data["working_hours"] == '{"Monday":"6a.m.-10p.m."}'
    assert data["about"] == '{"Accessibility":{"Wheelchair ramp":true}}'
