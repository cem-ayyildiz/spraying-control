"""Aerial imagery: read what a picture can tell us, and place it on the ground.

Satellite basemaps top out around 0.3 m per pixel, which is too coarse to see a
garden bed. A photo from a drone or a mast is far sharper, so this module makes
one usable as the backdrop: work out its size, pull any GPS the camera recorded,
and turn that into a first guess at where it sits on the map.

Placement is stored as three corners - top-left, top-right and bottom-left.
That is the standard affine form: it carries position, scale and rotation, and
maps straight onto what a map needs to draw the image. Only PNG and JPEG headers
are parsed, so there is nothing to install.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .geo import LocalPlane

LatLon = tuple[float, float]


class ImageError(ValueError):
    pass


def image_size(data: bytes) -> tuple[int, int]:
    """(width, height) in pixels for a PNG or JPEG."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise ImageError("truncated PNG")
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)

    if data[:2] == b"\xff\xd8":
        return _jpeg_size(data)

    raise ImageError("only PNG and JPEG images are supported")


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """Walk the JPEG segments to the frame header, which carries the size."""
    i = 2
    n = len(data)
    while i + 3 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # Standalone markers carry no length.
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            break
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        # Any start-of-frame except the arithmetic/lossless oddities.
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 > n:
                break
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return int(width), int(height)
        i += 2 + seg_len
    raise ImageError("could not find the size in this JPEG")


# --- EXIF ------------------------------------------------------------------

_GPS_IFD = 0x8825
_EXIF_TAGS = {1: "lat_ref", 2: "lat", 3: "lon_ref", 4: "lon", 5: "alt_ref", 6: "alt"}
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


@dataclass
class ExifLocation:
    lat: float
    lon: float
    altitude_m: float | None = None


def exif_location(data: bytes) -> ExifLocation | None:
    """Latitude, longitude and altitude a camera stamped into a JPEG.

    Returns None when the photo has no GPS, which is the common case for a
    phone screenshot or an exported orthomosaic.
    """
    try:
        return _exif_location(data)
    except (struct.error, IndexError, ValueError, ZeroDivisionError):
        return None


def _exif_location(data: bytes) -> ExifLocation | None:
    start = data.find(b"Exif\x00\x00")
    if start < 0:
        return None
    tiff = start + 6
    byte_order = data[tiff : tiff + 2]
    if byte_order == b"II":
        end = "<"
    elif byte_order == b"MM":
        end = ">"
    else:
        return None

    def u16(off: int) -> int:
        return struct.unpack(end + "H", data[off : off + 2])[0]

    def u32(off: int) -> int:
        return struct.unpack(end + "I", data[off : off + 4])[0]

    ifd0 = tiff + u32(tiff + 4)
    gps_offset = None
    count = u16(ifd0)
    for k in range(count):
        entry = ifd0 + 2 + k * 12
        if u16(entry) == _GPS_IFD:
            gps_offset = tiff + u32(entry + 8)
            break
    if gps_offset is None:
        return None

    values: dict[str, object] = {}
    gps_count = u16(gps_offset)
    for k in range(gps_count):
        entry = gps_offset + 2 + k * 12
        tag = u16(entry)
        name = _EXIF_TAGS.get(tag)
        if name is None:
            continue
        typ = u16(entry + 2)
        n_vals = u32(entry + 4)
        size = _TYPE_SIZE.get(typ, 1) * n_vals
        pos = entry + 8 if size <= 4 else tiff + u32(entry + 8)

        if typ == 2:  # ASCII
            values[name] = data[pos : pos + n_vals].split(b"\x00")[0].decode("ascii", "replace")
        elif typ in (5, 10):  # rational
            out = []
            for j in range(n_vals):
                num, den = struct.unpack(end + ("ii" if typ == 10 else "II"), data[pos + j * 8 : pos + j * 8 + 8])
                out.append(num / den if den else 0.0)
            values[name] = out
        elif typ == 1:  # byte
            values[name] = data[pos]

    def dms(key: str, ref_key: str) -> float | None:
        parts = values.get(key)
        if not isinstance(parts, list) or len(parts) < 3:
            return None
        deg = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
        ref = str(values.get(ref_key, "")).upper()
        return -deg if ref in ("S", "W") else deg

    lat = dms("lat", "lat_ref")
    lon = dms("lon", "lon_ref")
    if lat is None or lon is None:
        return None

    altitude = None
    alt = values.get("alt")
    if isinstance(alt, list) and alt:
        altitude = alt[0]
        if values.get("alt_ref") == 1:  # below sea level
            altitude = -altitude

    return ExifLocation(lat=lat, lon=lon, altitude_m=altitude)


# --- placement --------------------------------------------------------------

