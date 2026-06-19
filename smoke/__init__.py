"""Smoke tests — providers vs. real upstream APIs.

Skipped by default. Enable with FUELCOMPARE_RUN_SMOKE=1.

These live OUTSIDE tests/ to avoid inheriting
pytest_homeassistant_custom_component, which installs a process-wide
socket block that smoke tests need to bypass.

Run with:

    FUELCOMPARE_RUN_SMOKE=1 pytest smoke -p no:homeassistant -v
"""
