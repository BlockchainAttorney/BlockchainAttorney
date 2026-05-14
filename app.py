import os
import io
import uuid
import json
import logging
import tempfile
import traceback
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, render_template, send_file, redirect, session, g
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB max upload
app.secret_key = os.getenv("FLASK_SECRET_KEY", "schimba-aceasta-cheie-in-productie-" + str(uuid.uuid4()))

if os.getenv("FLASK_ENV", "development") == "development":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# ===== LOGGING =====

LOGS_DIR = Path("logs")
try:
    LOGS_DIR.mkdir(exist_ok=True)
except Exception:
    LOGS_DIR = None

def _setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)
    if LOGS_DIR:
        try:
            fh = RotatingFileHandler(
                LOGS_DIR / "app.log", maxBytes=10 * 1024 * 1024,
                backupCount=5, encoding="utf-8",
            )
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception as e:
            root.warning(f"Nu pot crea log file: {e}")

_setup_logging()
log = logging.getLogger("transcriptor")

TRANSCRIPTS_DIR = Path("transcripts")
try:
    TRANSCRIPTS_DIR.mkdir(exist_ok=True)
except Exception:
    pass

LANGUAGE = os.getenv("LANGUAGE", "ro")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "whisper-large-v3")
GROQ_MAX_BYTES = 24 * 1024 * 1024  # 24 MB — Groq limit is 25 MB

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_DRIVE_FOLDER_NAME = os.getenv("GOOGLE_DRIVE_FOLDER_NAME", "Transcripte Clienti")
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# ===== REQUEST LOGGING MIDDLEWARE =====

@app.before_request
def _before():
    g.start_time = time.time()
    g.req_id = str(uuid.uuid4())[:8]

@app.after_request
def _after(response):
    ms = int((time.time() - g.start_time) * 1000)
    lvl = logging.WARNING if response.status_code >= 400 else logging.INFO
    log.log(lvl, "[%s] %s %s → %d (%dms)",
            g.req_id, request.method, request.path, response.status_code, ms)
    return response

@app.errorhandler(Exception)
def _unhandled(e):
    log.error("[%s] Eroare neașteptată: %s\n%s",
              getattr(g, "req_id", "?"), str(e), traceback.format_exc())
    return jsonify({"error": f"Eroare server: {str(e)}"}), 500

@app.errorhandler(413)
def _too_large(e):
    log.warning("Upload prea mare (>200MB)")
    return jsonify({"error": "Fișierul depășește 200 MB."}), 413


# ===== HALUCINAȚII WHISPER =====
# Fraze inventate de Whisper când detectează tăcere sau zgomot de fond

HALLUCINATION_PHRASES = [
    "mulțumesc pentru vizionare",
    "abonați-vă la canal",
    "dați like și abonați",
    "ne vedem în episodul",
    "vă mulțumesc că ați urmărit",
    "urmăriți în continuare",
    "canal de youtube",
    "pe youtube",
    "thank you for watching",
    "please subscribe",
    "like and subscribe",
    "don't forget to subscribe",
    "see you in the next",
    "see you next time",
    "copyright",
    "music by",
    "subtitles by",
    "captions by",
    "translated by",
    "www.",
    "http://",
    "https://",
    ".com",
    ".ro",
    "transcript provided by",
    "transcribed by",
]


def filter_hallucinations(segments: list) -> tuple:
    """Remove Whisper hallucinations. Returns (clean_segments, removed_count)."""
    clean = []
    removed = 0
    prev_text_low = ""

    for seg in segments:
        text = seg.get("text", "").strip()
        text_low = text.lower()

        if not text_low:
            removed += 1
            continue

        # Known hallucination phrases
        if any(p in text_low for p in HALLUCINATION_PHRASES):
            log.warning("Halucination eliminată: %.120s", text)
            removed += 1
            continue

        # Whisper loop: segment identic cu precedentul
        if text_low == prev_text_low:
            log.warning("Segment duplicat eliminat: %.80s", text)
            removed += 1
            continue

        prev_text_low = text_low
        clean.append(seg)

    if removed:
        log.info("Filtrare: %d halucinations eliminate din %d segmente",
                 removed, removed + len(clean))
    return clean, removed


# ===== TRANSCRIERE =====

