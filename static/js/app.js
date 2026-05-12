/* Transcriptor AI — Asociatia BlockchainLegal */

let mediaRecorder = null;
let audioChunks = [];
let timerInterval = null;
let secondsElapsed = 0;
let numSpeakers = 2;
let currentTranscriptData = null;
let currentSummary = null;
let hasApiKey = false;
let googleConfigured = false;
let googleConnected = false;
let currentAudioBlob = null;
let inputMode = "record"; // "record" | "upload"

// Voice profile
let vpMediaRecorder = null;
let vpAudioChunks = [];
let vpTimerInterval = null;
let vpSeconds = 0;
let vpAudioBlob = null;
const VP_KEY = "bl_voice_fingerprint";

// ===== DOM =====
const $ = id => document.getElementById(id);

const recordBtn       = $("record-btn");
const recordLabel     = $("record-label");
const recordingInfo   = $("recording-info");
const timerEl         = $("timer");
const audioPreview    = $("audio-preview");
const audioPlayer     = $("audio-player");
const transcribeBtn   = $("transcribe-btn");
const discardBtn      = $("discard-btn");
const progressArea    = $("progress-area");
const progressText    = $("progress-text");
const resultSection   = $("result-section");
const transcriptContainer = $("transcript-container");
const metaDuration    = $("meta-duration");
const metaLang        = $("meta-lang");
const summarySection  = $("summary-section");
const genSummaryBtn   = $("gen-summary-btn");
const summaryContent  = $("summary-content");
const summaryLoading  = $("summary-loading");
const summaryError    = $("summary-error");
const exportWord      = $("export-word");
const exportPdf       = $("export-pdf");
const apiStatus       = $("api-status");
const googleSigninBtn = $("google-signin-btn");
const googleSignoutBtn = $("google-signout-btn");
const googleUser      = $("google-user");
const googleEmail     = $("google-email");
const googleAvatar    = $("google-avatar");
const driveSaveSection = $("drive-save-section");
const driveTitle      = $("drive-title");
const driveClient     = $("drive-client");
const driveSaveBtn    = $("drive-save-btn");
const driveResult     = $("drive-result");
const foldersList     = $("folders-list");

// ===== API STATUS =====
async function checkApiStatus() {
    try {
        const res = await fetch("/check-api");
        const data = await res.json();
        hasApiKey = data.has_anthropic_key;
        googleConfigured = data.google_configured;

        if (data.has_groq_key) {
            apiStatus.textContent = "Transcriere activa";
            apiStatus.className = "api-badge ok";
        } else {
            apiStatus.textContent = "Groq lipseste";
            apiStatus.className = "api-badge no-key";
        }
    } catch {
        apiStatus.textContent = "Eroare";
        apiStatus.className = "api-badge no-key";
    }
}

// ===== GOOGLE AUTH =====
async function checkGoogleStatus() {
    try {
        const res = await fetch("/google/status");
        const data = await res.json();
        googleConnected = data.connected;
        googleConfigured = data.configured;

        if (!googleConfigured) {
            googleSigninBtn.classList.add("hidden");
            googleUser.classList.add("hidden");
            return;
        }

        if (googleConnected && data.user) {
            googleSigninBtn.classList.add("hidden");
            googleUser.classList.remove("hidden");
            googleEmail.textContent = data.user.email || "Conectat";
            if (data.user.picture) googleAvatar.src = data.user.picture;
            await loadFolders();
        } else {
            googleSigninBtn.classList.remove("hidden");
            googleUser.classList.add("hidden");
        }
    } catch {
        googleSigninBtn.classList.add("hidden");
    }
}

googleSigninBtn.addEventListener("click", () => {
    window.location.href = "/google/auth";
});

googleSignoutBtn.addEventListener("click", async () => {
    await fetch("/google/logout", { method: "POST" });
    location.reload();
});

async function loadFolders() {
    try {
        const res = await fetch("/google/folders");
        const data = await res.json();
        foldersList.innerHTML = "";
        (data.folders || []).forEach(name => {
            const opt = document.createElement("option");
            opt.value = name;
            foldersList.appendChild(opt);
        });
    } catch {}
}

