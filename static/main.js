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
 *   Mock Data         — getMockResult() + USE_MOCK_RESULTS demo toggle
 *
 * ── DEMO MODE ────────────────────────────────────────────────
 * Set USE_MOCK_RESULTS = true (below) to bypass the Flask backend
 * and render hard-coded results from getMockResult(). Intended for
 * presentations / demos when a live backend isn't available.
 * Default is false: the app talks to POST /upload and surfaces any
 * error (network, server, validation) instead of silently faking it.
 * ────────────────────────────────────────────────────────────── */


// ── Demo mode toggle ──────────────────────────────────────────
// Flip to true to render getMockResult() instead of calling the
// backend. Used for UI demos without a live Flask server.
const USE_MOCK_RESULTS = false;


// ── Session ID ────────────────────────────────────────────────
// Per-tab UUID generated once and stored in sessionStorage. Sent on
// every fetch as X-Kronos-Session so server-side error records can
// be correlated across an upload + its follow-ups. Not auth, not
// identity — pure correlation for triage.
const KRONOS_SESSION_HEADER = 'X-Kronos-Session';

function getSessionId() {
  try {
    let sid = sessionStorage.getItem('kronos.session_id');
    if (!sid) {
      // crypto.randomUUID() is available in all modern browsers; fall
      // back to a timestamp+random hex for ancient ones so we still
      // produce a traceable token rather than sending nothing.
      sid = (crypto && typeof crypto.randomUUID === 'function')
        ? crypto.randomUUID()
        : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
      sessionStorage.setItem('kronos.session_id', sid);
    }
    return sid;
  } catch {
    // sessionStorage disabled (private mode, some sandboxes). Fall back
    // to a per-page-load token — correlation across follow-ups in the
    // same page survives, it just won't match across reloads.
    if (!window.__kronosSessionId) {
      window.__kronosSessionId = (crypto && typeof crypto.randomUUID === 'function')
        ? crypto.randomUUID()
        : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
    }
    return window.__kronosSessionId;
  }
}


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
const lengthToggle    = document.getElementById('lengthToggle');    // 3-button length radiogroup
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
const cancelBtn       = document.getElementById('cancelBtn');        // Cancel button on transition screen
const inlineNotice    = document.getElementById('inlineNotice');     // transient notice on input panel


// ── State ─────────────────────────────────────────────────────
// These three variables persist across the full session.
// selectedFile is intentionally NOT cleared on New Analysis —
// allows the user to re-run against the same file without re-uploading.

let selectedFile    = null;  // File object from the last attach action
let activeCannedBtn = null;  // Currently selected quick-analysis button element
let activeMode      = null;  // Mode slug from /modes (e.g. "firm-level").
                             // Sent to server as 'mode' on /upload to route the
                             // correct slicer. Null for custom free-form questions.
let activeParameters = {};   // Validated parameter values for the active mode.
                             // Populated by the (future) parameter picker UI for
                             // parameterized modes. Sent to /upload as 'parameters'.
let activeLength    = 'full'; // Request-level length directive — one of
                             // 'full' | 'executive' | 'distillation'. Bound to
                             // the .length-toggle radiogroup. Read live at
                             // submit time on both initial and follow-up
                             // /upload, so changing the toggle between turns
                             // takes effect on the next request. Default mirrors
                             // the active class set in HTML so first paint matches.

// Follow-up request lifecycle. Module-level so unrelated handlers
// ("New Analysis", runAnalysis reset) can abort an in-flight request
// instead of letting it resolve into a hidden DOM and leak state.
let followupController = null;  // AbortController for the current /upload fetch
let followupInFlight   = false; // true between submit start and resolve/error
const FOLLOWUP_TIMEOUT_MS = 60_000;

// Primary (first-pass) /upload lifecycle. Mirrors the follow-up guards
// so the Cancel button on the transition screen can abort cleanly.
// primaryCancelled is a sticky flag read after each await boundary in
// runAnalysis — the cancel handler transitions the UI itself, so runAnalysis
// just needs to short-circuit before rendering stale results.
let primaryController = null;
let primaryCancelled  = false;

// Session state preserved across follow-ups. inheritedMode /
// inheritedParameters snapshot what the first-pass ran with, so a
// follow-up on the same thread re-runs the same slicer and the verifier
// checks against identical verifiable_values. lastNarrative is the plain
// text of the most recent narrative — fed back to the LLM as a prior AI
// turn on follow-ups (plain text only, no claims or verification metadata).
let inheritedMode       = '';
let inheritedParameters = {};
let lastNarrative       = '';


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


