// Punto de entrada: autenticación, biblioteca, filtros, búsqueda y navegación.
import { api, ApiError, mediaUrl, setToken, getToken } from './api.js';
import { initPlayer, playIndex, setQueue, currentTrack } from './player.js';
import { initAdmin, showAdmin } from './admin.js';
import { bindGlobalDropdownClose, debounce, elFrom, formatTime, toast } from './ui.js';

const $ = (id) => document.getElementById(id);

const ui = {
  user: null,
  filter: 'all',       // all | genres | artists | albums
  subId: null,         // id de género/artista/álbum activo
  sort: 'recent',
  search: '',
  meta: { genres: [], artists: [], albums: [] },
  tracks: [],
};

// ---------- Autenticación ----------

function showView(name) {
  $('auth-view').classList.toggle('hidden', name !== 'auth');
  $('app-view').classList.toggle('hidden', name !== 'app');
  $('admin-view').classList.toggle('hidden', name !== 'admin');
}

function authError(msg) { $('auth-error').textContent = msg || ''; }

function validateRegisterClientSide(f) {
  // Validación redundante en frontend (el backend re-valida siempre)
  const u = f.username.value.trim();
  if (!/^[a-zA-Z0-9_]{3,32}$/.test(u)) return 'Usuario: 3-32 letras, números o guion bajo.';
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(f.email.value.trim())) return 'Correo electrónico inválido.';
  const p = f.password.value;
  if (p.length < 10) return 'La contraseña debe tener al menos 10 caracteres.';
  if (!/[a-z]/.test(p) || !/[A-Z]/.test(p) || !/[0-9]/.test(p) || !/[^a-zA-Z0-9]/.test(p)) {
    return 'La contraseña necesita mayúscula, minúscula, dígito y símbolo.';
  }
  if (p !== f.password2.value) return 'Las contraseñas no coinciden.';
  return null;
}

function bindAuth() {
  $('tab-login').addEventListener('click', () => switchAuthTab(true));
  $('tab-register').addEventListener('click', () => switchAuthTab(false));

  $('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    authError('');
    const f = e.target;
    try {
      const { token, user } = await api.login({ username: f.username.value.trim(), password: f.password.value });
      setToken(token);
      enterApp(user);
    } catch (err) {
      authError(err instanceof ApiError ? err.message : 'No se pudo conectar con el servidor.');
    }
  });

  $('register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    authError('');
    const f = e.target;
    const clientErr = validateRegisterClientSide(f);
    if (clientErr) { authError(clientErr); return; }
    try {
      const { token, user } = await api.register({
        username: f.username.value.trim(), email: f.email.value.trim(), password: f.password.value,
      });
      setToken(token);
      enterApp(user);
    } catch (err) {
      authError(err instanceof ApiError ? err.message : 'No se pudo conectar con el servidor.');
    }
  });

  $('btn-logout').addEventListener('click', async () => {
    try { await api.logout(); } catch { /* la sesión local se limpia igualmente */ }
    setToken(null);
    ui.user = null;
    showView('auth');
  });
}

function switchAuthTab(login) {
  $('tab-login').classList.toggle('active', login);
  $('tab-register').classList.toggle('active', !login);
  $('login-form').classList.toggle('hidden', !login);
  $('register-form').classList.toggle('hidden', login);
  authError('');
}

async function enterApp(user) {
  ui.user = user;
  $('user-name').textContent = user.username;
  $('btn-admin').classList.toggle('hidden', user.role !== 'admin');
  showView('app');
  await refreshMeta();
  await refreshTracks();
}

// ---------- Biblioteca / filtros ----------

async function refreshMeta() {
  try { ui.meta = await api.meta(); } catch { toast('No se pudieron cargar los filtros.', true); }
  paintSubfilter();
}

function currentParams() {
  const p = { sort: ui.sort };
  if (ui.search) p.q = ui.search;
  if (ui.subId != null) {
    if (ui.filter === 'genres') p.genre_id = ui.subId;
    if (ui.filter === 'artists') p.artist_id = ui.subId;
    if (ui.filter === 'albums') p.album_id = ui.subId;
  }
  return p;
}

async function refreshTracks() {
  const list = $('playlist');
  list.replaceChildren(...Array.from({ length: 6 }, () => elFrom('li', 'skeleton')));
  try {
    const { tracks } = await api.tracks(currentParams());
    ui.tracks = tracks;
    setQueue(tracks);
    paintList();
  } catch (err) {
    list.replaceChildren();
    if (err instanceof ApiError && err.status === 401) { setToken(null); showView('auth'); return; }
    toast('Error al cargar la biblioteca.', true);
  }
}

function paintSubfilter() {
  const box = $('subfilter');
  box.replaceChildren();
  if (ui.filter === 'all') { box.classList.add('hidden'); return; }
  const items = ui.filter === 'genres' ? ui.meta.genres
    : ui.filter === 'artists' ? ui.meta.artists
    : ui.meta.albums;
  for (const item of items) {
    const label = ui.filter === 'albums' ? `${item.title}${item.artist ? ' · ' + item.artist : ''}` : item.name;
    const b = elFrom('button', ui.subId === item.id ? 'active' : '', label);
    b.addEventListener('click', () => {
      ui.subId = ui.subId === item.id ? null : item.id;
      paintSubfilter();
      refreshTracks();
    });
    box.appendChild(b);
  }
  box.classList.remove('hidden');
}

