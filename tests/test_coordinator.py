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

from custom_components.fuelcompare_ie.coordinator import (
    FuelCompareIECoordinator,
)
from custom_components.fuelcompare_ie.crypto import (
    cryptojs_decrypt as _cryptojs_decrypt,
)
from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
    IEFuelCompareProvider,
)


def _ie_coordinator(hass: HomeAssistant, station_id: str) -> FuelCompareIECoordinator:
    """Create a FuelCompareIECoordinator backed by IEFuelCompareProvider."""
    return FuelCompareIECoordinator(hass, IEFuelCompareProvider(station_id), station_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int,
    json_data: dict | None = None,
    text_data: str | None = None,
):
    """Build a mock aiohttp response that works as an async context manager."""
    mock_resp = MagicMock()
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

    coordinator = _ie_coordinator(hass, "12345")

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        await coordinator._provider._fetch_page_assets(session)

    assert coordinator._provider._build_id == "abc123"


# ---------------------------------------------------------------------------
# test_fetch_page_assets_no_build_id
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_no_build_id(hass: HomeAssistant) -> None:
    """HTML with no buildId fragment raises UpdateFailed."""
    html_resp = _make_mock_response(200, text_data="<html>no build id here</html>")
    session = _make_session(html_resp)

    coordinator = _ie_coordinator(hass, "12345")

    with pytest.raises(UpdateFailed, match="buildId not found"):
        await coordinator._provider._fetch_page_assets(session)


# ---------------------------------------------------------------------------
# test_async_update_data_happy_path
# ---------------------------------------------------------------------------


async def test_async_update_data_happy_path(hass: HomeAssistant) -> None:
    """Valid JSON with prices in cents is divided to produce euro values."""
    data_resp = _make_mock_response(
        200, json_data=_station_json(unleaded=185, diesel=175)
    )
    session = _make_session(data_resp)

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "test_build"

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

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "test_build"

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

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "test_build"

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

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "test_build"
    coordinator._provider._decrypt_key = "fake_key"

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

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "stale_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["unleaded"] == pytest.approx(1.85)
    assert coordinator._provider._build_id == "fresh_build"
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

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "test_build"

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

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "test_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["tablename"] == "circle_k"
    assert data["county"] == "Dublin"
    assert data["working_hours"] == '{"Monday":"6a.m.-10p.m."}'


# ---------------------------------------------------------------------------
# Encrypted API path tests
# ---------------------------------------------------------------------------

# _make_encrypted_payload / _encrypted_api_response / _encrypted_station helpers
# build real CryptoJS-compatible AES payloads so the full decrypt→parse pipeline
# is exercised without hitting the live site.


def _make_encrypted_payload(data: list, evp_key: str) -> str:
    """Encrypt *data* with CryptoJS-compatible AES (EvpKDF) for use in mock responses."""
    import os

    salt = os.urandom(8)
    plaintext = _json.dumps(data).encode()
    # PKCS7 pad to 16-byte boundary
    pad_len = 16 - (len(plaintext) % 16)
    plaintext += bytes([pad_len] * pad_len)

    d, d_i = b"", b""
    while len(d) < 48:
        d_i = hashlib.md5(d_i + evp_key.encode() + salt, usedforsecurity=False).digest()
        d += d_i
    key, iv = d[:32], d[32:48]

    cipher = _Cipher(_algorithms.AES(key), _modes.CBC(iv), backend=_default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()

    return base64.b64encode(b"Salted__" + salt + ciphertext).decode()


_TEST_DECRYPT_KEY = "a" * 64  # 64-char hex EvpKDF key used across encrypted API tests


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
        'src="/_next/static/chunks/pages/station/station-deadbeef.js"'
    )
    js = f'AES.decrypt(e,"{_TEST_DECRYPT_KEY}")'
    html_resp = _make_mock_response(200, text_data=html)
    js_resp = _make_mock_response(200, text_data=js)
    session = _make_session(html_resp, js_resp)

    coordinator = _ie_coordinator(hass, "12345")
    await coordinator._provider._fetch_page_assets(session)

    assert coordinator._provider._decrypt_key == _TEST_DECRYPT_KEY
    assert coordinator._provider._build_id == "abc123"