// ===== INPUT TABS =====
document.querySelectorAll(".input-tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".input-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        inputMode = tab.dataset.mode;
        $("mode-record").classList.toggle("hidden", inputMode !== "record");
        $("mode-upload").classList.toggle("hidden", inputMode !== "upload");
        // Reset both sides when switching
        currentAudioBlob = null;
        audioPlayer.src = "";
        audioPreview.classList.add("hidden");
        recordBtn.style.display = "";
    });
});

// ===== FILE UPLOAD =====
const uploadInput     = $("upload-input");
const uploadDropzone  = $("upload-dropzone");
const uploadBrowseBtn = $("upload-browse-btn");
const uploadChosen    = $("upload-chosen");
const uploadFilename  = $("upload-filename");
const uploadFilesize  = $("upload-filesize");
const uploadClearBtn  = $("upload-clear-btn");

uploadBrowseBtn.addEventListener("click", (e) => { e.stopPropagation(); uploadInput.click(); });
uploadDropzone.addEventListener("click", () => uploadInput.click());

uploadInput.addEventListener("change", () => {
    if (uploadInput.files[0]) handleUploadFile(uploadInput.files[0]);
});

uploadDropzone.addEventListener("dragover", (e) => { e.preventDefault(); uploadDropzone.classList.add("drag-over"); });
uploadDropzone.addEventListener("dragleave", () => uploadDropzone.classList.remove("drag-over"));
uploadDropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    uploadDropzone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file) handleUploadFile(file);
});

function handleUploadFile(file) {
    const allowed = ["audio/mpeg", "audio/wav", "audio/mp4", "audio/ogg", "audio/webm",
                     "audio/flac", "audio/aac", "audio/x-m4a", "video/mp4", "audio/x-flac"];
    const ext = file.name.split(".").pop().toLowerCase();
    const allowedExts = ["mp3","wav","m4a","ogg","webm","flac","aac","mp4","mpeg"];
    if (!allowed.includes(file.type) && !allowedExts.includes(ext)) {
        alert("Fisierul trebuie sa fie audio (MP3, WAV, M4A, OGG, FLAC, AAC).");
        return;
    }
    if (file.size > 100 * 1024 * 1024) {
        alert("Fisierul depaseste 100 MB. Foloseste un fisier mai mic.");
        return;
    }
    currentAudioBlob = file;
    uploadFilename.textContent = file.name;
    uploadFilesize.textContent = formatFileSize(file.size);
    uploadChosen.classList.remove("hidden");
    uploadDropzone.classList.add("hidden");
    audioPlayer.src = URL.createObjectURL(file);
    audioPreview.classList.remove("hidden");
}

uploadClearBtn.addEventListener("click", () => {
    currentAudioBlob = null;
    audioPlayer.src = "";
    uploadInput.value = "";
    uploadChosen.classList.add("hidden");
    uploadDropzone.classList.remove("hidden");
    audioPreview.classList.add("hidden");
});

function formatFileSize(bytes) {
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

// ===== SPEAKER SELECTION =====
document.querySelectorAll(".speaker-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".speaker-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        numSpeakers = parseInt(btn.dataset.n);
    });
});

// ===== RECORDING =====
recordBtn.addEventListener("click", async () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
        stopRecording();
    } else {
        await startRecording();
    }
});

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        const mimeType = getSupportedMime();
        mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            const blob = new Blob(audioChunks, { type: mimeType || "audio/webm" });
            currentAudioBlob = blob;
            audioPlayer.src = URL.createObjectURL(blob);
            showAudioPreview();
        };
        mediaRecorder.start(500);
        startTimer();
        recordBtn.classList.add("recording");
        recordLabel.textContent = "Apasa pentru a opri";
        recordingInfo.classList.remove("hidden");
    } catch (err) {
        alert("Nu s-a putut accesa microfonul. Verifica permisiunile browser-ului.\n\n" + err.message);
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
    stopTimer();
    recordBtn.classList.remove("recording");
    recordLabel.textContent = "Apasa pentru a inregistra";
    recordingInfo.classList.add("hidden");
}

