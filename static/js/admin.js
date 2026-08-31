// Panel de administración: biblioteca (subida/edición/borrado), géneros y usuarios.
import { api, ApiError } from './api.js';
import { elFrom, toast } from './ui.js';

const $ = (id) => document.getElementById(id);
let meta = { genres: [], artists: [], albums: [] };
let notifyLibraryChanged = () => {};
let onSettingsChanged = () => {};
let editingTrackId = null;

export function initAdmin(opts) {
  notifyLibraryChanged = opts.notifyLibraryChanged || notifyLibraryChanged;
  onSettingsChanged = opts.onSettingsChanged || onSettingsChanged;

  document.querySelectorAll('.admin-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.admin-tab').forEach((t) => t.classList.remove('active'));
      tab.classList.add('active');
      for (const name of ['library', 'genres', 'users']) {
        $(`admin-${name}`).classList.toggle('hidden', name !== tab.dataset.tab);
      }
    });
  });

  bindUpload();
  bindGenreForm();
  bindUserForm();
  bindEditDialog();
  bindSettings();
}

export async function showAdmin() {
  await Promise.all([loadMetaIntoForms(), loadTracksTable(), loadGenresList(), loadUsersTable(), loadSettings()]);
}

// ---------- Configuración del portal ----------

async function loadSettings() {
  try {
    const s = await api.admin.settings();
    $('reg-toggle').setAttribute('aria-checked', s.public_registration ? 'true' : 'false');
  } catch { /* sin permisos o error de red: el toggle queda como esté */ }
}

function bindSettings() {
  $('reg-toggle').addEventListener('click', async () => {
    const t = $('reg-toggle');
    const enable = t.getAttribute('aria-checked') !== 'true';
    try {
      const res = await api.admin.updateSettings({ public_registration: enable });
      t.setAttribute('aria-checked', res.public_registration ? 'true' : 'false');
      toast(res.public_registration
        ? 'Registro público ACTIVADO: cualquiera puede crear cuenta.'
        : 'Registro público DESACTIVADO: solo los admins crean cuentas.');
      onSettingsChanged();
    } catch (err) { toast(errMsg(err, 'No se pudo cambiar la configuración.'), true); }
  });
}

function errMsg(err, fallback) {
  return err instanceof ApiError ? err.message : fallback;
}

// ---------- Biblioteca ----------

async function loadMetaIntoForms() {
  try { meta = await api.meta(); } catch { return; }
  const genreSelects = [document.querySelector('#upload-form select[name=genre_id]'),
                        document.querySelector('#edit-form select[name=genre_id]')];
  for (const sel of genreSelects) {
    const current = sel.value;
    sel.replaceChildren(new Option('— Sin género —', ''));
    for (const g of meta.genres) sel.appendChild(new Option(g.name, String(g.id)));
    sel.value = current;
  }
  $('artists-list').replaceChildren(...meta.artists.map((a) => new Option(a.name)));
  $('albums-list').replaceChildren(...meta.albums.map((a) => new Option(a.title)));
}

function bindUpload() {
  $('upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    const file = f.media.files[0];
    if (!file) { toast('Selecciona un archivo.', true); return; }
    const okExt = ['.mp4', '.mp3', '.m4a', '.flac'].some((x) => file.name.toLowerCase().endsWith(x));
    if (!okExt) { toast('Extensión no permitida.', true); return; }

    const fd = new FormData();
    fd.append('media', file);
    if (f.cover.files[0]) fd.append('cover', f.cover.files[0]);
    fd.append('title', f.title.value.trim());
    fd.append('artist', f.artist.value.trim());
    fd.append('album', f.album.value.trim());
    if (f.year.value) fd.append('year', f.year.value);
    if (f.genre_id.value) fd.append('genre_id', f.genre_id.value);

    const prog = $('upload-progress');
    const status = $('upload-status');
    prog.classList.remove('hidden');
    prog.value = 0;
    status.textContent = 'Subiendo…';
    try {
      const res = await api.admin.uploadTrack(fd, (pct) => { prog.value = pct; if (pct === 100) status.textContent = 'Procesando con FFmpeg…'; });
      status.textContent = '';
      prog.classList.add('hidden');
      f.reset();
      // El backend informa si creó el tema o fusionó la modalidad con uno existente
      toast((res && res.message) ? res.message : 'Tema subido y procesado correctamente.');
      await Promise.all([loadTracksTable(), loadMetaIntoForms()]);
      notifyLibraryChanged();
    } catch (err) {
      prog.classList.add('hidden');
      status.textContent = '';
      toast(errMsg(err, 'Error al subir el archivo.'), true);
    }
  });
}