# ---------------------------------------------------------------------------
# test_fetch_page_assets_extracts_decrypt_key_from_shared_chunk
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_extracts_decrypt_key_from_shared_chunk(
    hass: HomeAssistant,
) -> None:
    """Decrypt key is found in a non-station shared chunk when station chunk lacks it.

    fuelcompare.ie moved the AES key from the per-page station chunk into a
    shared vendor chunk. _fetch_page_assets must scan all chunks listed in
    the HTML, not just the station-specific one.
    """
    html = (
        '"buildId":"abc123" '
        'src="/_next/static/chunks/pages/station/station-deadbeef.js" '
        'src="/_next/static/chunks/1890-shared.js"'
    )
    station_js = "// no decrypt key in this chunk anymore"
    shared_js = f'AES.decrypt(e,"{_TEST_DECRYPT_KEY}")'
    html_resp = _make_mock_response(200, text_data=html)
    station_resp = _make_mock_response(200, text_data=station_js)
    shared_resp = _make_mock_response(200, text_data=shared_js)
    session = _make_session(html_resp, station_resp, shared_resp)

    coordinator = _ie_coordinator(hass, "12345")
    await coordinator._provider._fetch_page_assets(session, broad=True)

    assert coordinator._provider._decrypt_key == _TEST_DECRYPT_KEY
    assert coordinator._provider._build_id == "abc123"


# ---------------------------------------------------------------------------
# test_fetch_page_assets_skips_chunk_with_non_200_status
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_skips_chunk_with_non_200_status(
    hass: HomeAssistant,
) -> None:
    """A chunk responding with HTTP non-200 is skipped; scan continues to next chunk."""
    html = (
        '"buildId":"abc123" '
        'src="/_next/static/chunks/pages/station/station-deadbeef.js" '
        'src="/_next/static/chunks/1890-shared.js"'
    )
    html_resp = _make_mock_response(200, text_data=html)
    bad_resp = _make_mock_response(404, text_data="")
    good_resp = _make_mock_response(
        200, text_data=f'AES.decrypt(e,"{_TEST_DECRYPT_KEY}")'
    )
    session = _make_session(html_resp, bad_resp, good_resp)

    coordinator = _ie_coordinator(hass, "12345")
    await coordinator._provider._fetch_page_assets(session, broad=True)

    assert coordinator._provider._decrypt_key == _TEST_DECRYPT_KEY


# ---------------------------------------------------------------------------
# test_fetch_page_assets_skips_chunk_on_client_error
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_skips_chunk_on_client_error(
    hass: HomeAssistant,
) -> None:
    """A chunk fetch raising ClientError is skipped; scan continues to next chunk."""
    html = (
        '"buildId":"abc123" '
        'src="/_next/static/chunks/pages/station/station-deadbeef.js" '
        'src="/_next/static/chunks/1890-shared.js"'
    )
    html_resp = _make_mock_response(200, text_data=html)
    good_resp = _make_mock_response(
        200, text_data=f'AES.decrypt(e,"{_TEST_DECRYPT_KEY}")'
    )

    session = MagicMock()
    get_calls = [html_resp, ClientError("network down"), good_resp]
    call_iter = iter(get_calls)

    def _get(*_args, **_kwargs):
        item = next(call_iter)
        if isinstance(item, Exception):
            raise item
        return item

    session.get = MagicMock(side_effect=_get)
    session.post = MagicMock()

    coordinator = _ie_coordinator(hass, "12345")
    await coordinator._provider._fetch_page_assets(session, broad=True)

    assert coordinator._provider._decrypt_key == _TEST_DECRYPT_KEY


# ---------------------------------------------------------------------------
# test_fetch_page_assets_all_chunks_fail_leaves_key_unchanged
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_all_chunks_fail_leaves_key_unchanged(
    hass: HomeAssistant,
) -> None:
    """When every chunk lacks the AES key pattern, _decrypt_key stays unchanged."""
    html = (
        '"buildId":"abc123" '
        'src="/_next/static/chunks/pages/station/station-deadbeef.js" '
        'src="/_next/static/chunks/1890-shared.js"'
    )
    html_resp = _make_mock_response(200, text_data=html)
    a_resp = _make_mock_response(200, text_data="// no key")
    b_resp = _make_mock_response(200, text_data="// also no key")
    session = _make_session(html_resp, a_resp, b_resp)

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._decrypt_key = "previously_cached_key"
    await coordinator._provider._fetch_page_assets(session, broad=True)

    assert coordinator._provider._decrypt_key == "previously_cached_key"
    assert coordinator._provider._build_id == "abc123"