// ── Length Toggle ────────────────────────────────────────────
// Three-button radiogroup that controls the request-level `length`
// directive. Updates `activeLength`; both /upload call sites read
// it live at submit time, so changing the toggle between turns
// applies on the next request.
lengthToggle.addEventListener('click', (e) => {
  const btn = e.target.closest('.length-btn');
  if (!btn || !lengthToggle.contains(btn)) return;
  const next = btn.dataset.length;
  if (!next || next === activeLength) return;
  activeLength = next;
  lengthToggle.querySelectorAll('.length-btn').forEach(b => {
    const isActive = b === btn;
    b.classList.toggle('active', isActive);
    b.setAttribute('aria-pressed', String(isActive));
  });
});


// ── Canned Prompts ────────────────────────────────────────────
// Buttons are rendered dynamically from GET /modes — the YAML-driven
// registry in config/modes.yaml is the source of truth. To add,
// remove, or rename a button, edit config/modes.yaml. The frontend
// has no hard-coded knowledge of which modes exist.
//
// Modes carry a `status` flag; placeholders render in a muted style
// and surface a clear "coming soon" message on click.

function attachCannedHandler(btn) {
  btn.addEventListener('click', () => {
    if (activeCannedBtn) activeCannedBtn.classList.remove('active');

    // Clicking the already-active button deselects it
    if (activeCannedBtn === btn) {
      activeCannedBtn = null;
      activeMode = null;
      activeParameters = {};
      customPrompt.value = '';
      promptHint.textContent = '';
      updateRunBtn();
      return;
    }

    activeCannedBtn = btn;
    activeMode = btn.dataset.mode || null;
    activeParameters = {};   // cleared on every mode change; future
                             // parameter picker writes back here
    btn.classList.add('active');
    customPrompt.value = btn.dataset.prompt;
    promptHint.textContent = btn.querySelector('.canned-title').textContent;
    updateRunBtn();
    customPrompt.focus();
  });
}

// Fetch /modes and build the button grid on page load
fetch(new URL('modes', document.baseURI).href, {
  headers: { [KRONOS_SESSION_HEADER]: getSessionId() },
})
  .then(r => r.json())
  .then(({ modes }) => {
    if (!Array.isArray(modes)) {
      console.warn('[KRONOS] /modes returned no modes array', modes);
      return;
    }
    modes.forEach(m => {
      const btn = document.createElement('button');
      btn.className = 'canned-btn';
      if (m.status === 'placeholder') btn.classList.add('placeholder');
      btn.dataset.prompt = m.user_prompt || '';
      btn.dataset.mode   = m.slug || '';
      btn.dataset.status = m.status || '';
      // The parameter list is JSON-stringified onto the element so the
      // future picker UI can read it without a second registry lookup.
      btn.dataset.parameters = JSON.stringify(m.parameters || []);
      btn.innerHTML =
        `<span class="canned-title">${m.display_name}</span>` +
        `<span class="canned-desc">${m.description || ''}</span>`;
      if (m.status === 'placeholder') {
        btn.title = 'Placeholder mode — backend not wired yet.';
      }
      cannedGrid.appendChild(btn);
      attachCannedHandler(btn);
    });
  })
  .catch(err => {
    // /modes unavailable — grid stays empty, user can still type a
    // custom question against the default prompt.
    console.warn('[KRONOS] /modes fetch failed; canned grid empty', err);
  });