function startTimer() {
    secondsElapsed = 0;
    updateTimerDisplay();
    timerInterval = setInterval(() => { secondsElapsed++; updateTimerDisplay(); }, 1000);
}

function stopTimer() {
    clearInterval(timerInterval);
    timerInterval = null;
}

function updateTimerDisplay() {
    const m = String(Math.floor(secondsElapsed / 60)).padStart(2, "0");
    const s = String(secondsElapsed % 60).padStart(2, "0");
    timerEl.textContent = `${m}:${s}`;
}

function getSupportedMime() {
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    return types.find(t => MediaRecorder.isTypeSupported(t)) || "";
}

function showAudioPreview() {
    if (inputMode === "record") recordBtn.style.display = "none";
    audioPreview.classList.remove("hidden");
}

discardBtn.addEventListener("click", () => {
    currentAudioBlob = null;
    audioPlayer.src = "";
    audioPreview.classList.add("hidden");
    resultSection.classList.add("hidden");
    currentTranscriptData = null;
    currentSummary = null;
    if (inputMode === "record") {
        recordBtn.style.display = "";
    } else {
        uploadInput.value = "";
        uploadChosen.classList.add("hidden");
        uploadDropzone.classList.remove("hidden");
    }
});

// ===== TRANSCRIPTION =====
transcribeBtn.addEventListener("click", async () => {
    if (!currentAudioBlob) return;
    audioPreview.classList.add("hidden");
    progressArea.classList.remove("hidden");
    resultSection.classList.add("hidden");

    const activeFP = getActiveFP();
    if (activeFP) {
        progressText.textContent = `Se transcrie si se identifica vorbitorii (activ: ${localStorage.getItem(VP_ACTIVE_KEY)})...`;
    } else if (inputMode === "upload") {
        progressText.textContent = "Se incarca si transcrie fisierul... (poate dura 10-60 sec in functie de marime)";
    } else {
        progressText.textContent = "Se trimite audio la Groq Whisper... (de obicei 5-30 sec)";
    }

    const formData = new FormData();
    const filename = (inputMode === "upload" && currentAudioBlob.name) ? currentAudioBlob.name : "recording.webm";
    formData.append("audio", currentAudioBlob, filename);
    formData.append("num_speakers", numSpeakers);
    if (activeFP) formData.append("voice_fingerprint", JSON.stringify(activeFP));

    try {
        const res = await fetch("/transcribe", { method: "POST", body: formData });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || "Eroare server");
        }
        const data = await res.json();
        currentTranscriptData = data;
        currentSummary = null;
        progressArea.classList.add("hidden");
        displayResult(data);

        // Auto-generate summary if available
        if (hasApiKey) {
            generateSummary();
        }
    } catch (err) {
        progressArea.classList.add("hidden");
        audioPreview.classList.remove("hidden");
        alert("Eroare la transcriere:\n" + err.message);
    }
});

