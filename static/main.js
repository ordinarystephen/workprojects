/* ── KRONOS · main.js ─────────────────────────────────────────
 *
 * All frontend logic. Runs entirely in the browser — no build step.
 * Communicates with Flask via fetch() calls to POST /upload.
 *
 * Section map:
 *   DOM refs          — element handles used throughout
 *   State             — runtime variables
 *   Upload            — file selection, drag-drop, validation
 *   Canned Prompts    — quick analysis button rendering + selection
 *   Run Button        — enable/disable + ready pulse
 *   Pipeline          — loading animation + API call orchestration
 *   Results           — renders narrative and metric tiles
 *   Follow-up FAB     — floating + button and follow-up chat thread
 *   Mock Data         — REMOVE ON MERGE (fallback for demo without backend)
 *
 * ── WHAT TO REMOVE WHEN MERGING ──────────────────────────────
 *   1. getMockResult() function at the bottom of this file — entire block
 *   2. The `|| getMockResult()` fallback in runAnalysis() — marked below
 *   3. The getMockResult().narrative fallback in submitFollowup() — marked below
 *   Nothing else in this file needs to change for the pipeline merge.
 * ────────────────────────────────────────────────────────────── */


// ── DOM refs ──────────────────────────────────────────────────
// Handles to every element this script interacts with.
// These IDs are defined in index.html — do not rename without updating both.

const chatArea        = document.getElementById('chatArea');        // drag-drop target + textarea wrapper
const browseBtn       = document.getElementById('browseBtn');       // paperclip icon — triggers file picker
const fileInput       = document.getElementById('fileInput');       // hidden <input type="file">
const fileAttachment  = document.getElementById('fileAttachment');  // strip shown above textarea after file selected
const filePillFormat  = document.getElementById('filePillFormat');  // format badge (xlsx / csv)
const fileName        = document.getElementById('fileName');        // filename label in attachment strip
const fileSize        = document.getElementById('fileSize');        // formatted file size in attachment strip
const removeFile      = document.getElementById('removeFile');      // ✕ button in attachment strip
const uploadError     = document.getElementById('uploadError');     // error strip below chat area
const uploadErrorText = document.getElementById('uploadErrorText'); // error message text

const cannedGrid      = document.getElementById('cannedGrid');      // grid of quick-analysis buttons
const customPrompt    = document.getElementById('customPrompt');    // main textarea
const promptHint      = document.getElementById('promptHint');      // inline hint / error in toolbar
const runBtn          = document.getElementById('runBtn');          // Run Analysis button

const inputPanel      = document.getElementById('inputPanel');      // landing/input view
const loadingPanel    = document.getElementById('loadingPanel');    // pipeline progress view
const resultsPanel    = document.getElementById('resultsPanel');    // results view
const newAnalysisBtn  = document.getElementById('newAnalysisBtn'); // "New Analysis" button in results header

const narrativeText   = document.getElementById('narrativeText');   // first response text container
const metricGrid      = document.getElementById('metricGrid');      // data snapshot tile grid
const resultsTimestamp= document.getElementById('resultsTimestamp');// timestamp in results header
const messageThread   = document.getElementById('messageThread');   // thread container (first + follow-up blocks)
const followupFab     = document.getElementById('followupFab');     // floating + button


// ── State ─────────────────────────────────────────────────────
// These three variables persist across the full session.
// selectedFile is intentionally NOT cleared on New Analysis —
// allows the user to re-run against the same file without re-uploading.

let selectedFile    = null;  // File object from the last attach action
let activeCannedBtn = null;  // Currently selected quick-analysis button element
let activeMode      = null;  // Mode slug from prompts.json (e.g. "lending-risk").
                             // Sent to server as formData 'mode' to route the correct
                             // pipeline script. Null when user types a custom question.


// ── Upload ────────────────────────────────────────────────────

// Paperclip click → open native file picker
browseBtn.addEventListener('click', (e) => {
  e.stopPropagation(); // prevent event bubbling into chatArea
  fileInput.click();
});

// Drag-drop into the chat area.
// dragCounter tracks nested enter/leave events so the drag-over highlight
// doesn't flicker when the cursor moves over child elements inside chatArea.
let dragCounter = 0;

chatArea.addEventListener('dragenter', (e) => {
  e.preventDefault();
  dragCounter++;
  chatArea.classList.add('drag-over'); // CSS grey wash + "Drop to attach" overlay
});

