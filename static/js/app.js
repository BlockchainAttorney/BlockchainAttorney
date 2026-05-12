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
    recordBtn.style.display = "none";
    audioPreview.classList.remove("hidden");
}

discardBtn.addEventListener("click", () => {
    currentAudioBlob = null;
    audioPlayer.src = "";
    audioPreview.classList.add("hidden");
    recordBtn.style.display = "";
    resultSection.classList.add("hidden");
    currentTranscriptData = null;
    currentSummary = null;
});

// ===== TRANSCRIPTION =====
transcribeBtn.addEventListener("click", async () => {
    if (!currentAudioBlob) return;
    audioPreview.classList.add("hidden");
    progressArea.classList.remove("hidden");
    resultSection.classList.add("hidden");

    const storedFP = localStorage.getItem(VP_KEY);
    progressText.textContent = storedFP
        ? "Se transcrie si se identifica vorbitorii dupa voce..."
        : "Se trimite audio la Groq Whisper... (de obicei 5-30 sec)";

    const formData = new FormData();
    formData.append("audio", currentAudioBlob, "recording.webm");
    formData.append("num_speakers", numSpeakers);
    if (storedFP) formData.append("voice_fingerprint", storedFP);

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

    transcriptContainer.innerHTML = "";
    (data.segments || []).forEach(seg => {
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
        textEl.textContent = seg.text;

        line.append(timeTag, speakerTag, textEl);
        transcriptContainer.appendChild(line);
    });

    summarySection.classList.toggle("hidden", !hasApiKey);
    summaryContent.classList.add("hidden");
    summaryError.classList.add("hidden");

    driveSaveSection.classList.toggle("hidden", !googleConnected);
    driveResult.classList.add("hidden");
    driveTitle.value = "";

    resultSection.classList.remove("hidden");
    resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
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

// ===== VOICE PROFILE =====
const vpToggle      = $("vp-toggle");
const vpBody        = $("vp-body");
const vpChevron     = vpToggle.querySelector(".vp-chevron");
const vpStatus      = $("vp-status");
const vpRecordBtn   = $("vp-record-btn");
const vpBtnLabel    = $("vp-btn-label");
const vpTimerRow    = $("vp-timer-row");
const vpTimerEl     = $("vp-timer");
const vpPreview     = $("vp-preview");
const vpAudioEl     = $("vp-audio");
const vpSaveBtn     = $("vp-save-btn");
const vpDiscardBtn  = $("vp-discard-btn");
const vpLoading     = $("vp-loading");
const vpSaved       = $("vp-saved");
const vpResetBtn    = $("vp-reset-btn");
const metaDiarization = $("meta-diarization");

vpToggle.addEventListener("click", () => {
    const open = !vpBody.classList.contains("hidden");
    vpBody.classList.toggle("hidden", open);
    vpChevron.classList.toggle("open", !open);
});

function initVoiceProfileUI() {
    const fp = localStorage.getItem(VP_KEY);
    if (fp) {
        vpStatus.textContent = "Voce inregistrata ✓";
        vpStatus.className = "vp-status-badge set";
        vpSaved.classList.remove("hidden");
        vpPreview.classList.add("hidden");
    } else {
        vpStatus.textContent = "Neinregistrat";
        vpStatus.className = "vp-status-badge unset";
        vpSaved.classList.add("hidden");
    }
}

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
                vpSaved.classList.add("hidden");
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
    if (!vpAudioBlob) return;
    vpLoading.classList.remove("hidden");
    vpPreview.classList.add("hidden");

    const formData = new FormData();
    formData.append("audio", vpAudioBlob, "voice_sample.webm");

    try {
        const res = await fetch("/voice/register", { method: "POST", body: formData });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        localStorage.setItem(VP_KEY, JSON.stringify(data.fingerprint));
        initVoiceProfileUI();
    } catch (err) {
        alert("Eroare la salvarea profilului: " + err.message);
        vpPreview.classList.remove("hidden");
    } finally {
        vpLoading.classList.add("hidden");
    }
});

vpResetBtn.addEventListener("click", () => {
    localStorage.removeItem(VP_KEY);
    initVoiceProfileUI();
});

// ===== INIT =====
checkApiStatus();
checkGoogleStatus();
initVoiceProfileUI();
