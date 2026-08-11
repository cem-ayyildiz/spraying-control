"""Home Assistant integration: read Companion-app GPS, write results back.

Works in two contexts. Inside a Home Assistant add-on the Supervisor injects
``SUPERVISOR_TOKEN`` and proxies the core API, so no configuration is needed.
Standalone, pass the HA URL and a long-lived access token.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from .models import M2_PER_HA, AnalysisResult, Track
from .parsers import parse_ha_history

SUPERVISOR_API = "http://supervisor/core/api"
DEFAULT_TIMEOUT = 60.0


class HAError(RuntimeError):
    pass


@dataclass
class SensorSpec:
    key: str
    name: str
    unit: str | None
    icon: str
    value: float | int
    state_class: str | None = "measurement"
    device_class: str | None = None


class HAClient:
    """Thin wrapper over the Home Assistant REST API."""

    def __init__(self, base_url: str, token: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/api"):
            self.base_url += "/api"
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )

    @classmethod
    def from_env(cls, timeout: float = DEFAULT_TIMEOUT) -> "HAClient":
        """Build a client from the add-on environment, or from HA_URL/HA_TOKEN."""
        supervisor = os.environ.get("SUPERVISOR_TOKEN")
        if supervisor:
            return cls(SUPERVISOR_API, supervisor, timeout)
        url = os.environ.get("HA_URL")
        token = os.environ.get("HA_TOKEN")
        if not url or not token:
            raise HAError(
                "No Home Assistant credentials. Run inside an add-on, or set "
                "HA_URL and HA_TOKEN (a long-lived access token)."
            )
        return cls(url, token, timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HAClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _get(self, path: str, **params):
        try:
            resp = self._client.get(f"{self.base_url}{path}", params=params or None)
        except httpx.HTTPError as exc:
            raise HAError(f"could not reach Home Assistant: {exc}") from exc
        if resp.status_code == 401:
            raise HAError("Home Assistant rejected the token (401).")
        if resp.status_code >= 400:
            raise HAError(f"Home Assistant returned {resp.status_code} for {path}: {resp.text[:200]}")
        return resp.json()

    def ping(self) -> str:
        return self._get("/config").get("version", "unknown")

    def list_device_trackers(self) -> list[dict]:
        """Trackers that currently report a position, newest first."""
        states = self._get("/states")
        out = []
        for st in states:
            entity = st.get("entity_id", "")
            if not entity.startswith("device_tracker."):
                continue
            attrs = st.get("attributes", {})
            out.append(
                {
                    "entity_id": entity,
                    "name": attrs.get("friendly_name", entity),
                    "has_gps": attrs.get("latitude") is not None,
                    "source_type": attrs.get("source_type"),
                    "last_updated": st.get("last_updated"),
                }
            )
        out.sort(key=lambda e: (not e["has_gps"], e["name"]))
        return out

    def fetch_track(
        self,
        entity_id: str,
        start: datetime,
        end: datetime | None = None,
    ) -> Track:
        """Pull position history for one ``device_tracker`` entity.

        ``minimal_response`` is deliberately not requested: the Companion app
        keeps latitude and longitude in the state attributes, and that flag
        strips them.
        """
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        end = end or datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        payload = self._get(
            f"/history/period/{start.isoformat()}",
            filter_entity_id=entity_id,
            end_time=end.isoformat(),
            significant_changes_only=0,
        )
        track = parse_ha_history(payload, entity_id)
        track.name = f"{entity_id} {start:%Y-%m-%d %H:%M}"
        return track

    def set_state(self, entity_id: str, state, attributes: dict | None = None) -> None:
        body = {"state": str(state), "attributes": attributes or {}}
        try:
            resp = self._client.post(f"{self.base_url}/states/{entity_id}", json=body)
        except httpx.HTTPError as exc:
            raise HAError(f"could not reach Home Assistant: {exc}") from exc
        if resp.status_code >= 400:
            raise HAError(f"could not set {entity_id}: {resp.status_code} {resp.text[:200]}")

    def remove_state(self, entity_id: str) -> bool:
        """Drop an entity from the state machine. Returns False if it was absent."""
        try:
            resp = self._client.delete(f"{self.base_url}/states/{entity_id}")
        except httpx.HTTPError as exc:
            raise HAError(f"could not reach Home Assistant: {exc}") from exc
        if resp.status_code == 404:
            return False
        if resp.status_code >= 400:
            raise HAError(f"could not remove {entity_id}: {resp.status_code} {resp.text[:200]}")
        return True

    def push_result(self, result: AnalysisResult, prefix: str = "spray") -> list[str]:
        """Publish the analysis as Home Assistant sensors.

        These are created through the REST API, so they live in the state
        machine but are not restored by a Home Assistant restart - the next
        analysis re-creates them.
        """
        cov = result.coverage
        specs = [
            SensorSpec("area_sprayed", "Spray area sprayed", "ha", "mdi:texture-box", round(cov.sprayed_area_m2 / M2_PER_HA, 3)),
            SensorSpec("volume_used", "Spray volume used", "L", "mdi:cup-water", round(result.total_volume_l, 1)),
            SensorSpec("application_rate", "Spray application rate", "L/ha", "mdi:speedometer", round(result.overall_rate_l_per_ha, 1)),
            SensorSpec("refills", "Spray tank refills", None, "mdi:reload", result.refill_count, state_class="total"),
            SensorSpec("tank_loads", "Spray tank loads", None, "mdi:propane-tank", len(result.loads), state_class="total"),
            SensorSpec("coverage", "Spray coverage", "%", "mdi:check-decagram", round(cov.coverage_pct, 1)),
            SensorSpec("missed_area", "Spray missed area", "ha", "mdi:alert-circle-outline", round(cov.gap_area_m2 / M2_PER_HA, 3)),
            SensorSpec("overlap", "Spray overlap", "%", "mdi:layers-triple", round(cov.overlap_pct, 1)),
            SensorSpec("distance", "Spray distance", "km", "mdi:map-marker-path", round(result.total_distance_m / 1000, 2)),
            SensorSpec("duration", "Spray working time", "h", "mdi:clock-outline", round(result.spraying_time_s / 3600, 2), device_class="duration"),
        ]

        shared = {
            "track": result.track_name,
            "run_start": _iso(result.start_t),
            "run_end": _iso(result.end_t),
            "boom_width_m": result.config.boom_width_m,
            "tank_capacity_l": result.config.tank_capacity_l,
        }

        created: list[str] = []
        for spec in specs:
            attrs = {
                "friendly_name": spec.name,
                "icon": spec.icon,
                **({"unit_of_measurement": spec.unit} if spec.unit else {}),
                **({"state_class": spec.state_class} if spec.state_class else {}),
                **({"device_class": spec.device_class} if spec.device_class else {}),
                **shared,
            }
            entity_id = f"sensor.{prefix}_{spec.key}"
            self.set_state(entity_id, spec.value, attrs)
            created.append(entity_id)

        # One richer entity carrying the detail that does not fit in a state.
        detail_id = f"sensor.{prefix}_last_run"
        self.set_state(
            detail_id,
            _iso(result.end_t),
            {
                "friendly_name": "Spray last run",
                "icon": "mdi:sprinkler-variant",
                "device_class": "timestamp",
                **shared,
                "summary": result.summary(),
                "warnings": result.warnings,
                "loads": [
                    {
                        "index": load.index + 1,
                        "area_ha": round(load.area_ha, 3),
                        "volume_l": round(load.volume_l, 1),
                        "rate_l_per_ha": round(load.rate_l_per_ha, 1),
                        "complete": load.is_complete,
                        "start": _iso(load.start_t),
                        "end": _iso(load.end_t),
                    }
                    for load in result.loads
                ],
                "largest_gaps": [
                    {
                        "area_m2": round(gap.area_m2),
                        "max_width_m": round(gap.max_width_m, 1),
                        "latitude": gap.lat,
                        "longitude": gap.lon,
                    }
                    for gap in result.gaps[:10]
                ],
            },
        )
        created.append(detail_id)
        return created


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def day_bounds(day: str | None = None, tz_offset_h: float = 0.0) -> tuple[datetime, datetime]:
    """Start and end of a local day (``YYYY-MM-DD``, default today) as UTC."""
    tz = timezone(timedelta(hours=tz_offset_h))
    target = datetime.strptime(day, "%Y-%m-%d").date() if day else datetime.now(tz).date()
    start = datetime.combine(target, datetime.min.time(), tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)
