// Kairos — page d'accueil : recherche, bibliothèque, ingestion locale.

const $ = (id) => document.getElementById(id);

// Base des appels API : location.origin ne contient JAMAIS les identifiants,
// contrairement à l'URL de la page (http://user:mdp@hote) — fetch() refuse les
// URL avec identifiants, donc les chemins relatifs planteraient dans ce cas.
const API = location.origin;

const EXAMPLES = [
  "de quoi parle cette vidéo ?",
  "quel est le sujet principal ?",
  "où est-ce qu'on explique le fonctionnement ?",
];

function fmtTime(ms) {
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0");
  return (h ? `${h}:` : "") + `${mm}:${String(s).padStart(2, "0")}`;
}

function esc(str) {
  const d = document.createElement("div");
  d.textContent = str ?? "";
  return d.innerHTML;
}

/* ---------------- recherche ---------------- */

async function doSearch(query) {
  const box = $("results");
  const section = $("resultats");
  section.hidden = false;
  $("results-count").textContent = "";
  box.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  section.scrollIntoView({ block: "start" });

  let data;
  try {
    const res = await fetch(`${API}/search?q=${encodeURIComponent(query)}&limit=15`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    box.innerHTML = `<div class="empty"><b>La recherche a échoué</b>${esc(e.message)}</div>`;
    return;
  }

  if (!data.hits.length) {
    box.innerHTML =
      '<div class="empty"><b>Aucun résultat</b>Indexez d\'abord une vidéo, puis reformulez votre question.</div>';
    return;
  }

  $("results-count").textContent = `${data.hits.length} moment${data.hits.length > 1 ? "s" : ""}`;
  box.innerHTML = data.hits
    .map((h) => {
      const badge = h.source === "visual"
        ? '<span class="badge visual">à l\'écran</span>'
        : '<span class="badge audio">parlé</span>';
      return `<a class="hit" href="/video/${h.rtvc_id}?t=${h.start_seconds}">
        <div class="hit-head">
          ${badge}
          <span class="hit-time">${fmtTime(h.start_ms)}</span>
          <span class="hit-title">${esc(h.title || "média #" + h.rtvc_id)}</span>
          <span class="hit-score">${Math.round(h.score * 100)}%</span>
        </div>
        <div class="hit-text">${esc(h.text)}</div>
      </a>`;
    })
    .join("");
}

$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (q) doSearch(q);
});

$("examples").innerHTML =
  '<span>Essayez&nbsp;:</span>' +
  EXAMPLES.map((t) => `<button class="chip" type="button">${esc(t)}</button>`).join("");
$("examples").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  $("q").value = chip.textContent;
  doSearch(chip.textContent);
});

/* ---------------- bibliothèque ---------------- */

async function loadMedia() {
  const grid = $("media");
  let items = [];
  try {
    items = await (await fetch(API + "/media")).json();
  } catch {
    grid.innerHTML = '<div class="empty">Bibliothèque indisponible.</div>';
    return;
  }

  $("media-count").textContent = items.length ? `${items.length}` : "";
  if (!items.length) {
    grid.innerHTML =
      '<div class="empty"><b>Rien d\'indexé pour l\'instant</b>Choisissez une vidéo dans « Indexer une vidéo » ci-dessous.</div>';
    return;
  }

  grid.innerHTML = items
    .map((m) => {
      const title = esc(m.title || `média #${m.rtvc_id}`);
      const dur = m.duration_ms ? fmtTime(m.duration_ms) : "—";
      const inner = `<h3>${title}</h3>
        <div class="media-meta">
          <span class="state ${m.status}">${m.status}</span>
          <span>${m.source}</span>
          <span>${dur}</span>
        </div>`;
      return m.status === "ready"
        ? `<a class="media-card" href="/video/${m.rtvc_id}">${inner}</a>`
        : `<div class="media-card">${inner}</div>`;
    })
    .join("");
}

/* ---------------- ingestion locale ---------------- */

async function loadFiles() {
  const list = $("files");
  let data;
  try {
    data = await (await fetch(API + "/ingest/browse")).json();
  } catch {
    list.innerHTML = '<div class="empty">Dossier d\'entrée inaccessible.</div>';
    return;
  }
  if (!data.files || !data.files.length) {
    list.innerHTML = `<div class="empty"><b>Aucune vidéo trouvée</b>Déposez un fichier dans <code>${esc(data.root)}</code>.</div>`;
    return;
  }
  list.innerHTML = data.files
    .map(
      (f) => `<div class="file-row">
        <span class="fname" title="${esc(f.path)}">${esc(f.name)}</span>
        <span class="fsize">${f.size_mb} Mo</span>
        <button class="btn" data-path="${esc(f.path)}" data-name="${esc(f.name)}">Indexer</button>
      </div>`
    )
    .join("");
}

