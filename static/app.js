const editor = document.getElementById("editor");
const output = document.getElementById("quotation-output");
const coverageList = document.getElementById("coverage-list");
const sourceDetail = document.getElementById("source-detail");
const matchesList = document.getElementById("matches-list");
const stats = document.getElementById("stats");
const citationStyle = document.getElementById("citation-style");
const shell = document.getElementById("shell");
const splitter = document.getElementById("splitter");
const manuscriptPanel = document.getElementById("manuscript-panel");
const manuscriptOutput = document.getElementById("manuscript-output");
const exportManuscriptButton = document.getElementById("export-manuscript");
const copyManuscriptButton = document.getElementById("copy-manuscript");
const backToDraftButton = document.getElementById("back-to-draft");
const draftViewButton = document.getElementById("draft-view-button");
const manuscriptViewButton = document.getElementById("manuscript-view-button");
const draftCount = document.getElementById("draft-count");
const manuscriptCount = document.getElementById("manuscript-count");
const draftSaveStatus = document.getElementById("draft-save-status");
const composeStatus = document.getElementById("compose-status");
const matchCount = document.getElementById("match-count");
const matchMode = document.getElementById("match-mode");
const minConfidence = document.getElementById("min-confidence");
const confidenceValue = document.getElementById("confidence-value");
const allowComposite = document.getElementById("allow-composite");
const preferExact = document.getElementById("prefer-exact");

let composeTimer = null;
let splitRatio = 0.5;
let currentPayload = { html: "", matches: [], segments: [], notes: [] };
let manuscriptMode = false;
let composeAbortController = null;
let composeRequestId = 0;
let selectedSourceId = null;
const splitStorageKey = "the-already-said.split-ratio";
const draftStorageKey = "the-already-said.draft";
const tuningStorageKey = "the-already-said.tuning";

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
  return String(value == null ? "" : value)
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

function sourceComponents(matches) {
  return matches.flatMap((match, matchIndex) =>
    matchComponents(match).map((component, componentIndex) => ({
      ...component,
      sourceId: `${matchIndex}:${componentIndex}:primary`,
      parentInput: match.input,
      parentQuality: match.quality,
      candidateRole: "Selected",
    })),
  );
}

function sourceCandidates(matches) {
  return matches.flatMap((match, matchIndex) =>
    matchComponents(match).flatMap((component, componentIndex) => {
      const primary = {
        ...component,
        sourceId: `${matchIndex}:${componentIndex}:primary`,
        parentInput: match.input,
        parentQuality: match.quality,
        candidateRole: "Selected",
      };
      const alternatives = (component.alternatives || []).map((alternative, alternativeIndex) => ({
        ...alternative,
        sourceId: `${matchIndex}:${componentIndex}:alt:${alternativeIndex}`,
        parentInput: match.input,
        parentQuality: match.quality,
        candidateRole: `Alternative ${alternativeIndex + 1}`,
        clauseIndex: component.clauseIndex,
        clauseCount: component.clauseCount,
      }));
      return [primary, ...alternatives];
    }),
  );
}

function countWords(text) {
  return text.trim().match(/\S+/g)?.length || 0;
}

function updateDraftMetrics() {
  const words = countWords(editor.value);
  const segments = sentenceChunks(editor.value).length;
  draftCount.textContent = `${words.toLocaleString()} ${words === 1 ? "word" : "words"} / ${segments.toLocaleString()} ${segments === 1 ? "sentence" : "sentences"}`;
}

function updateMatchMetrics(matches) {
  const sources = sourceComponents(matches).length;
  const lines = matches.length;
  matchCount.textContent = `${sources.toLocaleString()} ${sources === 1 ? "source" : "sources"} across ${lines.toLocaleString()} ${lines === 1 ? "line" : "lines"}`;
  manuscriptCount.textContent = `${sources.toLocaleString()} ${sources === 1 ? "reference" : "references"}`;
}

function currentTuningOptions() {
  return {
    mode: matchMode.value,
    allow_composite: allowComposite.checked,
    min_confidence: Number.parseFloat(minConfidence.value),
    prefer_exact: preferExact.checked,
  };
}

