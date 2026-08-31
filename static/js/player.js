// Reproductor: switch Audio/Video en tiempo real, cola, autoplay, modo cinema.
import { api, mediaUrl } from './api.js';
import { store, toast, formatTime } from './ui.js';

const el = (id) => document.getElementById(id);

const state = {
  queue: [],          // lista de tracks visibles (la cola)
  index: -1,          // posición actual en la cola
  current: null,      // tema en reproducción (independiente de la cola filtrada)
  mode: 'video',      // 'video' | 'audio'
  playCounted: false,
  switching: false,
  lastToggle: 0,
};

let media, coverPane, coverImg, emptyPane;

export function currentTrack() {
  return state.current;
}

export function setQueue(tracks) {
  state.queue = tracks;
  // Mantener el índice apuntando al tema actual si sigue en la lista;
  // si el filtro lo excluye, index=-1 y el autoplay continuará desde el inicio de la nueva cola.
  state.index = state.current ? tracks.findIndex((t) => t.id === state.current.id) : -1;
}

export function initPlayer({ onTrackChange }) {
  media = el('media-el');
  coverPane = el('cover-pane');
  coverImg = el('cover-img');
  emptyPane = el('player-empty');
  state.onTrackChange = onTrackChange;

  // Volumen con memoria
  const vol = el('volume');
  const savedVol = Number(store.get('mitube_vol') || 80);
  vol.value = Number.isFinite(savedVol) ? savedVol : 80;
  media.volume = vol.value / 100;
  paintRange(vol);
  vol.addEventListener('input', () => {
    media.volume = vol.value / 100;
    paintRange(vol);
    store.set('mitube_vol', vol.value);
  });

  // Seek
  const seek = el('seek');
  let seeking = false;
  seek.addEventListener('input', () => {
    seeking = true;
    paintRange(seek);
    el('time-cur').textContent = formatTime((seek.value / 1000) * (media.duration || 0));
  });
  seek.addEventListener('change', () => {
    if (media.duration) media.currentTime = (seek.value / 1000) * media.duration;
    seeking = false;
  });
  media.addEventListener('timeupdate', () => {
    if (!seeking && media.duration) {
      seek.value = Math.round((media.currentTime / media.duration) * 1000);
      paintRange(seek);
      el('time-cur').textContent = formatTime(media.currentTime);
    }
    maybeCountPlay();
  });
  media.addEventListener('loadedmetadata', () => {
    el('time-dur').textContent = formatTime(media.duration);
  });
  media.addEventListener('play', () => paintPlayState(true));
  media.addEventListener('pause', () => paintPlayState(false));
  media.addEventListener('ended', () => next());
  media.addEventListener('error', () => {
    if (state.index >= 0 && !state.switching) toast('No se pudo cargar el archivo multimedia.', true);
  });

  el('btn-play').addEventListener('click', togglePlay);
  el('btn-next').addEventListener('click', next);
  el('btn-prev').addEventListener('click', prev);
  el('av-toggle').addEventListener('click', toggleMode);
  el('btn-cinema').addEventListener('click', toggleCinema);
  el('btn-fullscreen').addEventListener('click', toggleFullscreen);
  document.addEventListener('fullscreenchange', onFullscreenChange);

  // Atajos de teclado (ignorando campos de texto)
  document.addEventListener('keydown', (e) => {
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (!document.getElementById('app-view') || document.getElementById('app-view').classList.contains('hidden')) return;
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    else if (e.code === 'ArrowRight') media.currentTime = Math.min((media.currentTime || 0) + 5, media.duration || 0);
    else if (e.code === 'ArrowLeft') media.currentTime = Math.max((media.currentTime || 0) - 5, 0);
  });

  // Modo cinema recordado
  if (store.get('mitube_cinema') === '1') el('layout').classList.add('cinema');
}

function paintRange(input) {
  const pct = ((input.value - input.min) / (input.max - input.min)) * 100;
  input.style.setProperty('--fill', `${pct}%`);
}

function paintPlayState(playing) {
  el('ic-play').classList.toggle('hidden', playing);
  el('ic-pause').classList.toggle('hidden', !playing);
}

function togglePlay() {
  if (state.index < 0) return;
  if (media.paused) media.play().catch(() => { /* interacción requerida */ });
  else media.pause();
}

function toggleCinema() {
  const on = el('layout').classList.toggle('cinema');
  store.set('mitube_cinema', on ? '1' : '0');
}

// ---------- Pantalla completa (sin afectar el modo cinema) ----------

async function toggleFullscreen() {
  try {
    if (!document.fullscreenElement) {
      await (el('player-shell').requestFullscreen?.() ?? document.body.requestFullscreen());
    } else {
      await document.exitFullscreen();
    }
  } catch { /* soporte opcional: no es crítico */ }
}

function onFullscreenChange() {
  const on = !!document.fullscreenElement;
  el('btn-fullscreen').classList.toggle('active', on);
  el('layout').classList.toggle('fullscreen', on);
}

// ---------- Núcleo: carga y switch Audio/Video ----------

