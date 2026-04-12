const editor = document.getElementById("editor");
const output = document.getElementById("quotation-output");
const matchesList = document.getElementById("matches-list");
const stats = document.getElementById("stats");
const citationStyle = document.getElementById("citation-style");
const shell = document.getElementById("shell");
const splitter = document.getElementById("splitter");
const loadFileButton = document.getElementById("load-file");
const saveFileButton = document.getElementById("save-file");
const fileInput = document.getElementById("file-input");

let composeTimer = null;
let splitRatio = 0.56;
const splitStorageKey = "the-already-said.split-ratio";

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function applySplitRatio(ratio) {
  splitRatio = clamp(ratio, 0.3, 0.7);
  shell.style.gridTemplateColumns = `minmax(320px, ${splitRatio}fr) 12px minmax(280px, ${1 - splitRatio}fr)`;
}

function loadSplitRatio() {
  const savedRatio = Number.parseFloat(window.localStorage.getItem(splitStorageKey) || "");
  if (!Number.isNaN(savedRatio)) {
    applySplitRatio(savedRatio);
  }
}

function saveSplitRatio() {
  window.localStorage.setItem(splitStorageKey, String(splitRatio));
}

function onSplitDrag(event) {
  if (window.innerWidth <= 980) {
    return;
  }
  const bounds = shell.getBoundingClientRect();
  const ratio = (event.clientX - bounds.left) / bounds.width;
  applySplitRatio(ratio);
}

function beginSplitDrag(event) {
  event.preventDefault();
  shell.classList.add("is-dragging");
  onSplitDrag(event);

  function handleMove(moveEvent) {
    onSplitDrag(moveEvent);
  }

  function handleUp() {
    shell.classList.remove("is-dragging");
    window.removeEventListener("pointermove", handleMove);
    window.removeEventListener("pointerup", handleUp);
    saveSplitRatio();
  }

  window.addEventListener("pointermove", handleMove);
  window.addEventListener("pointerup", handleUp);
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function loadStats() {
  const response = await fetch("/api/stats");
  const payload = await response.json();
  const books = payload.indexed_books.toLocaleString();
  const passages = payload.indexed_passages.toLocaleString();
  stats.textContent = `${books} books\n${passages} passages`;
}

function renderMatches(matches) {
  if (!matches.length) {
    matchesList.innerHTML = "<p class='empty'>Search matches will appear here as soon as the archive can answer your draft.</p>";
    return;
  }

  matchesList.innerHTML = matches
    .map(
      (match) => `
        <article class="match-card">
          <p class="match-meta">Your line</p>
          <p>${escapeHtml(match.input)}</p>
          <p class="match-meta">Found quotation</p>
          <p>"${escapeHtml(match.quote)}"</p>
          <h4>${escapeHtml(match.title)}</h4>
          <p class="match-meta">${escapeHtml(match.author || "Unknown author")} · ${escapeHtml(match.year || "n.d.")}</p>
          <p><a href="${escapeHtml(match.sourceUrl)}" target="_blank" rel="noreferrer">Project Gutenberg source</a></p>
        </article>
      `,
    )
    .join("");
}

async function compose() {
  const response = await fetch("/api/compose", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: editor.value,
      style: citationStyle.value,
    }),
  });

  const payload = await response.json();
  output.innerHTML = payload.html;
  renderMatches(payload.matches);
}

function scheduleCompose() {
  clearTimeout(composeTimer);
  composeTimer = setTimeout(compose, 220);
}

async function loadSelectedFile(event) {
  const [file] = event.target.files || [];
  if (!file) {
    return;
  }
  const text = await file.text();
  editor.value = text;
  scheduleCompose();
  event.target.value = "";
}

function saveCurrentText() {
  const text = editor.value;
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "the-already-said.txt";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

editor.addEventListener("input", scheduleCompose);
citationStyle.addEventListener("change", compose);
splitter.addEventListener("pointerdown", beginSplitDrag);
loadFileButton.addEventListener("click", () => fileInput.click());
saveFileButton.addEventListener("click", saveCurrentText);
fileInput.addEventListener("change", loadSelectedFile);

loadStats().catch(() => {
  stats.textContent = "Index unavailable";
});

loadSplitRatio();
renderMatches([]);
