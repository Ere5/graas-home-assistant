"""The GRAAS API client: what each kind of answer turns into."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.graas.api import GraasApi, GraasAuthError, GraasCommandError, GraasError, GraasRateLimitError

from .conftest import TOKEN

BASE = "https://graas.example.com"
NGINX_502 = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"


def _api(hass: HomeAssistant) -> GraasApi:
    return GraasApi(async_get_clientsession(hass), BASE, TOKEN)


async def test_state_is_returned(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/api/ha/state", json={"account": {"id": 1}, "devices": []})

    assert await _api(hass).async_get_state() == {"account": {"id": 1}, "devices": []}
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("status", [502, 504])
async def test_an_html_gateway_error_is_a_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    aioclient_mock.get(f"{BASE}/api/ha/state", status=status, text=NGINX_502)

    with pytest.raises(GraasError) as err:
        await _api(hass).async_get_state()

    assert not isinstance(err.value, GraasCommandError)
    assert str(status) in str(err.value)


async def test_a_non_json_success_is_a_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{BASE}/api/ha/state", text="<html>captive portal</html>")

    with pytest.raises(GraasError):
        await _api(hass).async_get_state()


async def test_401_is_an_auth_error_even_without_json(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/api/ha/state", status=401, text="<html>Unauthorized</html>")

    with pytest.raises(GraasAuthError):
        await _api(hass).async_get_state()


@pytest.mark.parametrize("status", [400, 403, 404, 409, 422, 429])
async def test_a_graas_refusal_carries_the_servers_message(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    aioclient_mock.post(
        f"{BASE}/api/ha/zones/5/start",
        status=status,
        json={"error": {"code": "device_offline", "message": "Device is offline"}},
    )

    with pytest.raises(GraasCommandError) as err:
        await _api(hass).async_start_zone(5, duration_minutes=10)

    assert str(err.value) == "Device is offline"
    assert err.value.code == "device_offline"


async def test_a_4xx_without_a_body_is_still_a_refusal(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{BASE}/api/ha/zones/5/stop", status=413, text="<html>Too large</html>")

    with pytest.raises(GraasCommandError, match="413"):
        await _api(hass).async_stop_zone(5)


async def test_an_empty_success_is_an_empty_answer(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{BASE}/api/ha/zones/5/stop", status=204)

    await _api(hass).async_stop_zone(5)


async def test_429_is_rate_limited_with_retry_after(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(
        f"{BASE}/api/ha/state",
        status=429,
        headers={"Retry-After": "60"},
        json={"error": {"code": "rate_limited", "message": "Too many requests."}},
    )

    with pytest.raises(GraasRateLimitError) as err:
        await _api(hass).async_get_state()

    assert not isinstance(err.value, GraasAuthError)
    assert err.value.retry_after == 60


async def test_a_token_without_read_scope_is_an_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{BASE}/api/ha/state",
        status=403,
        json={"error": {"code": "api_token_no_read", "message": "This API token cannot read."}},
    )

    with pytest.raises(GraasAuthError):
        await _api(hass).async_get_state()


async def test_a_read_only_token_refused_a_command_is_not_an_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        f"{BASE}/api/ha/zones/5/stop",
        status=403,
        json={"error": {"code": "api_token_read_only", "message": "This API token can only read."}},
    )

    with pytest.raises(GraasCommandError, match="can only read") as err:
        await _api(hass).async_stop_zone(5)

    assert not isinstance(err.value, GraasAuthError)


async def test_validation_details_are_part_of_the_message(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        f"{BASE}/api/ha/zones/5/start",
        status=422,
        json={
            "error": {
                "code": "validation_failed",
                "message": "Validation failed",
                "details": {"liters": ["The whole run may not exceed 1000 L."]},
            }
        },
    )

    with pytest.raises(GraasCommandError) as err:
        await _api(hass).async_start_zone(5, liters=900)

    assert str(err.value) == "Validation failed: liters: The whole run may not exceed 1000 L."
    assert err.value.code == "validation_failed"