WHISPER_LEGAL_PROMPT = (
    "Transcript convorbire juridică. "
    "Termeni: dosar, tribunal, judecătorie, instanță, complet de judecată, reclamant, pârât, "
    "inculpat, parte vătămată, martor, expert, probă, înscris, interogatoriu, depoziție, "
    "sentință, decizie, hotărâre judecătorească, apel, recurs, căi de atac, prescripție, "
    "termen, citație, somație, notificare, contract, clauză, nulitate, reziliere, "
    "daune, despăgubiri, penalități, garanție, ipotecă, gaj, cesiune, procură, mandat, "
    "executor judecătoresc, executare silită, poprire, sechestru, lichidator, "
    "administrator judiciar, insolvență, faliment, ICCJ, Curtea de Apel, parchet, "
    "DNA, DIICOT, procuror, rechizitoriu, trimitere în judecată, achitare, condamnare, "
    "suspendare, amânare, inadmisibil, nefondat, calitate procesuală, competență, "
    "excepție, întâmpinare, cerere reconvențională."
)

POSTPROCESS_PROMPT = """Ești un corector tehnic de transcrieri audio în limba română. \
Primești segmente dintr-o transcriere automată și corectezi EXCLUSIV erorile evidente de recunoaștere vocală.

REGULI ABSOLUTE:
1. NU adăuga nicio informație care nu există deja în text
2. NU reformula, nu parafrazeza, nu completa propoziții
3. NU schimba înțelesul sau ordinea ideilor
4. NU "îmbunătăți" stilul — lasă limbajul vorbitorului intact
5. Corectează NUMAI: cuvinte greșite fonetic, erori clare de punctuație, capitalizare
6. Dacă nu ești 100% sigur că e eroare de transcriere, lasă NESCHIMBAT
7. Răspunde cu un JSON array cu același număr de elemente ca inputul

Input (JSON array de texte):
"""


def get_groq_client(model_override=None):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY lipsește. Adaugă cheia în .env (https://console.groq.com)")
    from groq import Groq
    return Groq(api_key=GROQ_API_KEY), model_override or GROQ_MODEL


def _call_groq_transcribe(audio_path: str, model: str) -> dict:
    """Single Groq API call for one file."""
    client, model = get_groq_client(model)
    file_size = os.path.getsize(audio_path)
    log.info("Groq transcribe — fișier: %.1f MB, model: %s", file_size / 1_048_576, model)

    with open(audio_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f.read()),
            model=model,
            response_format="verbose_json",
            language=LANGUAGE,
            temperature=0.0,
            prompt=WHISPER_LEGAL_PROMPT,
        )

    segments_raw = getattr(transcription, "segments", None) or []
    result_segments = []
    for seg in segments_raw:
        if isinstance(seg, dict):
            start, end, text = seg.get("start", 0), seg.get("end", 0), seg.get("text", "")
        else:
            start, end, text = getattr(seg, "start", 0), getattr(seg, "end", 0), getattr(seg, "text", "")
        result_segments.append({
            "start": round(float(start), 2),
            "end": round(float(end), 2),
            "text": text.strip(),
            "speaker": "Vorbitor",
        })

    full_text = getattr(transcription, "text", "") or " ".join(s["text"] for s in result_segments)
    duration = getattr(transcription, "duration", 0) or (result_segments[-1]["end"] if result_segments else 0)
    language = getattr(transcription, "language", LANGUAGE) or LANGUAGE

    return {
        "segments": result_segments,
        "full_text": full_text,
        "language": language,
        "duration": round(float(duration), 2),
    }


def _audio_to_wav(src_path: str) -> str:
    """Convert any audio format to 16kHz mono WAV using librosa+soundfile.
    Returns path to WAV temp file (caller must delete)."""
    import librosa
    import soundfile as sf
    y, sr = librosa.load(src_path, sr=16000, mono=True)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, y, sr)
    tmp.close()
    return tmp.name


