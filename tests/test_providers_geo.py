"""Tests for providers/_geo.py shared helpers."""

from __future__ import annotations

import pytest

from custom_components.fuelcompare_ie.providers._geo import (
    filter_within_radius,
    haversine_km,
)


def test_haversine_perth_to_fremantle() -> None:
    """Perth CBD ↔ Fremantle ≈ 16 km (within 1 % tolerance)."""
    d = haversine_km(-31.9523, 115.8613, -32.0569, 115.7439)
    assert 15.5 < d < 16.5


def test_haversine_identity_zero() -> None:
    assert haversine_km(0.0, 0.0, 0.0, 0.0) == pytest.approx(0.0)


def test_haversine_antipodes_half_circumference() -> None:
    # Half the earth's circumference (~20015 km) within 1 %
    d = haversine_km(0.0, 0.0, 0.0, 180.0)
    assert 19_900 < d < 20_100


def test_filter_returns_all_when_radius_none() -> None:
    items = [("a", {"latitude": 0.0, "longitude": 0.0})]
    assert filter_within_radius(items, 0.0, 0.0, None) == items


def test_filter_returns_all_when_radius_zero() -> None:
    items = [("a", {"latitude": 0.0, "longitude": 0.0})]
    assert filter_within_radius(items, 0.0, 0.0, 0) == items


def test_filter_returns_all_when_lat_none() -> None:
    items = [("a", {"latitude": 0.0, "longitude": 0.0})]
    assert filter_within_radius(items, None, 0.0, 5.0) == items


def test_filter_drops_distant_station() -> None:
    rows = [
        ("near", {"latitude": -31.95, "longitude": 115.86}),
        ("far", {"latitude": -32.06, "longitude": 115.74}),
    ]
    kept = filter_within_radius(rows, -31.9523, 115.8613, 5.0)
    assert [sid for sid, _ in kept] == ["near"]


def test_filter_drops_station_without_coords_when_active() -> None:
    rows = [
        ("ok", {"latitude": 0.0, "longitude": 0.0}),
        ("nolat", {"latitude": None, "longitude": 0.0}),
        ("nolng", {"latitude": 0.0, "longitude": None}),
    ]
    kept = filter_within_radius(rows, 0.0, 0.0, 5.0)
    assert [sid for sid, _ in kept] == ["ok"]


def test_filter_drops_station_with_non_numeric_coords() -> None:
    rows = [
        ("ok", {"latitude": 0.0, "longitude": 0.0}),
        ("bad", {"latitude": "not-a-number", "longitude": 0.0}),
    ]
    kept = filter_within_radius(rows, 0.0, 0.0, 5.0)
    assert [sid for sid, _ in kept] == ["ok"]


def test_filter_custom_keys() -> None:
    rows = [("a", {"lat": 0.0, "lon": 0.0})]
    kept = filter_within_radius(rows, 0.0, 0.0, 5.0, lat_key="lat", lng_key="lon")
    assert kept == rows


def test_filter_radius_boundary_inclusive() -> None:
    # Two coords ~16 km apart; radius=16.5 keeps it, radius=15 drops it.
    rows = [("fremantle", {"latitude": -32.0569, "longitude": 115.7439})]
    assert filter_within_radius(rows, -31.9523, 115.8613, 16.5) == rows
    assert filter_within_radius(rows, -31.9523, 115.8613, 15.0) == []
