const editor = document.getElementById("editor");
const output = document.getElementById("quotation-output");
const matchesList = document.getElementById("matches-list");
const stats = document.getElementById("stats");
const citationStyle = document.getElementById("citation-style");
const shell = document.getElementById("shell");
const splitter = document.getElementById("splitter");
const manuscriptPanel = document.getElementById("manuscript-panel");
const manuscriptOutput = document.getElementById("manuscript-output");
const exportManuscriptButton = document.getElementById("export-manuscript");

let composeTimer = null;
let splitRatio = 0.5;
let currentPayload = { html: "", matches: [], notes: [] };
let manuscriptMode = false;
const splitStorageKey = "the-already-said.split-ratio";

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function applySplitRatio(ratio) {
  splitRatio = clamp(ratio, 0.3, 0.7);
  shell.style.gridTemplateColumns = `minmax(320px, ${splitRatio}fr) 12px minmax(320px, ${1 - splitRatio}fr)`;
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

function sentenceChunks(text) {
  return text.match(/[^.!?\n]+[.!?]?/g)?.map((chunk) => chunk.trim()).filter(Boolean) || [];
}

function paragraphChunks(text) {
  return text
    .split(/\n\s*\n/)
    .map((chunk) => chunk.trim())
    .filter(Boolean);
}

async function loadStats() {
  const response = await fetch("/api/stats");
  const payload = await response.json();
  const books = payload.indexed_books.toLocaleString();
  const passages = payload.indexed_passages.toLocaleString();
  stats.textContent = `${books} books\n${passages} passages`;
}

function renderMatchReference(match, index) {
  const author = escapeHtml(match.author || "Unknown author");
  const year = escapeHtml(match.year || "n.d.");
  const title = escapeHtml(match.title || "Untitled");

  if (citationStyle.value === "oxford") {
    return `${index + 1}. ${author}, <em>${title}</em> (${year}), Project Gutenberg.`;
  }

  return `${author}, ${year}, ${title}`;
}

function manuscriptInTextCitation(match, index) {
  const author = match.author || "Unknown author";
  const year = match.year || "n.d.";
  const surname = author.split(",")[0].trim() || author.trim();
  if (citationStyle.value === "oxford") {
    return `${index + 1}`;
  }
  return `(${surname}, ${year})`;
}

function manuscriptReferenceEntry(match, index) {
  const author = escapeHtml(match.author || "Unknown author");
  const year = escapeHtml(match.year || "n.d.");
  const title = escapeHtml(match.title || "Untitled");
  if (citationStyle.value === "oxford") {
    return `${index + 1}. ${author}, <em>${title}</em> (${year}), Project Gutenberg.`;
  }
  return `${author} (${year}) <em>${title}</em>. Project Gutenberg.`;
}

function renderManuscript(payload) {
  const paragraphsSource = paragraphChunks(editor.value);
  if (!paragraphsSource.length) {
    manuscriptOutput.innerHTML = "<p class='empty'>Press Tab to move your draft into a cited full-width manuscript view.</p>";
    return;
  }

  const matches = [...payload.matches];
  let matchCursor = 0;
  const paragraphs = [];

  for (const paragraph of paragraphsSource) {
    const segments = sentenceChunks(paragraph);
    const paragraphSegments = segments.length ? segments : [paragraph];
    const rebuilt = [];

    for (const segment of paragraphSegments) {
      const currentMatch = matches[matchCursor];
      if (currentMatch && currentMatch.input === segment) {
        const citation = manuscriptInTextCitation(currentMatch, matchCursor);
        const citationHtml = citationStyle.value === "oxford"
          ? `<sup class="manuscript-citation">${citation}</sup>`
          : ` <span class="manuscript-citation">${escapeHtml(citation)}</span>`;
        rebuilt.push(`${escapeHtml(segment)}${citationHtml}`);
        matchCursor += 1;
      } else {
        rebuilt.push(escapeHtml(segment));
      }
    }

    paragraphs.push(`<p>${rebuilt.join(" ")}</p>`);
  }

  const references = payload.matches.length
    ? `
      <section class="manuscript-reference-list">
        <h3>${citationStyle.value === "oxford" ? "Notes" : "References"}</h3>
        ${payload.matches.map((match, index) => `<p>${manuscriptReferenceEntry(match, index)}</p>`).join("")}
      </section>
    `
    : "";

  manuscriptOutput.innerHTML = paragraphs.join("") + references;
}

function setViewMode(nextMode) {
  manuscriptMode = nextMode;
  shell.classList.toggle("is-manuscript", manuscriptMode);
  manuscriptPanel.hidden = !manuscriptMode;
  if (!manuscriptMode) {
    editor.focus();
  }
}

function toggleViewMode() {
  setViewMode(!manuscriptMode);
}

function buildExportHtml() {
  const title = "The Already Said";
  const body = manuscriptOutput.innerHTML;
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${title}</title>
  <style>
    body { margin: 48px auto; max-width: 760px; padding: 0 28px 48px; color: #161616; background: #fcfcf8; font-family: "Helvetica Neue", "Univers", Arial, sans-serif; font-size: 18px; line-height: 1.8; }
    h1 { margin: 0 0 2rem; font-size: 14px; text-transform: uppercase; letter-spacing: 0.2em; }
    h3 { margin: 2.8rem 0 1rem; font-size: 12px; text-transform: uppercase; letter-spacing: 0.2em; color: #666; }
    p { margin: 0 0 1.2rem; }
    .manuscript-reference-list { border-top: 1px solid rgba(22, 22, 22, 0.16); padding-top: 1rem; }
    .manuscript-citation { color: #6d6d6d; font-size: 0.8em; white-space: nowrap; }
    em { font-style: italic; }
  </style>
</head>
<body>
  <h1>Cited draft</h1>
  ${body}
</body>
</html>`;
}

function downloadExportHtml() {
  const html = buildExportHtml();
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "the-already-said-typeset.html";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportDocumentHtml() {
  if (window.alreadySaidApp?.requestTypesetExport) {
    window.alreadySaidApp.requestTypesetExport();
    return;
  }
  downloadExportHtml();
}

function handleTabToggle(event) {
  if (event.key !== "Tab" || event.metaKey || event.ctrlKey || event.altKey) {
    return;
  }
  event.preventDefault();
  toggleViewMode();
}

function renderMatches(matches) {
  if (!matches.length) {
    matchesList.innerHTML = "<p class='empty'>Search matches will appear here as soon as the archive can answer your draft.</p>";
    return;
  }

  matchesList.innerHTML = matches
    .map(
      (match, index) => `
        <article class="match-card">
          <p class="match-meta">Your line</p>
          <p>${escapeHtml(match.input)}</p>
          <p class="match-meta">Found quotation</p>
          <p>"${escapeHtml(match.quote)}"</p>
          <h4>${escapeHtml(match.title)}</h4>
          <p class="match-meta">${renderMatchReference(match, index)}</p>
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
  currentPayload = payload;
  output.innerHTML = payload.html;
  renderMatches(payload.matches);
  renderManuscript(payload);
}

function scheduleCompose() {
  clearTimeout(composeTimer);
  composeTimer = setTimeout(compose, 220);
}

function setDraftText(text) {
  editor.value = text;
  scheduleCompose();
}

function getDraftText() {
  return editor.value;
}

editor.addEventListener("input", scheduleCompose);
citationStyle.addEventListener("change", compose);
splitter.addEventListener("pointerdown", beginSplitDrag);
window.addEventListener("keydown", handleTabToggle);
exportManuscriptButton.addEventListener("click", exportDocumentHtml);

loadStats().catch(() => {
  stats.textContent = "Index unavailable";
});

loadSplitRatio();
renderMatches([]);
renderManuscript(currentPayload);

window.alreadySaidApp = {
  getDraftText,
  getTypesetHtml: buildExportHtml,
  requestTypesetExport: null,
  setDraftText,
};