// If the user starts editing the prompt, visually deselect the active
// canned button so the UI signals "this prompt has been edited" — but
// preserve activeMode and activeParameters so the correct slicer still
// runs at submit time. Without this, a user who picks Industry
// Portfolio Analysis and tweaks the prompt would route to
// placeholder_processor (mode=""), producing 0 verifiable_values and
// 0% verification rate. To truly clear the mode, the user can click
// the canned button again (the attachCannedHandler toggle path) or
// hit "New Analysis".
customPrompt.addEventListener('input', () => {
  if (activeCannedBtn) {
    activeCannedBtn.classList.remove('active');
    activeCannedBtn = null;
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


// ── Cancel (primary analysis) ─────────────────────────────────
// Visible on the transition screen for the entire duration of the
// analysis. On click, aborts the in-flight fetch and returns the user
// to the input panel with all of their state intact: attached file,
// selected mode, parameter values, and the prompt textarea content all
// persist (none of those are cleared by runAnalysis). A transient
// neutral notice confirms the cancellation.
function abortPrimary(reason = 'user-cancel') {
  primaryCancelled = true;
  if (primaryController) {
    try { primaryController.abort(reason); } catch { /* no-op */ }
    primaryController = null;
  }
}

function showInlineNotice(message, visibleMs = 3000, fadeMs = 300) {
  if (!inlineNotice) return;
  inlineNotice.textContent = message;
  inlineNotice.classList.remove('fade-out');
  inlineNotice.hidden = false;
  setTimeout(() => {
    inlineNotice.classList.add('fade-out');
    setTimeout(() => {
      inlineNotice.hidden = true;
      inlineNotice.classList.remove('fade-out');
      inlineNotice.textContent = '';
    }, fadeMs);
  }, visibleMs);
}

if (cancelBtn) {
  cancelBtn.addEventListener('click', () => {
    abortPrimary('user-cancel');
    loadingPanel.hidden = true;
    inputPanel.hidden = false;
    inputPanel.classList.remove('fade-in');
    void inputPanel.offsetWidth; // reflow to restart fade-in
    inputPanel.classList.add('fade-in');
    showInlineNotice('Analysis cancelled.');
  });
}


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

// Short placeholder delays — just enough visual motion to register that
// the steps are advancing. The *displayed* times (time-0…time-4) are
// overwritten with real server-measured durations once the API returns,
// so what the user reads is honest even though the animation itself is
// paced artificially during the wait.
// Step 3 delay is 0 because it blocks on the real API call.
const STEP_DELAYS = [150, 250, 100, 0, 150];

async function runAnalysis() {
  const prompt = customPrompt.value.trim();
  if (!selectedFile || !prompt) return;

  // Reset cancellation state for this run. Arm the AbortController so the
  // Cancel button can abort the fetch; armed synchronously (before any await)
  // so a very-early cancel click still finds a live controller.
  primaryCancelled = false;
  primaryController = new AbortController();
  const primarySignal = primaryController.signal;

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
  abortFollowup();
  narrativeText.textContent = '';
  document.getElementById('firstMessage').querySelector('.message-meta')?.remove();
  messageThread.querySelectorAll('.thread-divider, .thinking-dots, .message-block:not(#firstMessage), .followup-input').forEach(el => el.remove());
  followupFab.hidden = true;
  followupFab.classList.remove('open');

  // Fire the API call immediately in parallel with the loading animation.
  // The animation for steps 0–2 runs while waiting for the response.
  //
  // In demo mode we skip the network entirely so the animation can run
  // against the hard-coded mock without touching Flask.
  //
  // Transport: JSON body with base64-encoded file. We previously sent
  // multipart/form-data, but the Domino workspace proxy silently drops
  // multipart POSTs. Plain application/json passes through.
  const uploadUrl = new URL('upload', document.baseURI).href;
  console.log('[KRONOS] Submitting upload', {
    resolvedUrl: uploadUrl,
    pageBaseURI: document.baseURI,
    mode: activeMode || '(none)',
    length: activeLength,
    fileName: selectedFile?.name,
    fileSize: selectedFile?.size,
    promptLength: prompt.length,
  });

  const apiCall = USE_MOCK_RESULTS
    ? Promise.resolve(null)
    : fileToBase64(selectedFile)
        .then(file_b64 => {
          // fileToBase64 doesn't honor signal — re-check before the fetch
          // so a user-cancel during base64 encoding still bails cleanly.
          if (primarySignal.aborted) {
            throw Object.assign(new Error('aborted'), { name: 'AbortError' });
          }
          return fetch(uploadUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              [KRONOS_SESSION_HEADER]: getSessionId(),
            },
            body: JSON.stringify({
              file_name: selectedFile.name,
              file_b64,
              prompt,
              mode: activeMode || '',
              parameters: activeParameters || {},
              length: activeLength,
            }),
            signal: primarySignal,
          });
        })
        .then(async r => {
          console.log('[KRONOS] /upload response received', {
            status: r.status,
            ok: r.ok,
            type: r.type,
            url: r.url,
          });
          const data = await r.json().catch(err => {
            console.error('[KRONOS] Failed to parse /upload JSON', err);
            return null;
          });
          if (!r.ok) {
            console.error('[KRONOS] /upload returned non-OK', { status: r.status, body: data });
            return { __error: data?.error || `Server error (${r.status})` };
          }
          return data;
        })
        .catch(err => {
          if (err?.name === 'AbortError') {
            // Cancel handler has already transitioned the UI. Return a
            // sentinel so runAnalysis can short-circuit before showResults.
            return { __aborted: true };
          }
          console.error('[KRONOS] /upload fetch failed (network/CORS/proxy)', err);
          return { __error: `Network error — could not reach the server. (${err?.message || err})` };
        });

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

  // User-cancel during any stage: the cancel handler already hid the
  // loading panel and restored the input panel with its state intact.
  // Don't proceed into step 4 / showResults — just exit cleanly.
  if (primaryCancelled || apiResult?.__aborted) {
    primaryController = null;
    return;
  }

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

  // Replace wall-clock step labels with real server stage durations
  // when the backend reports them. Server returns timings_ms: { analyze,
  // llm, verify }. We split analyze across steps 0+1 (parse / pipeline),
  // show a tiny fixed value for the client-side "build prompt context"
  // step, map llm to step 3, and verify to step 4.
  const timings = apiResult?.timings_ms;
  if (timings) {
    const fmt = (ms) => (ms / 1000).toFixed(1) + 's';
    const analyzeHalf = Math.round((timings.analyze || 0) / 2);
    document.getElementById('time-0').textContent = fmt(analyzeHalf);
    document.getElementById('time-1').textContent = fmt(analyzeHalf);
    document.getElementById('time-2').textContent = fmt(50); // prompt build is near-instant
    document.getElementById('time-3').textContent = fmt(timings.llm || 0);
    document.getElementById('time-4').textContent = fmt(timings.verify || 0);
  }

  await delay(300); // brief pause before transitioning to results

  // Pick what to render:
  //   - demo mode          → hard-coded mock
  //   - real call w/ error → render the error message as the narrative
  //   - real call ok       → render the server response
  let result;
  if (USE_MOCK_RESULTS) {
    result = getMockResult();
  } else if (apiResult?.__error) {
    result = {
      narrative: `Error: ${apiResult.__error}`,
      metrics: {},
      claims: [],
    };
  } else {
    result = apiResult;
  }
  primaryController = null;
  showResults(result, t4);
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

// Reads a File as a base64 string (no data: URL prefix).
// Used to ship the upload inside a JSON body since the Domino
// workspace proxy drops multipart/form-data requests.
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = () => {
      const s = reader.result || '';
      const i = s.indexOf(',');
      resolve(i >= 0 ? s.slice(i + 1) : s);
    };
    reader.onerror = () => reject(reader.error || new Error('file read error'));
    reader.readAsDataURL(file);
  });
}


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

  // Snapshot the mode + parameters the first-pass ran with. Follow-ups
  // on this thread inherit both so the slicer re-runs identically and
  // the verifier checks against the same verifiable_values.
  inheritedMode       = activeMode || '';
  inheritedParameters = { ...(activeParameters || {}) };
  lastNarrative       = narrative;

  // Inject tab bar above the narrative (Analysis | Claims).
  // Only shows the Claims tab if the LLM returned structured claims.
  buildNarrativeTabs(firstBlock, claims.length > 0);

  narrativeText.textContent = narrative;

  // Render the Claims panel (hidden behind the tab — toggle via tab bar).
  // Empty if structured output fell back to plain text.
  renderClaimsPanel(firstBlock, claims, verification);

  // Add verification badge to the message-meta line already in the DOM.
  if (verification && verification.total > 0) {
    renderVerificationBadge(firstBlock, verification);
  }

  // Add "View source data" expandable below narrative + claims.
  if (contextSent) renderDataUsedPanel(firstBlock, contextSent);

  addCopyButton(firstBlock, narrative);
  followupFab.classList.remove('open'); // ensure the FAB starts as +, not ×
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
// Renders a card for each structured claim returned by the LLM,
// with a per-claim verification badge (verified / unverified / mismatch).
// Inserted after #narrativeText, hidden by default (shown via tab click).