chatArea.addEventListener('dragover', (e) => {
  e.preventDefault(); // required to allow drop
});

chatArea.addEventListener('dragleave', () => {
  dragCounter--;
  if (dragCounter === 0) chatArea.classList.remove('drag-over');
});

chatArea.addEventListener('drop', (e) => {
  e.preventDefault();
  dragCounter = 0;
  chatArea.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) setFile(file);
});

// Native file picker selection
fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});

removeFile.addEventListener('click', clearFile);

// Validate and register the selected file.
// Only .xlsx, .xls, and .csv are accepted — anything else shows an error.
function setFile(file) {
  const allowed = ['.xlsx', '.xls', '.csv'];
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  if (!allowed.includes(ext)) {
    showUploadError('Unsupported file type. Use .xlsx, .xls, or .csv.');
    return;
  }

  clearUploadError();
  selectedFile = file;
  fileName.textContent = file.name;
  fileSize.textContent = formatBytes(file.size);
  filePillFormat.textContent = ext.slice(1); // e.g. "xlsx"

  // Slide the attachment strip into view
  fileAttachment.hidden = false;
  fileAttachment.classList.remove('slide-down');
  void fileAttachment.offsetWidth; // force reflow to re-trigger CSS animation
  fileAttachment.classList.add('slide-down');

  updateRunBtn();
}

// Clear the current file and reset attachment UI
function clearFile() {
  selectedFile = null;
  fileInput.value = '';
  fileAttachment.hidden = true;
  clearUploadError();
  updateRunBtn();
}

// Show a shaking error strip below the chat area, auto-dismisses after 5s
function showUploadError(msg) {
  uploadErrorText.textContent = msg;
  uploadError.hidden = false;
  uploadError.classList.remove('shake');
  void uploadError.offsetWidth; // reflow to re-trigger shake animation
  uploadError.classList.add('shake');
  setTimeout(clearUploadError, 5000);
}

function clearUploadError() {
  uploadError.hidden = true;
  uploadErrorText.textContent = '';
}

// Human-readable file size (B / KB / MB)
function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// Show a temporary inline error in the toolbar hint area
function showInlineError(msg) {
  promptHint.textContent = msg;
  promptHint.style.color = 'var(--danger)';
  setTimeout(() => { promptHint.textContent = ''; promptHint.style.color = ''; }, 4000);
}


// ── Canned Prompts ────────────────────────────────────────────
// Buttons are rendered dynamically from static/prompts.json.
// To add, remove, or edit a button — only touch prompts.json.
// Each entry requires: title, desc, mode, prompt.
//
// TODO (naming): If you rename the mode slugs in prompts.json,
// make sure SCRIPT_MAP keys in pipeline/analyze.py match exactly.

function attachCannedHandler(btn) {
  btn.addEventListener('click', () => {
    if (activeCannedBtn) activeCannedBtn.classList.remove('active');

    // Clicking the already-active button deselects it
    if (activeCannedBtn === btn) {
      activeCannedBtn = null;
      activeMode = null; // no script to route to
      customPrompt.value = '';
      promptHint.textContent = '';
      updateRunBtn();
      return;
    }

    activeCannedBtn = btn;
    activeMode = btn.dataset.mode || null; // slug that routes to the correct pipeline script server-side
    btn.classList.add('active');
    customPrompt.value = btn.dataset.prompt; // pre-fill textarea with the canned prompt text
    promptHint.textContent = btn.querySelector('.canned-title').textContent; // show title as hint
    updateRunBtn();
    customPrompt.focus();
  });
}

// Fetch prompts.json and build the button grid on page load
fetch('prompts.json')
  .then(r => r.json())
  .then(prompts => {
    prompts.forEach(p => {
      const btn = document.createElement('button');
      btn.className = 'canned-btn';
      btn.dataset.prompt = p.prompt;           // full prompt text pre-fills textarea on click
      btn.dataset.mode   = p.mode || '';       // mode slug stored on element, read on click
      btn.innerHTML = `<span class="canned-title">${p.title}</span><span class="canned-desc">${p.desc}</span>`;
      cannedGrid.appendChild(btn);
      attachCannedHandler(btn);
    });
  })
  .catch(() => {
    // prompts.json unavailable — grid stays empty, user can still type a custom question
  });

