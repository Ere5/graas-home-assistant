"""Small client for the GRAAS cloud API used by Home Assistant (/api/ha)."""

from __future__ import annotations

import json
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


class GraasRateLimitError(GraasCommandError):
    """GRAAS asked to slow down (HTTP 429); retry_after in seconds when it said."""

    def __init__(self, message: str, code: str | None = None, retry_after: float | None = None) -> None:
        super().__init__(message, code)
        self.retry_after = retry_after


# 403 codes meaning the token itself can't be used to read: as good as revoked.
_UNUSABLE_TOKEN_CODES = frozenset({"api_token_no_read"})


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
        body: dict[str, Any] = {"liters": liters} if liters is not None else {"durationMinutes": duration_minutes}
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
                # Status first: a proxy in front of GRAAS answers errors in HTML.
                if resp.status == 401:
                    raise GraasAuthError("API token was rejected")
                text = await resp.text()
                status = resp.status
                retry_after = _seconds(resp.headers.get("Retry-After"))
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GraasError(f"Cannot reach GRAAS: {err}") from err

        data = _parse_json(text)
        if status >= 400:
            error = data.get("error") if isinstance(data, dict) else None
            error = error if isinstance(error, dict) else {}
            message = error.get("message") if isinstance(error.get("message"), str) else None
            code = error.get("code") if isinstance(error.get("code"), str) else None
            message = _with_details(message, error.get("details"))
            if status == 429:
                raise GraasRateLimitError(message or "Too many requests", code, retry_after)
            if status == 403 and code in _UNUSABLE_TOKEN_CODES:
                raise GraasAuthError(message or "API token cannot read")
            if status < 500:
                # GRAAS refused (offline device, read-only token, limits): its own words.
                raise GraasCommandError(message or f"GRAAS answered {status}", code)
            raise GraasError(message or f"GRAAS answered {status}")
        if not text.strip():
            return {}
        if not isinstance(data, dict):
            raise GraasError(f"Unexpected answer from GRAAS (HTTP {status})")
        return data


def _parse_json(text: str) -> Any:
    """The decoded body, or None when it isn't JSON (e.g. an HTML error page)."""
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _seconds(value: str | None) -> float | None:
    """Retry-After in seconds (the HTTP-date form is ignored)."""
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _with_details(message: str | None, details: Any) -> str | None:
    """'Validation failed: liters: …' — GRAAS puts the useful part of a 422 in details."""
    if not isinstance(details, dict) or not details:
        return message
    parts = []
    for field, problems in details.items():
        texts = problems if isinstance(problems, list) else [problems]
        parts.append(f"{field}: {' '.join(str(t) for t in texts)}")
    detail = "; ".join(parts)
    return f"{message}: {detail}" if message else detail