// ===== DISPLAY RESULT =====
function displayResult(data) {
    metaDuration.textContent = formatTime(data.duration || 0);
    metaLang.textContent = (data.language || "ro").toUpperCase();
    if (data.diarization_used) {
        metaDiarization.classList.remove("hidden");
    } else {
        metaDiarization.classList.add("hidden");
    }

    renderTranscript(data.segments || []);
    copyTranscriptBtn.textContent = "Copiaza tot";

    summarySection.classList.toggle("hidden", !hasApiKey);
    summaryContent.classList.add("hidden");
    summaryError.classList.add("hidden");

    driveSaveSection.classList.toggle("hidden", !googleConnected);
    driveResult.classList.add("hidden");
    driveTitle.value = "";

    resultSection.classList.remove("hidden");
    resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderTranscript(segments) {
    transcriptContainer.innerHTML = "";
    segments.forEach((seg, idx) => {
        const line = document.createElement("div");
        line.className = "transcript-line";

        const timeTag = document.createElement("span");
        timeTag.className = "time-tag";
        timeTag.textContent = formatTime(seg.start);

        const speakerTag = document.createElement("span");
        speakerTag.className = "speaker-tag " + getSpeakerClass(seg.speaker);
        speakerTag.textContent = seg.speaker;

        const textEl = document.createElement("span");
        textEl.className = "seg-text";
        textEl.contentEditable = "true";
        textEl.spellcheck = true;
        textEl.textContent = seg.text;
        textEl.dataset.idx = idx;
        textEl.title = "Click pentru a edita";

        textEl.addEventListener("input", () => {
            if (currentTranscriptData && currentTranscriptData.segments[idx] !== undefined) {
                currentTranscriptData.segments[idx].text = textEl.textContent;
            }
        });

        textEl.addEventListener("keydown", (e) => {
            if (e.key === "Enter") { e.preventDefault(); textEl.blur(); }
        });

        line.append(timeTag, speakerTag, textEl);
        transcriptContainer.appendChild(line);
    });
}

function getSpeakerClass(speaker) {
    if (!speaker) return "speaker-default";
    const s = speaker.toLowerCase();
    if (s.includes("avocat")) return "speaker-avocat";
    if (s.includes("client")) return "speaker-client";
    return "speaker-default";
}

function formatTime(sec) {
    const m = String(Math.floor(sec / 60)).padStart(2, "0");
    const s = String(Math.floor(sec % 60)).padStart(2, "0");
    return `${m}:${s}`;
}

// ===== SUMMARY =====
genSummaryBtn.addEventListener("click", generateSummary);

async function generateSummary() {
    if (!currentTranscriptData) return;

    summaryLoading.classList.remove("hidden");
    summaryError.classList.add("hidden");
    summaryContent.classList.add("hidden");
    genSummaryBtn.disabled = true;

    try {
        const res = await fetch("/summarize", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ segments: currentTranscriptData.segments }),
        });
        const data = await res.json();
        summaryLoading.classList.add("hidden");

        if (data.error) {
            summaryError.textContent = data.error;
            summaryError.classList.remove("hidden");
        } else {
            currentSummary = data;
            renderSummary(data);
            summaryContent.classList.remove("hidden");
            genSummaryBtn.textContent = "Regenereaza";

            // Pre-fill drive title with suggestion
            if (data.titlu_sugerat && !driveTitle.value) {
                driveTitle.value = data.titlu_sugerat;
            }
        }
    } catch (err) {
        summaryLoading.classList.add("hidden");
        summaryError.textContent = "Eroare: " + err.message;
        summaryError.classList.remove("hidden");
    } finally {
        genSummaryBtn.disabled = false;
    }
}

function renderSummary(data) {
    $("rezumat-text").textContent = data.rezumat || "";

    renderList("puncte-cheie-list", data.puncte_cheie, "summary-puncte-cheie");
    renderList("intrebari-list", data.intrebari_juridice, "summary-intrebari");
    renderList("actiuni-list", data.actiuni, "summary-actiuni");
    renderList("termene-list", data.termene_importante, "summary-termene");
    renderList("informatii-list", data.informatii_client, "summary-informatii");
}

function renderList(listId, items, sectionId) {
    const list = $(listId);
    const section = $(sectionId);
    list.innerHTML = "";
    if (!items || items.length === 0) {
        section.classList.add("hidden");
        return;
    }
    items.forEach(item => {
        const li = document.createElement("li");
        li.textContent = item;
        list.appendChild(li);
    });
    section.classList.remove("hidden");
}