def transcribe_audio(audio_path: str, model: str = None) -> dict:
    """Transcribe audio — splits into chunks if file > 24 MB, filters hallucinations."""
    t0 = time.time()
    file_size = os.path.getsize(audio_path)
    model = model or GROQ_MODEL

    if file_size <= GROQ_MAX_BYTES:
        # Small file — send directly
        try:
            result = _call_groq_transcribe(audio_path, model)
        except Exception as e:
            # Format might be incompatible — try converting to WAV first
            err_str = str(e).lower()
            if any(k in err_str for k in ["format", "codec", "invalid", "unsupported", "decode"]):
                log.warning("Format incompatibil, convertesc la WAV: %s", e)
                wav_path = _audio_to_wav(audio_path)
                try:
                    result = _call_groq_transcribe(wav_path, model)
                finally:
                    try:
                        os.unlink(wav_path)
                    except Exception:
                        pass
            else:
                raise

        segs, removed = filter_hallucinations(result["segments"])
        result["segments"] = segs
        result["hallucinations_removed"] = removed
        log.info("Transcriere gata — %d segmente, %.1f sec, în %.1fs",
                 len(segs), result["duration"], time.time() - t0)
        return result

    # Large file — split into chunks
    return _transcribe_chunked(audio_path, model, t0)


def _transcribe_chunked(audio_path: str, model: str, t0: float) -> dict:
    """Split large audio into 5-min WAV chunks, transcribe each, merge."""
    import librosa
    import soundfile as sf
    import numpy as np

    file_size = os.path.getsize(audio_path)
    log.info("Fișier mare (%.1f MB) — transcriere în bucăți", file_size / 1_048_576)

    try:
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
    except Exception as e:
        log.warning("librosa load eșuat (%s) — încerc conversie WAV", e)
        wav_path = _audio_to_wav(audio_path)
        try:
            y, sr = librosa.load(wav_path, sr=16000, mono=True)
        finally:
            try:
                os.unlink(wav_path)
            except Exception:
                pass

    chunk_secs = 300  # 5 minutes per chunk
    chunk_samples = chunk_secs * sr
    total_duration = len(y) / sr
    n_chunks = max(1, int(np.ceil(len(y) / chunk_samples)))
    log.info("Împărțit în %d bucăți de ~%ds (total %.0fs)", n_chunks, chunk_secs, total_duration)

    all_segments = []
    full_text_parts = []
    language = LANGUAGE
    chunks_ok = 0

    for i in range(n_chunks):
        start_s = i * chunk_samples
        end_s = min((i + 1) * chunk_samples, len(y))
        chunk_y = y[start_s:end_s]
        time_offset = start_s / sr

        chunk_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            sf.write(chunk_tmp.name, chunk_y, sr)
            chunk_tmp.close()
            log.info("Transcriere bucată %d/%d (offset %.0fs)", i + 1, n_chunks, time_offset)
            chunk_res = _call_groq_transcribe(chunk_tmp.name, model)
            for seg in chunk_res.get("segments", []):
                seg["start"] = round(seg["start"] + time_offset, 2)
                seg["end"] = round(seg["end"] + time_offset, 2)
            all_segments.extend(chunk_res.get("segments", []))
            full_text_parts.append(chunk_res.get("full_text", ""))
            language = chunk_res.get("language", LANGUAGE)
            chunks_ok += 1
        except Exception as e:
            log.error("Eroare bucată %d/%d: %s", i + 1, n_chunks, e)
        finally:
            try:
                os.unlink(chunk_tmp.name)
            except Exception:
                pass

    segs, removed = filter_hallucinations(all_segments)
    log.info("Transcriere completă — %d bucăți, %d segmente, %.1fs total",
             chunks_ok, len(segs), time.time() - t0)
    return {
        "segments": segs,
        "full_text": " ".join(full_text_parts),
        "language": language,
        "duration": round(total_duration, 2),
        "hallucinations_removed": removed,
        "chunks_processed": n_chunks,
    }


def extract_voice_fingerprint(audio_path: str) -> list:
    import librosa
    import numpy as np
    log.info("Extragere amprentă vocală: %s", os.path.basename(audio_path))
    y, sr = librosa.load(audio_path, sr=16000, mono=True, duration=30)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20)
    return np.mean(mfcc, axis=1).tolist()


