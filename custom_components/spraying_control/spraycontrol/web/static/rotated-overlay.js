'use strict';

/* A Leaflet image overlay pinned by three corners.
 *
 * The built-in L.ImageOverlay can only sit square to the map, but a photo from
 * a drone is taken at whatever angle the aircraft was pointing. Three corners -
 * top-left, top-right, bottom-left - pin an affine transform, which is enough
 * to position, scale, rotate and even shear the picture onto the ground.
 *
 * The image is drawn as a plain <img> with a CSS matrix, so it stays sharp at
 * any zoom and costs nothing to redraw.
 */
L.RotatedOverlay = L.Layer.extend({
  options: { opacity: 1, interactive: false, className: '' },

  initialize(url, corners, options) {
    this._url = url;
    this.setCorners(corners, true);
    L.setOptions(this, options);
  },

  /* corners: {topLeft, topRight, bottomLeft} as L.LatLng or [lat, lon]. */
  setCorners(corners, skipRedraw) {
    const toLatLng = (c) => (c instanceof L.LatLng ? c : L.latLng(c[0], c[1]));
    this._topLeft = toLatLng(corners.topLeft);
    this._topRight = toLatLng(corners.topRight);
    this._bottomLeft = toLatLng(corners.bottomLeft);
    if (!skipRedraw && this._map) this._reset();
    return this;
  },

  getCorners() {
    return { topLeft: this._topLeft, topRight: this._topRight, bottomLeft: this._bottomLeft };
  },

  /* The implied fourth corner, so callers can draw a full outline. */
  getBottomRight() {
    return L.latLng(
      this._topRight.lat + this._bottomLeft.lat - this._topLeft.lat,
      this._topRight.lng + this._bottomLeft.lng - this._topLeft.lng,
    );
  },

  setOpacity(opacity) {
    this.options.opacity = opacity;
    if (this._image) this._image.style.opacity = opacity;
    return this;
  },

  setUrl(url) {
    this._url = url;
    if (this._image) this._image.src = url;
    return this;
  },

  onAdd() {
    if (!this._image) {
      const img = (this._image = L.DomUtil.create('img', 'leaflet-image-layer ' + this.options.className));
      img.style.opacity = this.options.opacity;
      img.style.transformOrigin = '0 0';
      img.style.pointerEvents = this.options.interactive ? 'auto' : 'none';
      img.alt = '';
      // Natural size drives the transform, so wait for it before the first draw.
      img.onload = () => this._reset();
      img.src = this._url;
    }
    this.getPane().appendChild(this._image);
    this._reset();
  },

  onRemove() {
    if (this._image && this._image.parentNode) this._image.parentNode.removeChild(this._image);
  },

  getEvents() {
    // Wrapped, because Leaflet hands these an event object and _reset takes a
    // zoom level - passing the event straight through reads as "zoom to [object]".
    return { zoom: this._onViewChange, viewreset: this._onViewChange, zoomanim: this._animateZoom };
  },

  _onViewChange() {
    this._reset();
  },

  _animateZoom(e) {
    // Keep the picture glued to the ground mid-zoom instead of letting it drift.
    this._reset(e.zoom, e.center);
  },

  _reset(zoom, center) {
    const img = this._image;
    if (!img || !this._map) return;
    if (!img.naturalWidth || !img.naturalHeight) return;

    const animating = typeof zoom === 'number' && center !== undefined;
    const project = (latlng) =>
      animating
        ? this._map.project(latlng, zoom).subtract(this._map._getNewPixelOrigin(center, zoom))
        : this._map.latLngToLayerPoint(latlng);

    const tl = project(this._topLeft);
    const tr = project(this._topRight);
    const bl = project(this._bottomLeft);

    // Map the image's own pixel box onto those three points. Columns of the
    // matrix are where the unit x and y axes of the image end up.
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    const a = (tr.x - tl.x) / w;
    const b = (tr.y - tl.y) / w;
    const c = (bl.x - tl.x) / h;
    const d = (bl.y - tl.y) / h;

    img.style.transform = `matrix(${a}, ${b}, ${c}, ${d}, ${tl.x}, ${tl.y})`;
  },
});

L.rotatedOverlay = (url, corners, options) => new L.RotatedOverlay(url, corners, options);