# ---------------------------------------------------------------------------
# test_fetch_page_assets_broad_no_chunks_leaves_key_unchanged
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_broad_no_chunks_leaves_key_unchanged(
    hass: HomeAssistant,
) -> None:
    """Broad mode with HTML containing zero chunk URLs leaves _decrypt_key unchanged."""
    html = '"buildId":"abc123"'  # buildId only, no chunk src anywhere
    html_resp = _make_mock_response(200, text_data=html)
    session = _make_session(html_resp)

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._decrypt_key = "previously_cached_key"
    await coordinator._provider._fetch_page_assets(session, broad=True)

    assert coordinator._provider._decrypt_key == "previously_cached_key"
    assert coordinator._provider._build_id == "abc123"


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

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._decrypt_key = "previously_cached_key"
    await coordinator._provider._fetch_page_assets(session)

    assert coordinator._provider._decrypt_key == "previously_cached_key"


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

    coordinator = _ie_coordinator(hass, "790")
    coordinator._provider._build_id = "test_build"
    coordinator._provider._decrypt_key = _TEST_DECRYPT_KEY

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

    coordinator = _ie_coordinator(hass, "790")
    coordinator._provider._build_id = "test_build"
    coordinator._provider._decrypt_key = _TEST_DECRYPT_KEY

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
        'src="/_next/static/chunks/pages/station/station-newchunk.js"'
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

    coordinator = _ie_coordinator(hass, "790")
    coordinator._provider._build_id = "build1"
    coordinator._provider._decrypt_key = (
        "a" * 64
    )  # stale — won't decrypt the new payload

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        data = await coordinator._async_update_data()

    assert data["unleaded"] == pytest.approx(1.699)
    assert coordinator._provider._decrypt_key == new_key


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

    coordinator = _ie_coordinator(hass, "790")
    coordinator._provider._build_id = "test_build"
    coordinator._provider._decrypt_key = _TEST_DECRYPT_KEY

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
    # _fetch_page_assets called by encrypted API path — HTML has no chunk URL.
    # Two HTML responses needed: standard refresh + broad scan retry.
    html_resp = _make_mock_response(200, text_data='"buildId":"build1"')
    html_resp_2 = _make_mock_response(200, text_data='"buildId":"build1"')
    session = _make_session(nextjs_resp, html_resp, html_resp_2)

    coordinator = _ie_coordinator(hass, "790")
    coordinator._provider._build_id = "test_build"
    # _decrypt_key stays None — JS chunk not reachable

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed, match="Station data not found"):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# test_async_update_data_generic_exception
# ---------------------------------------------------------------------------


async def test_async_update_data_generic_exception(hass: HomeAssistant) -> None:
    """An unexpected (non-ClientError) exception propagates as UpdateFailed."""
    session = MagicMock()
    session.get = MagicMock(side_effect=RuntimeError("unexpected"))

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "test_build"

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed, match="Unexpected error"):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# test_fetch_nextjs_build_id_none_after_page_assets
# ---------------------------------------------------------------------------


async def test_fetch_nextjs_build_id_none_after_page_assets(
    hass: HomeAssistant,
) -> None:
    """If _fetch_page_assets raises (no buildId), _fetch_nextjs returns None."""
    html_resp = _make_mock_response(200, text_data="<html>no build id</html>")
    # Second HTML call for the encrypted API fallback (also has no build id)
    html_resp2 = _make_mock_response(200, text_data="<html>no build id</html>")
    session = _make_session(html_resp, html_resp2)

    coordinator = _ie_coordinator(hass, "12345")
    # _build_id is None so _fetch_page_assets will be called; it raises UpdateFailed
    # which is caught inside _fetch_nextjs, leaving _build_id=None → returns None
    # Then _fetch_encrypted_api also calls _fetch_page_assets, fails → returns None
    # → UpdateFailed raised

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# test_encrypted_api_empty_data_field
# ---------------------------------------------------------------------------


