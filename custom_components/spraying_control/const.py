"""Constants for the Spraying Control integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "spraying_control"

CONF_TRACKER: Final = "tracker"
CONF_BOOM_WIDTH: Final = "boom_width_m"
CONF_TANK_CAPACITY: Final = "tank_capacity_l"
CONF_BASE: Final = "base"
CONF_BASE_DWELL: Final = "base_min_stop_s"
CONF_MIN_SPEED: Final = "min_speed_kmh"
CONF_MAX_SPEED: Final = "max_speed_kmh"
CONF_MAX_GAP: Final = "max_gap_s"
CONF_MAX_ACCURACY: Final = "max_accuracy_m"
CONF_DAILY_TIME: Final = "daily_time"

SERVICE_ANALYZE: Final = "analyze"
ATTR_DATE: Final = "date"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

DEFAULT_BOOM_WIDTH: Final = 12.0
DEFAULT_TANK_CAPACITY: Final = 1000.0
DEFAULT_BASE_RADIUS: Final = 30.0
DEFAULT_BASE_DWELL: Final = 120.0
DEFAULT_MIN_SPEED: Final = 1.5
DEFAULT_MAX_SPEED: Final = 18.0
DEFAULT_MAX_GAP: Final = 60.0
DEFAULT_MAX_ACCURACY: Final = 30.0