def diarize_audio(audio_path: str, num_speakers: int, voice_fingerprint: list = None) -> list:
    import librosa
    import numpy as np
    from sklearn.cluster import KMeans

    try:
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
    except Exception as e:
        log.error("Diarizare — nu pot încărca audio: %s", e)
        return []

    hop_length = int(sr * 0.4)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop_length).T

    if len(mfcc) < num_speakers * 4:
        log.warning("Diarizare — prea puține frame-uri (%d) pentru %d vorbitori", len(mfcc), num_speakers)
        return []

    kmeans = KMeans(n_clusters=num_speakers, random_state=42, n_init=10)
    labels = kmeans.fit_predict(mfcc)

    frame_dur = hop_length / sr
    raw_segs = []
    current = int(labels[0])
    start_idx = 0
    for i, label in enumerate(labels[1:], 1):
        if int(label) != current:
            raw_segs.append([start_idx * frame_dur, i * frame_dur, current])
            current = int(label)
            start_idx = i
    raw_segs.append([start_idx * frame_dur, len(labels) * frame_dur, current])

    merged = []
    for seg in raw_segs:
        if merged and (seg[1] - seg[0]) < 0.8:
            prev = merged[-1]
            if prev[2] == seg[2]:
                prev[1] = seg[1]
                continue
        merged.append(seg)

    if voice_fingerprint and len(voice_fingerprint) == 20:
        fp = np.array(voice_fingerprint)
        centroids = kmeans.cluster_centers_
        sims = [float(np.dot(fp, c) / (np.linalg.norm(fp) * np.linalg.norm(c) + 1e-8)) for c in centroids]
        avocat_idx = int(np.argmax(sims))
        label_map = {avocat_idx: "Avocat"}
        client_n = 1
        for i in range(num_speakers):
            if i != avocat_idx:
                label_map[i] = "Client" if num_speakers == 2 else f"Client {client_n}"
                client_n += 1
    else:
        label_map = {0: "Vorbitor 1", 1: "Vorbitor 2"} if num_speakers == 2 \
            else {i: f"Vorbitor {i+1}" for i in range(num_speakers)}

    log.info("Diarizare — %d segmente pentru %d vorbitori", len(merged), num_speakers)
    return [(s[0], s[1], label_map.get(s[2], "Vorbitor")) for s in merged]


def post_process_transcript(segments: list) -> list:
    if not ANTHROPIC_API_KEY or not segments:
        return segments
    t0 = time.time()
    try:
        import anthropic
        texts = [s.get("text", "") for s in segments]
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": POSTPROCESS_PROMPT + json.dumps(texts, ensure_ascii=False),
            }],
        )
        raw = message.content[0].text.strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            corrected = json.loads(match.group(0))
            if isinstance(corrected, list) and len(corrected) == len(segments):
                log.info("Post-procesare — %d segmente în %.1fs", len(segments), time.time() - t0)
                return [{**s, "text": corrected[i]} for i, s in enumerate(segments)]
            log.warning("Post-procesare — lungime diferită (%d vs %d)",
                        len(corrected) if isinstance(corrected, list) else -1, len(segments))
        else:
            log.warning("Post-procesare — JSON invalid de la Claude: %.200s", raw)
    except Exception as e:
        log.error("Post-procesare eroare: %s\n%s", e, traceback.format_exc())
    return segments


def align_diarization(groq_segments: list, diarization: list) -> list:
    if not diarization:
        return groq_segments
    result = []
    for seg in groq_segments:
        best_overlap = -1.0
        speaker = "Vorbitor"
        for d_start, d_end, d_spk in diarization:
            overlap = max(0.0, min(seg["end"], d_end) - max(seg["start"], d_start))
            if overlap > best_overlap:
                best_overlap = overlap
                speaker = d_spk
        result.append({**seg, "speaker": speaker})
    return result


# ===== REZUMAT AI =====

SUMMARY_PROMPT = """Esti asistentul unui avocat specializat in blockchain, criptomonede si tehnologie. Analizeaza acest transcript al unei convorbiri cu un client.

Returneaza STRICT un obiect JSON valid (fara text inainte/dupa), cu urmatoarea structura:
{
  "titlu_sugerat": "Titlu scurt si descriptiv pentru fisier",
  "rezumat": "Rezumat concis in 2-4 propozitii",
  "puncte_cheie": ["Punct important 1", "Punct important 2"],
  "intrebari_juridice": ["Intrebare juridica ridicata de client"],
  "actiuni": ["Actiune concreta necesara"],
  "termene_importante": ["Termen/data importanta mentionata"],
  "informatii_client": ["Detaliu factual despre client/situatie"]
}

Reguli:
- Foloseste limba romana
- Daca o sectiune nu are continut relevant, returneaza array gol []
- Fii concis si specific
- Foloseste exact aceste chei

TRANSCRIPT:
"""

