'use strict';

/* Snap an aerial photo onto the ground using the walked track.
 *
 * A spraying route is not random: it runs along paths, bed edges and the sides
 * of a plot, and those same features are what stand out in the picture. So the
 * track doubles as a set of control points - slide, turn and scale the photo
 * until the route lies on the lines in it.
 *
 * Everything happens in the browser on a canvas, so any image the browser can
 * open works, it costs no dependency, and it runs offline.
 */

const Snap = (() => {
  const WORK = 256;          // the feature map is matched at this size
  const BLUR_PASSES = 2;     // widen the edges so the search has something to climb

  /* Grey, edge-detected and blurred: bright where the picture has structure. */
  function featureMap(img) {
    const w = WORK;
    const h = Math.max(1, Math.round((WORK * img.naturalHeight) / img.naturalWidth));
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, w, h);
    const px = ctx.getImageData(0, 0, w, h).data;

    const grey = new Float32Array(w * h);
    for (let i = 0, p = 0; i < grey.length; i++, p += 4) {
      grey[i] = (px[p] * 0.299 + px[p + 1] * 0.587 + px[p + 2] * 0.114) / 255;
    }

    // Sobel magnitude: paths, walls and bed edges all show up as ridges.
    const edge = new Float32Array(w * h);
    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        const i = y * w + x;
        const gx =
          -grey[i - w - 1] - 2 * grey[i - 1] - grey[i + w - 1] +
          grey[i - w + 1] + 2 * grey[i + 1] + grey[i + w + 1];
        const gy =
          -grey[i - w - 1] - 2 * grey[i - w] - grey[i - w + 1] +
          grey[i + w - 1] + 2 * grey[i + w] + grey[i + w + 1];
        edge[i] = Math.hypot(gx, gy);
      }
    }

    // Blur, so a track a few pixels off still feels the pull of a nearby line.
    let src = edge;
    for (let pass = 0; pass < BLUR_PASSES; pass++) {
      const dst = new Float32Array(w * h);
      for (let y = 1; y < h - 1; y++) {
        for (let x = 1; x < w - 1; x++) {
          const i = y * w + x;
          dst[i] =
            (src[i] * 4 + src[i - 1] * 2 + src[i + 1] * 2 + src[i - w] * 2 + src[i + w] * 2 +
             src[i - w - 1] + src[i - w + 1] + src[i + w - 1] + src[i + w + 1]) / 16;
        }
      }
      src = dst;
    }

    let peak = 0;
    for (let i = 0; i < src.length; i++) if (src[i] > peak) peak = src[i];
    if (peak > 0) for (let i = 0; i < src.length; i++) src[i] /= peak;

    return { data: src, w, h };
  }

  /* How well the track sits on the picture's features, for one placement.
   *
   * Averaged over the *whole* route, not just the part that happens to land on
   * the photo. Dividing by the points inside instead rewards exactly the wrong
   * thing: slide the picture until only a handful of lucky points remain on it,
   * all of them on strong edges, and the average goes up. That degenerate
   * answer beat the true alignment every time until this was averaged properly.
   */
  function score(map, pts, centre, widthM, rotDeg, per) {
    const t = (rotDeg * Math.PI) / 180;
    const cos = Math.cos(t);
    const sin = Math.sin(t);
    const heightM = (widthM * map.h) / map.w;
    let total = 0;
    let inside = 0;

    for (let i = 0; i < pts.length; i += 2) {
      // Ground offset from the photo centre, in metres.
      const ex = (pts[i + 1] - centre[1]) * per.lon;
      const ny = (pts[i] - centre[0]) * per.lat;
      // Undo the photo's turn to get picture-frame metres.
      const fx = ex * cos - ny * sin;
      const fy = ex * sin + ny * cos;
      // Metres to pixels; picture y runs down.
      const u = ((fx / widthM) + 0.5) * map.w;
      const v = (0.5 - fy / heightM) * map.h;
      if (u < 0 || v < 0 || u >= map.w - 1 || v >= map.h - 1) continue;
      inside++;
      total += map.data[(v | 0) * map.w + (u | 0)];
    }
    const n = pts.length / 2;
    if (inside < n * 0.8) return -1;   // the route has to sit on the picture
    return total / n;
  }

  /* Tighten up a placement the user has already made roughly.
   *
   * Deliberately a refinement, not a hunt across the whole picture. Planted
   * rows repeat, so sliding a photo by exactly one row spacing scores the same
   * as not sliding it at all; given a free rein the search happily walks off to
   * one of those false matches at the wrong scale. Kept close to where it
   * started it converges on the real thing, and the angle - the part that is
   * most painful by hand - comes back accurately even from tens of degrees out.
   */
  function refine(map, points, start, per, opts = {}) {
    const free = opts.lockWidth ? 0 : 1;
    const spans = opts.spans || [
      { move: start.widthM * 0.20, rot: 22, scale: 0.12 * free, steps: 5 },
      { move: start.widthM * 0.08, rot: 7, scale: 0.05 * free, steps: 5 },
      { move: start.widthM * 0.03, rot: 2.0, scale: 0.02 * free, steps: 5 },
      { move: start.widthM * 0.010, rot: 0.6, scale: 0.008 * free, steps: 5 },
    ];

    const pts = new Float64Array(points.length * 2);
    points.forEach((p, i) => { pts[i * 2] = p[0]; pts[i * 2 + 1] = p[1]; });

    const before = score(map, pts, start.centre, start.widthM, start.rotDeg, per);
    let best = { ...start, score: before };

    for (const span of spans) {
      const n = span.steps;
      const half = (n - 1) / 2;
      const current = { ...best };
      for (let i = 0; i < n; i++) {
        const dLat = ((i - half) / half) * (span.move / per.lat);
        for (let j = 0; j < n; j++) {
          const dLon = ((j - half) / half) * (span.move / per.lon);
          for (let k = 0; k < n; k++) {
            const rot = current.rotDeg + ((k - half) / half) * span.rot;
            for (let m = 0; m < n; m++) {
              const width = current.widthM * (1 + ((m - half) / half) * span.scale);
              if (width <= 0.5) continue;
              const centre = [current.centre[0] + dLat, current.centre[1] + dLon];
              const s = score(map, pts, centre, width, rot, per);
              if (s > best.score) best = { centre, widthM: width, rotDeg: rot, score: s };
            }
          }
        }
      }
    }
    // How much better it got, so the caller can say whether it was worth it.
    best.before = before;
    best.gain = before > 0 ? best.score / before - 1 : 0;
    return best;
  }

  return { featureMap, refine, score };
})();
