"""End-to-end checks of the Home Assistant integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spraying_control.const import DOMAIN, SERVICE_ANALYZE
from custom_components.spraying_control.spraycontrol.demo import synthetic_run

TRACKER = "device_tracker.sprayer"

CONFIG = {
    "tracker": TRACKER,
    "boom_width_m": 12.0,
    "tank_capacity_l": 1000.0,
    "base": {"latitude": 38.3005, "longitude": 32.8985, "radius": 40.0},
    "base_min_stop_s": 120.0,
    "min_speed_kmh": 1.5,
    "max_speed_kmh": 18.0,
    "max_gap_s": 60.0,
    "max_accuracy_m": 30.0,
    "daily_time": "",
}

EXPECTED_SENSORS = [
    "area_sprayed", "volume_used", "application_rate", "refills", "tank_loads",
    "coverage", "missed_area", "overlap", "distance", "working_time", "last_run",
]


def _demo_states() -> list[State]:
    """The synthetic run, shaped like Companion-app tracker history."""
    track, _, _ = synthetic_run(interval_s=4.0)
    return [
        State(
            TRACKER,
            "not_home",
            {"latitude": float(la), "longitude": float(lo), "gps_accuracy": 6, "source_type": "gps"},
            last_updated=datetime.fromtimestamp(float(ts), timezone.utc),
        )
        for ts, la, lo in zip(track.t, track.lat, track.lon)
    ]


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=CONFIG, unique_id=TRACKER, title="Spraying · Sprayer")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_entry(recorder_mock, hass: HomeAssistant) -> None:
    hass.states.async_set(TRACKER, "home", {"friendly_name": "Sprayer phone"})

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CONFIG)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Spraying · Sprayer phone"
    assert result["data"]["boom_width_m"] == 12.0


async def test_config_flow_rejects_inverted_speed_window(recorder_mock, hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONFIG, "min_speed_kmh": 20.0, "max_speed_kmh": 10.0}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"min_speed_kmh": "speed_window"}


async def test_config_flow_blocks_duplicate_tracker(recorder_mock, hass: HomeAssistant) -> None:
    await _setup(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CONFIG)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_setup_creates_all_sensors(recorder_mock, hass: HomeAssistant) -> None:
    await _setup(hass)

    for key in EXPECTED_SENSORS:
        state = hass.states.get(f"sensor.spraying_sprayer_{key}")
        assert state is not None, f"sensor for {key} was not created"
        # Nothing analysed yet, so every sensor reports unavailable.
        assert state.state == STATE_UNAVAILABLE

    assert hass.services.has_service(DOMAIN, SERVICE_ANALYZE)


async def test_analysis_populates_sensors(recorder_mock, hass: HomeAssistant) -> None:
    """Drive the whole pipeline: tracker history in, coverage numbers out."""
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    states = _demo_states()

    async def fake_history(start, end):
        return states

    coordinator._async_history = fake_history

    start = states[0].last_updated
    await coordinator.async_analyze_period(start, states[-1].last_updated + timedelta(minutes=1))
    await hass.async_block_till_done()

    def value(key: str) -> str:
        state = hass.states.get(f"sensor.spraying_sprayer_{key}")
        assert state is not None
        return state.state

    # The synthetic run works 19 passes of 300 m with a 12 m boom.
    assert float(value("area_sprayed")) == pytest.approx(6.9, abs=0.4)
    assert int(float(value("refills"))) == 2
    assert int(float(value("tank_loads"))) == 3
    # Two full tanks plus a scaled partial.
    assert 2000 < float(value("volume_used")) <= 3000
    assert float(value("application_rate")) > 0
    # One pass is skipped and one is shifted, so both defects must show.
    assert float(value("missed_area")) > 0.03
    assert float(value("overlap")) > 0.5

    loads = hass.states.get("sensor.spraying_sprayer_volume_used").attributes["loads"]
    assert len(loads) == 3
    assert [load["complete"] for load in loads] == [True, True, False]

    gaps = hass.states.get("sensor.spraying_sprayer_missed_area").attributes
    assert gaps["patch_count"] >= 1
    assert gaps["patches"][0]["max_width_m"] > 4

    last_run = hass.states.get("sensor.spraying_sprayer_last_run")
    assert last_run.state != STATE_UNAVAILABLE
    assert "warnings" in last_run.attributes


async def test_service_reports_empty_history(recorder_mock, hass: HomeAssistant) -> None:
    """Asking for a day with no recorded positions must fail loudly, not silently."""
    from homeassistant.exceptions import ServiceValidationError

    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_ANALYZE, {"date": "2020-01-01"}, blocking=True
        )


async def test_options_flow_updates_settings(recorder_mock, hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    changed = {k: v for k, v in CONFIG.items() if k != "tracker"}
    changed["boom_width_m"] = 24.0
    result = await hass.config_entries.options.async_configure(result["flow_id"], changed)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["boom_width_m"] == 24.0


async def test_unload_entry(recorder_mock, hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_ANALYZE)
