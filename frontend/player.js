// Kairos — lecteur : lit la vidéo, saute au timestamp, cherche dans le média.

const $ = (id) => document.getElementById(id);

// Base API sans identifiants (voir app.js)
const API = location.origin;

const mediaId = parseInt(location.pathname.split("/").filter(Boolean).pop(), 10);
const startAt = parseFloat(new URLSearchParams(location.search).get("t") || "0");
const video = $("player");

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

function seekTo(seconds) {
  const go = () => {
    video.currentTime = seconds;
    video.play().catch(() => {});
  };
  if (video.readyState >= 1) go();
  else video.addEventListener("loadedmetadata", go, { once: true });
}

/* ---------------- chargement ---------------- */

async function boot() {
  let meta = null;
  try {
    meta = await (await fetch(`${API}/media/${mediaId}/status`)).json();
    $("title").textContent = meta.title || `Média #${mediaId}`;
    const bits = [meta.source === "local" ? "vidéo locale" : "RTVC"];
    if (meta.duration_ms) bits.push(`durée ${fmtTime(meta.duration_ms)}`);
    $("subtitle").textContent = bits.join(" · ");
  } catch {
    $("title").textContent = `Média #${mediaId}`;
  }

  // Kairos sert lui-même la vidéo dès qu'il en a une copie transcodée
  // (fichier local ou téléchargé depuis le NAS RTVC). Sinon on demande à RTVC
  // un flux HLS via stream-token.
  if (meta && meta.has_playback) {
    video.src = `${API}/media/${mediaId}/video`;
  } else {
    try {
      const res = await fetch(`${API}/video/${mediaId}/stream-token`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      video.src = (await res.json()).master_url;
    } catch (e) {
      $("player-error").textContent =
        `Lecture indisponible : ${e.message}. RTVC n'a pas fourni de flux pour ce média.`;
      return;
    }
  }

  video.addEventListener("error", () => {
    $("player-error").textContent = "Le navigateur n'a pas pu lire ce flux vidéo.";
  });

  if (startAt > 0) seekTo(startAt);
}

/* ---------------- recherche dans la vidéo ---------------- */

async function search(query) {
  const box = $("results");
  box.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  let data;
  try {
    const res = await fetch(
      `/search?q=${encodeURIComponent(query)}&media_id=${mediaId}&limit=12`
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    box.innerHTML = `<div class="empty"><b>Recherche impossible</b>${esc(e.message)}</div>`;
    return;
  }

  if (!data.hits.length) {
    box.innerHTML = '<div class="empty">Aucun passage trouvé dans cette vidéo.</div>';
    return;
  }

  box.innerHTML = data.hits
    .map((h) => {
      const badge = h.source === "visual"
        ? '<span class="badge visual">à l\'écran</span>'
        : '<span class="badge audio">parlé</span>';
      return `<button class="hit" type="button" data-t="${h.start_seconds}">
        <div class="hit-head">
          ${badge}
          <span class="hit-time">${fmtTime(h.start_ms)}</span>
          <span class="hit-score">${Math.round(h.score * 100)}%</span>
        </div>
        <div class="hit-text">${esc(h.text)}</div>
      </button>`;
    })
    .join("");
}

$("results").addEventListener("click", (e) => {
  const hit = e.target.closest(".hit");
  if (!hit) return;
  seekTo(parseFloat(hit.dataset.t));
  video.scrollIntoView({ block: "center", behavior: "smooth" });
});

$("in-video-search").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (q) search(q);
});

boot();
