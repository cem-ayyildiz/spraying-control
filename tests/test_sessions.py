"""Several sessions in one picture, aerial imagery, and the project store."""

from __future__ import annotations

import numpy as np
import pytest

from spraycontrol import analyze
from spraycontrol.demo import synthetic_run, to_gpx
from spraycontrol.imagery import (
    ImageError,
    Placement,
    exif_location,
    image_size,
    place_centred,
    placement_from_world_file,
)
from spraycontrol.report import write_png
from spraycontrol.store import ProjectStore

SWATH = 1.0
LANE = 40.0


def _session(n_passes, skip=(), day_offset=0.0, name="Session"):
    """A walk over `n_passes` lanes, optionally on a later day."""
    track, base, cfg = synthetic_run(
        n_passes=n_passes, skip_passes=skip, shift_passes={}, passes_per_load=99
    )
    track.t = track.t + day_offset * 86400.0
    track.name = name
    return track, base, cfg


class TestMultipleSessions:
    """Session A works lanes 0-11; a week later B works lanes 8-23, so four
    lanes are covered twice."""

    @staticmethod
    def _pair():
        a, base, cfg = _session(12, name="A")
        b, _, _ = _session(24, skip=tuple(range(8)), day_offset=7, name="B")
        return a, b, base, cfg

    def test_repeat_ground_is_reported_as_overlap(self):
        a, b, base, cfg = self._pair()
        combined = analyze([a, b], cfg, base, render=False)
        # Four lanes, one spray width wide, one lane long.
        assert combined.coverage.overlap_area_m2 == pytest.approx(4 * SWATH * LANE, rel=0.25)

    def test_union_adds_up(self):
        """Combined = A + B - the ground they share."""
        a, b, base, cfg = self._pair()
        only_a = analyze([a], cfg, base, render=False).coverage.sprayed_area_m2
        only_b = analyze([b], cfg, base, render=False).coverage.sprayed_area_m2
        combined = analyze([a, b], cfg, base, render=False)
        assert combined.coverage.sprayed_area_m2 == pytest.approx(
            only_a + only_b - combined.coverage.overlap_area_m2, rel=0.02
        )

    def test_per_session_new_versus_repeat(self):
        a, b, base, cfg = self._pair()
        result = analyze([a, b], cfg, base, render=False)
        first, second = result.tracks
        assert (first.name, second.name) == ("A", "B")
        # The earlier session cannot repeat anything.
        assert first.repeat_area_m2 == pytest.approx(0.0, abs=1.0)
        assert second.repeat_area_m2 == pytest.approx(4 * SWATH * LANE, rel=0.25)
        assert second.new_area_m2 + second.repeat_area_m2 == pytest.approx(second.area_m2, rel=0.01)

    def test_order_does_not_change_the_totals(self):
        """Passing them the other way round still sorts by time."""
        a, b, base, cfg = self._pair()
        forwards = analyze([a, b], cfg, base, render=False)
        backwards = analyze([b, a], cfg, base, render=False)
        assert backwards.coverage.sprayed_area_m2 == pytest.approx(forwards.coverage.sprayed_area_m2)
        assert [t.name for t in backwards.tracks] == ["A", "B"]

    def test_volume_and_loads_add_up(self):
        a, b, base, cfg = self._pair()
        result = analyze([a, b], cfg, base, render=False)
        assert len(result.loads) == 2  # one tank each
        assert result.total_volume_l == pytest.approx(2 * cfg.tank_capacity_l)
        assert sum(t.volume_l for t in result.tracks) == pytest.approx(result.total_volume_l)

    def test_one_session_matches_the_single_track_form(self):
        a, _, base, cfg = self._pair()
        as_list = analyze([a], cfg, base, render=False)
        as_single = analyze(a, cfg, base, render=False)
        assert as_list.coverage.sprayed_area_m2 == pytest.approx(as_single.coverage.sprayed_area_m2)
        assert as_single.track_name == "A"

    def test_a_broken_session_does_not_sink_the_rest(self):
        a, b, base, cfg = self._pair()
        broken = a.subset(np.array([True] + [False] * (len(a) - 1)))  # one point
        broken.name = "Broken"
        result = analyze([a, broken, b], cfg, base, render=False)
        assert [t.name for t in result.tracks] == ["A", "B"]
        assert any("Broken" in w for w in result.warnings)