// If the user starts typing, deselect any active canned button.
// Their custom text takes over and activeMode is cleared (no specific script).
customPrompt.addEventListener('input', () => {
  if (activeCannedBtn) {
    activeCannedBtn.classList.remove('active');
    activeCannedBtn = null;
    activeMode = null; // custom question has no associated pipeline script
    promptHint.textContent = '';
  }
  // Auto-grow textarea height with content
  customPrompt.style.height = 'auto';
  customPrompt.style.height = customPrompt.scrollHeight + 'px';
  updateRunBtn();
});


// ── Run Button ────────────────────────────────────────────────
// Enabled only when both a file and a non-empty prompt are present.
// Pulses once (scale + glow) when it first becomes enabled.

function updateRunBtn() {
  const hasFile    = !!selectedFile;
  const hasPrompt  = customPrompt.value.trim().length > 0;
  const nowEnabled = hasFile && hasPrompt;

  // Pulse once on transition from disabled → enabled
  if (runBtn.disabled && nowEnabled) {
    runBtn.classList.remove('ready-pulse');
    void runBtn.offsetWidth; // force reflow to re-trigger animation
    runBtn.classList.add('ready-pulse');
    runBtn.addEventListener('animationend', () => runBtn.classList.remove('ready-pulse'), { once: true });
  }

  runBtn.disabled = !nowEnabled;
}

runBtn.addEventListener('click', runAnalysis);


// ── Pipeline ──────────────────────────────────────────────────
// Orchestrates the loading animation and the API call.
// Steps 0–2 animate on fixed delays (UI timing only, not real durations).
// Step 3 waits for the real API response.
// Step 4 is a short finalize delay before showing results.

const STEPS = [
  'Parsing file',
  'Running analysis pipeline',
  'Building prompt context',
  'Generating narrative',
  'Finalizing output'
];

// UI-only timing delays in ms. These don't reflect actual server timing —
// they just make the loading animation feel natural.
// Step 3 delay is 0 because it blocks on the real API call.
const STEP_DELAYS = [800, 1400, 600, 0, 500];

async function runAnalysis() {
  const prompt = customPrompt.value.trim();
  if (!selectedFile || !prompt) return;

  // Switch to loading view
  inputPanel.hidden  = true;
  resultsPanel.hidden = true;
  loadingPanel.hidden = false;
  loadingPanel.classList.add('fade-in');

  // Reset all pipeline step indicators
  STEPS.forEach((_, i) => {
    const step = document.getElementById(`step-${i}`);
    step.classList.remove('active', 'done');
    document.getElementById(`time-${i}`).textContent = '';
  });

  // Clear previous thread content: follow-ups, dividers, first message label
  narrativeText.textContent = '';
  document.getElementById('firstMessage').querySelector('.message-meta')?.remove();
  messageThread.querySelectorAll('.thread-divider, .thinking-dots, .message-block:not(#firstMessage), .followup-input').forEach(el => el.remove());
  followupFab.hidden = true;
  followupFab.classList.remove('open');

  // Fire the API call immediately in parallel with the loading animation.
  // The animation for steps 0–2 runs while waiting for the response.
  //
  // TODO: Once server.py /upload route is implemented, this fetch returns real data.
  // Until then the .catch() returns null and getMockResult() is used as fallback.
  const formData = new FormData();
  formData.append('file', selectedFile);
  formData.append('prompt', prompt);
  formData.append('mode', activeMode || ''); // routes server to correct pipeline script; empty for custom questions

  const apiCall = fetch('/upload', { method: 'POST', body: formData })
    .then(r => r.json())
    .catch(() => null); // null triggers getMockResult() fallback below — REMOVE ON MERGE

  // Animate steps 0–2 with fixed delays
  const t0 = Date.now();
  for (let i = 0; i <= 2; i++) {
    const step = document.getElementById(`step-${i}`);
    step.classList.add('active');
    await delay(STEP_DELAYS[i]);
    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
    step.classList.remove('active');
    step.classList.add('done');
    document.getElementById(`time-${i}`).textContent = `${elapsed}s`;
  }

  // Step 3 — blocks until the real API call resolves
  const step3 = document.getElementById('step-3');
  step3.classList.add('active');
  const apiResult = await apiCall;
  const t3 = ((Date.now() - t0) / 1000).toFixed(1);
  step3.classList.remove('active');
  step3.classList.add('done');
  document.getElementById('time-3').textContent = `${t3}s`;

  // Step 4 — short finalize pause
  const step4 = document.getElementById('step-4');
  step4.classList.add('active');
  await delay(STEP_DELAYS[4]);
  const t4 = ((Date.now() - t0) / 1000).toFixed(1);
  step4.classList.remove('active');
  step4.classList.add('done');
  document.getElementById('time-4').textContent = `${t4}s`;

  await delay(300); // brief pause before transitioning to results

  // TODO (REMOVE ON MERGE): Delete the `|| getMockResult()` fallback once
  // the real /upload route is live. apiResult will be the parsed JSON response.
  // Expected shape: { narrative: string, metrics: { ... } }
  const result = apiResult || getMockResult();
  showResults(result, t4);
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }


// ── Results ───────────────────────────────────────────────────
// Transitions from loading → results view and populates the
// narrative column and data snapshot tiles.

function showResults(result, elapsed) {
  loadingPanel.hidden  = false;
  resultsPanel.hidden  = false;
  loadingPanel.hidden  = true;
  resultsPanel.classList.add('fade-in');

  // Timestamp shown in results header (date · time · total elapsed)
  const now     = new Date();
  const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  resultsTimestamp.textContent = `${now.toLocaleDateString()} · ${timeStr} · ${elapsed}s`;

  // Add a question label to the first message block (canned title or first 48 chars of custom)
  const firstBlock    = document.getElementById('firstMessage');
  const existingMeta  = firstBlock.querySelector('.message-meta');
  if (!existingMeta) {
    const label = activeCannedBtn
      ? activeCannedBtn.querySelector('.canned-title').textContent
      : customPrompt.value.trim().slice(0, 48) + (customPrompt.value.trim().length > 48 ? '…' : '');
    const meta = document.createElement('div');
    meta.className = 'message-meta';
    meta.innerHTML = `<span class="message-label">${escapeHtml(label)}</span><span class="message-time mono">${timeStr}</span>`;
    firstBlock.insertBefore(meta, narrativeText);
  }

  // Render data snapshot tiles from metrics object
  // renderMetrics() handles dynamic sections and tile count — no hardcoding needed here
  renderMetrics(result.metrics || {});

  // Dump narrative text and attach copy button
  const narrative    = result.narrative    || '(No narrative returned)';
  const claims       = result.claims       || [];
  const contextSent  = result.context_sent || null;
  const verification = result.verification || null;

  // Inject tab bar above the narrative (Analysis | Claims).
  // Only shows the Claims tab if the LLM returned structured claims.
  buildNarrativeTabs(firstBlock, claims.length > 0);

  narrativeText.textContent = narrative;

  // Render the Claims panel (hidden behind the tab — toggle via tab bar).
  // Empty if structured output fell back to plain text.
  renderClaimsPanel(firstBlock, claims);

  // Add verification badge to the message-meta line already in the DOM.
  if (verification && verification.total > 0) {
    renderVerificationBadge(firstBlock, verification);
  }

  // Add "View source data" expandable below narrative + claims.
  if (contextSent) renderDataUsedPanel(firstBlock, contextSent);

  addCopyButton(firstBlock, narrative);
  followupFab.hidden = false; // show the follow-up FAB
}


// ── Narrative Tabs ────────────────────────────────────────────
// Builds "Analysis | Claims (N)" tab bar and inserts it before
// #narrativeText inside the firstMessage block.
// Tab clicks toggle between narrativeText and .claims-panel.

function buildNarrativeTabs(block, hasClaims) {
  // Remove any existing tabs from a previous run
  block.querySelector('.narrative-tabs')?.remove();

  const tabs = document.createElement('div');
  tabs.className = 'narrative-tabs';

  // Tab 1 — Analysis (always shown, active by default)
  const analysisTab = document.createElement('button');
  analysisTab.className = 'narrative-tab active';
  analysisTab.textContent = 'Analysis';
  analysisTab.dataset.panel = 'narrative';

  // Tab 2 — Claims (only shown when structured output succeeded)
  const claimsTab = document.createElement('button');
  claimsTab.className = 'narrative-tab' + (hasClaims ? '' : ' tab-disabled');
  claimsTab.dataset.panel = 'claims';
  // Label is set in renderClaimsPanel once we know the count

  tabs.appendChild(analysisTab);
  tabs.appendChild(claimsTab);

  // Insert tab bar immediately before #narrativeText
  block.insertBefore(tabs, narrativeText);

  // Tab click handler — toggles between the two panels
  tabs.addEventListener('click', (e) => {
    const btn = e.target.closest('.narrative-tab');
    if (!btn || btn.classList.contains('tab-disabled')) return;

    tabs.querySelectorAll('.narrative-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');

    const showClaims = btn.dataset.panel === 'claims';
    narrativeText.hidden   = showClaims;
    const claimsPanel = block.querySelector('.claims-panel');
    if (claimsPanel) claimsPanel.hidden = !showClaims;
  });
}


