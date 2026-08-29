// Capa de acceso a la API. Token Bearer en memoria con respaldo en cookie
// (el iframe de vista previa bloquea los almacenes del navegador; se degrada con gracia).
import { store } from './ui.js';

// En la vista previa de Perplexity el marcador se reescribe a la ruta del proxy;
// en local y detrás de nginx queda sin reescribir y se usa el mismo origen (rutas relativas).
const API = '__PORT_9000__'.startsWith('__') ? '' : '__PORT_9000__';

let _token = null;

export function getToken() {
  if (_token) return _token;
  _token = store.get('mitube_token');
  return _token;
}

export function setToken(token) {
  _token = token;
  if (token) store.set('mitube_token', token);
  else store.remove('mitube_token');
}

export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}

async function request(path, { method = 'GET', body, isForm = false, onProgress } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;

  if (onProgress) return uploadWithProgress(path, body, headers, onProgress);

  if (body && !isForm) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${API}${path}`, {
    method, headers,
    body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* respuesta sin cuerpo */ }
  if (!res.ok) {
    const detail = data && data.detail
      ? (typeof data.detail === 'string' ? data.detail : 'Datos inválidos.')
      : 'Error de conexión con el servidor.';
    throw new ApiError(res.status, detail);
  }
  return data;
}

// XHR para poder reportar progreso de subida
function uploadWithProgress(path, formData, headers, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}${path}`);
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    });
    xhr.addEventListener('load', () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* vacío */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, (data && typeof data.detail === 'string') ? data.detail : 'Error al subir.'));
    });
    xhr.addEventListener('error', () => reject(new ApiError(0, 'Error de red durante la subida.')));
    xhr.send(formData);
  });
}

export const api = {
  config: () => request('/api/auth/config'),
  register: (d) => request('/api/auth/register', { method: 'POST', body: d }),
  login: (d) => request('/api/auth/login', { method: 'POST', body: d }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  me: () => request('/api/auth/me'),
  tracks: (params) => request(`/api/tracks?${new URLSearchParams(params)}`),
  track: (id) => request(`/api/tracks/${id}`),
  meta: () => request('/api/meta'),
  registerPlay: (id) => request(`/api/tracks/${id}/play`, { method: 'POST' }),
  admin: {
    settings: () => request('/api/admin/settings'),
    updateSettings: (d) => request('/api/admin/settings', { method: 'PUT', body: d }),
    users: () => request('/api/admin/users'),
    createUser: (d) => request('/api/admin/users', { method: 'POST', body: d }),
    updateUser: (id, d) => request(`/api/admin/users/${id}`, { method: 'PUT', body: d }),
    deleteUser: (id) => request(`/api/admin/users/${id}`, { method: 'DELETE' }),
    createGenre: (d) => request('/api/admin/genres', { method: 'POST', body: d }),
    updateGenre: (id, d) => request(`/api/admin/genres/${id}`, { method: 'PUT', body: d }),
    deleteGenre: (id) => request(`/api/admin/genres/${id}`, { method: 'DELETE' }),
    uploadTrack: (formData, onProgress) => request('/api/admin/tracks', { method: 'POST', body: formData, isForm: true, onProgress }),
    editTrack: (id, d) => request(`/api/admin/tracks/${id}`, { method: 'PUT', body: d }),
    deleteTrack: (id) => request(`/api/admin/tracks/${id}`, { method: 'DELETE' }),
  },
};

// URLs para elementos <video>/<img>/<a> (no admiten cabeceras → token por query)
export const mediaUrl = {
  stream: (id, kind) => `${API}/api/stream/${id}/${kind}?t=${encodeURIComponent(getToken() || '')}`,
  download: (id, kind) => `${API}/api/download/${id}/${kind}?t=${encodeURIComponent(getToken() || '')}`,
  cover: (id) => `${API}/api/cover/${id}?t=${encodeURIComponent(getToken() || '')}`,
};