function saveTuningOptions() {
  window.localStorage.setItem(tuningStorageKey, JSON.stringify(currentTuningOptions()));
}

function applyTuningModeDefaults(mode) {
  const defaults = {
    strict: { confidence: 4.7, exact: true },
    associative: { confidence: 3.6, exact: false },
    uncanny: { confidence: 2.8, exact: false },
  };
  const next = defaults[mode] || defaults.associative;
  minConfidence.value = String(next.confidence);
  preferExact.checked = next.exact;
  updateConfidenceLabel();
}

function loadTuningOptions() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(tuningStorageKey) || "{}");
    if (["strict", "associative", "uncanny"].includes(saved.mode)) {
      matchMode.value = saved.mode;
    }
    if (typeof saved.allow_composite === "boolean") {
      allowComposite.checked = saved.allow_composite;
    }
    if (Number.isFinite(Number(saved.min_confidence))) {
      minConfidence.value = String(clamp(Number(saved.min_confidence), 1, 8));
    }
    if (typeof saved.prefer_exact === "boolean") {
      preferExact.checked = saved.prefer_exact;
    }
  } catch {
    applyTuningModeDefaults("associative");
  }
  updateConfidenceLabel();
}

function updateConfidenceLabel() {
  confidenceValue.textContent = Number.parseFloat(minConfidence.value).toFixed(1);
}

function qualityLabel(value) {
  const numericValue = Number.parseFloat(value);
  if (Number.isNaN(numericValue) || numericValue <= 0) {
    return "No score";
  }
  if (numericValue >= 6) {
    return "Strong";
  }
  if (numericValue >= 4.2) {
    return "Good";
  }
  return "Tentative";
}

function renderLensSummary(component) {
  const lens = component.lens || {};
  const terms = lens.sharedTerms?.length ? lens.sharedTerms.join(", ") : "none";
  return `
    <div class="lens-grid">
      <p><span>Type</span>${escapeHtml(lens.matchType || component.matchedBy || "search")}</p>
      <p><span>Quality</span>${escapeHtml(component.quality ?? lens.quality ?? "n/a")}</p>
      <p><span>Similarity</span>${escapeHtml(lens.similarity ?? "n/a")}</p>
      <p><span>Terms</span>${escapeHtml(terms)}</p>
    </div>
  `;
}

function renderClauseParts(match) {
  if (!match.composite) {
    return "";
  }
  const clauses = match.clauses?.length ? match.clauses : matchComponents(match).map((component) => component.input);
  return `
    <div class="clause-map">
      ${clauses.map((clause, index) => `
        <div class="clause-row">
          <span>${index + 1}</span>
          <p>${escapeHtml(clause)}</p>
        </div>
      `).join("")}
    </div>
  `;
}

function renderCoverage(segments = []) {
  if (!segments.length) {
    coverageList.innerHTML = "<p class='empty'>Sentence coverage will appear here once there is a draft.</p>";
    return;
  }

  coverageList.innerHTML = segments
    .map((segment) => {
      const components = segment.components || [];
      const isMissing = segment.status === "missing";
      const statusLabel = isMissing
        ? "Missing"
        : segment.status === "composite"
          ? "Composite"
          : "Matched";
      const sourceLabel = components.length
        ? `${components.length} ${components.length === 1 ? "source" : "sources"}`
        : "0 sources";
      const alternativeCount = components.reduce((count, component) => count + (component.alternatives?.length || 0), 0);
      const alternativeLabel = alternativeCount ? ` / ${alternativeCount} alternatives` : "";
      const quality = isMissing ? "Needs corpus" : qualityLabel(segment.quality);
      return `
        <article class="coverage-row ${isMissing ? "is-missing" : "is-matched"}">
          <div class="coverage-index">${Number(segment.index) + 1}</div>
          <div class="coverage-main">
            <p>${escapeHtml(segment.input)}</p>
            <p class="match-meta">${statusLabel} / ${sourceLabel}${alternativeLabel} / ${quality}</p>
            ${segment.status === "composite" ? `
              <div class="coverage-clauses">
                ${components.map((component) => `
                  <p><span>${Number(component.clauseIndex) + 1}</span>${escapeHtml(component.input)}</p>
                `).join("")}
              </div>
            ` : ""}
          </div>
        </article>
      `;
    })
    .join("");
}

