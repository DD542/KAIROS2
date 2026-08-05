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
  // innerHTML n'echappe ni " ni ' : sans ces deux remplacements, un dossier
  // nomme  Best "of" 2026  cassait l'attribut data-dir et le clic ne marchait
  // plus (et un nom bien choisi pouvait injecter du balisage).
  return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Lit un champ numérique en préservant le 0 (= « pas de limite »). `parseInt(..)
// || défaut` trahissait l'utilisateur : saisir 0 relançait la valeur par défaut
// et la vidéo était tronquée alors qu'il demandait l'inverse.
function numField(id, fallback = 0) {
  const v = parseInt($(id).value, 10);
  return Number.isNaN(v) ? fallback : Math.max(v, 0);
}

/* ---------------- recherche ---------------- */

// Surligne, dans un texte déjà échappé, les mots de la requête (>2 lettres).
function highlight(text, query) {
  const safe = esc(text);
  const words = query
    .toLowerCase()
    .split(/\s+/)
    .filter((w) => w.length > 2)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length) return safe;
  const re = new RegExp(`(${words.join("|")})`, "gi");
  return safe.replace(re, "<mark>$1</mark>");
}

async function doSearch(query, pushUrl = true) {
  const box = $("results");
  const section = $("resultats");
  section.hidden = false;
  // La requête vit dans l'URL : le bouton « retour » du navigateur ramène aux
  // résultats après avoir consulté une vidéo, et le lien est partageable.
  if (pushUrl) {
    const u = new URL(location.href);
    u.searchParams.set("q", query);
    history.replaceState(null, "", u);
    sessionStorage.setItem("kairos:lastQuery", query);
    remember(query);
  }
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

  lastHits = data.hits;
  lastQuery = query;
  renderHits();
}

// Filtre par origine du résultat (parlé / à l'écran), sans relancer la requête.
let lastHits = [];
let lastQuery = "";
let srcFilter = "all";

function renderHits() {
  const box = $("results");
  const hits =
    srcFilter === "all" ? lastHits : lastHits.filter((h) => h.source === srcFilter);

  if (!hits.length) {
    box.innerHTML = '<div class="empty">Aucun résultat pour ce filtre.</div>';
    $("results-count").textContent = "0";
    return;
  }
  $("results-count").textContent = `${hits.length} moment${hits.length > 1 ? "s" : ""}`;
  const data = { hits };
  const query = lastQuery;
  box.innerHTML = data.hits
    .map((h) => {
      const badge = h.source === "visual"
        ? '<span class="badge visual">à l\'écran</span>'
        : '<span class="badge audio">parlé</span>';
      // Un résultat trouvé par le texte exact (nom propre, sigle, chiffre) a
      // souvent un score sémantique bas : on explique sa présence plutôt que
      // de laisser croire à une erreur de classement.
      const why = h.matched === "mots"
        ? '<span class="badge exact" title="Trouvé par correspondance exacte du texte">texte exact</span>'
        : "";
      // Pas de vignette pour un media dont Kairos n'a pas le fichier (resultat
      // venant de la base externe de transcription) : demander l'image
      // donnerait un 404 a chaque resultat.
      const thumb = h.has_thumbnail
        ? `<img class="hit-thumb" loading="lazy" alt=""
             src="${API}/media/${h.rtvc_id}/thumbnail?t=${h.start_seconds}"
             onerror="this.remove()" />`
        : "";
      return `<a class="hit" href="/video/${h.rtvc_id}?t=${h.start_seconds}">
        ${thumb}
        <div class="hit-body">
          <div class="hit-head">
            ${badge}${why}
            <span class="hit-time">${fmtTime(h.start_ms)}</span>
            <span class="hit-title">${esc(h.title || "média #" + h.rtvc_id)}</span>
            <span class="hit-score" title="Pertinence relative au meilleur résultat">${Math.round((h.relevance ?? h.score) * 100)}%</span>
          </div>
          <div class="hit-text">${highlight(h.text, query)}</div>
        </div>
      </a>`;
    })
    .join("");
}

$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (q) {
    closeSuggest();
    doSearch(q);
  }
});

