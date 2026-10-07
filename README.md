# GRAAS Irrigation for Home Assistant

Control GRAAS controllers from [Home Assistant](https://www.home-assistant.io/). Zones become water valves. Sensors, status and a "Stop all" button come along.

It works through the GRAAS cloud, the same way as the GRAAS app, so it supports every controller (Wi-Fi and GSM). Home Assistant needs internet access. Controllers keep running their own schedules without it.

## Setup
1. In the GRAAS app, open **Settings → Home Assistant** and create a token. Copy it: it is shown only once.
2. In Home Assistant, install the integration (HACS: add `https://github.com/Ere5/graas-home-assistant` as a custom repository of type Integration; or copy `custom_components/graas` into your config folder), then **Settings → Devices & services → Add integration → GRAAS Irrigation**.
3. Paste the token.

A read-only token shows everything but can't start or stop zones.

## What you get
Each controller is a device, and **each zone is its own device** linked to it. Zones can be put in areas ("Front yard", "Greenhouse"), and each zone's page holds only that zone.

| Controller device | |
|---|---|
| Online | `binary_sensor` (connectivity) |
| Stop all zones | `button` |
| Rain delay | `number`, 0–14 days (0 = off). Every zone skips its scheduled runs until it ends |
| Watering paused until | `sensor` (timestamp), while a rain delay is on |
| Air temperature / humidity, soil temperature, water pressure, wind | `sensor`, only the ones the controller reports |
| Signal strength, last seen | diagnostic `sensor` |

| Zone device (e.g. "Lawn") | |
|---|---|
| The zone | `valve` (water). Open = water for the run time, close = stop |
| Run time | `number`, 1–120 min. Starts at the zone's schedule length, otherwise 15 |
| Skip next run | `switch`, only on zones with a schedule or program. Turns itself off once that run has passed |
| Watering ends | `sensor` (timestamp), while a timed run is on |
| Next run | `sensor` (timestamp), as in the GRAAS app |
| Last watered | `sensor` (timestamp) |
| Soil moisture / temperature / EC / pH | `sensor`, only where a soil sensor is fitted |
| Rain postponed | `binary_sensor`, only on Lawncare/Agrogator zones with rain postponement on |
| Flow rate | `sensor`, off by default (most controllers have no flow meter) |

Skip next run and rain delay hold scheduled and program runs only; opening a valve still waters. GRAAS keeps them while a controller is offline and sends them when it reconnects. Only valves go "Unavailable" while their controller is offline.

Zones with a default name ("Zone 2") get the controller's name in front ("Garden Zone 2"), so zones of different controllers can be told apart. On Irigator controllers a litres amount is per plant, and the valve shows the plant count.

## Ready-made dashboard
**Developer tools → Actions → "GRAAS Irrigation: Dashboard" → Perform action**, then copy the `yaml` from the response. Create a dashboard (**Settings → Dashboards → Add dashboard**), open it, then **Edit → ⋮ → Raw configuration editor**, paste and save.

You get one section per controller (status, Stop all) and one per zone: an open/close control, the run time with −/+ buttons, next run, last watered and soil readings. "Watering ends" shows while a zone runs. It uses only built-in cards, so there's nothing to install. Run the action again after adding controllers or zones.

### Service `graas.start_zone`
Water one or more zones for a time **or** a volume:

```yaml
action: graas.start_zone
target:
  entity_id: valve.garden_lawn
data:
  duration_minutes: 10   # 1–120, or…
  # liters: 40           # …0.1–2000 (needs a flow meter)
```

Runs always end on their own. GRAAS never accepts an open-ended start from Home Assistant. Starts go through the same checks as the app: the controller must be online, and only one zone runs at a time. They show in GRAAS history as "Home Assistant".

## Limits
- State is polled every 30 s, and refreshed straight after each command.
- A controller or zone added later shows up after reloading the integration.
- Valve commands are limited to 30 per minute per token.

## Development
The integration lives in this monorepo at `home-assistant/`. HACS installs from a repository whose root holds `custom_components/graas`, so a public release is a mirror of this folder (for example `git subtree split --prefix home-assistant`).

Tests run on Python 3.13 with Home Assistant's test harness:

```sh
docker run --rm -v "$PWD":/app -w /app python:3.13 sh -c \
  "pip install -q -r requirements_test.txt && python -m pytest -q"
```

Backend endpoints used: `GET /api/ha/state`, `POST /api/ha/zones/{id}/start|stop`, `POST /api/ha/devices/{id}/stop-all`, `POST|DELETE /api/ha/zones/{id}/skip-next`, `POST|DELETE /api/ha/devices/{id}/rain-delay` (see `web-server/src/Controller/Api/Integration/HomeAssistantController.php`).