function setComposeStatus(payload) {
  const segments = payload.segments || [];
  if (!segments.length) {
    composeStatus.textContent = payload.matches?.length ? "Archive aligned" : "No sources yet";
    return;
  }

  const recovered = segments.filter((segment) => segment.status !== "missing").length;
  if (recovered === segments.length) {
    composeStatus.textContent = "Archive aligned";
    return;
  }
  if (recovered > 0) {
    composeStatus.textContent = `${recovered} of ${segments.length} recovered`;
    return;
  }
  composeStatus.textContent = "No sources yet";
}

function renderSourceInspector(component) {
  if (!component) {
    sourceDetail.innerHTML = "<p class='empty'>Select a source match to inspect how the archive found it.</p>";
    return;
  }

  const lens = component.lens || {};
  const sourceUrl = component.sourceUrl
    ? `<p><a href="${escapeHtml(component.sourceUrl)}" target="_blank" rel="noreferrer">Project Gutenberg source</a></p>`
    : "";
  sourceDetail.innerHTML = `
    <p class="match-meta">Archive lens</p>
    <h4>${escapeHtml(component.title || "Untitled")}</h4>
    <p class="source-author">${escapeHtml(component.author || "Unknown author")} / ${escapeHtml(component.year || "n.d.")} / ${escapeHtml(component.candidateRole || "Selected")}</p>
    ${renderLensSummary(component)}
    <p class="match-meta">Your line</p>
    <p>${escapeHtml(component.parentInput || component.input)}</p>
    ${component.clauseCount ? `<p class="match-meta">Composite clause ${Number(component.clauseIndex) + 1} of ${component.clauseCount}</p><p>${escapeHtml(component.input)}</p>` : ""}
    <p class="match-meta">Recovered quotation</p>
    <blockquote>${escapeHtml(component.quote)}</blockquote>
    <p class="match-meta">Surrounding passage</p>
    <p class="source-excerpt">${escapeHtml(lens.sourceExcerpt || component.quote)}</p>
    ${sourceUrl}
  `;
}

function selectedSourceFromPayload() {
  const sources = sourceCandidates(currentPayload.matches || []);
  if (!sources.length) {
    selectedSourceId = null;
    return null;
  }
  return sources.find((source) => source.sourceId === selectedSourceId) || sources[0];
}

function saveDraft() {
  window.localStorage.setItem(draftStorageKey, editor.value);
  draftSaveStatus.textContent = editor.value.trim() ? "Draft saved locally" : "Local draft empty";
}

function loadDraft() {
  const savedDraft = window.localStorage.getItem(draftStorageKey);
  if (savedDraft) {
    editor.value = savedDraft;
  }
  updateDraftMetrics();
  saveDraft();
}

