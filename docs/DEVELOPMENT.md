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
- One HA device per zone, identifier `(graas, <device serial>_zone_<zone id>)`, linked to its controller with `via_device_id`.
- `unique_id`s are built from the GRAAS device serial or the zone id. **Never change their format**: that would orphan entities in existing installs.
- Entities and devices follow the poll: a new controller, zone, sensor reading, schedule (skip switch) or rain postponement gets its entities without a reload; a controller or zone GRAAS no longer reports loses its device after `MISSING_POLLS_BEFORE_REMOVAL` (2) successful polls in a row without it; optional entities (`removable` in `async_add_entities_dynamically`, e.g. the skip switch) follow the same rule. A poll with no controllers at all, after this entry had some, removes nothing (logged once) until GRAAS reports controllers again. Failed polls never count. `async_remove_config_entry_device` lets users delete devices GRAAS no longer reports.
- A controller seen by two accounts (owner and shared user, two config entries) is provided by one entry only, so entity `unique_id`s stay unique. Precedence per controller, never decided by which poll returns first: the entry whose `/state` has `isOwner: true` for it, then the oldest entry (`created_at`), then the lowest `entry_id`. An entry yields only to a higher-precedence entry that reports the controller or has not polled yet (starting, reloading or in setup retry: it may still report it, possibly as owner); disabled or failed entries (setup error, auth failure) do not count. When a higher-precedence entry picks up a controller a lower one provides, the lower one lets go on its next poll (its devices go at once, no grace) and the higher one takes it on its following poll; the controller then stays there. Device ids change only on such a move.
- After a valve command the valve is optimistic (`opening` / `closing`) until a poll confirms it or 60 s pass; the coordinator is polled again 5 s and 15 s after the command.

### Config entry

- `unique_id`: the account id for the default server (unchanged since 0.1); `<account id>@<host>` for another server. Older entries for another server get the host added at setup.
- Title: the account's e-mail or name when `/state` sends one, otherwise "GRAAS" (plus the host for another server).
- The server URL must be `https://`, except a local development server (localhost, private IP, single-label or `.local`/`.internal` name), where `http://` is allowed.
- Reconfigure (**⋮ → Reconfigure**) changes the server or the token, for the same account. Account ids are per server, so moving to another host also needs the account's e-mail or name from `/state` to match the entry's title (without the host suffix); a server that sends neither cannot be moved to. The title is rebuilt for the new host. Reauth always stays on the entry's own server, so the account id is enough there.

## API used

Base URL: `https://ss.graasautomation.com`. Override it in the config flow (Advanced) for local development.

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/api/ha/state` | read | Account, controllers, zones, sensors, holds in one call |
| POST | `/api/ha/zones/{id}/start` | control | Body `{"durationMinutes": 1–300}` **or** `{"liters": 0.1–1000}` (server `IrrigationLimits`, mirrored in `const.py`) |
| POST | `/api/ha/zones/{id}/stop` | control | Stop one zone |
| POST | `/api/ha/devices/{id}/stop-all` | control | Stop every zone on a controller |
| POST / DELETE | `/api/ha/zones/{id}/skip-next` | control | Skip / un-skip the next scheduled run |
| POST / DELETE | `/api/ha/devices/{id}/rain-delay` | control | Body `{"days": 1–14}` / clear |

Errors use `{"error": {"code": "...", "message": "..."}}`. Known codes map to translated messages (`coordinator.py`, `_COMMAND_ERROR_KEYS`); unknown ones show the server's message.

| Status | Meaning | What the integration does |
|---|---|---|
| 401 `invalid_api_token` | Token unknown or revoked, account pending deletion, or the user signed out everywhere (e.g. password reset) | Starts re-authentication |
| 403 `api_token_no_read` | Token lacks the `read` scope, on `/state` | Starts re-authentication |
| 403 `api_token_read_only` | Token lacks the `control` scope, on commands | Raises a translated error |
| 404 | Unknown id, or no access (same response for both) | Raises an error |
| 409 | `device_offline`, `zone_already_running`, or `no_next_run` for skip-next | Raises a translated error |
| 422 `validation_failed` | Bad amount, e.g. litres × plants > 1000 on per-plant devices | Raises the server's message |
| 422 `litres_need_flow_meter` | Litres on a controller without a flow meter | Raises a translated error |
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

- **HACS validation** (`hacs/action`, pinned to a release commit)
- **hassfest** (Home Assistant's manifest and translation checks, pinned to a commit)
- **ruff** (`ruff check` and `ruff format --check`)
- **pytest** (`pytest-homeassistant-custom-component` pinned in `requirements_test.txt`)

## Releasing

1. Bump `version` in `custom_components/graas/manifest.json` (semantic versioning).
2. Commit, then tag `vX.Y.Z` and push the tag.
3. Create a GitHub Release from the tag with short release notes. HACS offers the new version to users.

Keep `hacs.json` → `homeassistant` (currently 2025.10.0, needed for platform entity services) at the oldest Home Assistant version the code runs on.

## Screenshots

The README images live in `docs/images/`. They are taken from the local dev instance with demo data ("Demo Online", "Demo Offline") at a 1280 px wide window, and saved as PNG.