/* ---------------- suggestions pendant la frappe ---------------- */

// Les propositions viennent du contenu RÉELLEMENT indexé : l'utilisateur voit
// ce qu'il peut trouver au lieu de deviner la bonne formulation. On y ajoute
// ses recherches passées, qui sont souvent celles qu'il veut rejouer.
const HISTORY_KEY = "kairos:history";
const HISTORY_MAX = 8;

function recentQueries() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  } catch {
    return [];
  }
}

function remember(query) {
  const q = query.trim();
  if (!q) return;
  const list = [q, ...recentQueries().filter((h) => h !== q)].slice(0, HISTORY_MAX);
  localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
}

let suggestItems = [];
let suggestIndex = -1;
let suggestTimer = null;
let suggestSeq = 0;

function closeSuggest() {
  $("suggest").hidden = true;
  $("q").setAttribute("aria-expanded", "false");
  suggestItems = [];
  suggestIndex = -1;
}

const KIND_LABEL = { titre: "vidéo", audio: "parlé", visual: "à l'écran", recent: "récent" };

function renderSuggest() {
  const box = $("suggest");
  if (!suggestItems.length) return closeSuggest();
  box.innerHTML = suggestItems
    .map(
      (s, i) => `<li role="option" id="sg-${i}" data-i="${i}"
        class="${i === suggestIndex ? "active" : ""}" aria-selected="${i === suggestIndex}">
        <span class="sg-text">${esc(s.text)}</span>
        <span class="sg-kind">${esc(KIND_LABEL[s.kind] || s.kind)}</span>
      </li>`
    )
    .join("");
  box.hidden = false;
  $("q").setAttribute("aria-expanded", "true");
  $("q").setAttribute(
    "aria-activedescendant",
    suggestIndex >= 0 ? `sg-${suggestIndex}` : ""
  );
}

async function fetchSuggest(q) {
  const recents = recentQueries()
    .filter((h) => h.toLowerCase().includes(q.toLowerCase()) && h !== q)
    .slice(0, 3)
    .map((h) => ({ text: h, kind: "recent" }));

  // Un identifiant de séquence évite qu'une réponse lente écrase l'affichage
  // d'une frappe plus récente.
  const seq = ++suggestSeq;
  let remote = [];
  try {
    const res = await fetch(`${API}/suggest?q=${encodeURIComponent(q)}&limit=8`);
    if (res.ok) remote = (await res.json()).suggestions || [];
  } catch {
    /* hors ligne : on garde au moins l'historique */
  }
  if (seq !== suggestSeq) return;

  const seen = new Set(recents.map((r) => r.text.toLowerCase()));
  suggestItems = [
    ...recents,
    ...remote.filter((r) => !seen.has((r.text || "").toLowerCase())),
  ].slice(0, 8);
  suggestIndex = -1;
  renderSuggest();
}

$("q").addEventListener("input", () => {
  const q = $("q").value.trim();
  clearTimeout(suggestTimer);
  if (q.length < 2) return closeSuggest();
  // 180 ms : assez court pour paraître instantané, assez long pour ne pas
  // lancer une requête à chaque touche.
  suggestTimer = setTimeout(() => fetchSuggest(q), 180);
});

$("q").addEventListener("keydown", (e) => {
  if ($("suggest").hidden) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const step = e.key === "ArrowDown" ? 1 : -1;
    suggestIndex = (suggestIndex + step + suggestItems.length + 1) % (suggestItems.length + 1);
    if (suggestIndex === suggestItems.length) suggestIndex = -1;
    renderSuggest();
  } else if (e.key === "Enter" && suggestIndex >= 0) {
    e.preventDefault();
    pickSuggest(suggestIndex);
  } else if (e.key === "Escape") {
    closeSuggest();
  }
});

function pickSuggest(i) {
  const s = suggestItems[i];
  if (!s) return;
  $("q").value = s.text;
  closeSuggest();
  remember(s.text);
  doSearch(s.text);
}