function normalizeForMatch(text) {
  return text
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[“”"]/g, "\"")
    .replace(/[’]/g, "'")
    .trim();
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

function matchComponents(match) {
  return match.components?.length ? match.components : [match];
}

function citationBaseIndex(matches, uptoIndex) {
  return matches
    .slice(0, uptoIndex)
    .reduce((count, candidate) => count + matchComponents(candidate).length, 0);
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
    updateMatchMetrics([]);
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
      const normalizedSegment = normalizeForMatch(segment);
      let matchedIndex = -1;

      for (let index = matchCursor; index < matches.length; index += 1) {
        if (normalizeForMatch(matches[index].input) === normalizedSegment) {
          matchedIndex = index;
          break;
        }
      }

      if (matchedIndex !== -1) {
        const matched = matches[matchedIndex];
        const components = matchComponents(matched);
        if (components.length > 1) {
          const baseIndex = citationBaseIndex(matches, matchedIndex);
          const citations = components
            .map((component, componentIndex) => {
              const citation = manuscriptInTextCitation(component, baseIndex + componentIndex);
              return citationStyle.value === "oxford"
                ? `<sup class="manuscript-citation">${citation}</sup>`
                : `<span class="manuscript-citation">${escapeHtml(citation)}</span>`;
            })
            .join(citationStyle.value === "oxford" ? "" : " ");
          rebuilt.push(`${escapeHtml(segment)} ${citations} <span class="manuscript-composite-label">[recovered from multiple archive sources]</span>`);
        } else {
          const citation = manuscriptInTextCitation(matched, citationBaseIndex(matches, matchedIndex));
          const citationHtml = citationStyle.value === "oxford"
            ? `<sup class="manuscript-citation">${citation}</sup>`
            : ` <span class="manuscript-citation">${escapeHtml(citation)}</span>`;
          rebuilt.push(`${escapeHtml(segment)}${citationHtml}`);
        }
        matchCursor = matchedIndex + 1;
      } else {
        rebuilt.push(escapeHtml(segment));
      }
    }

    paragraphs.push(`<p>${rebuilt.join(" ")}</p>`);
  }

  const referencesSource = sourceComponents(payload.matches);
  const references = referencesSource.length
    ? `
      <section class="manuscript-reference-list">
        <h3>${citationStyle.value === "oxford" ? "Notes" : "References"}</h3>
        ${referencesSource.map((match, index) => `<p>${manuscriptReferenceEntry(match, index)}</p>`).join("")}
      </section>
    `
    : "";

  manuscriptOutput.innerHTML = paragraphs.join("") + references;
  updateMatchMetrics(payload.matches);
}

function setViewMode(nextMode) {
  manuscriptMode = nextMode;
  shell.classList.toggle("is-manuscript", manuscriptMode);
  manuscriptPanel.hidden = !manuscriptMode;
  draftViewButton.classList.toggle("is-active", !manuscriptMode);
  manuscriptViewButton.classList.toggle("is-active", manuscriptMode);
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

async function copyManuscriptText() {
  const text = manuscriptOutput.innerText.trim();
  if (!text) {
    return;
  }
  await navigator.clipboard.writeText(text);
  copyManuscriptButton.textContent = "Copied";
  window.setTimeout(() => {
    copyManuscriptButton.textContent = "Copy";
  }, 1200);
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
    updateMatchMetrics([]);
    renderSourceInspector(null);
    return;
  }

  const availableSources = sourceCandidates(matches);
  if (!availableSources.some((source) => source.sourceId === selectedSourceId)) {
    selectedSourceId = availableSources[0]?.sourceId || null;
  }

  matchesList.innerHTML = matches
    .map(
      (match, index) => {
        const baseIndex = citationBaseIndex(matches, index);
        return `
        <article class="match-card">
          <p class="match-meta">Your line</p>
          <p>${escapeHtml(match.input)}</p>
          ${matchComponents(match).length > 1 ? `<p class="match-badge">Recovered from multiple archive sources</p>` : ""}
          ${renderClauseParts(match)}
          <p class="match-meta">${matchComponents(match).length > 1 ? "Recovered as parts" : "Found quotation"}</p>
          ${matchComponents(match).map((component, componentIndex) => `
            ${component.clauseCount ? `<p class="match-meta">Clause ${Number(component.clauseIndex) + 1} of ${component.clauseCount}</p>` : ""}
            <button class="source-pick ${selectedSourceId === `${index}:${componentIndex}:primary` ? "is-active" : ""}" type="button" data-source-id="${index}:${componentIndex}:primary">
              <span>${escapeHtml(component.lens?.matchType || "archive source")}</span>
              <strong>${escapeHtml(qualityLabel(component.quality))}</strong>
            </button>
            <p>"${escapeHtml(component.quote)}"</p>
            ${renderLensSummary(component)}
            ${component.alternatives?.length ? `
              <div class="alternatives">
                <p class="match-meta">Alternatives</p>
                ${component.alternatives.map((alternative, alternativeIndex) => `
                  <button class="source-pick alternative ${selectedSourceId === `${index}:${componentIndex}:alt:${alternativeIndex}` ? "is-active" : ""}" type="button" data-source-id="${index}:${componentIndex}:alt:${alternativeIndex}">
                    <span>${escapeHtml(alternative.title || "Untitled")}</span>
                    <strong>${escapeHtml(qualityLabel(alternative.quality))}</strong>
                  </button>
                `).join("")}
              </div>
            ` : ""}
            <h4>${escapeHtml(component.title)}</h4>
            <p class="match-meta">${renderMatchReference(component, baseIndex + componentIndex)}</p>
            <p><a href="${escapeHtml(component.sourceUrl)}" target="_blank" rel="noreferrer">Project Gutenberg source</a></p>
          `).join("")}
        </article>
      `;
      },
    )
    .join("");
  updateMatchMetrics(matches);
  renderSourceInspector(selectedSourceFromPayload());
}

