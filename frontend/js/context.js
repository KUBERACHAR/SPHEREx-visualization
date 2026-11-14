// Multi-mission sky context via Aladin Lite v3 (CDS), loaded only on demand (~2 MB, WebGL).
const ALADIN_SRC = 'https://cdn.jsdelivr.net/npm/aladin-lite@3.6.5/dist/aladin.js';

let loading = null;

// The npm build is an ES module whose default export is the `A` namespace.
function loadAladin() {
  if (!loading) {
    loading = import(ALADIN_SRC).then((mod) => {
      const A = mod.default || window.A;
      if (!A || !A.aladin) throw new Error('Aladin Lite failed to initialise');
      return A;
    }).catch((e) => {
      loading = null;
      throw new Error(/WebGL/i.test(String(e)) ? 'The sky atlas needs WebGL2, which this browser/GPU does not provide.'
        : 'Could not download the sky atlas (offline?).');
    });
  }
  return loading;
}

export class SkyContext {
  constructor(el) {
    this.el = el;
    this.aladin = null;
    this.overlay = null;
    // NASA/IRSA mirrors of the HiPS surveys (independent of CDS availability)
    this.survey = 'https://irsa.ipac.caltech.edu/data/hips/CDS/DSS2/color';
    this.pending = null;
  }

  get ready() { return !!this.aladin; }

  async load() {
    if (this.aladin) return;
    this.el.innerHTML = '<div class="muted center-msg">Loading sky atlas…</div>';
    const A = await loadAladin();
    try { await A.init; } catch (e) {
      throw new Error(/WebGL/i.test(String(e)) ? 'The sky atlas needs WebGL2, which this browser/GPU does not provide.' : String(e));
    }
    this.A = A;
    this.el.innerHTML = '';
    const div = document.createElement('div');
    div.style.cssText = 'width:100%;height:100%';
    this.el.appendChild(div);
    this.aladin = A.aladin(div, {
      survey: this.survey, fov: 0.25, target: '0 0', cooFrame: 'ICRSd',
      showReticle: true, showZoomControl: true, showFullscreenControl: true,
      showLayersControl: true, showGotoControl: false, showCooGrid: false, showFrame: true,
      log: false, // no usage telemetry to CDS
    });
    this.overlay = A.graphicOverlay({ color: '#ffc857', lineWidth: 2, lineDash: [6, 4] });
    this.aladin.addOverlay(this.overlay);
    if (this.pending) this.show(this.pending);
  }

  setSurvey(id) {
    this.survey = id;
    if (!this.aladin) return;
    try {
      if (this.aladin.setBaseImageLayer) this.aladin.setBaseImageLayer(id);
      else this.aladin.setImageSurvey(id);
    } catch (e) { console.warn('survey switch failed', e); }
  }

  /** Centre on the field and draw the SPHEREx field-of-view box. corners: [[ra,dec] x4] */
  show({ ra, dec, fovDeg, corners }) {
    this.pending = { ra, dec, fovDeg, corners };
    if (!this.aladin) return;
    try {
      this.aladin.gotoRaDec(ra, dec);
      this.aladin.setFoV(Math.min(180, Math.max(0.02, fovDeg * 2.2)));
      this.overlay.removeAll();
      if (corners) this.overlay.add(this.A.polygon(corners));
    } catch (e) { console.warn('Aladin update failed', e); }
  }
}