function renderClaimsPanel(block, claims, verification) {
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

  // Per-claim verification results from the server, keyed by index.
  // claim_results[i] = { claim_index, status, reason, expected, actual }.
  const claimResults = (verification && Array.isArray(verification.claim_results))
    ? verification.claim_results
    : [];
  const resultByIndex = Object.create(null);
  claimResults.forEach(r => { resultByIndex[r.claim_index] = r; });

  if (claims.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'claims-empty';
    empty.textContent = 'Structured claims are not available for this response. The LLM returned plain text output.';
    panel.appendChild(empty);
  } else {
    claims.forEach((claim, i) => {
      const r = resultByIndex[i];
      const status = r?.status || 'unverified';
      const card = document.createElement('div');
      card.className = 'claim-card claim-' + status;
      card.style.animationDelay = `${i * 40}ms`;

      // Tooltip explains expected vs actual on hover, and the reason code.
      const reason = r?.reason ? ` (${r.reason})` : '';
      const expected = r?.expected ? `\nExpected: ${r.expected}` : '';
      const actual = r?.actual ? `\nCited: ${r.actual}` : '';
      const tip = `Status: ${status}${reason}${expected}${actual}`;

      card.innerHTML = `
        <div class="claim-header">
          <span class="claim-status claim-status-${status}" title="${escapeHtml(tip)}">${statusLabel(status)}</span>
          <span class="claim-status-reason" title="${escapeHtml(tip)}">${r?.reason ? escapeHtml(r.reason) : ''}</span>
        </div>
        <div class="claim-sentence">"${escapeHtml(claim.sentence)}"</div>
        <div class="claim-meta">
          <span class="claim-source-label">Source</span>
          <span class="claim-source">${escapeHtml(claim.source_field || '—')}</span>
          <span class="claim-divider">·</span>
          <span class="claim-value mono">${escapeHtml(claim.cited_value || '—')}</span>
          ${r?.expected && status !== 'verified'
            ? `<span class="claim-divider">·</span><span class="claim-expected mono" title="Value in the source data">expected ${escapeHtml(r.expected)}</span>`
            : ''}
        </div>`;
      panel.appendChild(card);
    });
  }

  narrativeText.insertAdjacentElement('afterend', panel);
}