MAX_SUMMARY_CHARS = 12000  # truncate very long transcripts to avoid Claude token limits


def generate_summary(transcript_text: str) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"error": "Adaugă ANTHROPIC_API_KEY în .env pentru rezumat AI"}

    # Truncate if too long
    if len(transcript_text) > MAX_SUMMARY_CHARS:
        transcript_text = transcript_text[:MAX_SUMMARY_CHARS] + "\n[...transcript trunchiat...]"
        log.warning("Transcript trunchiat la %d caractere pentru rezumat", MAX_SUMMARY_CHARS)

    raw = ""
    t0 = time.time()
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": SUMMARY_PROMPT + transcript_text}],
        )
        raw = message.content[0].text.strip()

        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if json_match:
            raw = json_match.group(1)
        elif not raw.startswith("{"):
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start:end + 1]

        data = json.loads(raw)
        for key in ["titlu_sugerat", "rezumat", "puncte_cheie", "intrebari_juridice",
                    "actiuni", "termene_importante", "informatii_client"]:
            if key not in data:
                data[key] = [] if key not in ["rezumat", "titlu_sugerat"] else ""

        log.info("Rezumat generat în %.1fs", time.time() - t0)
        return data

    except json.JSONDecodeError as e:
        log.error("Rezumat — JSON invalid: %s | Brut: %.300s", e, raw)
        return {"error": f"AI nu a returnat JSON valid: {str(e)}", "raw": raw}
    except Exception as e:
        log.error("Rezumat eroare: %s\n%s", e, traceback.format_exc())
        return {"error": str(e)}


# ===== UTILE =====

def format_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def build_docx(segments, summary_data, timestamp, duration):
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    title = doc.add_heading("Transcript Convorbire Client", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    info = doc.add_paragraph()
    info.add_run(f"Data: {timestamp}    |    Durata: {format_time(duration)}").bold = True
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph()

    if isinstance(summary_data, dict) and not summary_data.get("error"):
        if summary_data.get("rezumat"):
            doc.add_heading("Rezumat", level=1)
            doc.add_paragraph(summary_data["rezumat"])
        for key, title_ro in [
            ("puncte_cheie", "Puncte cheie discutate"),
            ("intrebari_juridice", "Intrebari / Aspecte juridice"),
            ("actiuni", "Actiuni necesare"),
            ("termene_importante", "Termene importante"),
            ("informatii_client", "Informatii despre client"),
        ]:
            items = summary_data.get(key, [])
            if items:
                doc.add_heading(title_ro, level=2)
                for item in items:
                    doc.add_paragraph(item, style="List Bullet")
        doc.add_paragraph()

    doc.add_heading("Transcript complet", level=1)
    speaker_colors = {"Avocat": RGBColor(0x00, 0x53, 0x9F), "Client": RGBColor(0x2E, 0x7D, 0x32)}
    for seg in segments:
        p = doc.add_paragraph()
        speaker = seg.get("speaker", "Vorbitor")
        run_time = p.add_run(f"[{format_time(seg['start'])}] ")
        run_time.font.size = Pt(9)
        run_time.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
        run_speaker = p.add_run(f"{speaker}: ")
        run_speaker.bold = True
        run_speaker.font.color.rgb = speaker_colors.get(speaker, RGBColor(0x33, 0x33, 0x33))
        p.add_run(seg.get("text", ""))

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def build_pdf(segments, summary_data, timestamp, duration):
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Transcript Convorbire Client", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, f"Data: {timestamp}  |  Durata: {format_time(duration)}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    if isinstance(summary_data, dict) and not summary_data.get("error"):
        if summary_data.get("rezumat"):
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(0, 8, "Rezumat", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.set_fill_color(245, 245, 245)
            pdf.multi_cell(0, 6, summary_data["rezumat"], fill=True)
            pdf.ln(3)
        for key, title_ro in [
            ("puncte_cheie", "Puncte cheie discutate"),
            ("intrebari_juridice", "Intrebari / Aspecte juridice"),
            ("actiuni", "Actiuni necesare"),
            ("termene_importante", "Termene importante"),
            ("informatii_client", "Informatii despre client"),
        ]:
            items = summary_data.get(key, [])
            if items:
                pdf.set_font("Helvetica", "B", 11)
                pdf.cell(0, 7, title_ro, new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 10)
                for item in items:
                    pdf.multi_cell(0, 5, f"  - {item}")
                pdf.ln(2)

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Transcript complet", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    speaker_colors = {"Avocat": (0, 83, 159), "Client": (46, 125, 50)}
    for seg in segments:
        speaker = seg.get("speaker", "Vorbitor")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.write(6, f"[{format_time(seg['start'])}] ")
        color = speaker_colors.get(speaker, (50, 50, 50))
        pdf.set_text_color(*color)
        pdf.set_font("Helvetica", "B", 10)
        pdf.write(6, f"{speaker}: ")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 10)
        pdf.write(6, seg.get("text", ""))
        pdf.ln(7)

    return io.BytesIO(pdf.output())


# ===== GOOGLE DRIVE =====

def get_redirect_uri():
    base = os.getenv("APP_URL", "").rstrip("/")
    if not base:
        base = request.url_root.rstrip("/")
    return f"{base}/google/callback"


def build_flow(state=None):
    from google_auth_oauthlib.flow import Flow
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [get_redirect_uri()],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=GOOGLE_SCOPES, state=state)
    flow.redirect_uri = get_redirect_uri()
    return flow