export function playIndex(i, { autoplay = true } = {}) {
  if (i < 0 || i >= state.queue.length) return;
  state.index = i;
  state.playCounted = false;
  const t = state.queue[i];
  state.current = t;
  emptyPane.classList.add('hidden');

  // Si el tema no tiene video, forzar modo audio
  const toggle = el('av-toggle');
  if (!t.has_video) {
    state.mode = 'audio';
    toggle.setAttribute('disabled', '');
    toggle.setAttribute('aria-checked', 'false');
  } else {
    toggle.removeAttribute('disabled');
    toggle.setAttribute('aria-checked', state.mode === 'video' ? 'true' : 'false');
  }

  loadRendition(t, state.mode, 0, autoplay);
  paintTrackInfo(t);
  if (state.onTrackChange) state.onTrackChange(t);
}

function loadRendition(track, mode, startAt, autoplay) {
  const kind = mode === 'video' && track.has_video ? 'video' : 'audio';
  media.src = mediaUrl.stream(track.id, kind);
  media.load();
  const onMeta = () => {
    media.removeEventListener('loadedmetadata', onMeta);
    if (startAt > 0) restorePosition(startAt);
    if (autoplay) media.play().catch(() => { /* autoplay bloqueado hasta interacción */ });
  };
  media.addEventListener('loadedmetadata', onMeta);
  setPane(kind === 'video' ? 'video' : 'audio', track);
}

// Chequeo redundante de sincronía: reintenta el seek una vez si el desvío supera 0.5 s
function restorePosition(target) {
  media.currentTime = target;
  const verify = () => {
    media.removeEventListener('seeked', verify);
    const drift = Math.abs(media.currentTime - target);
    if (drift > 0.5) {
      console.warn(`[MiTube] Desvío de sincronía ${drift.toFixed(2)}s; reintentando seek.`);
      media.currentTime = target;
    }
  };
  media.addEventListener('seeked', verify);
}

function setPane(mode, track) {
  const showCover = mode === 'audio';
  if (showCover) {
    coverImg.src = track.has_cover ? mediaUrl.cover(track.id) : fallbackCover();
    coverPane.classList.remove('hidden');
  } else {
    coverPane.classList.add('hidden');
  }
}

function fallbackCover() {
  return 'data:image/svg+xml,' + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300"><rect width="300" height="300" rx="16" fill="#1b1b22"/><path d="M120 90v120l90-60z" fill="#ff3d6e"/></svg>`);
}

function toggleMode() {
  const t = currentTrack();
  if (!t || !t.has_video) return;
  // Debounce: evita conmutaciones repetidas demasiado rápidas
  const now = Date.now();
  if (now - state.lastToggle < 300 || state.switching) return;
  state.lastToggle = now;
  state.switching = true;

  const prevMode = state.mode;
  const newMode = prevMode === 'video' ? 'audio' : 'video';
  const pos = media.currentTime || 0;
  const wasPlaying = !media.paused && !media.ended;
  const rate = media.playbackRate;

  el('av-toggle').setAttribute('aria-checked', newMode === 'video' ? 'true' : 'false');
  state.mode = newMode;

  const kind = newMode === 'video' ? 'video' : 'audio';
  const onMeta = () => {
    media.removeEventListener('loadedmetadata', onMeta);
    media.removeEventListener('error', onErr);
    restorePosition(pos);
    media.playbackRate = rate;
    if (wasPlaying) media.play().catch(() => { /* requiere interacción */ });
    state.switching = false;
  };
  const onErr = () => {
    media.removeEventListener('loadedmetadata', onMeta);
    media.removeEventListener('error', onErr);
    // Revertir al modo anterior sin detener la experiencia
    toast('No se pudo cambiar de modo; se restauró el anterior.', true);
    state.mode = prevMode;
    el('av-toggle').setAttribute('aria-checked', prevMode === 'video' ? 'true' : 'false');
    media.src = mediaUrl.stream(t.id, prevMode === 'video' ? 'video' : 'audio');
    media.load();
    const back = () => {
      media.removeEventListener('loadedmetadata', back);
      restorePosition(pos);
      if (wasPlaying) media.play().catch(() => { /* requiere interacción */ });
      state.switching = false;
    };
    media.addEventListener('loadedmetadata', back);
    setPane(prevMode, t);
  };
  media.addEventListener('loadedmetadata', onMeta);
  media.addEventListener('error', onErr);
  media.src = mediaUrl.stream(t.id, kind);
  media.load();
  setPane(newMode, t);
}

function next() {
  if (!state.queue.length) return;
  const i = state.index + 1;
  if (i < state.queue.length) playIndex(i);
  else paintPlayState(false); // fin de la cola
}

function prev() {
  if (!state.queue.length) return;
  if (media.currentTime > 3) { media.currentTime = 0; return; }
  playIndex(Math.max(0, state.index - 1));
}

function maybeCountPlay() {
  const t = currentTrack();
  if (!t || state.playCounted || !media.duration) return;
  const threshold = Math.min(10, media.duration * 0.25);
  if (media.currentTime >= threshold) {
    state.playCounted = true;
    api.registerPlay(t.id)
      .then(() => { t.play_count += 1; paintTrackInfo(t); })
      .catch(() => { /* silencioso: no es crítico */ });
  }
}

function paintTrackInfo(t) {
  document.getElementById('track-info').classList.remove('hidden');
  document.getElementById('ti-title').textContent = t.title;
  document.getElementById('ti-artist').textContent = t.artist;
  document.getElementById('ti-album').textContent = t.album || '';
  document.getElementById('ti-year').textContent = t.year ? String(t.year) : '';
  document.getElementById('ti-genre').textContent = t.genre || '';
  document.getElementById('ti-plays').textContent = `${t.play_count} reproducciones`;
}
