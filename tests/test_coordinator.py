"""Tests for FuelCompareIECoordinator."""

from __future__ import annotations

import base64
import hashlib
import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError
from cryptography.hazmat.backends import default_backend as _default_backend
from cryptography.hazmat.primitives.ciphers import (
    Cipher as _Cipher,
    algorithms as _algorithms,
    modes as _modes,
)
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


def _make_session(*responses, post_responses=()):
    """Return a mock session whose .get() call cycles through *responses*.

    Each element of *responses* is either a single mock_resp (reused) or a list
    of mock_resps returned in order. *post_responses* cycles through POST calls.
    """
    session = MagicMock()
    call_iter = iter(responses)
    post_iter = iter(post_responses)

    def _get(*_args, **_kwargs):
        resp = next(call_iter)
        return resp

    def _post(*_args, **_kwargs):
        resp = next(post_iter)
        return resp

    session.get = MagicMock(side_effect=_get)
    session.post = MagicMock(side_effect=_post)
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
# test_fetch_page_assets_extracts_build_id
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_extracts_build_id(hass: HomeAssistant) -> None:
    """HTML containing buildId JSON fragment sets _build_id on coordinator."""
    html = '<script id="__NEXT_DATA__">{"buildId":"abc123","page":"/station"}</script>'
    html_resp = _make_mock_response(200, text_data=html)
    # No JS chunk in HTML so _decrypt_key stays None — that's fine for this test
    session = _make_session(html_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        await coordinator._fetch_page_assets(session)

    assert coordinator._build_id == "abc123"


# ---------------------------------------------------------------------------
# test_fetch_page_assets_no_build_id
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_no_build_id(hass: HomeAssistant) -> None:
    """HTML with no buildId fragment raises UpdateFailed."""
    html_resp = _make_mock_response(200, text_data="<html>no build id here</html>")
    session = _make_session(html_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")

    with pytest.raises(UpdateFailed, match="buildId not found"):
        await coordinator._fetch_page_assets(session)


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
    """Response with no initialStation falls back to encrypted API; if that also fails, raises UpdateFailed."""
    data_resp = _make_mock_response(200, json_data={"pageProps": {}})
    post_resp = _make_mock_response(200, json_data={"success": False})
    session = _make_session(data_resp, post_responses=(post_resp,))

    coordinator = FuelCompareIECoordinator(hass, "12345")
    coordinator._build_id = "test_build"
    coordinator._decrypt_key = "fake_key"

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
    """404 on first data fetch triggers page asset refresh; retry returns data."""
    stale_resp = _make_mock_response(404)

    html = '"buildId":"fresh_build"'
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
    )  # stale data fetch + html refresh (no JS chunk in html) + retry data fetch


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


# ---------------------------------------------------------------------------
# Encrypted API path tests
# ---------------------------------------------------------------------------

# _make_encrypted_payload / _encrypted_api_response / _encrypted_station helpers
# build real CryptoJS-compatible AES payloads so the full decrypt→parse pipeline
# is exercised without hitting the live site.


def _make_encrypted_payload(data: list, passphrase: str) -> str:
    """Encrypt *data* with CryptoJS-compatible AES for use in mock responses."""
    import os

    salt = os.urandom(8)
    plaintext = _json.dumps(data).encode()
    # PKCS7 pad to 16-byte boundary
    pad_len = 16 - (len(plaintext) % 16)
    plaintext += bytes([pad_len] * pad_len)

    d, d_i = b"", b""
    while len(d) < 48:
        d_i = hashlib.md5(d_i + passphrase.encode() + salt).digest()
        d += d_i
    key, iv = d[:32], d[32:48]

    cipher = _Cipher(_algorithms.AES(key), _modes.CBC(iv), backend=_default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()

    return base64.b64encode(b"Salted__" + salt + ciphertext).decode()


_TEST_DECRYPT_KEY = "a" * 64  # 64-char hex passphrase used across encrypted API tests


def _encrypted_api_response(station: dict) -> dict:
    """Return a mock encrypted API payload wrapping *station*."""
    encrypted = _make_encrypted_payload([[station], {}], _TEST_DECRYPT_KEY)
    return {"success": True, "data": encrypted}


def _encrypted_station(
    unleaded="179.90",
    diesel="189.90",
    tablename="circle_k",
    state="Co. Dublin",
    working_hours='{"Monday":"8a.m.-10p.m."}',
    about='{"Offerings":{"Diesel fuel":true}}',
    lastupdated="2026-05-18T03:50:39.000Z",
) -> dict:
    """Return a minimal station dict as returned by the encrypted API."""
    return {
        "id": 790,
        "tablename": tablename,
        "state": state,
        "working_hours": working_hours,
        "about": about,
        "lastupdated": lastupdated,
        "unleaded": unleaded,
        "diesel": diesel,
    }


# ---------------------------------------------------------------------------
# test_fetch_page_assets_extracts_decrypt_key
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_extracts_decrypt_key(hass: HomeAssistant) -> None:
    """HTML with station JS chunk causes _decrypt_key to be extracted from the chunk."""
    html = (
        '"buildId":"abc123" '
        'src="/_next/static/chunks/pages/station/%5Bid%5D-deadbeef.js"'
    )
    js = f'AES.decrypt(e,"{_TEST_DECRYPT_KEY}")'
    html_resp = _make_mock_response(200, text_data=html)
    js_resp = _make_mock_response(200, text_data=js)
    session = _make_session(html_resp, js_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")
    await coordinator._fetch_page_assets(session)

    assert coordinator._decrypt_key == _TEST_DECRYPT_KEY
    assert coordinator._build_id == "abc123"


# ---------------------------------------------------------------------------
# test_fetch_page_assets_no_js_chunk_leaves_key_unchanged
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_no_js_chunk_leaves_key_unchanged(
    hass: HomeAssistant,
) -> None:
    """HTML with no station JS chunk URL leaves _decrypt_key unchanged."""
    html = '"buildId":"abc123"'  # no chunk src
    html_resp = _make_mock_response(200, text_data=html)
    session = _make_session(html_resp)

    coordinator = FuelCompareIECoordinator(hass, "12345")
    coordinator._decrypt_key = "previously_cached_key"
    await coordinator._fetch_page_assets(session)

    assert coordinator._decrypt_key == "previously_cached_key"


# ---------------------------------------------------------------------------
# test_encrypted_api_path_success
# ---------------------------------------------------------------------------


async def test_encrypted_api_path_success(hass: HomeAssistant) -> None:
    """Encrypted API fallback decrypts correctly and returns parsed prices."""
    nextjs_resp = _make_mock_response(
        200, json_data={"pageProps": {"initialStation": None}}
    )
    post_resp = _make_mock_response(
        200, json_data=_encrypted_api_response(_encrypted_station())
    )
    session = _make_session(nextjs_resp, post_responses=(post_resp,))

    coordinator = FuelCompareIECoordinator(hass, "790")
    coordinator._build_id = "test_build"
    coordinator._decrypt_key = _TEST_DECRYPT_KEY

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["unleaded"] == pytest.approx(1.799)
    assert data["diesel"] == pytest.approx(1.899)
    assert data["tablename"] == "circle_k"


# ---------------------------------------------------------------------------
# test_encrypted_api_state_mapped_to_county
# ---------------------------------------------------------------------------


async def test_encrypted_api_state_mapped_to_county(hass: HomeAssistant) -> None:
    """Encrypted API 'state' field is mapped to 'county' for sensor compatibility."""
    nextjs_resp = _make_mock_response(
        200, json_data={"pageProps": {"initialStation": None}}
    )
    post_resp = _make_mock_response(
        200,
        json_data=_encrypted_api_response(_encrypted_station(state="Co. Dublin")),
    )
    session = _make_session(nextjs_resp, post_responses=(post_resp,))

    coordinator = FuelCompareIECoordinator(hass, "790")
    coordinator._build_id = "test_build"
    coordinator._decrypt_key = _TEST_DECRYPT_KEY

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["county"] == "Co. Dublin"


# ---------------------------------------------------------------------------
# test_encrypted_api_stale_key_refreshed_and_retried
# ---------------------------------------------------------------------------


async def test_encrypted_api_stale_key_refreshed_and_retried(
    hass: HomeAssistant,
) -> None:
    """Decrypt failure triggers key refresh; second attempt with new key succeeds."""
    nextjs_resp = _make_mock_response(
        200, json_data={"pageProps": {"initialStation": None}}
    )
    # HTML and JS chunk for key refresh
    html = (
        '"buildId":"build2" '
        'src="/_next/static/chunks/pages/station/%5Bid%5D-newchunk.js"'
    )
    new_key = "b" * 64
    js = f'AES.decrypt(e,"{new_key}")'
    html_resp = _make_mock_response(200, text_data=html)
    js_resp = _make_mock_response(200, text_data=js)

    # POST payload encrypted with the *new* key
    post_resp = _make_mock_response(
        200,
        json_data={
            "success": True,
            "data": _make_encrypted_payload(
                [[_encrypted_station(unleaded="169.9", diesel="179.9")], {}], new_key
            ),
        },
    )
    session = _make_session(
        nextjs_resp, html_resp, js_resp, post_responses=(post_resp,)
    )

    coordinator = FuelCompareIECoordinator(hass, "790")
    coordinator._build_id = "build1"
    coordinator._decrypt_key = "a" * 64  # stale — won't decrypt the new payload

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["unleaded"] == pytest.approx(1.699)
    assert coordinator._decrypt_key == new_key


# ---------------------------------------------------------------------------
# test_encrypted_api_both_paths_fail_raises
# ---------------------------------------------------------------------------


async def test_encrypted_api_both_paths_fail_raises(hass: HomeAssistant) -> None:
    """Both Next.js and encrypted API returning no data raises UpdateFailed."""
    nextjs_resp = _make_mock_response(
        200, json_data={"pageProps": {"initialStation": None}}
    )
    post_resp = _make_mock_response(200, json_data={"success": False})
    session = _make_session(nextjs_resp, post_responses=(post_resp,))

    coordinator = FuelCompareIECoordinator(hass, "790")
    coordinator._build_id = "test_build"
    coordinator._decrypt_key = _TEST_DECRYPT_KEY

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed, match="Station data not found"):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# test_encrypted_api_decrypt_key_unavailable_skips_api
# ---------------------------------------------------------------------------


async def test_encrypted_api_decrypt_key_unavailable_skips_api(
    hass: HomeAssistant,
) -> None:
    """If key cannot be extracted from JS, encrypted API is skipped and UpdateFailed raised."""
    nextjs_resp = _make_mock_response(
        200, json_data={"pageProps": {"initialStation": None}}
    )
    # _fetch_page_assets called by encrypted API path — HTML has no chunk URL
    html_resp = _make_mock_response(200, text_data='"buildId":"build1"')
    session = _make_session(nextjs_resp, html_resp)

    coordinator = FuelCompareIECoordinator(hass, "790")
    coordinator._build_id = "test_build"
    # _decrypt_key stays None — JS chunk not reachable

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed, match="Station data not found"):
            await coordinator._async_update_data()