// Short human-readable label for a claim verification status
function statusLabel(status) {
  if (status === 'verified')  return '✓ Verified';
  if (status === 'mismatch')  return '✕ Mismatch';
  return '⚠ Unverified';
}


// ── Verification Badge ────────────────────────────────────────
// Adds a small badge to the .message-meta line summarizing the
// claim-based verification result.
//   Green  — all claims verified
//   Red    — at least one claim mismatches the source data
//   Amber  — some claims unverified (calculated / field not in catalog)
//   Grey   — no structured claims produced

function renderVerificationBadge(block, verification) {
  block.querySelector('.verification-badge')?.remove();

  const meta = block.querySelector('.message-meta');
  if (!meta || !verification) return;

  const total      = verification.total || 0;
  const verified   = verification.verified_count   || 0;
  const unverified = verification.unverified_count || 0;
  const mismatches = verification.mismatch_count   || 0;
  const notes      = Array.isArray(verification.notes) ? verification.notes : [];

  const badge = document.createElement('span');

  // Tone: mismatch beats unverified beats clear.
  let tone;
  if (total === 0)          tone = 'none';
  else if (mismatches > 0)  tone = 'mismatch';
  else if (verification.all_clear) tone = 'verified';
  else                      tone = 'unverified';
  badge.className = 'verification-badge tone-' + tone;

  // Headline text.
  if (total === 0) {
    badge.textContent = 'No structured claims';
  } else if (tone === 'verified') {
    badge.textContent = `${total} claim${total === 1 ? '' : 's'} · all verified`;
  } else {
    const parts = [];
    if (mismatches) parts.push(`${mismatches} mismatch${mismatches === 1 ? '' : 'es'}`);
    if (unverified) parts.push(`${unverified} unverified`);
    badge.textContent = `${verified} of ${total} verified · ${parts.join(', ')}`;
  }

  // Tooltip: list each non-verified claim with its reason.
  const claimResults = Array.isArray(verification.claim_results)
    ? verification.claim_results
    : [];
  const flagged = claimResults.filter(c => c.status !== 'verified');

  const tipLines = [];
  if (tone === 'verified') {
    tipLines.push('Every claim in the narrative matched the source data.');
  } else if (tone === 'none') {
    tipLines.push('The LLM did not return structured claims for this response.');
  } else {
    tipLines.push('Claims flagged for review:');
    flagged.slice(0, 8).forEach(c => {
      const label = c.status === 'mismatch' ? '✕' : '⚠';
      const reason = c.reason ? ` (${c.reason})` : '';
      tipLines.push(`${label} ${c.status}${reason}`);
    });
    if (flagged.length > 8) tipLines.push(`…and ${flagged.length - 8} more`);
  }
  if (notes.length) tipLines.push('Notes: ' + notes.join(', '));
  badge.title = tipLines.join('\n');

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

  // Three accepted shapes from the backend:
  //   1) Section → array of tile objects: { "Section": [{ label, value, sentiment, delta }, ...] }
  //      This is the documented response shape (see CLAUDE.md) and what firm_level.py returns.
  //   2) Section → nested k/v object:     { "Section": { "Key": "Value", ... } }
  //   3) Flat key/value pairs:            { "Key": "Value", ... }
  const entries    = Object.entries(metrics);
  const isSectioned = entries.some(
    ([, v]) => Array.isArray(v) || (typeof v === 'object' && v !== null)
  );

  if (isSectioned) {
    entries.forEach(([groupKey, groupVal]) => {
      if (Array.isArray(groupVal)) {
        addSectionHeader(groupKey);
        groupVal.forEach(tile => addMetricCard(tile.label, tile.value, tile));
      } else if (groupVal && typeof groupVal === 'object') {
        addSectionHeader(groupKey);
        Object.entries(groupVal).forEach(([k, v]) => addMetricCard(k, v));
      } else {
        addMetricCard(groupKey, groupVal);
      }
    });
  } else {
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

function addMetricCard(key, value, tile = null) {
  const card = document.createElement('div');
  card.className = 'metric-card';
  card.style.animationDelay = `${metricCardIndex * 60}ms`; // stagger in
  metricCardIndex++;

  const keyEl = document.createElement('div');
  keyEl.className = 'metric-key';
  keyEl.textContent = formatKey(key);

  const valEl = document.createElement('div');
  valEl.className = 'metric-value';

  const raw = value == null ? '' : String(value);
  valEl.textContent = raw;

  // Sentiment from the tile object wins over heuristic sign/percent detection.
  const sentiment = tile && tile.sentiment;
  if (sentiment === 'positive') {
    valEl.classList.add('positive');
  } else if (sentiment === 'negative') {
    valEl.classList.add('negative');
  } else if (sentiment === 'warning') {
    valEl.classList.add('warning');
  } else if (!sentiment) {
    if (raw.startsWith('+') || (raw.includes('%') && parseFloat(raw) > 0 && !raw.startsWith('-'))) {
      valEl.classList.add('positive');
    } else if (raw.startsWith('-') || (raw.includes('%') && parseFloat(raw) < 0)) {
      valEl.classList.add('negative');
    }
  }

  card.appendChild(keyEl);
  card.appendChild(valEl);

  if (tile && tile.delta) {
    const deltaEl = document.createElement('div');
    deltaEl.className = 'metric-delta';
    deltaEl.textContent = tile.delta;
    card.appendChild(deltaEl);
  }

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
  abortFollowup();
  // Drop the inherited-session snapshot — the next first-pass will
  // set it fresh in showResults() against whatever mode/parameters
  // the user picks for that run.
  inheritedMode = '';
  inheritedParameters = {};
  lastNarrative = '';
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
    // Already open — close it. If the user has typed a draft, confirm
    // before discarding (closing nukes the textarea contents along with
    // the element).
    const draft = existing.querySelector('.followup-textarea')?.value.trim();
    if (draft && !window.confirm('Discard your follow-up draft?')) return;
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
    if (!q) { flashEmpty(ta); return; }
    submitFollowup(q, inputEl);
  });

  // Cmd/Ctrl+Enter to submit; Esc to close (mirrors FAB-click logic).
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      const q = ta.value.trim();
      if (!q) { flashEmpty(ta); return; }
      submitFollowup(q, inputEl);
    } else if (e.key === 'Escape') {
      const draft = ta.value.trim();
      if (draft && !window.confirm('Discard your follow-up draft?')) return;
      inputEl.remove();
      followupFab.classList.remove('open');
      followupFab.focus();
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

// Aborts any in-flight follow-up fetch and clears the lifecycle flags.
// Safe to call when nothing is in flight — does nothing.
// Called by: runAnalysis() (new run), newAnalysisBtn (reset to input).
function abortFollowup() {
  if (followupController) {
    followupController.abort();
    followupController = null;
  }
  followupInFlight = false;
  followupFab.classList.remove('open');
}

// Submits a follow-up question.
// Interaction states: idle → submitting → (rendering | error | aborted).
//   submitting: input disabled, FAB hidden, dots visible, AbortController armed
//   rendering:  input removed, response block appended
//   error:      input restored, inline error row with Retry
//   aborted:    parent reset (New Analysis / runAnalysis) — bail silently
//
// Reentrancy: a followupInFlight guard prevents double-submits (e.g. a
// second Cmd/Enter while readonly, since keydown still fires on readonly
// textareas). A 60s timeout aborts the fetch so a hung proxy can't pin
// the UI in the submitting state forever.
async function submitFollowup(question, inputEl) {
  if (followupInFlight) return;            // ignore double-submits

  // Defensive guard: selectedFile should always be set on this path
  // (the FAB only appears after a successful first run), but route to
  // the error UX rather than throwing a confusing TypeError if the
  // module-level reference has been cleared.
  if (!selectedFile) {
    showFollowupError(inputEl, 'Something went wrong with the follow-up. Please start a new analysis.');
    return;
  }

  followupInFlight = true;

  const textarea   = inputEl.querySelector('.followup-textarea');
  const sendBtn    = inputEl.querySelector('.followup-send');
  const oldError   = inputEl.querySelector('.followup-error');
  if (oldError) oldError.remove();

  // ── submitting state ────────────────────────────────────────
  textarea.readOnly   = true;
  sendBtn.disabled    = true;
  sendBtn.setAttribute('aria-busy', 'true');
  const originalSend  = sendBtn.innerHTML;
  sendBtn.innerHTML   = '<span class="followup-spinner" aria-hidden="true"></span>Sending…';
  followupFab.hidden  = true; // prevent a second follow-up mid-request

  // Thread divider + thinking dots
  const divider = document.createElement('div');
  divider.className = 'thread-divider';
  divider.textContent = 'Follow-up';
  messageThread.appendChild(divider);

  const dots = document.createElement('div');
  dots.className = 'thinking-dots';
  dots.innerHTML = '<span class="thinking-dot"></span><span class="thinking-dot"></span><span class="thinking-dot"></span>';
  messageThread.appendChild(dots);
  dots.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  // Reuse the same file — still re-uploaded on each follow-up since the
  // server is stateless. If proxy payload size ever becomes a concern, we
  // can add a session-cache on the server and send just a file id.
  //
  // Follow-ups inherit the mode + parameters from the first pass on this
  // thread and send the prior narrative so the LLM has conversational
  // context. The slicer re-runs server-side → verifier checks against the
  // same verifiable_values as the first pass.

  // Fetch with typed outcome. In demo mode we skip the network and
  // reuse the mock narrative after a short fake think.
  let result   = null;
  let errorMsg = null;
  let aborted  = false;
  if (USE_MOCK_RESULTS) {
    await delay(1200);
    result = getMockResult();
  } else {
    const uploadUrl = new URL('upload', document.baseURI).href;
    console.log('[KRONOS] Submitting follow-up', {
      resolvedUrl: uploadUrl,
      pageBaseURI: document.baseURI,
      inheritedMode: inheritedMode || '(none)',
      inheritedParameters,
      length: activeLength,
      hasPriorNarrative: !!lastNarrative,
      fileName: selectedFile?.name,
      promptLength: question.length,
    });
    followupController = new AbortController();
    const signal = followupController.signal;
    const timeoutId = setTimeout(() => {
      // Distinguish a user/parent abort (no controller anymore) from a
      // timeout abort, so the catch block can show a useful message.
      if (followupController) followupController.abort('timeout');
    }, FOLLOWUP_TIMEOUT_MS);
    try {
      const file_b64 = await fileToBase64(selectedFile);
      // fileToBase64 doesn't honor signal — re-check before the fetch
      // so a parent-reset during base64 encoding still bails cleanly.
      if (signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      const res = await fetch(uploadUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          [KRONOS_SESSION_HEADER]: getSessionId(),
        },
        body: JSON.stringify({
          file_name: selectedFile.name,
          file_b64,
          prompt: question,
          mode: inheritedMode,
          parameters: inheritedParameters,
          prior_narrative: lastNarrative,
          length: activeLength,
        }),
        signal: followupController.signal,
      });
      console.log('[KRONOS] follow-up /upload response received', {
        status: res.status,
        ok: res.ok,
        type: res.type,
        url: res.url,
      });
      result = await res.json().catch(err => {
        console.error('[KRONOS] Failed to parse follow-up JSON', err);
        return null;
      });
      if (!res.ok) {
        console.error('[KRONOS] follow-up /upload returned non-OK', { status: res.status, body: result });
        errorMsg = result?.error || `Server error (${res.status})`;
      }
    } catch (err) {
      if (err?.name === 'AbortError') {
        // Timeout fires the abort with reason='timeout'. A parent reset
        // (New Analysis / runAnalysis) calls abortFollowup() with no
        // reason — in that case the DOM is already gone, so bail silent.
        if (followupController?.signal?.reason === 'timeout') {
          errorMsg = 'Request timed out after 60s. The server may be busy — try again.';
        } else {
          aborted = true;
        }
      } else {
        console.error('[KRONOS] follow-up /upload fetch failed (network/CORS/proxy)', err);
        errorMsg = `Network error — could not reach the server. (${err?.message || err})`;
      }
    } finally {
      clearTimeout(timeoutId);
      followupController = null;
    }
  }

  followupInFlight = false;

  // ── aborted (parent reset) ──────────────────────────────────
  // The thread DOM has already been wiped by runAnalysis() / newAnalysisBtn.
  // Don't touch divider/dots/inputEl — they're detached. Just return.
  if (aborted) return;

  // ── error state ─────────────────────────────────────────────
  if (errorMsg) {
    divider.remove();
    dots.remove();
    textarea.readOnly = false;
    sendBtn.disabled  = false;
    sendBtn.removeAttribute('aria-busy');
    sendBtn.innerHTML = originalSend;
    followupFab.hidden = false;
    showFollowupError(inputEl, errorMsg);
    textarea.focus();
    return;
  }

  // ── rendering state ─────────────────────────────────────────
  dots.remove();
  inputEl.remove();
  followupFab.classList.remove('open');

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

  const narrative = result?.narrative || '';

  // If the follow-up response includes updated metrics, re-render the data panel
  if (result?.metrics) updateMetrics(result.metrics, timeStr);

  block.querySelector('.narrative-text').textContent = narrative;
  addCopyButton(block, narrative);
  followupFab.hidden = false;

  // Advance the conversation snapshot — the next follow-up sees THIS
  // narrative as the prior AI turn.
  if (narrative) lastNarrative = narrative;
}

