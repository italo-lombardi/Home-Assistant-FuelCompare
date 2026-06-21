"""Repairs flows for the Fuel Compare integration.

Currently exposes one fixable repair issue:

* ``ie_pumps_tls_disabled_<entry_id>`` — raised when the user has enabled the
  ``allow_insecure_tls`` option for an ``ie_pumps`` config entry.  The fix
  flow shows a confirmation dialog and, on accept, clears the option from
  ``entry.options`` and reloads the entry.  The post-load run will see the
  flag is False and ``async_delete_issue`` will remove the repair.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_ALLOW_INSECURE_TLS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class _IePumpsTlsFixFlow(RepairsFlow):
    """Confirm-then-disable fix flow for the ie_pumps TLS-disabled issue."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is None:
                return self.async_create_entry(title="", data={})
            new_options = {
                k: v for k, v in entry.options.items() if k != CONF_ALLOW_INSECURE_TLS
            }
            self.hass.config_entries.async_update_entry(entry, options=new_options)
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Return the fix flow for a repair issue.

    Issue IDs follow the ``ie_pumps_tls_disabled_<entry_id>`` shape.  We carry
    the entry_id in ``data`` (set when the issue is created) and fall back to
    parsing the issue_id if data was lost across a HA restart.
    """
    entry_id: str | None = None
    if data and isinstance(data.get("entry_id"), str):
        entry_id = data["entry_id"]  # type: ignore[assignment]
    if entry_id is None:
        prefix = "ie_pumps_tls_disabled_"
        if issue_id.startswith(prefix):
            entry_id = issue_id[len(prefix) :]
    if entry_id is None:
        # Unknown issue — return a no-op flow so HA does not crash.  The
        # confirm step still renders but does nothing on submit.
        _LOGGER.warning(
            "%s: cannot resolve entry_id for repair issue %s; fix flow is a no-op",
            DOMAIN,
            issue_id,
        )
        entry_id = ""
    return _IePumpsTlsFixFlow(entry_id)