async function compose() {
  const draftText = editor.value;
  if (!draftText.trim()) {
    currentPayload = { html: "<p class='empty'>Start writing on the left. Quotations will gather here.</p>", matches: [], segments: [], notes: [] };
    output.innerHTML = currentPayload.html;
    renderCoverage([]);
    renderMatches([]);
    renderManuscript(currentPayload);
    composeStatus.textContent = "Waiting for draft";
    return;
  }

  composeAbortController?.abort();
  composeAbortController = new AbortController();
  const requestId = composeRequestId + 1;
  composeRequestId = requestId;
  composeStatus.textContent = "Searching archive...";

  try {
    const response = await fetch("/api/compose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: composeAbortController.signal,
      body: JSON.stringify({
        text: draftText,
        style: citationStyle.value,
        options: currentTuningOptions(),
      }),
    });

    if (!response.ok) {
      throw new Error(`Compose failed with ${response.status}`);
    }

    const payload = await response.json();
    if (requestId !== composeRequestId) {
      return;
    }
    currentPayload = payload;
    output.innerHTML = payload.html;
    renderCoverage(payload.segments || []);
    renderMatches(payload.matches);
    renderManuscript(payload);
    setComposeStatus(payload);
  } catch (error) {
    if (error.name === "AbortError") {
      return;
    }
    composeStatus.textContent = "Archive unavailable";
    output.innerHTML = "<p class='missing'>The archive could not answer this draft. Check that the local server and index are available.</p>";
    renderCoverage([]);
  }
}

function scheduleCompose() {
  clearTimeout(composeTimer);
  updateDraftMetrics();
  saveDraft();
  composeStatus.textContent = editor.value.trim() ? "Draft changed" : "Waiting for draft";
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
matchMode.addEventListener("change", () => {
  applyTuningModeDefaults(matchMode.value);
  saveTuningOptions();
  compose();
});
minConfidence.addEventListener("input", () => {
  updateConfidenceLabel();
  saveTuningOptions();
  scheduleCompose();
});
allowComposite.addEventListener("change", () => {
  saveTuningOptions();
  compose();
});
preferExact.addEventListener("change", () => {
  saveTuningOptions();
  compose();
});
splitter.addEventListener("pointerdown", beginSplitDrag);
window.addEventListener("keydown", handleTabToggle);
matchesList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-source-id]");
  if (!button) {
    return;
  }
  selectedSourceId = button.dataset.sourceId;
  renderMatches(currentPayload.matches || []);
});
exportManuscriptButton.addEventListener("click", exportDocumentHtml);
copyManuscriptButton.addEventListener("click", () => {
  copyManuscriptText().catch(() => {
    copyManuscriptButton.textContent = "Copy failed";
    window.setTimeout(() => {
      copyManuscriptButton.textContent = "Copy";
    }, 1200);
  });
});
backToDraftButton.addEventListener("click", () => setViewMode(false));
draftViewButton.addEventListener("click", () => setViewMode(false));
manuscriptViewButton.addEventListener("click", () => setViewMode(true));

loadStats().catch(() => {
  stats.textContent = "Index unavailable";
});

loadSplitRatio();
loadTuningOptions();
loadDraft();
renderCoverage([]);
renderMatches([]);
renderManuscript(currentPayload);
if (editor.value.trim()) {
  scheduleCompose();
}

window.alreadySaidApp = {
  getDraftText,
  getTypesetHtml: buildExportHtml,
  requestTypesetExport: null,
  setDraftText,
};
