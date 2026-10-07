"""Small client for the GRAAS cloud API used by Home Assistant (/api/ha)."""

from __future__ import annotations

from typing import Any

import aiohttp


class GraasError(Exception):
    """Talking to GRAAS failed (network, server, unexpected answer)."""


class GraasAuthError(GraasError):
    """The API token is missing, wrong or revoked."""


class GraasCommandError(GraasError):
    """GRAAS refused a command (device offline, read-only token, limits)."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class GraasApi:
    """The /api/ha endpoints, authenticated with a personal API token."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = token

    async def async_get_state(self) -> dict[str, Any]:
        """Every device, sensor and zone the token's account can see."""
        return await self._request("GET", "/api/ha/state")

    async def async_start_zone(
        self, zone_id: int, *, duration_minutes: int | None = None, liters: float | None = None
    ) -> dict[str, Any]:
        """Start a bounded run: exactly one of duration_minutes or liters."""
        body: dict[str, Any] = (
            {"liters": liters} if liters is not None else {"durationMinutes": duration_minutes}
        )
        return await self._request("POST", f"/api/ha/zones/{zone_id}/start", body)

    async def async_stop_zone(self, zone_id: int) -> None:
        await self._request("POST", f"/api/ha/zones/{zone_id}/stop")

    async def async_stop_all(self, device_id: int) -> None:
        await self._request("POST", f"/api/ha/devices/{device_id}/stop-all")

    async def async_skip_next_run(self, zone_id: int) -> None:
        """Hold the zone's next scheduled or program run (409 when it has none)."""
        await self._request("POST", f"/api/ha/zones/{zone_id}/skip-next")

    async def async_cancel_skip(self, zone_id: int) -> None:
        await self._request("DELETE", f"/api/ha/zones/{zone_id}/skip-next")

    async def async_set_rain_delay(self, device_id: int, days: int) -> None:
        """Hold every zone's scheduled runs for 1–14 days."""
        await self._request("POST", f"/api/ha/devices/{device_id}/rain-delay", {"days": days})

    async def async_end_rain_delay(self, device_id: int) -> None:
        await self._request("DELETE", f"/api/ha/devices/{device_id}/rain-delay")

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data: Any = await resp.json(content_type=None) if resp.content_length != 0 else {}
                if resp.status == 401:
                    raise GraasAuthError("API token was rejected")
                if resp.status >= 400:
                    error = (data or {}).get("error", {}) if isinstance(data, dict) else {}
                    message = error.get("message") or f"GRAAS answered {resp.status}"
                    if resp.status in (403, 404, 409, 422, 429):
                        raise GraasCommandError(message, error.get("code"))
                    raise GraasError(message)
                return data if isinstance(data, dict) else {}
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GraasError(f"Cannot reach GRAAS: {err}") from err