$("suggest").addEventListener("mousedown", (e) => {
  // mousedown (et non click) : le blur du champ fermerait la liste avant le clic
  const li = e.target.closest("li[data-i]");
  if (!li) return;
  e.preventDefault();
  pickSuggest(parseInt(li.dataset.i, 10));
});

$("q").addEventListener("blur", () => setTimeout(closeSuggest, 120));

// Filtre parlé / à l'écran
document.querySelector(".seg-filter").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-src]");
  if (!b) return;
  srcFilter = b.dataset.src;
  document
    .querySelectorAll(".seg-filter .chip")
    .forEach((c) => c.classList.toggle("active", c === b));
  renderHits();
});

// Raccourci « / » : place le curseur dans la recherche (sauf si on tape déjà).
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || "");
  if (e.key === "/" && !typing) {
    e.preventDefault();
    $("q").focus();
    $("q").select();
  }
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

let allMedia = [];
// Empreinte du dernier état connu : le rafraîchissement automatique interroge
// un compteur (quelques octets) et ne retélécharge la bibliothèque que si elle
// a réellement changé. Sans cela, une bibliothèque de plusieurs centaines de
// médias était retransmise en entier toutes les 4 secondes.
let mediaFingerprint = null;

async function pollMedia() {
  try {
    const s = await (await fetch(API + "/media/summary")).json();
    if (s.empreinte === mediaFingerprint) return;
    mediaFingerprint = s.empreinte;
  } catch {
    return; // serveur momentanément indisponible : on réessaiera au tour suivant
  }
  loadMedia();
}

async function loadMedia() {
  try {
    allMedia = await (await fetch(API + "/media?limit=500")).json();
  } catch {
    $("media").innerHTML = '<div class="empty">Bibliothèque indisponible.</div>';
    return;
  }
  const nbFailed = allMedia.filter((m) => m.status === "failed").length;
  $("retry-failed").hidden = nbFailed === 0;
  $("retry-failed").textContent = `Relancer les échoués (${nbFailed})`;
  renderMedia();
}

function renderMedia() {
  const grid = $("media");
  const q = ($("media-filter").value || "").toLowerCase();
  const items = q
    ? allMedia.filter((m) => (m.title || "").toLowerCase().includes(q))
    : allMedia;

  $("media-count").textContent = allMedia.length ? `${allMedia.length}` : "";
  if (!items.length) {
    grid.innerHTML = q
      ? '<div class="empty">Aucun média ne correspond au filtre.</div>'
      : '<div class="empty"><b>Rien d\'indexé pour l\'instant</b>Choisissez une vidéo ci-dessous.</div>';
    return;
  }

  grid.innerHTML = items
    .map((m) => {
      const title = esc(m.title || `média #${m.rtvc_id}`);
      const dur = m.duration_ms ? fmtTime(m.duration_ms) : "—";
      const retry = m.status === "failed"
        ? `<button class="btn ghost" data-retry="${m.rtvc_id}" type="button">Réessayer</button>`
        : "";
      const del = `<button class="btn ghost danger" data-del="${m.rtvc_id}" title="Supprimer" type="button">✕</button>`;
      const lang = m.language
        ? `<span class="lang" title="Langue détectée">${esc(m.language.toUpperCase())}</span>`
        : "";
      const inner = `<h3>${title}</h3>
        <div class="media-meta">
          <span class="state ${m.status}">${m.status}</span>
          <span>${m.source}</span>
          <span>${dur}</span>
          ${lang}${retry}${del}
        </div>`;
      return m.status === "ready"
        ? `<a class="media-card" href="/video/${m.rtvc_id}">${inner}</a>`
        : `<div class="media-card">${inner}</div>`;
    })
    .join("");
}

$("media-filter").addEventListener("input", renderMedia);

$("retry-failed").addEventListener("click", async () => {
  $("retry-failed").disabled = true;
  try {
    await fetch(`${API}/media/retry-failed`, { method: "POST" });
    loadMedia();
  } finally {
    $("retry-failed").disabled = false;
  }
});