class TestImagery:
    def test_png_size(self):
        png = write_png(np.zeros((37, 91, 4), dtype=np.uint8))
        assert image_size(png) == (91, 37)

    def test_unknown_format_is_rejected(self):
        with pytest.raises(ImageError):
            image_size(b"GIF89a not really an image")

    def test_placement_keeps_the_aspect_ratio(self):
        placement = place_centred((38.3, 32.9), 800, 400, 60.0)
        width, height = placement.ground_size_m()
        assert (width, height) == pytest.approx((60.0, 30.0), rel=1e-3)

    @pytest.mark.parametrize("angle", [0.0, 30.0, -45.0, 90.0])
    def test_rotation_round_trips(self, angle):
        placement = place_centred((38.3, 32.9), 800, 400, 60.0, rotation_deg=angle)
        assert placement.rotation_deg() == pytest.approx(angle, abs=0.01)
        width, height = placement.ground_size_m()
        assert (width, height) == pytest.approx((60.0, 30.0), rel=1e-3)

    def test_placement_survives_a_round_trip(self):
        placement = place_centred((38.3, 32.9), 640, 480, 25.0, rotation_deg=12.0)
        assert Placement.from_dict(placement.as_dict()).as_dict() == placement.as_dict()

    def test_fourth_corner_completes_the_rectangle(self):
        placement = place_centred((38.3, 32.9), 400, 400, 40.0)
        lat, lon = placement.bottom_right
        assert lat == pytest.approx(placement.bottom_left[0], abs=1e-9)
        assert lon == pytest.approx(placement.top_right[1], abs=1e-9)

    @pytest.mark.parametrize("angle", [0.0, 25.0, -70.0])
    def test_pixel_and_gps_are_inverses(self, angle):
        """Every pixel maps to a point on the ground, and back again."""
        width, height = 900, 600
        placement = place_centred((38.3005, 32.8985), width, height, 60.0, rotation_deg=angle)
        for px, py in [(0, 0), (width, 0), (0, height), (width, height), (123, 456)]:
            lat, lon = placement.pixel_to_latlon(px, py, width, height)
            back = placement.latlon_to_pixel(lat, lon, width, height)
            assert back == pytest.approx((px, py), abs=1e-6)

    def test_corners_land_on_the_corners(self):
        width, height = 800, 400
        placement = place_centred((38.3, 32.9), width, height, 50.0, rotation_deg=15.0)
        assert placement.pixel_to_latlon(0, 0, width, height) == pytest.approx(placement.top_left)
        assert placement.pixel_to_latlon(width, 0, width, height) == pytest.approx(placement.top_right)
        assert placement.pixel_to_latlon(0, height, width, height) == pytest.approx(placement.bottom_left)
        assert placement.pixel_to_latlon(width, height, width, height) == pytest.approx(placement.bottom_right)

    def test_a_point_outside_the_photo_reads_outside(self):
        width, height = 600, 600
        placement = place_centred((38.3, 32.9), width, height, 40.0)
        # Roughly a kilometre away, so far off the picture.
        px, py = placement.latlon_to_pixel(38.31, 32.91, width, height)
        assert not (0 <= px <= width and 0 <= py <= height)

    def test_world_file(self):
        text = "0.0000002\n0.0\n0.0\n-0.0000002\n32.9\n38.3"
        placement = placement_from_world_file(text, 1000, 500)
        assert placement.top_left == pytest.approx((38.3, 32.9))
        assert placement.top_right[1] == pytest.approx(32.9 + 0.0002)
        assert placement.bottom_left[0] == pytest.approx(38.3 - 0.0001)

    def test_projected_world_file_is_refused_with_advice(self):
        """Metres-based world files would need a full CRS stack."""
        with pytest.raises(ImageError, match="EPSG:4326"):
            placement_from_world_file("0.05\n0\n0\n-0.05\n500000\n4200000", 100, 100)

    def test_photo_without_exif(self):
        assert exif_location(write_png(np.zeros((4, 4, 4), dtype=np.uint8))) is None


