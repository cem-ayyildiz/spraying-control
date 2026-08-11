"""Raster coverage model.

The whole analysis hangs off one grid of pass counts. Rasterising the boom
swath instead of doing polygon booleans keeps overlap counting exact and cheap:
the number of passes over a cell falls straight out of the accumulator, and
gaps are just the cells inside the field that nobody reached.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .geo import LocalPlane
from .models import GapPatch, SprayerConfig

# Ceiling on grid cells, to bound memory. Exceeding it coarsens the cell size.
MAX_CELLS = 12_000_000
MAX_GAP_POLYGON_CELLS = 20_000


@dataclass
class CoverageGrid:
    """Pass counts over a regular grid in local plane metres.

    ``counts[row, col]``; row 0 is the southern edge, col 0 the western.
    """

    counts: np.ndarray  # int16
    x0: float
    y0: float
    cell: float
    plane: LocalPlane

    @property
    def cell_area(self) -> float:
        return self.cell * self.cell

    @property
    def shape(self) -> tuple[int, int]:
        return self.counts.shape

    def cell_centers(self) -> tuple[np.ndarray, np.ndarray]:
        ny, nx = self.counts.shape
        xs = self.x0 + (np.arange(nx) + 0.5) * self.cell
        ys = self.y0 + (np.arange(ny) + 0.5) * self.cell
        return xs, ys

    def bounds_latlon(self) -> tuple[float, float, float, float]:
        """(south, west, north, east) of the grid rectangle."""
        ny, nx = self.counts.shape
        lat_s, lon_w = self.plane.inverse(self.x0, self.y0)
        lat_n, lon_e = self.plane.inverse(self.x0 + nx * self.cell, self.y0 + ny * self.cell)
        return float(lat_s), float(lon_w), float(lat_n), float(lon_e)

    def rowcol_to_latlon(self, row: float, col: float) -> tuple[float, float]:
        x = self.x0 + (col + 0.5) * self.cell
        y = self.y0 + (row + 0.5) * self.cell
        lat, lon = self.plane.inverse(x, y)
        return float(lat), float(lon)


def make_grid(
    x: np.ndarray,
    y: np.ndarray,
    plane: LocalPlane,
    cfg: SprayerConfig,
    warnings: list[str] | None = None,
) -> CoverageGrid:
    """Allocate a grid covering the sprayed extent plus room for the boom and
    the morphological closing used to infer the field boundary."""
    margin = cfg.boom_width_m * (cfg.field_close_factor + 1.0) + 4.0 * cfg.cell_size_m
    xmin, xmax = float(np.min(x)) - margin, float(np.max(x)) + margin
    ymin, ymax = float(np.min(y)) - margin, float(np.max(y)) + margin

    cell = cfg.cell_size_m
    while True:
        nx = int(np.ceil((xmax - xmin) / cell))
        ny = int(np.ceil((ymax - ymin) / cell))
        if nx * ny <= MAX_CELLS or cell > 50.0:
            break
        cell *= 2.0

    if cell != cfg.cell_size_m and warnings is not None:
        warnings.append(
            f"Track extent is large; grid resolution coarsened from "
            f"{cfg.cell_size_m:g} m to {cell:g} m cells."
        )

    return CoverageGrid(
        counts=np.zeros((max(ny, 1), max(nx, 1)), dtype=np.int16),
        x0=xmin,
        y0=ymin,
        cell=cell,
        plane=plane,
    )


def swath_windows(
    grid: CoverageGrid,
    xs: np.ndarray,
    ys: np.ndarray,
    half_width: float,
):
    """Yield ``(row0, row1, col0, col1, mask)`` for each segment's swath.

    Working a window at a time keeps every operation local to the few hundred
    cells a segment actually touches, rather than the whole field.

    The boom is a line, so the two ends of the polyline get square caps: a pass
    sprays exactly ``length x boom_width``. Interior joins stay round, which is
    what fills the wedge on the outside of a turn.
    """
    ny, nx = grid.counts.shape
    cell = grid.cell
    r = half_width
    last = len(xs) - 2

    for i in range(len(xs) - 1):
        ax, ay, bx, by = xs[i], ys[i], xs[i + 1], ys[i + 1]

        c0 = max(int(np.floor((min(ax, bx) - r - grid.x0) / cell)), 0)
        c1 = min(int(np.ceil((max(ax, bx) + r - grid.x0) / cell)) + 1, nx)
        r0 = max(int(np.floor((min(ay, by) - r - grid.y0) / cell)), 0)
        r1 = min(int(np.ceil((max(ay, by) + r - grid.y0) / cell)) + 1, ny)
        if c0 >= c1 or r0 >= r1:
            continue

        dx, dy = bx - ax, by - ay
        seg_len2 = dx * dx + dy * dy
        if seg_len2 <= 0:
            continue

        gx = (grid.x0 + (np.arange(c0, c1) + 0.5) * cell)[None, :]
        gy = (grid.y0 + (np.arange(r0, r1) + 0.5) * cell)[:, None]

        t_raw = ((gx - ax) * dx + (gy - ay) * dy) / seg_len2
        t = np.clip(t_raw, 0.0, 1.0)
        dist2 = (gx - (ax + t * dx)) ** 2 + (gy - (ay + t * dy)) ** 2
        hit = dist2 <= r * r

        if i == 0:
            hit &= t_raw >= 0.0
        if i == last:
            hit &= t_raw <= 1.0

        yield r0, r1, c0, c1, hit


def stamp_polyline(
    grid: CoverageGrid,
    xs: np.ndarray,
    ys: np.ndarray,
    half_width: float,
    out: np.ndarray,
) -> None:
    """OR the whole polyline's swath into ``out``, ignoring re-entry timing."""
    for r0, r1, c0, c1, hit in swath_windows(grid, xs, ys, half_width):
        out[r0:r1, c0:c1] |= hit