def get_drive_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds_data = session.get("google_creds")
    if not creds_data:
        return None
    creds = Credentials(**creds_data)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_or_create_folder(service, name, parent_id=None):
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get("files", [])
    if folders:
        return folders[0]["id"]
    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = service.files().create(body=metadata, fields="id").execute()
    return folder.get("id")


@app.route("/google/auth")
def google_auth():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return jsonify({"error": "Google OAuth nu este configurat"}), 400
    flow = build_flow()
    auth_url, state = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
    session["google_oauth_state"] = state
    return redirect(auth_url)


@app.route("/google/callback")
def google_callback():
    if request.args.get("error"):
        log.warning("Google OAuth refuzat: %s", request.args.get("error"))
        return redirect("/?oauth_error=" + request.args.get("error"))
    state = session.get("google_oauth_state")
    if not state:
        return redirect("/")
    try:
        flow = build_flow(state=state)
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        session["google_creds"] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else [],
        }
        try:
            from googleapiclient.discovery import build as g_build
            oauth2 = g_build("oauth2", "v2", credentials=creds, cache_discovery=False)
            user_info = oauth2.userinfo().get().execute()
            session["google_user"] = {
                "email": user_info.get("email"),
                "name": user_info.get("name"),
                "picture": user_info.get("picture"),
            }
            log.info("Google OAuth reușit: %s", user_info.get("email", "?"))
        except Exception as e:
            log.warning("Nu pot prelua info utilizator Google: %s", e)
            session["google_user"] = {}
    except Exception as e:
        log.error("Google OAuth eroare: %s\n%s", e, traceback.format_exc())
        return redirect("/?oauth_error=token_error")
    return redirect("/")


@app.route("/google/logout", methods=["POST"])
def google_logout():
    session.pop("google_creds", None)
    session.pop("google_user", None)
    session.pop("google_oauth_state", None)
    return jsonify({"ok": True})


@app.route("/google/status")
def google_status():
    return jsonify({
        "connected": bool(session.get("google_creds")),
        "user": session.get("google_user"),
        "configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
    })