function paintList() {
  const list = $('playlist');
  list.replaceChildren();
  $('list-empty').classList.toggle('hidden', ui.tracks.length > 0);
  $('list-count').textContent = `${ui.tracks.length} temas`;
  const cur = currentTrack();

  ui.tracks.forEach((t, i) => {
    const li = elFrom('li', 'pl-item' + (cur && cur.id === t.id ? ' active' : ''));
    li.setAttribute('role', 'listitem');
    li.tabIndex = 0;

    const thumb = elFrom('div', 'pl-thumb');
    if (t.has_cover) {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.alt = '';
      img.src = mediaUrl.cover(t.id);
      thumb.appendChild(img);
    } else {
      thumb.textContent = '♪';
    }

    const text = elFrom('div', 'pl-text');
    text.appendChild(elFrom('div', 'pl-title', t.title));
    text.appendChild(elFrom('div', 'pl-sub', `${t.artist}${t.album ? ' · ' + t.album : ''}`));

    const right = elFrom('div', 'pl-right dropdown-host');
    if (t.has_video) right.appendChild(elFrom('span', 'pl-badge', 'VIDEO'));
    right.appendChild(elFrom('span', 'pl-dur', formatTime(t.duration)));

    // Botón cuadrado de descarga (aparece al hover) con menú MP3/MP4
    const dl = elFrom('button', 'pl-dl');
    dl.title = 'Descargar';
    dl.setAttribute('aria-label', `Descargar ${t.title}`);
    dl.setAttribute('aria-expanded', 'false');
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24'); icon.setAttribute('width', '15'); icon.setAttribute('height', '15');
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', 'M12 4v10m0 0 4-4m-4 4-4-4M5 19h14');
    p.setAttribute('stroke', 'currentColor'); p.setAttribute('stroke-width', '2'); p.setAttribute('fill', 'none');
    icon.appendChild(p);
    dl.appendChild(icon);

    const menu = buildDownloadMenu(t);
    dl.addEventListener('click', (e) => {
      e.stopPropagation();
      const wasHidden = menu.classList.contains('hidden');
      document.querySelectorAll('.dropdown').forEach((d) => d.classList.add('hidden'));
      menu.classList.toggle('hidden', !wasHidden);
      dl.setAttribute('aria-expanded', String(wasHidden));
    });
    right.appendChild(dl);
    right.appendChild(menu);

    li.appendChild(thumb);
    li.appendChild(text);
    li.appendChild(right);

    li.addEventListener('click', (e) => {
      if (e.target.closest('.pl-dl') || e.target.closest('.dropdown')) return;
      playIndex(i);
      paintList();
    });
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { playIndex(i); paintList(); }
    });
    list.appendChild(li);
  });
}

function buildDownloadMenu(t) {
  const menu = elFrom('div', 'dropdown hidden');
  menu.setAttribute('role', 'menu');
  const optAudio = elFrom('button', '', 'Descargar Audio (MP3/M4A)');
  optAudio.addEventListener('click', () => { triggerDownload(t.id, 'audio'); menu.classList.add('hidden'); });
  const optVideo = elFrom('button', '', 'Descargar Video (MP4)');
  if (!t.has_video) { optVideo.disabled = true; optVideo.textContent = 'Video no disponible'; }
  else optVideo.addEventListener('click', () => { triggerDownload(t.id, 'video'); menu.classList.add('hidden'); });
  menu.appendChild(optAudio);
  menu.appendChild(optVideo);
  return menu;
}

function triggerDownload(id, kind) {
  const a = document.createElement('a');
  a.href = mediaUrl.download(id, kind);
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast('Descarga iniciada…');
}

// ---------- Eventos globales ----------

function bindLibraryControls() {
  document.querySelectorAll('.filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      ui.filter = btn.dataset.filter;
      ui.subId = null;
      paintSubfilter();
      refreshTracks();
    });
  });

  $('sort-select').addEventListener('change', (e) => {
    ui.sort = e.target.value;
    refreshTracks();
  });

  $('search-input').addEventListener('input', debounce((e) => {
    ui.search = e.target.value.trim();
    refreshTracks();
  }, 300));

  // Descargar tema actual (dropdown)
  $('btn-download-current').addEventListener('click', (e) => {
    e.stopPropagation();
    const t = currentTrack();
    if (!t) { toast('No hay ningún tema en reproducción.', true); return; }
    const host = $('dl-current-menu');
    host.replaceChildren(...buildDownloadMenu(t).children);
    host.classList.toggle('hidden');
  });

  $('btn-admin').addEventListener('click', () => { showView('admin'); showAdmin(); });
  $('btn-back-app').addEventListener('click', async () => {
    showView('app');
    await refreshMeta();
    await refreshTracks();
  });
}

// ---------- Arranque ----------

async function applyPublicConfig() {
  try {
    const cfg = await api.config();
    const allowed = !!cfg.public_registration;
    $('tab-register').classList.toggle('hidden', !allowed);
    if (!allowed) switchAuthTab(true); // forzar pestaña de login
  } catch { /* si falla, se deja el comportamiento por defecto */ }
}

async function boot() {
  bindAuth();
  bindLibraryControls();
  bindGlobalDropdownClose();
  initPlayer({ onTrackChange: () => paintList() });
  initAdmin({ notifyLibraryChanged: refreshMeta, onSettingsChanged: applyPublicConfig });
  applyPublicConfig();

  const token = getToken();
  if (token) {
    try {
      const user = await api.me();
      await enterApp(user);
      return;
    } catch { setToken(null); }
  }
  showView('auth');
}

boot();