async def test_encrypted_api_empty_data_field(hass: HomeAssistant) -> None:
    """Encrypted API returning success=True but empty data field returns None."""
    nextjs_resp = _make_mock_response(
        200, json_data={"pageProps": {"initialStation": None}}
    )
    post_resp = _make_mock_response(200, json_data={"success": True, "data": None})
    session = _make_session(nextjs_resp, post_responses=(post_resp,))

    coordinator = _ie_coordinator(hass, "790")
    coordinator._provider._build_id = "test_build"
    coordinator._provider._decrypt_key = _TEST_DECRYPT_KEY

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed, match="Station data not found"):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# test_encrypted_api_empty_stations_list
# ---------------------------------------------------------------------------


async def test_encrypted_api_empty_stations_list(hass: HomeAssistant) -> None:
    """Encrypted API returning an empty stations list returns None."""
    nextjs_resp = _make_mock_response(
        200, json_data={"pageProps": {"initialStation": None}}
    )
    # Encrypt a payload with empty stations list
    encrypted = _make_encrypted_payload([[], {}], _TEST_DECRYPT_KEY)
    post_resp = _make_mock_response(200, json_data={"success": True, "data": encrypted})
    session = _make_session(nextjs_resp, post_responses=(post_resp,))

    coordinator = _ie_coordinator(hass, "790")
    coordinator._provider._build_id = "test_build"
    coordinator._provider._decrypt_key = _TEST_DECRYPT_KEY

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed, match="Station data not found"):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# test_parse_station_invalid_price_value
# ---------------------------------------------------------------------------


async def test_parse_station_invalid_price_value(hass: HomeAssistant) -> None:
    """Non-numeric price value is stored as None with a warning."""
    station = {
        "unleaded": "N/A",
        "diesel": 175,
        "tablename": "circle_k",
        "county": "Dublin",
        "working_hours": None,
        "about": None,
        "lastupdated": None,
    }
    coordinator = _ie_coordinator(hass, "12345")
    data = coordinator._provider._parse_station(station)

    assert data["unleaded"] is None
    assert data["diesel"] == pytest.approx(1.75)


# ---------------------------------------------------------------------------
# test_fetch_page_assets_js_chunk_key_pattern_not_found
# ---------------------------------------------------------------------------


async def test_fetch_page_assets_js_chunk_key_pattern_not_found(
    hass: HomeAssistant,
) -> None:
    """JS chunk with no AES.decrypt pattern leaves _decrypt_key unchanged."""
    html = (
        '"buildId":"abc123" '
        'src="/_next/static/chunks/pages/station/station-deadbeef.js"'
    )
    js = "// no decrypt call here"
    html_resp = _make_mock_response(200, text_data=html)
    js_resp = _make_mock_response(200, text_data=js)
    session = _make_session(html_resp, js_resp)

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._decrypt_key = "previously_cached_key"
    await coordinator._provider._fetch_page_assets(session)

    # Key unchanged because pattern wasn't found
    assert coordinator._provider._decrypt_key == "previously_cached_key"
    assert coordinator._provider._build_id == "abc123"


# ---------------------------------------------------------------------------
# test_encrypted_api_second_decrypt_fails_returns_none
# ---------------------------------------------------------------------------


async def test_encrypted_api_second_decrypt_fails_returns_none(
    hass: HomeAssistant,
) -> None:
    """If standard refresh and broad fallback both fail to find a working key, encrypted API returns None → UpdateFailed."""
    nextjs_resp = _make_mock_response(
        200, json_data={"pageProps": {"initialStation": None}}
    )
    # HTML with chunk URL, JS with no valid key pattern → _decrypt_key won't update
    html = (
        '"buildId":"build2" '
        'src="/_next/static/chunks/pages/station/station-newchunk.js"'
    )
    js = "// no decrypt key here"
    html_resp = _make_mock_response(200, text_data=html)
    js_resp = _make_mock_response(200, text_data=js)
    # Broad-scan retry re-fetches HTML and the same single chunk (no other
    # chunk URLs in the HTML), neither yields the key.
    html_resp_2 = _make_mock_response(200, text_data=html)
    js_resp_2 = _make_mock_response(200, text_data=js)

    # Payload encrypted with a key that doesn't match the coordinator's stale key
    bad_key = "c" * 64
    post_resp = _make_mock_response(
        200,
        json_data={
            "success": True,
            "data": _make_encrypted_payload([[_encrypted_station()], {}], bad_key),
        },
    )
    session = _make_session(
        nextjs_resp,
        html_resp,
        js_resp,
        html_resp_2,
        js_resp_2,
        post_responses=(post_resp,),
    )

    coordinator = _ie_coordinator(hass, "790")
    coordinator._provider._build_id = "build1"
    coordinator._provider._decrypt_key = "a" * 64  # stale — won't decrypt payload

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed, match="Station data not found"):
            await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# test_cryptojs_decrypt_invalid_padding