// ── Claims Panel ──────────────────────────────────────────────
// Renders a card for each structured claim returned by the LLM.
// Inserted after #narrativeText, hidden by default (shown via tab click).
// Each card shows: the cited sentence, source field, and cited value.

function renderClaimsPanel(block, claims) {
  // Remove any existing claims panel
  block.querySelector('.claims-panel')?.remove();

  const panel = document.createElement('div');
  panel.className = 'claims-panel';
  panel.hidden = true; // hidden until "Claims" tab is clicked

  // Update the Claims tab label with the count now that we have it
  const claimsTab = block.querySelector('.narrative-tab[data-panel="claims"]');
  if (claimsTab) {
    if (claims.length > 0) {
      claimsTab.textContent = `Claims (${claims.length})`;
    } else {
      claimsTab.textContent = 'Claims';
      claimsTab.title = 'Structured claims not available — LLM used plain text mode';
    }
  }

  if (claims.length === 0) {
    // Structured output fell back — show a helpful note
    const empty = document.createElement('div');
    empty.className = 'claims-empty';
    empty.textContent = 'Structured claims are not available for this response. The LLM returned plain text output.';
    panel.appendChild(empty);
  } else {
    claims.forEach((claim, i) => {
      const card = document.createElement('div');
      card.className = 'claim-card';
      card.style.animationDelay = `${i * 40}ms`;
      card.innerHTML = `
        <div class="claim-sentence">"${escapeHtml(claim.sentence)}"</div>
        <div class="claim-meta">
          <span class="claim-source-label">Source</span>
          <span class="claim-source">${escapeHtml(claim.source_field || '—')}</span>
          <span class="claim-divider">·</span>
          <span class="claim-value mono">${escapeHtml(claim.cited_value || '—')}</span>
        </div>`;
      panel.appendChild(card);
    });
  }

  // Insert immediately after #narrativeText
  narrativeText.insertAdjacentElement('afterend', panel);
}


// ── Verification Badge ────────────────────────────────────────
// Adds a small badge to the .message-meta line showing how many
// numbers in the narrative were found in the source data.
// Green = all verified. Amber = some unverified (calculated/inferred).

function renderVerificationBadge(block, verification) {
  // Remove any existing badge
  block.querySelector('.verification-badge')?.remove();

  const meta = block.querySelector('.message-meta');
  if (!meta) return;

  const badge = document.createElement('span');
  badge.className = 'verification-badge ' + (verification.all_clear ? 'verified' : 'unverified');

  if (verification.all_clear) {
    badge.textContent = `${verification.total} figures · all in source data`;
    badge.title = 'Every number in the narrative was found in the data sent to the AI.';
  } else {
    badge.textContent = `${verification.unverified_count} of ${verification.total} figures not in source data`;
    badge.title =
      'These figures may be calculated (e.g. weighted averages) or inferred.\n' +
      'Unverified: ' + verification.unverified.join(', ');
  }

  meta.appendChild(badge);
}


// ── Data Used Panel ───────────────────────────────────────────
// Collapsible section below the narrative showing the exact context
// string that was sent to the LLM. Allows the user to see exactly
// what data the AI had access to when generating the narrative.

function renderDataUsedPanel(block, contextSent) {
  // Remove any existing panel
  block.querySelector('.data-used-panel')?.remove();

  const panel = document.createElement('div');
  panel.className = 'data-used-panel';

  const toggle = document.createElement('button');
  toggle.className = 'data-used-toggle';
  toggle.innerHTML = 'View source data sent to AI <span class="data-used-arrow">↓</span>';

  const content = document.createElement('pre');
  content.className = 'data-used-content';
  content.textContent = contextSent;
  content.hidden = true;

  toggle.addEventListener('click', () => {
    const isOpen = !content.hidden;
    content.hidden = isOpen;
    toggle.querySelector('.data-used-arrow').textContent = isOpen ? '↓' : '↑';
    toggle.classList.toggle('open', !isOpen);
  });

  panel.appendChild(toggle);
  panel.appendChild(content);

  // Insert the panel after the claims panel (or after narrativeText
  // if no claims panel exists), before the copy button which is added last
  const claimsPanel = block.querySelector('.claims-panel');
  const insertAfter  = claimsPanel || narrativeText;
  insertAfter.insertAdjacentElement('afterend', panel);
}