// ===== GOOGLE DRIVE SAVE =====
driveSaveBtn.addEventListener("click", async () => {
    if (!currentTranscriptData) return;

    driveSaveBtn.disabled = true;
    driveSaveBtn.textContent = "Se salveaza...";
    driveResult.classList.add("hidden");

    try {
        const res = await fetch("/google/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                segments: currentTranscriptData.segments,
                summary: currentSummary || {},
                timestamp: currentTranscriptData.timestamp || "",
                duration: currentTranscriptData.duration || 0,
                title: driveTitle.value.trim(),
                client_folder: driveClient.value.trim(),
            }),
        });
        const data = await res.json();

        if (data.error) {
            driveResult.className = "drive-result error";
            driveResult.textContent = "Eroare: " + data.error;
        } else {
            driveResult.className = "drive-result";
            driveResult.innerHTML = `
                <strong>Salvat in Google Drive!</strong><br>
                ${escapeHtml(data.name)}
                <a href="${data.url}" target="_blank">Deschide documentul ↗</a>
                <a href="${data.folder_url}" target="_blank">Deschide folder ↗</a>
            `;
            loadFolders();
        }
        driveResult.classList.remove("hidden");
    } catch (err) {
        driveResult.className = "drive-result error";
        driveResult.textContent = "Eroare: " + err.message;
        driveResult.classList.remove("hidden");
    } finally {
        driveSaveBtn.disabled = false;
        driveSaveBtn.textContent = "Salveaza in Google Drive";
    }
});

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// ===== EXPORT =====
exportWord.addEventListener("click", () => exportDoc("word"));
exportPdf.addEventListener("click", () => exportDoc("pdf"));

async function exportDoc(type) {
    if (!currentTranscriptData) return;
    const payload = {
        segments: currentTranscriptData.segments,
        summary: currentSummary || {},
        timestamp: currentTranscriptData.timestamp || "",
        duration: currentTranscriptData.duration || 0,
    };
    try {
        const res = await fetch(`/export/${type}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error("Export esuat");
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        const ext = type === "word" ? "docx" : "pdf";
        a.download = `transcript_${currentTranscriptData.timestamp || "export"}.${ext}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (err) {
        alert("Eroare la export: " + err.message);
    }
}

// ===== VOICE PROFILES (multi-user) =====
// Storage: bl_voice_profiles = [{name, fingerprint, createdAt}, ...]
// Storage: bl_active_profile = name (string) | null

const VP_PROFILES_KEY = "bl_voice_profiles";
const VP_ACTIVE_KEY   = "bl_active_profile";
const metaDiarization = $("meta-diarization");

const vpToggle       = $("vp-toggle");
const vpBody         = $("vp-body");
const vpChevron      = vpToggle.querySelector(".vp-chevron");
const vpStatus       = $("vp-status");
const vpProfilesList = $("vp-profiles-list");
const vpActiveRow    = $("vp-active-row");
const vpActiveSelect = $("vp-active-select");
const vpClearActive  = $("vp-clear-active");
const vpRecordBtn    = $("vp-record-btn");
const vpBtnLabel     = $("vp-btn-label");
const vpTimerRow     = $("vp-timer-row");
const vpTimerEl      = $("vp-timer");
const vpPreview      = $("vp-preview");
const vpAudioEl      = $("vp-audio");
const vpSaveBtn      = $("vp-save-btn");
const vpDiscardBtn   = $("vp-discard-btn");
const vpLoading      = $("vp-loading");
const vpNameInput    = $("vp-name-input");

vpToggle.addEventListener("click", () => {
    const open = !vpBody.classList.contains("hidden");
    vpBody.classList.toggle("hidden", open);
    vpChevron.classList.toggle("open", !open);
});

function getProfiles() {
    try { return JSON.parse(localStorage.getItem(VP_PROFILES_KEY) || "[]"); }
    catch { return []; }
}

function saveProfiles(profiles) {
    localStorage.setItem(VP_PROFILES_KEY, JSON.stringify(profiles));
}

function getActiveProfile() {
    const name = localStorage.getItem(VP_ACTIVE_KEY);
    if (!name) return null;
    return getProfiles().find(p => p.name === name) || null;
}

function getActiveFP() {
    const p = getActiveProfile();
    return p ? p.fingerprint : null;
}

function initVoiceProfileUI() {
    const profiles = getProfiles();
    const activeName = localStorage.getItem(VP_ACTIVE_KEY);

    // Status badge
    if (profiles.length === 0) {
        vpStatus.textContent = "Niciun profil";
        vpStatus.className = "vp-status-badge unset";
    } else if (activeName) {
        vpStatus.textContent = `Activ: ${activeName}`;
        vpStatus.className = "vp-status-badge set";
    } else {
        vpStatus.textContent = `${profiles.length} profil${profiles.length > 1 ? "uri" : ""} inregistrat${profiles.length > 1 ? "e" : ""}`;
        vpStatus.className = "vp-status-badge set";
    }

    // Render profile list
    vpProfilesList.innerHTML = "";
    profiles.forEach(p => {
        const isActive = p.name === activeName;
        const item = document.createElement("div");
        item.className = "vp-profile-item" + (isActive ? " active-profile" : "");

        const initials = p.name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
        item.innerHTML = `
            <div class="vp-profile-avatar">${escapeHtml(initials)}</div>
            <div class="vp-profile-info">
                <div class="vp-profile-name">${escapeHtml(p.name)}</div>
                <div class="vp-profile-date">Inregistrat: ${p.createdAt || "—"}</div>
            </div>
            <div class="vp-profile-actions">
                <button class="vp-activate-btn ${isActive ? "active" : ""}" data-name="${escapeHtml(p.name)}">
                    ${isActive ? "Activ ✓" : "Selecteaza"}
                </button>
                <button class="vp-delete-btn" data-name="${escapeHtml(p.name)}" title="Sterge profil">✕</button>
            </div>
        `;
        vpProfilesList.appendChild(item);
    });

    // Activate buttons
    vpProfilesList.querySelectorAll(".vp-activate-btn:not(.active)").forEach(btn => {
        btn.addEventListener("click", () => {
            localStorage.setItem(VP_ACTIVE_KEY, btn.dataset.name);
            initVoiceProfileUI();
        });
    });
    vpProfilesList.querySelectorAll(".vp-delete-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            if (!confirm(`Stergi profilul lui "${btn.dataset.name}"?`)) return;
            const updated = getProfiles().filter(p => p.name !== btn.dataset.name);
            saveProfiles(updated);
            if (activeName === btn.dataset.name) localStorage.removeItem(VP_ACTIVE_KEY);
            initVoiceProfileUI();
        });
    });

    // Active row (dropdown selector)
    if (profiles.length > 0) {
        vpActiveRow.classList.remove("hidden");
        vpActiveSelect.innerHTML = `<option value="">— Niciun avocat activ —</option>`;
        profiles.forEach(p => {
            const opt = document.createElement("option");
            opt.value = p.name;
            opt.textContent = p.name;
            if (p.name === activeName) opt.selected = true;
            vpActiveSelect.appendChild(opt);
        });
    } else {
        vpActiveRow.classList.add("hidden");
    }
}