class TestProjectStore:
    def test_a_project_survives_a_reload(self, tmp_path):
        store = ProjectStore(tmp_path)
        project = store.create("Back garden")
        track, base, cfg = _session(12, name="Walk")
        store.add_track(project, to_gpx(track).encode(), "walk.gpx", name="Walk")

        reopened = store.load(project.id)
        assert reopened.name == "Back garden"
        assert [t.name for t in reopened.tracks] == ["Walk"]
        assert reopened.tracks[0].n_points > 100

        tracks = store.load_tracks(reopened)
        assert len(tracks) == 1
        result = analyze(tracks, reopened.sprayer_config(), base, render=False)
        assert result.coverage.sprayed_area_m2 > 0

    def test_sessions_are_listed_oldest_first(self, tmp_path):
        store = ProjectStore(tmp_path)
        project = store.create()
        later, _, _ = _session(6, day_offset=7, name="Later")
        earlier, _, _ = _session(6, name="Earlier")
        store.add_track(project, to_gpx(later).encode(), "b.gpx", name="Later")
        store.add_track(project, to_gpx(earlier).encode(), "a.gpx", name="Earlier")
        assert [t.name for t in store.load(project.id).tracks] == ["Earlier", "Later"]

    def test_only_ticked_sessions_are_loaded(self, tmp_path):
        store = ProjectStore(tmp_path)
        project = store.create()
        for i in range(3):
            track, _, _ = _session(6, day_offset=i, name=f"S{i}")
            store.add_track(project, to_gpx(track).encode(), f"s{i}.gpx", name=f"S{i}")
        project.tracks[1].enabled = False
        store.save(project)
        assert [t.name for t in store.load_tracks(store.load(project.id))] == ["S0", "S2"]

    def test_an_overlay_is_centred_on_the_tracks(self, tmp_path):
        store = ProjectStore(tmp_path)
        project = store.create()
        track, _, _ = _session(10, name="Walk")
        store.add_track(project, to_gpx(track).encode(), "walk.gpx")

        image = write_png(np.zeros((300, 600, 4), dtype=np.uint8))
        record = store.add_overlay(
            project, image, "aerial.png", fallback_centre=store.tracks_centre(project)
        )
        assert (record.width_px, record.height_px) == (600, 300)

        placement = record.as_placement()
        width, height = placement.ground_size_m()
        assert width / height == pytest.approx(2.0, rel=0.01)

        # It lands on the walk, not somewhere off the map.
        centre = store.tracks_centre(project)
        assert placement.top_left[0] == pytest.approx(centre[0], abs=0.001)

    def test_removing_a_session_deletes_its_file(self, tmp_path):
        store = ProjectStore(tmp_path)
        project = store.create()
        track, _, _ = _session(6, name="Walk")
        record = store.add_track(project, to_gpx(track).encode(), "walk.gpx")
        path = store.track_path(project.id, record)
        assert path.is_file()

        assert store.remove_track(project, record.id)
        assert not path.exists()
        assert store.load(project.id).tracks == []

    def test_a_rubbish_upload_is_refused(self, tmp_path):
        from spraycontrol.parsers import TrackParseError

        store = ProjectStore(tmp_path)
        project = store.create()
        with pytest.raises((TrackParseError, ValueError)):
            store.add_track(project, b"this is not a track", "notes.txt")
        assert store.load(project.id).tracks == []