@app.route("/google/save", methods=["POST"])
def google_save():
    from googleapiclient.http import MediaIoBaseUpload
    service = get_drive_service()
    if not service:
        return jsonify({"error": "Nu ești conectat la Google."}), 401

    data = request.get_json()
    segments = data.get("segments", [])
    summary_data = data.get("summary", {}) or {}
    timestamp = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    duration = data.get("duration", 0)
    client_folder = (data.get("client_folder") or "").strip()
    custom_title = (data.get("title") or "").strip()

    try:
        root_folder_id = get_or_create_folder(service, GOOGLE_DRIVE_FOLDER_NAME)
        target_folder = root_folder_id
        if client_folder:
            target_folder = get_or_create_folder(service, client_folder, parent_id=root_folder_id)

        docx_buf = build_docx(segments, summary_data, timestamp, duration)
        suggested_title = summary_data.get("titlu_sugerat", "") if isinstance(summary_data, dict) else ""
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str} - {custom_title or suggested_title or f'Transcript {timestamp}'}"

        media = MediaIoBaseUpload(
            docx_buf,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            resumable=False,
        )
        file = service.files().create(
            body={"name": filename, "mimeType": "application/vnd.google-apps.document", "parents": [target_folder]},
            media_body=media,
            fields="id, webViewLink, name",
        ).execute()
        log.info("Salvat în Google Drive: %s", file.get("name"))
        return jsonify({
            "ok": True, "id": file.get("id"), "name": file.get("name"),
            "url": file.get("webViewLink"),
            "folder_url": f"https://drive.google.com/drive/folders/{target_folder}",
        })
    except Exception as e:
        log.error("Google Drive save: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route("/google/folders")
def google_folders():
    service = get_drive_service()
    if not service:
        return jsonify({"folders": []})
    try:
        root_id = get_or_create_folder(service, GOOGLE_DRIVE_FOLDER_NAME)
        results = service.files().list(
            q=f"'{root_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id, name)", orderBy="name",
        ).execute()
        return jsonify({"folders": [f["name"] for f in results.get("files", [])]})
    except Exception as e:
        log.warning("Google folders: %s", e)
        return jsonify({"folders": []})


# ===== RUTE PRINCIPALE =====

@app.route("/")
def index():
    return render_template("index.html")


def _save_transcript(result: dict):
    try:
        ts = result.get("timestamp", datetime.now().strftime("%Y-%m-%d_%H-%M"))
        sid = result.get("session_id", str(uuid.uuid4())[:8])
        path = TRANSCRIPTS_DIR / f"{ts}_{sid}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("Nu pot salva transcriptul local: %s", e)


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "Niciun fișier audio trimis"}), 400

    audio_file = request.files["audio"]
    num_speakers = int(request.form.get("num_speakers", 2))
    model = request.form.get("model", GROQ_MODEL)

    suffix = ".webm"
    original_name = audio_file.filename or ""
    if "." in original_name:
        suffix = "." + original_name.rsplit(".", 1)[-1].lower()

    log.info("Cerere transcriere — fișier: %s, vorbitori: %d, model: %s",
             original_name or "recording", num_speakers, model)

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    voice_fingerprint = None
    voice_fp_raw = request.form.get("voice_fingerprint", "")
    if voice_fp_raw:
        try:
            voice_fingerprint = json.loads(voice_fp_raw)
        except Exception as e:
            log.warning("Voice fingerprint JSON invalid: %s", e)

    try:
        result = transcribe_audio(tmp_path, model=model)

        diarization = diarize_audio(tmp_path, num_speakers, voice_fingerprint)
        if diarization:
            result["segments"] = align_diarization(result["segments"], diarization)
            result["diarization_used"] = True
        else:
            result["segments"] = _fallback_speakers(result["segments"], num_speakers, voice_fingerprint is not None)
            result["diarization_used"] = False

        result["session_id"] = str(uuid.uuid4())[:8]
        result["timestamp"] = datetime.now().strftime("%Y-%m-%d_%H-%M")
        _save_transcript(result)
        return jsonify(result)

    except Exception as e:
        log.error("Eroare transcriere: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.route("/transcribe-chunk", methods=["POST"])
def transcribe_chunk():
    """Endpoint pentru transcrierea live — primește un chunk audio cu offset de timp."""
    if "audio" not in request.files:
        return jsonify({"error": "Niciun fișier audio"}), 400

    audio_file = request.files["audio"]
    time_offset = float(request.form.get("offset", 0))
    model = request.form.get("model", GROQ_MODEL)

    original_name = audio_file.filename or "chunk.webm"
    suffix = "." + original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ".webm"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        result = transcribe_audio(tmp_path, model=model)
        segments = result.get("segments", [])

        # Aplică offset-ul de timp
        for seg in segments:
            seg["start"] = round(seg["start"] + time_offset, 2)
            seg["end"] = round(seg["end"] + time_offset, 2)

        log.info("Chunk transcris (offset %.0fs) — %d segmente", time_offset, len(segments))
        return jsonify({
            "segments": segments,
            "duration": result.get("duration", 0),
            "language": result.get("language", LANGUAGE),
            "hallucinations_removed": result.get("hallucinations_removed", 0),
        })
    except Exception as e:
        log.error("Chunk transcription eroare: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _fallback_speakers(segments: list, num_speakers: int, has_profile: bool) -> list:
    if not segments:
        return segments
    if has_profile:
        labels = ["Avocat"] + [f"Client {i}" if num_speakers > 2 else "Client" for i in range(1, num_speakers)]
    else:
        labels = [f"Vorbitor {i+1}" for i in range(num_speakers)]
    current = 0
    result = []
    for i, seg in enumerate(segments):
        if i > 0 and seg["start"] - segments[i-1]["end"] > 1.2:
            current = (current + 1) % num_speakers
        result.append({**seg, "speaker": labels[current]})
    return result


@app.route("/voice/register", methods=["POST"])
def voice_register():
    if "audio" not in request.files:
        return jsonify({"error": "Niciun fișier audio"}), 400
    audio_file = request.files["audio"]
    original_name = audio_file.filename or "sample.webm"
    suffix = "." + original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ".webm"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    log.info("Înregistrare profil vocal: %s", original_name)
    try:
        fingerprint = extract_voice_fingerprint(tmp_path)
        return jsonify({"fingerprint": fingerprint, "ok": True})
    except Exception as e:
        log.error("Voice register: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.route("/summarize", methods=["POST"])
def summarize():
    data = request.get_json()
    segments = data.get("segments", [])
    if not segments:
        return jsonify({"error": "Transcript gol"}), 400
    transcript_text = "\n".join(
        f"{s.get('speaker', 'Vorbitor')}: {s.get('text', '')}" for s in segments
    )
    return jsonify(generate_summary(transcript_text))


@app.route("/postprocess", methods=["POST"])
def postprocess():
    data = request.get_json()
    segments = data.get("segments", [])
    if not segments:
        return jsonify({"error": "Transcript gol"}), 400
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "Adaugă ANTHROPIC_API_KEY în .env"}), 400
    return jsonify({"segments": post_process_transcript(segments)})


@app.route("/export/word", methods=["POST"])
def export_word():
    data = request.get_json()
    buf = build_docx(
        data.get("segments", []), data.get("summary", {}) or {},
        data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M")),
        data.get("duration", 0),
    )
    return send_file(
        buf, as_attachment=True,
        download_name=f"transcript_{data.get('timestamp', 'export').replace(':', '-')}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.route("/export/pdf", methods=["POST"])
def export_pdf():
    data = request.get_json()
    try:
        buf = build_pdf(
            data.get("segments", []), data.get("summary", {}) or {},
            data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M")),
            data.get("duration", 0),
        )
        return send_file(
            buf, as_attachment=True,
            download_name=f"transcript_{data.get('timestamp', 'export').replace(':', '-')}.pdf",
            mimetype="application/pdf",
        )
    except Exception as e:
        log.error("Export PDF: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": f"Export PDF eșuat: {str(e)}"}), 500


@app.route("/check-api", methods=["GET"])
def check_api():
    return jsonify({
        "has_groq_key": bool(GROQ_API_KEY),
        "has_anthropic_key": bool(ANTHROPIC_API_KEY),
        "google_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "model": GROQ_MODEL,
    })


@app.route("/logs")
def view_logs():
    log_key = os.getenv("LOG_ACCESS_KEY", "")
    if log_key and request.args.get("key") != log_key:
        return jsonify({"error": "Acces interzis. Adaugă ?key=<LOG_ACCESS_KEY>"}), 403

    n = min(int(request.args.get("n", 200)), 1000)
    log_file = LOGS_DIR / "app.log" if LOGS_DIR else None

    if not log_file or not log_file.exists():
        return jsonify({"error": "Log file indisponibil", "lines": []})

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        recent = [l.rstrip() for l in lines[-n:]]
        errors = [l for l in recent if "[ERROR]" in l or "[WARNING]" in l]
        return jsonify({
            "total_lines": len(lines),
            "returned": len(recent),
            "errors_warnings": len(errors),
            "lines": recent,
            "errors_only": errors[-50:],
        })
    except Exception as e:
        return jsonify({"error": str(e), "lines": []}), 500


if __name__ == "__main__":
    PORT = int(os.getenv("PORT", 8080))
    log.info("=" * 50)
    log.info("  Transcriptor AI - BlockchainAttorney")
    log.info("  Groq: %s | Claude: %s | Google: %s",
             "DA" if GROQ_API_KEY else "NU",
             "DA" if ANTHROPIC_API_KEY else "NU",
             "DA" if (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET) else "NU")
    log.info("  http://localhost:%d", PORT)
    log.info("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=PORT)