// Boutons Réessayer / Supprimer sur les cartes
$("media").addEventListener("click", async (e) => {
  const retryBtn = e.target.closest("button[data-retry]");
  const delBtn = e.target.closest("button[data-del]");
  if (retryBtn) {
    e.preventDefault();
    retryBtn.disabled = true;
    retryBtn.textContent = "Relance…";
    try {
      await fetch(`${API}/media/${retryBtn.dataset.retry}/retry`, { method: "POST" });
      loadMedia();
    } catch {
      retryBtn.disabled = false;
      retryBtn.textContent = "Réessayer";
    }
    return;
  }
  if (delBtn) {
    e.preventDefault();
    if (!confirm("Supprimer ce média et toutes ses données indexées ?")) return;
    try {
      await fetch(`${API}/media/${delBtn.dataset.del}`, { method: "DELETE" });
      loadMedia();
    } catch {}
  }
});

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
        max_seconds: numField("max-seconds"),
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
    // Un proxy (Cloudflare…) peut renvoyer une page d'erreur HTML si la requête
    // traîne : on le détecte pour afficher un message clair plutôt qu'une
    // erreur d'analyse JSON incompréhensible.
    const type = res.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      throw new Error(
        res.status === 200
          ? "réponse inattendue du serveur (délai dépassé côté réseau)"
          : `serveur indisponible (code ${res.status})`
      );
    }
    d = await res.json();
    if (!res.ok) throw new Error(d.detail || `HTTP ${res.status}`);
  } catch (e) {
    list.innerHTML = `<div class="empty"><b>NAS RTVC momentanément injoignable</b>
      <span>Le serveur RTVC ne répond pas pour l'instant — ${esc(String(e.message).slice(0, 110))}.</span><br>
      <span class="muted" style="font-size:.86rem">Les vidéos déjà indexées restent consultables et cherchables.</span><br>
      <button class="btn ghost" id="rtvc-retry" style="margin-top:.7rem" type="button">Réessayer</button>
    </div>`;
    document.getElementById("rtvc-retry")?.addEventListener("click", () => loadRtvc(rtvcPath));
    return;
  }

  const rows = [];
  if (path) {
    // RTVC désigne la racine par "/" dans `parent`, alors qu'il faut lui
    // renvoyer une chaîne vide : sans cette normalisation, remonter depuis le
    // premier niveau renvoyait une erreur 502.
    const raw = d.parent ?? path.replace(/\/[^/]*$/, "");
    const parent = raw === "/" ? "" : raw;
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

// « Tout indexer » : lance le scan récursif du dossier courant.
$("index-all").addEventListener("click", async () => {
  const toast = $("rtvc-toast");
  const btn = $("index-all");
  const where = rtvcPath || "/ (racine)";
  if (!confirm(
    `Indexer TOUTES les vidéos de « ${where} » et ses sous-dossiers ?\n\n` +
    `Chaque vidéo est téléchargée puis transcrite (plusieurs minutes chacune). ` +
    `Sur un gros dossier, cela peut prendre des heures. Continuer ?`
  )) return;
  toast.className = "toast";
  toast.textContent = "Scan du dossier en cours…";
  btn.disabled = true;
  try {
    const res = await fetch(`${API}/ingest/rtvc/index-all`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: rtvcPath,
        max_seconds: numField("rtvc-seconds"),
        max_mb: numField("rtvc-mb"),
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    toast.textContent = "Scan lancé : les vidéos du dossier (et sous-dossiers) s'indexent en arrière-plan. Suivez la progression dans « Bibliothèque indexée ».";
    loadMedia();
  } catch (e) {
    toast.className = "toast err";
    toast.textContent = `Échec du lancement : ${e.message}`;
  } finally {
    btn.disabled = false;
  }
});

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
        max_seconds: numField("rtvc-seconds"),
        max_mb: numField("rtvc-mb"),
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
setInterval(pollMedia, 4000);

// Restaure la dernière recherche (retour depuis le lecteur, ou lien partagé).
(function restoreSearch() {
  const q =
    new URLSearchParams(location.search).get("q") ||
    sessionStorage.getItem("kairos:lastQuery");
  if (q) {
    $("q").value = q;
    doSearch(q, false);
  }
})();