async function loadTracksTable() {
  let tracks = [];
  try { ({ tracks } = await api.tracks({ sort: 'recent', per_page: 200 })); }
  catch { toast('No se pudo cargar la biblioteca.', true); return; }
  const tbody = document.querySelector('#tracks-table tbody');
  tbody.replaceChildren();
  for (const t of tracks) {
    const tr = document.createElement('tr');
    tr.appendChild(elFrom('td', '', t.title));
    tr.appendChild(elFrom('td', '', t.artist));
    tr.appendChild(elFrom('td', '', t.album || '—'));
    tr.appendChild(elFrom('td', '', t.year ? String(t.year) : '—'));
    tr.appendChild(elFrom('td', '', t.genre || '—'));
    tr.appendChild(elFrom('td', '', String(t.play_count)));
    tr.appendChild(elFrom('td', '', t.has_video ? 'Sí' : 'No'));
    const actions = elFrom('td');
    const editBtn = elFrom('button', 'btn-mini', 'Editar');
    editBtn.addEventListener('click', () => openEditDialog(t));
    const delBtn = elFrom('button', 'btn-mini danger', 'Eliminar');
    delBtn.addEventListener('click', async () => {
      if (!confirm(`¿Eliminar "${t.title}" y sus archivos? Esta acción no se puede deshacer.`)) return;
      try {
        await api.admin.deleteTrack(t.id);
        toast('Tema eliminado.');
        await loadTracksTable();
        notifyLibraryChanged();
      } catch (err) { toast(errMsg(err, 'Error al eliminar.'), true); }
    });
    actions.append(editBtn, ' ', delBtn);
    tr.appendChild(actions);
    tbody.appendChild(tr);
  }
}

function bindEditDialog() {
  const dlg = $('edit-dialog');
  $('edit-cancel').addEventListener('click', () => dlg.close());
  $('edit-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api.admin.editTrack(editingTrackId, {
        title: f.title.value.trim(),
        artist: f.artist.value.trim(),
        album: f.album.value.trim(),
        year: f.year.value ? Number(f.year.value) : null,
        genre_id: f.genre_id.value ? Number(f.genre_id.value) : null,
      });
      dlg.close();
      toast('Tema actualizado.');
      await Promise.all([loadTracksTable(), loadMetaIntoForms()]);
      notifyLibraryChanged();
    } catch (err) { toast(errMsg(err, 'Error al guardar.'), true); }
  });
}

function openEditDialog(t) {
  editingTrackId = t.id;
  const f = $('edit-form');
  f.title.value = t.title;
  f.artist.value = t.artist;
  f.album.value = t.album || '';
  f.year.value = t.year || '';
  f.genre_id.value = t.genre_id ? String(t.genre_id) : '';
  $('edit-dialog').showModal();
}

// ---------- Géneros ----------

function bindGenreForm() {
  $('genre-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = e.target.name.value.trim();
    if (!name) return;
    try {
      await api.admin.createGenre({ name });
      e.target.reset();
      toast('Género creado.');
      await Promise.all([loadGenresList(), loadMetaIntoForms()]);
    } catch (err) { toast(errMsg(err, 'Error al crear el género.'), true); }
  });
}