def infer_field_mask(covered: np.ndarray, cfg: SprayerConfig, cell: float) -> np.ndarray:
    """Derive the field boundary from the sprayed area itself.

    A morphological closing bridges the normal spacing between adjacent passes,
    so the result is the worked block rather than the individual swaths. Holes
    are then filled, which turns any skipped area inside the block into part of
    the field — and therefore into a detectable gap once the sprayed area is
    subtracted.
    """
    radius_cells = max(1, int(round(cfg.boom_width_m * cfg.field_close_factor / cell)))
    struct = _disk(radius_cells)
    closed = ndimage.binary_closing(covered, structure=struct, border_value=0)
    return ndimage.binary_fill_holes(closed)


def _disk(radius: int) -> np.ndarray:
    span = np.arange(-radius, radius + 1)
    yy, xx = np.meshgrid(span, span, indexing="ij")
    return (xx * xx + yy * yy) <= radius * radius


def rasterize_field_polygons(grid: CoverageGrid, polygons: list) -> np.ndarray:
    """Burn user-supplied field boundaries (shapely polygons in plane metres)
    into a boolean mask."""
    import shapely

    ny, nx = grid.counts.shape
    mask = np.zeros((ny, nx), dtype=bool)
    if not polygons:
        return mask

    xs, ys = grid.cell_centers()
    for poly in polygons:
        if poly.is_empty:
            continue
        minx, miny, maxx, maxy = poly.bounds
        c0 = max(0, int(np.floor((minx - grid.x0) / grid.cell)))
        c1 = min(nx, int(np.ceil((maxx - grid.x0) / grid.cell)) + 1)
        r0 = max(0, int(np.floor((miny - grid.y0) / grid.cell)))
        r1 = min(ny, int(np.ceil((maxy - grid.y0) / grid.cell)) + 1)
        if c1 <= c0 or r1 <= r0:
            continue
        gx, gy = np.meshgrid(xs[c0:c1], ys[r0:r1])
        mask[r0:r1, c0:c1] |= shapely.intersects_xy(poly, gx, gy)
    return mask


def find_gaps(
    gap_mask: np.ndarray,
    grid: CoverageGrid,
    cfg: SprayerConfig,
    limit: int = 50,
) -> tuple[list[GapPatch], np.ndarray]:
    """Label unsprayed patches, discard specks, and describe the rest.

    Returns the patch list (largest first) and the cleaned gap mask.
    """
    labels, n = ndimage.label(gap_mask)
    if n == 0:
        return [], gap_mask

    cell_area = grid.cell_area
    min_cells = max(1, int(round(cfg.min_gap_area_m2 / cell_area)))
    sizes = ndimage.sum_labels(np.ones_like(labels, dtype=np.int32), labels, index=np.arange(1, n + 1))

    keep_ids = np.flatnonzero(sizes >= min_cells) + 1
    if keep_ids.size == 0:
        return [], np.zeros_like(gap_mask)

    cleaned = np.isin(labels, keep_ids)
    # Width of the widest inscribed circle: separates real skipped strips from
    # the ragged one-cell fringe along a swath edge.
    dist = ndimage.distance_transform_edt(cleaned, sampling=grid.cell)

    order = keep_ids[np.argsort(sizes[keep_ids - 1])[::-1]]
    patches: list[GapPatch] = []
    for label_id in order[:limit]:
        comp = labels == label_id
        area = float(comp.sum()) * cell_area
        rows, cols = np.nonzero(comp)
        lat, lon = grid.rowcol_to_latlon(float(rows.mean()), float(cols.mean()))
        patches.append(
            GapPatch(
                area_m2=area,
                lat=lat,
                lon=lon,
                max_width_m=float(dist[comp].max()) * 2.0,
                polygon=_mask_to_polygon(comp, grid),
            )
        )
    return patches, cleaned


def _mask_to_polygon(comp: np.ndarray, grid: CoverageGrid) -> list[list[float]]:
    """Outline of a labelled component as [[lon, lat], ...]."""
    from shapely import union_all
    from shapely.geometry import MultiPoint, box

    rows, cols = np.nonzero(comp)
    if rows.size == 0:
        return []

    if rows.size > MAX_GAP_POLYGON_CELLS:
        # Too many cells to union cheaply; the convex hull is enough to point
        # the operator at the right part of the field.
        pts = MultiPoint(
            [
                (grid.x0 + (c + 0.5) * grid.cell, grid.y0 + (r + 0.5) * grid.cell)
                for r, c in zip(rows[::17], cols[::17])
            ]
        )
        geom = pts.convex_hull
    else:
        boxes = [
            box(
                grid.x0 + c * grid.cell,
                grid.y0 + r * grid.cell,
                grid.x0 + (c + 1) * grid.cell,
                grid.y0 + (r + 1) * grid.cell,
            )
            for r, c in zip(rows, cols)
        ]
        geom = union_all(boxes).simplify(grid.cell * 0.75)

    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    if geom.geom_type != "Polygon" or geom.is_empty:
        return []

    xs, ys = np.asarray(geom.exterior.coords).T
    lat, lon = grid.plane.inverse(xs, ys)
    return [[round(float(a), 7), round(float(b), 7)] for a, b in zip(lon, lat)]