$("files").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-path]");
  if (!btn) return;
  const toast = $("ingest-toast");
  btn.disabled = true;
  btn.textContent = "Lancement…";
  toast.className = "toast";
  toast.textContent = "";

  try {
    const res = await fetch(API + "/ingest/local", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: btn.dataset.path,
        title: btn.dataset.name.replace(/\.[^.]+$/, ""),
        max_seconds: parseInt($("max-seconds").value, 10) || null,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    toast.textContent = `Traitement lancé (média #${data.rtvc_id}) : transcription, OCR puis indexation. Le statut se met à jour tout seul.`;
    btn.textContent = "En cours";
    loadMedia();
  } catch (err) {
    toast.className = "toast err";
    toast.textContent = `Échec du lancement : ${err.message}`;
    btn.disabled = false;
    btn.textContent = "Indexer";
  }
});

/* ---------------- explorateur NAS RTVC ---------------- */

let rtvcPath = "";

// Fil d'Ariane : chaque niveau du chemin devient un lien cliquable, pour
// remonter directement à n'importe quel dossier parent (plus fiable qu'un
// simple « .. » et sans avoir à recharger la page).
function renderBreadcrumb(path) {
  const el = $("rtvc-path");
  const parts = path.split("/").filter(Boolean);
  let acc = "";
  const links = [`<a href="#" data-dir="">Racine</a>`];
  for (const p of parts) {
    acc += "/" + p;
    links.push(`<a href="#" data-dir="${esc(acc)}">${esc(p)}</a>`);
  }
  el.innerHTML = links.join(' <span class="crumb-sep">›</span> ');
}

async function loadRtvc(path) {
  rtvcPath = path;
  const list = $("rtvc-list");
  renderBreadcrumb(path);
  list.innerHTML = '<div class="loading">Chargement…</div>';

  let d;
  try {
    const res = await fetch(`${API}/ingest/rtvc/browse?path=${encodeURIComponent(path)}`);
    d = await res.json();
    if (!res.ok) throw new Error(d.detail || `HTTP ${res.status}`);
  } catch (e) {
    list.innerHTML = `<div class="empty"><b>NAS RTVC momentanément injoignable</b>
      <span>${esc(String(e.message).slice(0, 140))}</span><br>
      <button class="btn ghost" id="rtvc-retry" style="margin-top:.7rem" type="button">Réessayer</button>
    </div>`;
    document.getElementById("rtvc-retry")?.addEventListener("click", () => loadRtvc(rtvcPath));
    return;
  }

  const rows = [];
  if (path) {
    const parent = d.parent ?? path.replace(/\/[^/]*$/, "");
    rows.push(`<div class="file-row">
      <span class="fname">↩ <a href="#" data-dir="${esc(parent)}">Dossier parent</a></span>
    </div>`);
  }
  for (const it of d.items || []) {
    if (it.isdir) {
      rows.push(`<div class="file-row">
        <span class="fname">📁 <a href="#" data-dir="${esc(it.path)}">${esc(it.name)}</a></span>
      </div>`);
    } else if (it.is_video) {
      rows.push(`<div class="file-row">
        <span class="fname">🎬 ${esc(it.name)}</span>
        <button class="btn" data-nas="${esc(it.path)}" data-name="${esc(it.name)}">Indexer</button>
      </div>`);
    }
  }
  list.innerHTML = rows.length
    ? rows.join("")
    : '<div class="empty">Dossier vide (aucune vidéo ni sous-dossier).</div>';
}

// Clic sur le fil d'Ariane (dans l'en-tête) : remonte au niveau choisi.
$("rtvc-path").addEventListener("click", (e) => {
  const crumb = e.target.closest("a[data-dir]");
  if (!crumb) return;
  e.preventDefault();
  loadRtvc(crumb.dataset.dir);
});

$("rtvc-list").addEventListener("click", async (e) => {
  const dir = e.target.closest("a[data-dir]");
  if (dir) {
    e.preventDefault();
    loadRtvc(dir.dataset.dir);
    return;
  }
  const btn = e.target.closest("button[data-nas]");
  if (!btn) return;

  const toast = $("rtvc-toast");
  btn.disabled = true;
  btn.textContent = "Lancement…";
  toast.className = "toast";
  try {
    const res = await fetch(API + "/ingest/rtvc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nas_path: btn.dataset.nas,
        title: btn.dataset.name.replace(/\.[^.]+$/, ""),
        max_seconds: parseInt($("rtvc-seconds").value, 10) || 180,
        max_mb: parseInt($("rtvc-mb").value, 10) || 120,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    toast.textContent = `Média #${data.rtvc_id} en cours : téléchargement depuis le NAS, transcription, puis indexation.`;
    btn.textContent = "En cours";
    loadMedia();
  } catch (err) {
    toast.className = "toast err";
    toast.textContent = `Échec : ${err.message}`;
    btn.disabled = false;
    btn.textContent = "Indexer";
  }
});

/* ---------------- init ---------------- */

loadMedia();
loadFiles();
loadRtvc("");
setInterval(loadMedia, 4000);
