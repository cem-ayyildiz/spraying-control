"""Local metric projection.

A sprayer track covers a few kilometres at most, so rather than depending on
pyproj/PROJ we project onto a tangent plane anchored at the track centroid.
Scale error grows quadratically with distance from the anchor; over a 5 km
extent the residual is well under a metre, which is an order of magnitude below
the noise of the GPS sources we accept.
"""

from __future__ import annotations

import math

import numpy as np

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


class LocalPlane:
    """Equirectangular tangent-plane projection anchored at (lat0, lon0).

    x is metres east of the anchor, y is metres north.
    """

    __slots__ = ("lat0", "lon0", "_m_per_deg_lat", "_m_per_deg_lon")

    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0 = float(lat0)
        self.lon0 = float(lon0)
        phi = math.radians(self.lat0)
        sin2 = math.sin(phi) ** 2
        # Meridional and prime-vertical radii of curvature at the anchor.
        m_rad = WGS84_A * (1.0 - WGS84_E2) / (1.0 - WGS84_E2 * sin2) ** 1.5
        n_rad = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin2)
        self._m_per_deg_lat = m_rad * math.pi / 180.0
        self._m_per_deg_lon = n_rad * math.cos(phi) * math.pi / 180.0

    @classmethod
    def anchored_on(cls, lat: np.ndarray, lon: np.ndarray) -> "LocalPlane":
        return cls(float(np.mean(lat)), float(np.mean(lon)))

    def forward(self, lat, lon):
        """(lat, lon) degrees -> (x, y) metres."""
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        x = (lon - self.lon0) * self._m_per_deg_lon
        y = (lat - self.lat0) * self._m_per_deg_lat
        return x, y

    def inverse(self, x, y):
        """(x, y) metres -> (lat, lon) degrees."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        lon = self.lon0 + x / self._m_per_deg_lon
        lat = self.lat0 + y / self._m_per_deg_lat
        return lat, lon


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres. Used for base-location proximity, where
    we need a distance before a projection anchor has been chosen."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2.0 * WGS84_A * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