// ── Typewriter (unused — kept for reference) ──────────────────
// Previously used to animate narrative text character-by-character.
// Removed in favour of immediate text dump. Safe to delete entirely
// when merging — nothing calls this function.
function typewrite(el, text, speed = 2, onComplete) {
  el.textContent = '';
  const cursor = document.createElement('span');
  cursor.className = 'narrative-cursor';
  el.appendChild(cursor);

  let i = 0;
  function tick() {
    if (i < text.length) {
      cursor.insertAdjacentText('beforebegin', text[i++]);
      setTimeout(tick, speed);
    } else {
      cursor.remove();
      if (onComplete) onComplete();
    }
  }
  tick();
}


// ── Copy Button ───────────────────────────────────────────────
// Appended to each message block (first response + every follow-up).
// On click: copies plain text to clipboard, shows "Copied" for 2s then resets.
// Keep as-is after merge — no changes needed.

function addCopyButton(block, text) {
  const actions = document.createElement('div');
  actions.className = 'message-actions fade-in';
  actions.innerHTML = `
    <button class="copy-btn">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
      </svg>
      Copy response
    </button>`;
  block.appendChild(actions);

  actions.querySelector('.copy-btn').addEventListener('click', async function () {
    await navigator.clipboard.writeText(text);
    this.classList.add('copied');
    this.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="20 6 9 17 4 12"/>
      </svg>
      Copied`;
    setTimeout(() => {
      this.classList.remove('copied');
      this.innerHTML = `
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
        </svg>
        Copy response`;
    }, 2000);
  });
}


// ── Metrics ───────────────────────────────────────────────────
// Two functions:
//   updateMetrics() — called on follow-up turns if new metrics are returned.
//                     Flashes the data panel and shows an "Updated" timestamp.
//   renderMetrics() — fully replaces the tile grid from a metrics object.
//                     Called on first load and on each follow-up that returns metrics.
//
// The tile schema the backend must return:
//   {
//     "Section Name": [
//       { "label": "...", "value": "...", "delta": "...", "sentiment": "positive|negative|warning|neutral" }
//     ]
//   }
//
// Keep both functions as-is after merge — they are fully dynamic and need
// no changes regardless of what fields your pipeline returns.

function updateMetrics(metrics, timeStr) {
  const hasMetrics = metrics && typeof metrics === 'object' && !Array.isArray(metrics) && Object.keys(metrics).length > 0;
  if (!hasMetrics) return;

  renderMetrics(metrics);

  // Flash the data column to signal an update
  const dataCol = document.querySelector('.results-data');
  dataCol.classList.remove('metrics-updated');
  void dataCol.offsetWidth; // reflow to re-trigger flash animation
  dataCol.classList.add('metrics-updated');

  // Show "Updated · HH:MM" in data column header
  const updateTime = document.getElementById('metricsUpdateTime');
  updateTime.textContent = `Updated · ${timeStr}`;
  updateTime.hidden = false;
}

function renderMetrics(metrics) {
  metricGrid.innerHTML = '';
  metricCardIndex = 0;

  if (typeof metrics !== 'object' || Array.isArray(metrics)) {
    metricGrid.innerHTML = '<div style="padding:12px;font-size:0.8rem;color:var(--text-tertiary);">No data available.</div>';
    return;
  }

  // Detect whether metrics uses grouped sections (nested objects) or flat key-value pairs.
  // Both formats are supported — the backend can return either.
  const entries  = Object.entries(metrics);
  const isGrouped = entries.some(([, v]) => typeof v === 'object' && v !== null && !Array.isArray(v));

  if (isGrouped) {
    // Grouped: each top-level key is a section header, value is an array of metric objects
    entries.forEach(([groupKey, groupVal]) => {
      if (typeof groupVal === 'object' && !Array.isArray(groupVal)) {
        addSectionHeader(groupKey);
        Object.entries(groupVal).forEach(([k, v]) => addMetricCard(k, v));
      } else {
        addMetricCard(groupKey, groupVal);
      }
    });
  } else {
    // Flat: each entry is a label → value pair, rendered without section headers
    entries.forEach(([k, v]) => addMetricCard(k, v));
  }
}

function addSectionHeader(label) {
  const el = document.createElement('div');
  el.className = 'metric-section-header';
  el.textContent = formatKey(label);
  metricGrid.appendChild(el);
}

let metricCardIndex = 0; // used to stagger tile animation delays

function addMetricCard(key, value) {
  const card = document.createElement('div');
  card.className = 'metric-card';
  card.style.animationDelay = `${metricCardIndex * 60}ms`; // stagger in
  metricCardIndex++;

  const keyEl = document.createElement('div');
  keyEl.className = 'metric-key';
  keyEl.textContent = formatKey(key);

  const valEl = document.createElement('div');
  valEl.className = 'metric-value';

  const raw = String(value);
  valEl.textContent = raw;

  // Color positive/negative values based on sign or percent direction
  if (raw.startsWith('+') || (raw.includes('%') && parseFloat(raw) > 0 && !raw.startsWith('-'))) {
    valEl.classList.add('positive'); // green
  } else if (raw.startsWith('-') || (raw.includes('%') && parseFloat(raw) < 0)) {
    valEl.classList.add('negative'); // red
  }

  card.appendChild(keyEl);
  card.appendChild(valEl);
  metricGrid.appendChild(card);
}

// Format snake_case and camelCase keys into Title Case for display
function formatKey(k) {
  return k
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, c => c.toUpperCase());
}


// ── New Analysis ──────────────────────────────────────────────
// Resets to the input panel. Does NOT clear selectedFile — the user
// can immediately re-run against the same file with a new question.

newAnalysisBtn.addEventListener('click', () => {
  resultsPanel.hidden = true;
  loadingPanel.hidden = true;
  followupFab.hidden  = true;
  inputPanel.hidden   = false;
  inputPanel.classList.add('fade-in');
});


// ── Follow-up FAB ─────────────────────────────────────────────
// Fixed + button in the bottom-right corner. Appears after the first
// response is rendered. Toggles a follow-up input at the bottom of the
// narrative thread. Icon rotates 45° (→ ×) when the input is open.
//
// Follow-up calls the same POST /upload endpoint with the same selectedFile.
// Keep as-is after merge — no changes needed here.

followupFab.addEventListener('click', () => {
  const existing = document.getElementById('followupInput');
  if (existing) {
    // Already open — close it
    existing.remove();
    followupFab.classList.remove('open');
    return;
  }

  followupFab.classList.add('open'); // rotates icon to ×

  const inputEl = buildFollowupInput();
  messageThread.appendChild(inputEl);
  inputEl.classList.add('slide-down');

  const ta = inputEl.querySelector('.followup-textarea');
  ta.focus();

  // Auto-grow textarea
  ta.addEventListener('input', () => {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  });

  inputEl.querySelector('.followup-send').addEventListener('click', () => {
    const q = ta.value.trim();
    if (!q) return;
    submitFollowup(q, inputEl);
  });

  // Cmd/Ctrl+Enter to submit
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      const q = ta.value.trim();
      if (q) submitFollowup(q, inputEl);
    }
  });

  setTimeout(() => inputEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
});

// Builds the follow-up input DOM element (textarea + Send button)
function buildFollowupInput() {
  const wrap = document.createElement('div');
  wrap.className = 'followup-input';
  wrap.id = 'followupInput';
  wrap.innerHTML = `
    <div class="followup-chat-area">
      <textarea class="followup-textarea" placeholder="Ask a follow-up question about this file…" rows="2"></textarea>
      <div class="followup-toolbar">
        <button class="btn btn-primary btn-sm followup-send">
          Send
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
          </svg>
        </button>
      </div>
    </div>`;
  return wrap;
}

// Submits a follow-up question.
// Appends a thread divider, thinking dots, then the response block.
// If the response includes metrics, updates the data snapshot panel.
async function submitFollowup(question, inputEl) {
  inputEl.remove();
  followupFab.classList.remove('open');

  // Thread divider
  const divider = document.createElement('div');
  divider.className = 'thread-divider';
  divider.textContent = 'Follow-up';
  messageThread.appendChild(divider);

  // Thinking animation while API is in-flight
  const dots = document.createElement('div');
  dots.className = 'thinking-dots';
  dots.innerHTML = '<span class="thinking-dot"></span><span class="thinking-dot"></span><span class="thinking-dot"></span>';
  messageThread.appendChild(dots);
  dots.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  // Reuse the same file — no re-upload needed
  // Note: mode is not sent on follow-ups. The server may need to handle this
  // differently depending on whether follow-ups should re-run the pipeline
  // or only call the LLM with the existing context.
  const formData = new FormData();
  formData.append('file', selectedFile);
  formData.append('prompt', question);

  const result = await fetch('/upload', { method: 'POST', body: formData })
    .then(r => r.json())
    .catch(() => null); // null triggers getMockResult() fallback below — REMOVE ON MERGE

  dots.remove();

  // Render follow-up response block
  const block = document.createElement('div');
  block.className = 'message-block slide-down';

  const now     = new Date();
  const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  block.innerHTML = `
    <div class="message-meta">
      <span class="message-label">${escapeHtml(question.length > 48 ? question.slice(0, 48) + '…' : question)}</span>
      <span class="message-time mono">${timeStr}</span>
    </div>
    <div class="narrative-text"></div>`;

  messageThread.appendChild(block);
  block.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // TODO (REMOVE ON MERGE): Delete getMockResult().narrative fallback once
  // the real /upload route returns live data.
  const narrative = result?.narrative || getMockResult().narrative;

  // If the follow-up response includes updated metrics, re-render the data panel
  if (result?.metrics) updateMetrics(result.metrics, timeStr);

  block.querySelector('.narrative-text').textContent = narrative;
  addCopyButton(block, narrative);
  followupFab.hidden = false;
}

// Sanitise strings before inserting into innerHTML
function escapeHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}


// ── Mock Data ─────────────────────────────────────────────────
// TODO (REMOVE ON MERGE): Delete this entire function once the real
// POST /upload route is live and returning data.
//
// Purpose: provides realistic fallback data so the full UI can be
// demoed and tested without a running backend. Used in two places:
//   1. runAnalysis()    — `apiResult || getMockResult()`
//   2. submitFollowup() — `result?.narrative || getMockResult().narrative`
// Both fallbacks should be removed at the same time as this function.

function getMockResult() {
  return {
    narrative: `The portfolio demonstrates moderate resilience across most segments, though several indicators warrant close monitoring heading into Q2.

Total outstanding balances grew 3.2% quarter-over-quarter, largely driven by expansion in the prime and near-prime tiers. However, this growth has come alongside a 40 basis point uptick in the 60+ day delinquency rate, now sitting at 4.1% — the highest level in six quarters.

The commercial segment continues to outperform. Net charge-off rates remain below the 1.5% threshold, and weighted average FICO scores have improved modestly to 698. By contrast, the indirect auto book is showing stress: 90+ day delinquencies climbed to 2.8%, and early-stage roll rates suggest further deterioration is likely in the near term.

Vintage performance analysis reveals that the 2023 Q3 and Q4 cohorts are tracking approximately 15% worse than their 2022 equivalents at the same seasoning point. This divergence aligns with the rate environment at origination and points to tightening as a corrective measure.

Reserve coverage sits at 2.3x projected net charge-offs, which provides adequate buffer under base-case assumptions. Under a mild stress scenario — unemployment rising 100bps — coverage would compress to approximately 1.7x, still within acceptable range.

Recommended actions: (1) tighten origination criteria for indirect auto below 660 FICO, (2) flag 2023-vintage accounts for proactive outreach, and (3) revisit loss reserve adequacy at the next ALLL committee meeting.`,

    metrics: {
      portfolio_overview: {
        total_outstanding: '$4.82B',
        qoq_growth: '+3.2%',
        active_accounts: '142,700',
        avg_balance: '$33,800',
      },
      credit_quality: {
        wtd_avg_fico: '698',
        '30_day_delinquency': '6.8%',
        '60_day_delinquency': '4.1%',
        '90_day_delinquency': '2.3%',
      },
      loss_metrics: {
        net_charge_off_rate: '1.48%',
        gross_charge_off_rate: '2.10%',
        recovery_rate: '29.5%',
        reserve_coverage: '2.3x',
      },
    }
  };
}