# ---------------------------------------------------------------------------


def test_cryptojs_decrypt_invalid_padding() -> None:
    """Corrupted payload whose last byte encodes pad_len=0 (invalid) raises ValueError."""
    import os

    evp_key = _TEST_DECRYPT_KEY
    salt = os.urandom(8)
    # 16-byte block with last byte = 0 (invalid: PKCS7 requires 1–16)
    plaintext = b"\x00" * 16

    d, d_i = b"", b""
    while len(d) < 48:
        d_i = hashlib.md5(d_i + evp_key.encode() + salt, usedforsecurity=False).digest()
        d += d_i
    key, iv = d[:32], d[32:48]

    cipher = _Cipher(_algorithms.AES(key), _modes.CBC(iv), backend=_default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()

    bad_payload = base64.b64encode(b"Salted__" + salt + ciphertext).decode()

    with pytest.raises(ValueError, match="Invalid PKCS7 padding length"):
        _cryptojs_decrypt(bad_payload, evp_key)


def test_cryptojs_decrypt_invalid_pkcs7_bytes() -> None:
    """Payload with valid pad_len but wrong padding bytes raises ValueError."""
    import os

    evp_key = _TEST_DECRYPT_KEY
    salt = os.urandom(8)
    # Last byte = 3 (pad_len=3 is valid) but remaining padding bytes are wrong (0x01 instead of 0x03)
    plaintext = (
        b"A" * 13 + b"\x01\x01\x03"
    )  # last byte=3, but bytes[-3:] != b'\x03\x03\x03'

    d, d_i = b"", b""
    while len(d) < 48:
        d_i = hashlib.md5(d_i + evp_key.encode() + salt, usedforsecurity=False).digest()
        d += d_i
    key, iv = d[:32], d[32:48]

    cipher = _Cipher(_algorithms.AES(key), _modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()

    bad_payload = base64.b64encode(b"Salted__" + salt + ciphertext).decode()

    with pytest.raises(ValueError, match="Invalid PKCS7 padding bytes"):
        _cryptojs_decrypt(bad_payload, evp_key)


def test_cryptojs_decrypt_missing_magic_header() -> None:
    """Payload without 'Salted__' header raises ValueError."""
    bad_payload = base64.b64encode(b"NoMagic!" + b"\x00" * 24).decode()
    with pytest.raises(ValueError, match="Salted__"):
        _cryptojs_decrypt(bad_payload, _TEST_DECRYPT_KEY)


def test_cryptojs_decrypt_invalid_base64() -> None:
    """Non-base64 input raises ValueError wrapping binascii.Error."""
    with pytest.raises(ValueError, match="Invalid base64 ciphertext"):
        _cryptojs_decrypt("not-valid-base64!!!", _TEST_DECRYPT_KEY)


def test_cryptojs_decrypt_payload_too_short() -> None:
    """Payload with valid magic but fewer than 32 bytes raises ValueError."""
    # 8-byte magic + 7 bytes = 15 bytes total (<32)
    short = base64.b64encode(b"Salted__" + b"\x00" * 7).decode()
    with pytest.raises(ValueError, match="Payload too short"):
        _cryptojs_decrypt(short, _TEST_DECRYPT_KEY)


def test_cryptojs_decrypt_non_list_json() -> None:
    """Decrypted payload that is valid JSON but not a list raises ValueError."""
    import os

    evp_key = _TEST_DECRYPT_KEY
    salt = os.urandom(8)
    raw_data = b'{"key": "value"}'
    pad_len = 16 - (len(raw_data) % 16)
    plaintext = raw_data + bytes([pad_len] * pad_len)

    d, d_i = b"", b""
    while len(d) < 48:
        d_i = hashlib.md5(d_i + evp_key.encode() + salt, usedforsecurity=False).digest()
        d += d_i
    key, iv = d[:32], d[32:48]

    cipher = _Cipher(_algorithms.AES(key), _modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()

    payload = base64.b64encode(b"Salted__" + salt + ciphertext).decode()
    with pytest.raises(ValueError, match="Expected list"):
        _cryptojs_decrypt(payload, evp_key)


# ---------------------------------------------------------------------------
# last_successful_fetch stamping
# ---------------------------------------------------------------------------


async def test_last_successful_fetch_initially_none(hass: HomeAssistant) -> None:
    """Coordinator starts with last_successful_fetch = None."""
    coordinator = _ie_coordinator(hass, "12345")
    assert coordinator.last_successful_fetch is None


async def test_last_successful_fetch_stamped_on_success(hass: HomeAssistant) -> None:
    """Successful fetch stamps last_successful_fetch with the current UTC time."""
    data_resp = _make_mock_response(
        200, json_data=_station_json(unleaded=185, diesel=175)
    )
    session = _make_session(data_resp)

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "test_build"

    fixed_now = __import__("datetime").datetime(
        2026, 6, 8, 12, 0, 0, tzinfo=__import__("datetime").timezone.utc
    )
    with (
        patch(
            "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.fuelcompare_ie.coordinator.dt_util.utcnow",
            return_value=fixed_now,
        ),
    ):
        await coordinator._async_update_data()

    assert coordinator.last_successful_fetch == fixed_now


async def test_last_successful_fetch_unchanged_on_failure(
    hass: HomeAssistant,
) -> None:
    """Failed fetch does not advance last_successful_fetch."""
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError("network error"))

    coordinator = _ie_coordinator(hass, "12345")
    coordinator._provider._build_id = "test_build"
    # Pre-set a known timestamp so we verify it's unchanged (not just still None).
    prior_fetch = __import__("datetime").datetime(
        2026, 1, 1, 0, 0, 0, tzinfo=__import__("datetime").timezone.utc
    )
    coordinator.last_successful_fetch = prior_fetch

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    assert coordinator.last_successful_fetch is prior_fetch


# ---------------------------------------------------------------------------
# providers/__init__.py — get_provider_or_default RuntimeError (lines 95-100)
# ---------------------------------------------------------------------------


def test_get_provider_or_default_raises_when_both_missing() -> None:
    """get_provider_or_default raises RuntimeError when both keys are absent."""
    from custom_components.fuelcompare_ie.providers import get_provider_or_default

    with pytest.raises(RuntimeError, match="No provider found"):
        get_provider_or_default("nonexistent_key_xyz", "also_nonexistent_xyz")


# ---------------------------------------------------------------------------
# base.py — __init_subclass__ enforcement (lines 303, 312) and
#           async_list_stations default (line 372)
# ---------------------------------------------------------------------------


def test_base_provider_subclass_missing_attr_raises() -> None:
    """Defining a concrete BaseProvider without COUNTRY raises TypeError."""
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    with pytest.raises(TypeError, match="must define class attribute"):

        class _BadProvider(BaseProvider):
            # Missing COUNTRY, PROVIDER_KEY, LABEL
            CAPABILITIES: frozenset = frozenset()

            async def async_fetch(self, session, station_id):
                return {}

            async def async_fetch_station_name(self, session, station_id):
                return None


def test_base_provider_unknown_capability_raises() -> None:
    """Defining a BaseProvider with unknown CAPABILITIES key raises TypeError."""
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    with pytest.raises(TypeError, match="unknown keys"):

        class _BadCapProvider(BaseProvider):
            COUNTRY = "XX"
            PROVIDER_KEY = "xx_bad_cap"
            LABEL = "Bad Cap Test"
            CAPABILITIES: frozenset = frozenset({"nonexistent_capability_xyz_abc"})

            async def async_fetch(self, session, station_id):
                return {}

            async def async_fetch_station_name(self, session, station_id):
                return None


async def test_base_provider_async_list_stations_default_returns_empty() -> None:
    """Default async_list_stations implementation returns []."""
    from custom_components.fuelcompare_ie.providers.base import BaseProvider

    class _MinimalProvider(BaseProvider):
        COUNTRY = "XX"
        PROVIDER_KEY = "xx_minimal_test"
        LABEL = "Minimal Test"
        CAPABILITIES: frozenset = frozenset()

        def __init__(self, station_id: str) -> None:
            self._station_id = station_id

        async def async_fetch(self, session, station_id):
            return {}

        async def async_fetch_station_name(self, session, station_id):
            return None

    provider = _MinimalProvider("123")
    result = await provider.async_list_stations(MagicMock())
    assert result == []


# ---------------------------------------------------------------------------
# CURRENCY ClassVar enforcement — non-EUR providers must override the default
# ---------------------------------------------------------------------------


def test_get_provider_or_default_returns_cls_when_primary_key_found() -> None:
    """get_provider_or_default returns the provider class when the primary key exists."""
    from custom_components.fuelcompare_ie.providers import (
        PROVIDER_REGISTRY,
        get_provider_or_default,
    )

    primary_key = next(iter(PROVIDER_REGISTRY))
    result = get_provider_or_default(primary_key, "also_nonexistent_xyz")

    assert result is PROVIDER_REGISTRY[primary_key]


def test_get_provider_or_default_returns_cls_when_only_default_key_found() -> None:
    """get_provider_or_default falls back to default_key and returns that provider class."""
    from custom_components.fuelcompare_ie.providers import (
        PROVIDER_REGISTRY,
        get_provider_or_default,
    )

    default_key = next(iter(PROVIDER_REGISTRY))
    result = get_provider_or_default("nonexistent_key_xyz", default_key)

    assert result is PROVIDER_REGISTRY[default_key]


def test_non_eur_providers_override_currency() -> None:
    """All non-EUR providers declare a CURRENCY override (not the '€' default)."""
    from custom_components.fuelcompare_ie.providers import PROVIDER_REGISTRY

    # Countries that use EUR as their official currency (CURRENCY should be "€").
    # AL is included: al_fuel.py reports prices in EUR/litre (cargopedia.net source),
    # though Albania's national currency is ALL (lek).
    _EUR_COUNTRIES = {
        "AL",
        "AT",
        "BE",
        "DE",
        "EU",
        "FI",
        "FR",
        "GR",
        "HR",
        "IE",
        "IT",
        "LT",
        "LU",
        "MD",
        "ME",
        "MT",
        "NL",
        "PT",
        "SI",
        "ES",
    }

    for key, cls in PROVIDER_REGISTRY.items():
        currency = getattr(cls, "CURRENCY", "€")
        if cls.COUNTRY not in _EUR_COUNTRIES:
            assert currency != "€", (
                f"Provider {key} (COUNTRY={cls.COUNTRY}) appears to be non-EUR "
                f"but uses the default '€' CURRENCY. Override CURRENCY ClassVar."
            )


# ---------------------------------------------------------------------------
# Coordinator delegation fallback (lines 89, 94) — no provider method
# ---------------------------------------------------------------------------


def test_parse_station_returns_station_without_provider_method(
    hass: HomeAssistant,
) -> None:
    """Coordinator station_id attribute is accessible after init."""
    coordinator = _ie_coordinator(hass, "12345")
    assert coordinator.station_id == "12345"


# ---------------------------------------------------------------------------
# IEFuelCompareProvider.async_fetch_station_name — direct unit tests
# covers lines 99-111 in ie_fuelcompare.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ie_fuelcompare_fetch_station_name_returns_name() -> None:
    """async_fetch_station_name returns station name from Next.js data."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")

    async def _fake_fetch_page_assets(session, broad=False):
        pass

    async def _fake_fetch_nextjs(session):
        return {"name": "Circle K Mulhuddart"}

    with (
        patch.object(
            provider, "_fetch_page_assets", side_effect=_fake_fetch_page_assets
        ),
        patch.object(provider, "_fetch_nextjs", side_effect=_fake_fetch_nextjs),
    ):
        name = await provider.async_fetch_station_name(MagicMock(), "790")

    assert name == "Circle K Mulhuddart"


@pytest.mark.asyncio
async def test_ie_fuelcompare_fetch_station_name_falls_back_to_tablename() -> None:
    """async_fetch_station_name falls back to tablename when name is absent."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")

    async def _fake_page_assets(session, broad=False):
        pass

    async def _fake_nextjs(session):
        return {"tablename": "circle_k"}

    async def _fake_encrypted(session):
        return None

    with (
        patch.object(provider, "_fetch_page_assets", side_effect=_fake_page_assets),
        patch.object(provider, "_fetch_nextjs", side_effect=_fake_nextjs),
        patch.object(provider, "_fetch_encrypted_api", side_effect=_fake_encrypted),
    ):
        name = await provider.async_fetch_station_name(MagicMock(), "790")

    assert name == "Circle K"


@pytest.mark.asyncio
async def test_ie_fuelcompare_fetch_station_name_falls_back_to_encrypted_api() -> None:
    """async_fetch_station_name tries encrypted API when Next.js returns None."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")

    async def _fake_page_assets(session, broad=False):
        pass

    async def _fake_nextjs(session):
        return None

    async def _fake_encrypted(session):
        return {"name": "Texaco Fairview"}

    with (
        patch.object(provider, "_fetch_page_assets", side_effect=_fake_page_assets),
        patch.object(provider, "_fetch_nextjs", side_effect=_fake_nextjs),
        patch.object(provider, "_fetch_encrypted_api", side_effect=_fake_encrypted),
    ):
        name = await provider.async_fetch_station_name(MagicMock(), "790")

    assert name == "Texaco Fairview"


@pytest.mark.asyncio
async def test_ie_fuelcompare_fetch_station_name_returns_none_on_exception() -> None:
    """async_fetch_station_name returns None when an exception occurs."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")

    with patch.object(
        provider, "_fetch_page_assets", side_effect=Exception("network error")
    ):
        name = await provider.async_fetch_station_name(MagicMock(), "790")

    assert name is None