// Inline error row above the textarea. Retry resubmits the current
// textarea value (so the user can edit before retrying).
function showFollowupError(inputEl, message) {
  const chatArea = inputEl.querySelector('.followup-chat-area');
  const err = document.createElement('div');
  err.className = 'followup-error';
  err.setAttribute('role', 'alert');
  err.innerHTML = `
    <span class="followup-error-msg">${escapeHtml(message)}</span>
    <button type="button" class="followup-retry">Retry</button>`;
  err.querySelector('.followup-retry').addEventListener('click', () => {
    const q = inputEl.querySelector('.followup-textarea').value.trim();
    if (!q) return;
    err.remove();
    submitFollowup(q, inputEl);
  });
  chatArea.parentNode.insertBefore(err, chatArea);
  // Make sure the user actually sees the failure if they've scrolled
  // away — without this the textarea stays in view and Send looks like
  // it silently no-op'd.
  err.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Brief shake + tint on the textarea to signal "this is required".
// Used when the user hits Send / Cmd+Enter with empty content.
function flashEmpty(ta) {
  // Clear any prior error row — showing both "you have an error" and
  // "this is required" at once is contradictory.
  const inputEl = ta.closest('.followup-input');
  inputEl?.querySelector('.followup-error')?.remove();

  ta.classList.remove('shake');
  // Force a reflow so re-adding the class restarts the animation
  // (otherwise repeated empty-clicks would only animate the first time).
  void ta.offsetWidth;
  ta.classList.add('shake');
  ta.focus();
  setTimeout(() => ta.classList.remove('shake'), 400);
}

// Sanitise strings before inserting into innerHTML
function escapeHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}


// ── Mock Data ─────────────────────────────────────────────────
// Hard-coded demo response. Only returned when USE_MOCK_RESULTS
// (set near the top of this file) is true. The default path is the
// real POST /upload call; errors surface as a visible error message
// rather than silently falling back to this mock.

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
