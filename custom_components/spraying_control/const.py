"""Constants for the Spraying Control integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "spraying_control"

CONF_TRACKER: Final = "tracker"
CONF_SWATH_WIDTH: Final = "swath_width_m"
CONF_TANK_CAPACITY: Final = "tank_capacity_l"
CONF_BASE: Final = "base"
CONF_BASE_DWELL: Final = "base_min_stop_s"
CONF_MIN_SPEED: Final = "min_speed_kmh"
CONF_MAX_SPEED: Final = "max_speed_kmh"
CONF_MAX_GAP: Final = "max_gap_s"
CONF_MAX_ACCURACY: Final = "max_accuracy_m"
CONF_DAILY_TIME: Final = "daily_time"
CONF_HIGH_ACCURACY: Final = "high_accuracy"

SERVICE_ANALYZE: Final = "analyze"
ATTR_DATE: Final = "date"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

DEFAULT_SWATH_WIDTH: Final = 1.0
DEFAULT_TANK_CAPACITY: Final = 18.0
DEFAULT_BASE_RADIUS: Final = 8.0
DEFAULT_BASE_DWELL: Final = 60.0
DEFAULT_MIN_SPEED: Final = 0.4
DEFAULT_MAX_SPEED: Final = 4.5
DEFAULT_MAX_GAP: Final = 45.0
DEFAULT_MAX_ACCURACY: Final = 25.0