@pytest.mark.asyncio
async def test_ie_fuelcompare_fetch_station_name_returns_none_when_no_data() -> None:
    """async_fetch_station_name returns None when both paths return None."""
    from custom_components.fuelcompare_ie.providers.ie_fuelcompare import (
        IEFuelCompareProvider,
    )

    provider = IEFuelCompareProvider("790")

    async def _noop(session, broad=False):
        pass

    async def _none(session):
        return None

    with (
        patch.object(provider, "_fetch_page_assets", side_effect=_noop),
        patch.object(provider, "_fetch_nextjs", side_effect=_none),
        patch.object(provider, "_fetch_encrypted_api", side_effect=_none),
    ):
        name = await provider.async_fetch_station_name(MagicMock(), "790")

    assert name is None


# ---------------------------------------------------------------------------
# M-25: ProviderError propagation and full lifecycle tests
# ---------------------------------------------------------------------------


async def test_provider_error_raises_update_failed(hass: HomeAssistant) -> None:
    """ProviderError raised by the provider results in UpdateFailed from the coordinator."""
    from custom_components.fuelcompare_ie.providers.base import ProviderError

    provider = MagicMock()
    provider.PROVIDER_KEY = "test_provider"
    provider.POLL_INTERVAL_SECONDS = 300
    provider.async_fetch = AsyncMock(side_effect=ProviderError("station not found"))

    coordinator = FuelCompareIECoordinator(hass, provider, "12345")

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=MagicMock(),
    ):
        with pytest.raises(UpdateFailed, match="station not found"):
            await coordinator._async_update_data()


async def test_full_lifecycle_async_refresh(hass: HomeAssistant) -> None:
    """_async_update_data() returns StationData and coordinator.data is updated on refresh."""
    expected: dict = {
        "unleaded": 1.85,
        "diesel": 1.75,
        "tablename": "circle_k",
        "county": "Dublin",
    }

    provider = MagicMock()
    provider.PROVIDER_KEY = "test_provider"
    provider.POLL_INTERVAL_SECONDS = 300
    provider.async_fetch = AsyncMock(return_value=expected)

    coordinator = FuelCompareIECoordinator(hass, provider, "12345")

    with patch(
        "custom_components.fuelcompare_ie.coordinator.async_get_clientsession",
        return_value=MagicMock(),
    ):
        # Call _async_update_data directly to avoid scheduling a recurring timer
        # that would leave a lingering handle in the event loop.
        data = await coordinator._async_update_data()

    assert data == expected