vpActiveSelect.addEventListener("change", () => {
    if (vpActiveSelect.value) localStorage.setItem(VP_ACTIVE_KEY, vpActiveSelect.value);
    else localStorage.removeItem(VP_ACTIVE_KEY);
    initVoiceProfileUI();
});

vpClearActive.addEventListener("click", () => {
    localStorage.removeItem(VP_ACTIVE_KEY);
    initVoiceProfileUI();
});

// Recording for new profile
vpRecordBtn.addEventListener("click", async () => {
    if (vpMediaRecorder && vpMediaRecorder.state === "recording") {
        vpMediaRecorder.stop();
        clearInterval(vpTimerInterval);
        vpTimerRow.classList.add("hidden");
        vpRecordBtn.classList.remove("recording");
        vpBtnLabel.textContent = "Incepe inregistrarea";
    } else {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            vpAudioChunks = [];
            const mime = getSupportedMime();
            vpMediaRecorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
            vpMediaRecorder.ondataavailable = e => { if (e.data.size > 0) vpAudioChunks.push(e.data); };
            vpMediaRecorder.onstop = () => {
                stream.getTracks().forEach(t => t.stop());
                vpAudioBlob = new Blob(vpAudioChunks, { type: mime || "audio/webm" });
                vpAudioEl.src = URL.createObjectURL(vpAudioBlob);
                vpPreview.classList.remove("hidden");
            };
            vpMediaRecorder.start(500);
            vpSeconds = 0;
            vpTimerEl.textContent = "00:00";
            vpTimerRow.classList.remove("hidden");
            vpRecordBtn.classList.add("recording");
            vpBtnLabel.textContent = "Opreste (min 20 sec)";
            vpTimerInterval = setInterval(() => {
                vpSeconds++;
                const m = String(Math.floor(vpSeconds / 60)).padStart(2, "0");
                const s = String(vpSeconds % 60).padStart(2, "0");
                vpTimerEl.textContent = `${m}:${s}`;
            }, 1000);
        } catch (err) {
            alert("Nu s-a putut accesa microfonul: " + err.message);
        }
    }
});

