// Thin wrapper around the backend REST API.

async function request(url, opts = {}, timeoutMs = 30000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { ...opts, signal: ctl.signal });
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try {
        const j = await r.json();
        if (j && j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
        else if (j && j.error) msg = j.error;
      } catch { /* not JSON */ }
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    return r;
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('The server took too long to respond.');
    if (e instanceof TypeError) throw new Error('Cannot reach the Planet X server. Is it running?');
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  config: async () => (await request('api/config')).json(),
  resolve: async (q) => (await request(`api/resolve?q=${encodeURIComponent(q)}`)).json(),
  submit: async (params) => (await request('api/jobs', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params),
  })).json(),
  status: async (id) => (await request(`api/jobs/${id}`, {}, 15000)).json(),
  cancel: async (id) => (await request(`api/jobs/${id}`, { method: 'DELETE' })).json(),
  frames: async (id, mode) => {
    const r = await request(`api/jobs/${id}/frames.bin?mode=${mode}`, {}, 120000);
    const buf = await r.arrayBuffer();
    return { data: new Float32Array(buf), nFrames: +r.headers.get('X-Frames'), n: +r.headers.get('X-Size') };
  },
  spectrum: async (id, q) => (await request(`api/jobs/${id}/spectrum?${new URLSearchParams(q)}`, {}, 60000)).json(),
  catalog: async (cat, ra, dec, radius) =>
    (await request(`api/catalog?cat=${cat}&ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&radius=${radius.toFixed(0)}`, {}, 45000)).json(),
  fitsUrl: (id, frame, mode) => `api/jobs/${id}/fits?frame=${frame}&mode=${mode}`,
};
