const editor = document.getElementById("editor");
const output = document.getElementById("quotation-output");
const matchesList = document.getElementById("matches-list");
const stats = document.getElementById("stats");
const citationStyle = document.getElementById("citation-style");

let composeTimer = null;

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
  stats.textContent = `${books} books indexed, ${passages} passages ready`;
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

editor.addEventListener("input", scheduleCompose);
citationStyle.addEventListener("change", compose);

loadStats().catch(() => {
  stats.textContent = "Index unavailable";
});

renderMatches([]);
