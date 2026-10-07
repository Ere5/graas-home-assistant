# Developer documentation

Technical notes for working on the GRAAS Irrigation integration. User documentation is in the [README](../README.md).

## Architecture

```
Home Assistant ──HTTPS──▶ GRAAS cloud API (/api/ha/*) ──MQTT──▶ GRAAS controller
   (this integration)        token auth, checks, history
```

- **Cloud polling** (`iot_class: cloud_polling`). One `DataUpdateCoordinator` per config entry polls `GET /api/ha/state` every 30 s, and refreshes straight after every command.
- **No direct device access.** All commands go through the GRAAS API, which applies the same checks as the GRAAS app: the controller must be online, runs are bounded, and history and audit are recorded.
- **Authentication.** A personal access token (`graas_pat_…`) sent as `Authorization: Bearer`. Tokens have the scope `read`, or `read` + `control`.
  - The server stores only a hash of the token.
  - A token can reach only the `/api/ha/*` endpoints.

### Files

| File | Purpose |
|---|---|
| `__init__.py` | Entry setup/unload, service registration (`start_zone`, `stop_all`, `dashboard`) |
| `api.py` | Thin async HTTP client, maps HTTP errors to exceptions |
| `coordinator.py` | Polling, raises `ConfigEntryAuthFailed` on 401 (starts re-auth) |
| `config_flow.py` | Token setup and re-auth; the server URL is under the collapsed "Advanced" section |
| `entity.py` | Base entity, device registry layout (controller → zone devices) |
| `valve.py`, `number.py`, `switch.py`, `button.py`, `sensor.py`, `binary_sensor.py` | Platforms |
| `dashboard.py` | Generates the Lovelace YAML returned by `graas.dashboard` |
| `diagnostics.py` | Diagnostics download, token redacted |
| `strings.json`, `translations/` | UI strings (en, lt) |
| `brand/` | Icons and logos shown by Home Assistant and HACS |

### Device and entity layout

- One HA device per controller, identifier `(graas, <device serial>)`.
- One HA device per zone, identifier `(graas, zone_<zone id>)`, linked to its controller with `via_device`.
- `unique_id`s are built from the GRAAS device serial or the zone id. **Never change their format**: that would orphan entities in existing installs.

## API used

Base URL: `https://ss.graasautomation.com`. Override it in the config flow (Advanced) for local development.

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/api/ha/state` | read | Account, controllers, zones, sensors, holds in one call |
| POST | `/api/ha/zones/{id}/start` | control | Body `{"durationMinutes": 1–120}` **or** `{"liters": 0.1–2000}` |
| POST | `/api/ha/zones/{id}/stop` | control | Stop one zone |
| POST | `/api/ha/devices/{id}/stop-all` | control | Stop every zone on a controller |
| POST / DELETE | `/api/ha/zones/{id}/skip-next` | control | Skip / un-skip the next scheduled run |
| POST / DELETE | `/api/ha/devices/{id}/rain-delay` | control | Body `{"days": 1–14}` / clear |

Errors use `{"error": {"code": "...", "message": "..."}}`.

| Status | Meaning | What the integration does |
|---|---|---|
| 401 `invalid_api_token` | Token unknown or revoked, account pending deletion, or the user signed out everywhere (e.g. password reset) | Starts re-authentication |
| 403 `api_token_no_read` | Token lacks the `read` scope, on `/state` | Starts re-authentication |
| 403 | Token lacks the `control` scope, on commands | Raises an error |
| 404 | Unknown id, or no access (same response for both) | Raises an error |
| 409 | Device offline, or `no_next_run` for skip-next | Raises the server's message |
| 422 `validation_failed` | Bad amount, e.g. litres × plants > 2000 on per-plant devices | Raises the server's message |
| 429 `rate_limited` | 30 commands/min per token, or 20 failed logins/min per IP (with `Retry-After`) | Backs off; never re-auth |

A start while another zone runs returns 201: the controller queues it and runs zones one at a time.
Skip-next is idempotent: pressing it again keeps the existing skip.

## Development environment

A local Home Assistant runs in Docker, with this integration mounted live:

```sh
docker compose -f dev/compose.yaml up -d
open http://localhost:8123
```

- Restart the container (`docker restart graas-ha-dev`) to load code changes.
- To use a local GRAAS backend, turn on **Advanced mode** in your HA user profile, then set the server to `http://host.docker.internal:8080` when adding the integration.

`dev/config/` (HA state, accounts, database) and `dev/local-token.txt` are git-ignored. Never commit them.

## Tests

The tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) on Python 3.13:

```sh
docker run --rm -v "$PWD":/app -w /app python:3.13 sh -c \
  "pip install -q -r requirements_test.txt && python -m pytest -q"
```

- Lint with `ruff check . && ruff format --check .`.
- Test data must be clearly fake (`graas_pat_test…`, `Demo …` names). Never use real tokens, device serials or e-mail addresses.

## Continuous integration

`.github/workflows/validate.yml` runs on every push and pull request:

- **HACS validation** (`hacs/action`)
- **hassfest** (Home Assistant's manifest and translation checks)
- **pytest**

## Releasing

1. Bump `version` in `custom_components/graas/manifest.json` (semantic versioning).
2. Commit, then tag `vX.Y.Z` and push the tag.
3. Create a GitHub Release from the tag with short release notes. HACS offers the new version to users.

Keep `hacs.json` → `homeassistant` (currently 2025.10.0, needed for platform entity services) at the oldest Home Assistant version the code runs on.

## Screenshots

The README images live in `docs/images/`. They are taken from the local dev instance with demo data ("Demo Online", "Demo Offline") at a 1280 px wide window, and saved as PNG.