@dataclass
class Placement:
    """Where an image sits on the ground, as three corners (lat, lon).

    Top-left, top-right and bottom-left pin an affine transform, so the image
    can be positioned, scaled and rotated. The fourth corner follows.
    """

    top_left: LatLon
    top_right: LatLon
    bottom_left: LatLon

    def as_dict(self) -> dict:
        return {
            "top_left": list(self.top_left),
            "top_right": list(self.top_right),
            "bottom_left": list(self.bottom_left),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Placement":
        return cls(
            top_left=tuple(raw["top_left"]),
            top_right=tuple(raw["top_right"]),
            bottom_left=tuple(raw["bottom_left"]),
        )

    @property
    def bottom_right(self) -> LatLon:
        """The implied fourth corner: top_right + (bottom_left - top_left)."""
        return (
            self.top_right[0] + self.bottom_left[0] - self.top_left[0],
            self.top_right[1] + self.bottom_left[1] - self.top_left[1],
        )

    def ground_size_m(self) -> tuple[float, float]:
        """Width and height on the ground, in metres."""
        plane = LocalPlane(self.top_left[0], self.top_left[1])
        tlx, tly = plane.forward(*self.top_left)
        trx, try_ = plane.forward(*self.top_right)
        blx, bly = plane.forward(*self.bottom_left)
        width = math.hypot(float(trx - tlx), float(try_ - tly))
        height = math.hypot(float(blx - tlx), float(bly - tly))
        return width, height

    def rotation_deg(self) -> float:
        """How far the image is turned clockwise from north-up.

        Zero means the top of the picture faces north, which is how a map-north
        orthomosaic sits. A north-up image has its top edge running due east, so
        that quarter turn is taken off the bearing.
        """
        plane = LocalPlane(self.top_left[0], self.top_left[1])
        tlx, tly = plane.forward(*self.top_left)
        trx, try_ = plane.forward(*self.top_right)
        bearing = math.degrees(math.atan2(float(trx - tlx), float(try_ - tly)))
        return (bearing - 90.0 + 180.0) % 360.0 - 180.0


def place_centred(
    centre: LatLon,
    width_px: int,
    height_px: int,
    ground_width_m: float,
    rotation_deg: float = 0.0,
) -> Placement:
    """Place an image centred on a point, at a given ground width.

    The height follows from the pixel aspect ratio, so the picture is never
    stretched.
    """
    if width_px <= 0 or height_px <= 0:
        raise ImageError("image has no size")
    ground_height_m = ground_width_m * height_px / width_px

    plane = LocalPlane(centre[0], centre[1])
    half_w, half_h = ground_width_m / 2.0, ground_height_m / 2.0
    theta = math.radians(rotation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    def corner(dx: float, dy: float) -> LatLon:
        # Rotate clockwise from north, then project back to lat/lon.
        x = dx * cos_t + dy * sin_t
        y = -dx * sin_t + dy * cos_t
        lat, lon = plane.inverse(x, y)
        return (float(lat), float(lon))

    return Placement(
        top_left=corner(-half_w, half_h),
        top_right=corner(half_w, half_h),
        bottom_left=corner(-half_w, -half_h),
    )


def placement_from_world_file(
    text: str, width_px: int, height_px: int
) -> Placement:
    """Read an ESRI world file (.jgw / .pgw / .wld).

    Six lines: x pixel size, y skew, x skew, y pixel size, and the centre of the
    top-left pixel. Drone mapping software writes these next to an orthomosaic.
    The values are assumed to be degrees (EPSG:4326); a projected world file
    would need a full CRS stack, which this deliberately avoids.
    """
    nums = [float(line.strip()) for line in text.split() if line.strip()]
    if len(nums) < 6:
        raise ImageError("a world file needs six numbers")
    a, d, b, e, c, f = nums[:6]

    # The origin gives it away: a projected file carries eastings and northings
    # in metres, which fall far outside the range of a latitude or longitude.
    # Pixel size is no help, since a fine orthomosaic is a small number either way.
    if abs(f) > 90.0 or abs(c) > 180.0:
        raise ImageError(
            "this world file looks projected (metres), not degrees. "
            "Export it in WGS84 / EPSG:4326, or place the image by hand."
        )

    def at(px: float, py: float) -> LatLon:
        return (f + d * px + e * py, c + a * px + b * py)  # (lat, lon)

    return Placement(
        top_left=at(0, 0),
        top_right=at(width_px, 0),
        bottom_left=at(0, height_px),
    )


def guess_placement(
    data: bytes,
    fallback_centre: LatLon | None = None,
    default_width_m: float = 60.0,
    world_file: str | None = None,
) -> tuple[Placement, str]:
    """Best first guess at where an image belongs, and how it was reached.

    Order of preference: a world file (exact), the camera's own GPS, then the
    middle of whatever the tracks cover. The result is a starting point - the
    user drags the corners to line it up with the ground.
    """
    width_px, height_px = image_size(data)

    if world_file:
        return placement_from_world_file(world_file, width_px, height_px), "world file"

    location = exif_location(data)
    if location is not None:
        # A camera looking straight down sees roughly its altitude across, for
        # the ~1:1 field of view typical of a drone camera. Rough, but it lands
        # the image in the right place at close to the right size.
        width_m = max(20.0, location.altitude_m or default_width_m)
        return (
            place_centred((location.lat, location.lon), width_px, height_px, width_m),
            "photo GPS",
        )

    if fallback_centre is None:
        raise ImageError(
            "this image has no GPS and no world file, and there are no tracks to "
            "centre it on. Add a track first, or place it by hand."
        )
    return (
        place_centred(fallback_centre, width_px, height_px, default_width_m),
        "centred on your tracks",
    )
