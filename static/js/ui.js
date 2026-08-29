// Utilidades de interfaz compartidas (sin innerHTML con datos: solo textContent/createElement).

// Almacén de preferencias: cookies con respaldo en memoria (el iframe de la
// vista previa bloquea los almacenes del navegador; esto degrada con gracia).
const _mem = new Map();

export const store = {
  get(key) {
    try {
      const m = document.cookie.match(new RegExp('(?:^|; )' + key + '=([^;]*)'));
      if (m) return decodeURIComponent(m[1]);
    } catch { /* sin cookies */ }
    return _mem.has(key) ? _mem.get(key) : null;
  },
  set(key, value) {
    _mem.set(key, value);
    try {
      const secure = location.protocol === 'https:' ? '; Secure' : '';
      document.cookie = `${key}=${encodeURIComponent(value)}; path=/; max-age=86400; SameSite=Lax${secure}`;
    } catch { /* sin cookies */ }
  },
  remove(key) {
    _mem.delete(key);
    try { document.cookie = `${key}=; path=/; max-age=0`; } catch { /* sin cookies */ }
  },
};

let toastTimer = null;

export function toast(msg, isError = false) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('error', isError);
  t.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 3500);
}

export function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function elFrom(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Cierra dropdowns al hacer clic fuera o con Escape
export function bindGlobalDropdownClose() {
  const closeAll = () => document.querySelectorAll('.dropdown').forEach((d) => d.classList.add('hidden'));
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.dropdown-host') && !e.target.closest('.pl-dl')) closeAll();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(); });
}

export function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}
