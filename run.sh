#!/usr/bin/env sh
# Home Assistant add-on entry point.
set -e

CONFIG=/data/options.json

# bashio is not present on the plain python base image, so read options with
# python and export them for the web app to use as form defaults.
if [ -f "$CONFIG" ]; then
    eval "$(python3 - "$CONFIG" <<'PY'
import json, shlex, sys

with open(sys.argv[1]) as fh:
    options = json.load(fh)

mapping = {
    "boom_width_m": "SPRAY_BOOM_WIDTH_M",
    "tank_capacity_l": "SPRAY_TANK_CAPACITY_L",
    "base_latitude": "SPRAY_BASE_LATITUDE",
    "base_longitude": "SPRAY_BASE_LONGITUDE",
    "base_radius_m": "SPRAY_BASE_RADIUS_M",
    "base_min_stop_s": "SPRAY_BASE_MIN_STOP_S",
    "min_speed_kmh": "SPRAY_MIN_SPEED_KMH",
    "max_speed_kmh": "SPRAY_MAX_SPEED_KMH",
    "sensor_prefix": "SPRAY_SENSOR_PREFIX",
    "log_level": "SPRAY_LOG_LEVEL",
}
for key, env in mapping.items():
    if key in options:
        print(f"export {env}={shlex.quote(str(options[key]))}")
PY
)"
fi

LOG_LEVEL=$(echo "${SPRAY_LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')
case "$LOG_LEVEL" in
    trace|debug) UVICORN_LOG=debug ;;
    notice|warning) UVICORN_LOG=warning ;;
    error|fatal) UVICORN_LOG=error ;;
    *) UVICORN_LOG=info ;;
esac

echo "[spraying-control] starting on :8099 (log level ${UVICORN_LOG})"
exec python3 -m uvicorn spraycontrol.web.app:app \
    --host 0.0.0.0 \
    --port 8099 \
    --log-level "${UVICORN_LOG}" \
    --proxy-headers \
    --forwarded-allow-ips '*'
