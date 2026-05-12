/* Transcriptor AI — BlockchainAttorney */

let mediaRecorder = null;
let audioChunks = [];
let timerInterval = null;
let secondsElapsed = 0;
let numSpeakers = 2;
let currentTranscriptData = null;

const recordBtn      = document.getElementById("record-btn");
const recordLabel    = document.getElementById("record-label");
const recordingInfo  = document.getElementById("recording-info");
const timerEl        = document.getElementById("timer");
const audioPreview   = document.getElementById("audio-preview");
const audioPlayer    = document.getElementById("audio-player");
const transcribeBtn  = document.getElementById("transcribe-btn");
const discardBtn     = document.getElementById("discard-btn");
const progressArea   = document.getElementById("progress-area");
const progressText   = document.getElementById("progress-text");
const resultSection  = document.getElementById("result-section");
const transcriptContainer = document.getElementById("transcript-container");
const metaDuration   = document.getElementById("meta-duration");
const metaLang       = document.getElementById("meta-lang");
const summarySection = document.getElementById("summary-section");
const genSummaryBtn  = document.getElementById("gen-summary-btn");
const summaryContent = document.getElementById("summary-content");
const summaryLoading = document.getElementById("summary-loading");
const summaryText    = document.getElementById("summary-text");
const exportWord     = document.getElementById("export-word");
const exportPdf      = document.getElementById("export-pdf");
const apiStatus      = document.getElementById("api-status");

let hasApiKey = false;
let currentAudioBlob = null;

// ===== CHECK API STATUS =====
async function checkApiStatus() {
    try {
        const res = await fetch("/check-api");
        const data = await res.json();
        hasApiKey = data.has_anthropic_key;
        if (hasApiKey) {
            apiStatus.textContent = "Rezumat AI activ";
            apiStatus.className = "api-badge ok";
        } else {
            apiStatus.textContent = "Fara cheie API";
            apiStatus.className = "api-badge no-key";
        }
    } catch {
        apiStatus.textContent = "Eroare conexiune";
        apiStatus.className = "api-badge no-key";
    }
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

        mediaRecorder.ondataavailable = e => {
            if (e.data.size > 0) audioChunks.push(e.data);
        };

        mediaRecorder.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            const blob = new Blob(audioChunks, { type: mimeType || "audio/webm" });
            currentAudioBlob = blob;
            const url = URL.createObjectURL(blob);
            audioPlayer.src = url;
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
    if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
    }
    stopTimer();
    recordBtn.classList.remove("recording");
    recordLabel.textContent = "Apasa pentru a inregistra";
    recordingInfo.classList.add("hidden");
}

function startTimer() {
    secondsElapsed = 0;
    updateTimerDisplay();
    timerInterval = setInterval(() => {
        secondsElapsed++;
        updateTimerDisplay();
    }, 1000);
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
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
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
});

// ===== TRANSCRIPTION =====
transcribeBtn.addEventListener("click", async () => {
    if (!currentAudioBlob) return;

    audioPreview.classList.add("hidden");
    progressArea.classList.remove("hidden");
    resultSection.classList.add("hidden");

    const formData = new FormData();
    formData.append("audio", currentAudioBlob, "recording.webm");
    formData.append("num_speakers", numSpeakers);

    progressText.textContent = "Se incarca modelul Whisper si se transcrie... Aceasta poate dura 1-2 minute la prima rulare.";

    try {
        const res = await fetch("/transcribe", { method: "POST", body: formData });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || "Eroare server");
        }
        const data = await res.json();
        currentTranscriptData = data;
        progressArea.classList.add("hidden");
        displayResult(data);
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

        line.appendChild(timeTag);
        line.appendChild(speakerTag);
        line.appendChild(textEl);
        transcriptContainer.appendChild(line);
    });

    if (hasApiKey) {
        summarySection.classList.remove("hidden");
    } else {
        summarySection.classList.add("hidden");
    }

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
genSummaryBtn.addEventListener("click", async () => {
    if (!currentTranscriptData) return;

    summaryContent.classList.remove("hidden");
    summaryLoading.classList.remove("hidden");
    summaryText.textContent = "";
    genSummaryBtn.disabled = true;

    const fullText = buildFullText(currentTranscriptData.segments);

    try {
        const res = await fetch("/summarize", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: fullText }),
        });
        const data = await res.json();
        summaryLoading.classList.add("hidden");
        summaryText.textContent = data.summary || "Rezumatul nu a putut fi generat.";
        genSummaryBtn.textContent = "Regenereaza";
        currentTranscriptData._summary = data.summary;
    } catch (err) {
        summaryLoading.classList.add("hidden");
        summaryText.textContent = "Eroare la generarea rezumatului.";
    } finally {
        genSummaryBtn.disabled = false;
    }
});

function buildFullText(segments) {
    return (segments || []).map(s => `${s.speaker}: ${s.text}`).join("\n");
}

// ===== EXPORT =====
exportWord.addEventListener("click", () => exportDoc("word"));
exportPdf.addEventListener("click", () => exportDoc("pdf"));

async function exportDoc(type) {
    if (!currentTranscriptData) return;

    const payload = {
        segments: currentTranscriptData.segments,
        summary: currentTranscriptData._summary || "",
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

// ===== INIT =====
checkApiStatus();