async function loadGenresList() {
  try { meta = await api.meta(); } catch { return; }
  const ul = $('genres-list');
  ul.replaceChildren();
  for (const g of meta.genres) {
    const li = document.createElement('li');
    li.appendChild(elFrom('span', '', g.name));
    const actions = elFrom('div', 'row-actions');
    const ren = elFrom('button', 'btn-mini', 'Renombrar');
    ren.addEventListener('click', async () => {
      const name = prompt('Nuevo nombre del género:', g.name);
      if (!name || !name.trim()) return;
      try {
        await api.admin.updateGenre(g.id, { name: name.trim() });
        toast('Género actualizado.');
        await Promise.all([loadGenresList(), loadMetaIntoForms()]);
      } catch (err) { toast(errMsg(err, 'Error al renombrar.'), true); }
    });
    const del = elFrom('button', 'btn-mini danger', 'Eliminar');
    del.addEventListener('click', async () => {
      if (!confirm(`¿Eliminar el género "${g.name}"?`)) return;
      try {
        await api.admin.deleteGenre(g.id);
        toast('Género eliminado.');
        await Promise.all([loadGenresList(), loadMetaIntoForms()]);
      } catch (err) { toast(errMsg(err, 'No se pudo eliminar.'), true); }
    });
    actions.append(ren, del);
    li.appendChild(actions);
    ul.appendChild(li);
  }
}

// ---------- Usuarios ----------

function bindUserForm() {
  $('user-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api.admin.createUser({
        username: f.username.value.trim(),
        email: f.email.value.trim(),
        password: f.password.value,
        role: f.role.value,
        is_active: true,
      });
      f.reset();
      toast('Usuario creado.');
      await loadUsersTable();
    } catch (err) { toast(errMsg(err, 'Error al crear el usuario.'), true); }
  });
}

async function loadUsersTable() {
  let users = [];
  try { users = await api.admin.users(); }
  catch { return; }
  const tbody = document.querySelector('#users-table tbody');
  tbody.replaceChildren();
  for (const u of users) {
    const tr = document.createElement('tr');
    tr.appendChild(elFrom('td', '', String(u.id)));
    tr.appendChild(elFrom('td', '', u.username));
    tr.appendChild(elFrom('td', '', u.email));
    tr.appendChild(elFrom('td', '', u.role));
    const st = elFrom('td');
    st.appendChild(elFrom('span', `status-pill ${u.is_active ? 'ok' : 'off'}`, u.is_active ? 'Activo' : 'Suspendido'));
    tr.appendChild(st);
    const actions = elFrom('td');
    const susp = elFrom('button', 'btn-mini', u.is_active ? 'Suspender' : 'Reactivar');
    susp.addEventListener('click', async () => {
      try {
        await api.admin.updateUser(u.id, {
          username: u.username, email: u.email, role: u.role, is_active: !u.is_active,
        });
        toast(u.is_active ? 'Usuario suspendido.' : 'Usuario reactivado.');
        await loadUsersTable();
      } catch (err) { toast(errMsg(err, 'Error al actualizar.'), true); }
    });
    const pw = elFrom('button', 'btn-mini', 'Cambiar contraseña');
    pw.addEventListener('click', async () => {
      const newPw = prompt(`Nueva contraseña para "${u.username}" (mín. 10, mayúscula, minúscula, dígito y símbolo):`);
      if (!newPw) return;
      if (newPw.length < 10) { toast('La contraseña debe tener al menos 10 caracteres.', true); return; }
      try {
        await api.admin.updateUser(u.id, {
          username: u.username, email: u.email, role: u.role, is_active: u.is_active, password: newPw,
        });
        toast(`Contraseña de "${u.username}" actualizada.`);
      } catch (err) { toast(errMsg(err, 'No se pudo cambiar la contraseña.'), true); }
    });
    const del = elFrom('button', 'btn-mini danger', 'Eliminar');
    del.addEventListener('click', async () => {
      if (!confirm(`¿Eliminar la cuenta "${u.username}"? Esta acción no se puede deshacer.`)) return;
      try {
        await api.admin.deleteUser(u.id);
        toast('Usuario eliminado.');
        await loadUsersTable();
      } catch (err) { toast(errMsg(err, 'No se pudo eliminar.'), true); }
    });
    actions.append(susp, ' ', pw, ' ', del);
    tr.appendChild(actions);
    tbody.appendChild(tr);
  }
}