vpDiscardBtn.addEventListener("click", () => {
    vpAudioBlob = null;
    vpAudioEl.src = "";
    vpPreview.classList.add("hidden");
});

vpSaveBtn.addEventListener("click", async () => {
    const name = vpNameInput.value.trim();
    if (!name) { alert("Introdu numele avocatului inainte de a salva."); vpNameInput.focus(); return; }
    if (!vpAudioBlob) { alert("Inregistreaza o proba vocala mai intai."); return; }
    if (getProfiles().some(p => p.name === name)) {
        if (!confirm(`Exista deja un profil pentru "${name}". Il suprascrii?`)) return;
    }

    vpLoading.classList.remove("hidden");
    vpPreview.classList.add("hidden");

    const formData = new FormData();
    formData.append("audio", vpAudioBlob, "voice_sample.webm");

    try {
        const res = await fetch("/voice/register", { method: "POST", body: formData });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        const profiles = getProfiles().filter(p => p.name !== name);
        profiles.push({
            name,
            fingerprint: data.fingerprint,
            createdAt: new Date().toLocaleDateString("ro-RO"),
        });
        saveProfiles(profiles);
        localStorage.setItem(VP_ACTIVE_KEY, name);

        // Reset form
        vpNameInput.value = "";
        vpAudioBlob = null;
        vpAudioEl.src = "";
        document.getElementById("vp-add-section").removeAttribute("open");

        initVoiceProfileUI();
    } catch (err) {
        alert("Eroare la salvarea profilului: " + err.message);
        vpPreview.classList.remove("hidden");
    } finally {
        vpLoading.classList.add("hidden");
    }
});

// ===== COPY TRANSCRIPT =====
const copyTranscriptBtn = $("copy-transcript-btn");

copyTranscriptBtn.addEventListener("click", async () => {
    if (!currentTranscriptData) return;
    const text = currentTranscriptData.segments
        .map(s => `[${formatTime(s.start)}] ${s.speaker}: ${s.text}`)
        .join("\n");
    try {
        await navigator.clipboard.writeText(text);
        copyTranscriptBtn.textContent = "Copiat ✓";
        setTimeout(() => { copyTranscriptBtn.textContent = "Copiaza tot"; }, 2000);
    } catch {
        // Fallback for browsers without clipboard API
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        copyTranscriptBtn.textContent = "Copiat ✓";
        setTimeout(() => { copyTranscriptBtn.textContent = "Copiaza tot"; }, 2000);
    }
});

// ===== POST-PROCESS =====
const postprocessBtn     = $("postprocess-btn");
const postprocessLoading = $("postprocess-loading");

postprocessBtn.addEventListener("click", async () => {
    if (!currentTranscriptData) return;
    postprocessBtn.disabled = true;
    postprocessLoading.classList.remove("hidden");

    try {
        const res = await fetch("/postprocess", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ segments: currentTranscriptData.segments }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        currentTranscriptData.segments = data.segments;
        renderTranscript(data.segments);
        postprocessBtn.textContent = "Corectat ✓";
    } catch (err) {
        alert("Eroare la corecție AI: " + err.message);
        postprocessBtn.disabled = false;
    } finally {
        postprocessLoading.classList.add("hidden");
    }
});

// ===== INIT =====
checkApiStatus();
checkGoogleStatus();
initVoiceProfileUI();
