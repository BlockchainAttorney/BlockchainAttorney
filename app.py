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

from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for, session, g
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB max upload
app.secret_key = os.getenv("FLASK_SECRET_KEY", "schimba-aceasta-cheie-in-productie-" + str(uuid.uuid4()))

# Allow OAuth over HTTP for local development
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

    # Console handler (visible in Render logs)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file handler (10 MB × 5 fișiere = 50 MB max)
    if LOGS_DIR:
        try:
            fh = RotatingFileHandler(
                LOGS_DIR / "app.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
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
    duration_ms = int((time.time() - g.start_time) * 1000)
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    log.log(level, "[%s] %s %s → %d (%dms)",
            g.req_id, request.method, request.path, response.status_code, duration_ms)
    return response

@app.errorhandler(Exception)
def _unhandled(e):
    log.error("[%s] Eroare neașteptată: %s\n%s",
              getattr(g, "req_id", "?"), str(e), traceback.format_exc())
    return jsonify({"error": f"Eroare server: {str(e)}"}), 500

@app.errorhandler(413)
def _too_large(e):
    log.warning("Upload prea mare (>100MB)")
    return jsonify({"error": "Fișierul depășește 100 MB. Comprimați înregistrarea și încercați din nou."}), 413


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
5. Corectează NUMAI: cuvinte greșite fonetic (sunau similar dar sunt altceva), \
erori clare de punctuație, capitalizarea numelor proprii și instituțiilor
6. Dacă nu ești 100% sigur că e eroare de transcriere, lasă NESCHIMBAT
7. Răspunde cu un JSON array cu același număr de elemente ca inputul, fiecare element \
fiind textul corectat al segmentului corespunzător

Exemple de corecții permise:
- "vânzare cumpărare" → "vânzare-cumpărare"
- "judecătoria sectorului doi" → "Judecătoria Sectorului 2"
- "a depus o plânjere" → "a depus o plângere"
- "prescripția extinctivă" rămâne neschimbat (corect deja)

Exemple de ce NU faci:
- "am nevoie de" → NU completa ce urmează
- "contractul" → NU adăuga detalii despre contract
- orice interpretare a contextului juridic

Input (JSON array de texte):
"""


def get_groq_client():
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY lipsește. Adaugă cheia în .env (https://console.groq.com)")
    from groq import Groq
    return Groq(api_key=GROQ_API_KEY)


def transcribe_audio(audio_path: str) -> dict:
    t0 = time.time()
    client = get_groq_client()
    file_size = os.path.getsize(audio_path)
    log.info("Transcriere start — fișier: %.1f MB, model: %s", file_size / 1_048_576, GROQ_MODEL)

    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), audio_file.read()),
            model=GROQ_MODEL,
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

    elapsed = time.time() - t0
    log.info("Transcriere finalizată — %d segmente, %.1f sec audio, în %.1fs",
             len(result_segments), float(duration), elapsed)

    return {
        "segments": result_segments,
        "full_text": full_text,
        "language": language,
        "duration": round(float(duration), 2),
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
        if num_speakers == 2:
            label_map = {0: "Vorbitor 1", 1: "Vorbitor 2"}
        else:
            label_map = {i: f"Vorbitor {i+1}" for i in range(num_speakers)}

    log.info("Diarizare — %d segmente detectate pentru %d vorbitori", len(merged), num_speakers)
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
                log.info("Post-procesare Claude — %d segmente corectate în %.1fs",
                         len(segments), time.time() - t0)
                return [{**s, "text": corrected[i]} for i, s in enumerate(segments)]
            else:
                log.warning("Post-procesare — răspuns Claude are lungime diferită (%d vs %d)",
                            len(corrected) if isinstance(corrected, list) else -1, len(segments))
        else:
            log.warning("Post-procesare — Claude nu a returnat JSON array valid. Răspuns brut: %.200s", raw)
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
  "titlu_sugerat": "Titlu scurt si descriptiv pentru fisier (ex: Consultatie initiala - dispute crypto wallet)",
  "rezumat": "Rezumat concis in 2-4 propozitii al subiectului principal si concluziilor",
  "puncte_cheie": [
    "Punct important 1 discutat in convorbire",
    "Punct important 2 discutat in convorbire"
  ],
  "intrebari_juridice": [
    "Intrebare/aspect juridic ridicat de client"
  ],
  "actiuni": [
    "Actiune concreta pe care trebuie sa o faca avocatul sau clientul"
  ],
  "termene_importante": [
    "Termen/data importanta mentionata in convorbire"
  ],
  "informatii_client": [
    "Detaliu factual despre client/situatie (nume companie, sume, blockchain folosit, etc.)"
  ]
}

Reguli:
- Foloseste limba romana
- Daca o sectiune nu are continut relevant, returneaza un array gol []
- Fii concis si specific, nu generic
- Foloseste exact aceste chei

TRANSCRIPT:
"""


def generate_summary(transcript_text: str) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"error": "Adaugă ANTHROPIC_API_KEY în .env pentru rezumat AI"}

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
        log.error("Rezumat — JSON invalid de la Claude: %s | Brut: %.300s", e, raw)
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
        time_str = f"[{format_time(seg['start'])}]"
        run_time = p.add_run(f"{time_str} ")
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
        time_str = f"[{format_time(seg['start'])}] "
        text = seg.get("text", "")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.write(6, time_str)
        color = speaker_colors.get(speaker, (50, 50, 50))
        pdf.set_text_color(*color)
        pdf.set_font("Helvetica", "B", 10)
        pdf.write(6, f"{speaker}: ")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 10)
        pdf.write(6, text)
        pdf.ln(7)

    # fpdf2 ≥ 2.7: output() returns bytes directly (dest="S" removed)
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
        return jsonify({"error": "Google OAuth nu este configurat. Adaugă GOOGLE_CLIENT_ID și GOOGLE_CLIENT_SECRET în .env"}), 400
    flow = build_flow()
    auth_url, state = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
    session["google_oauth_state"] = state
    return redirect(auth_url)


@app.route("/google/callback")
def google_callback():
    # Userul a refuzat permisiunea OAuth
    if request.args.get("error"):
        error = request.args.get("error")
        log.warning("Google OAuth refuzat de utilizator: %s", error)
        return redirect("/?oauth_error=" + error)

    state = session.get("google_oauth_state")
    if not state:
        log.warning("Google OAuth callback fără state în sesiune")
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
            log.info("Google OAuth reușit pentru: %s", user_info.get("email", "?"))
        except Exception as e:
            log.warning("Nu pot prelua info utilizator Google: %s", e)
            session["google_user"] = {}
    except Exception as e:
        log.error("Google OAuth fetch_token eroare: %s\n%s", e, traceback.format_exc())
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
        return jsonify({"error": "Nu ești conectat la Google. Conectează-te mai întâi."}), 401

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
        if custom_title:
            filename = f"{date_str} - {custom_title}"
        elif suggested_title:
            filename = f"{date_str} - {suggested_title}"
        else:
            filename = f"Transcript {timestamp}"

        media = MediaIoBaseUpload(
            docx_buf,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            resumable=False,
        )
        metadata = {
            "name": filename,
            "mimeType": "application/vnd.google-apps.document",
            "parents": [target_folder],
        }
        file = service.files().create(body=metadata, media_body=media, fields="id, webViewLink, name").execute()
        log.info("Salvat în Google Drive: %s", file.get("name"))
        return jsonify({
            "ok": True,
            "id": file.get("id"),
            "name": file.get("name"),
            "url": file.get("webViewLink"),
            "folder_url": f"https://drive.google.com/drive/folders/{target_folder}",
        })

    except Exception as e:
        log.error("Google Drive save eroare: %s\n%s", e, traceback.format_exc())
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
            fields="files(id, name)",
            orderBy="name",
        ).execute()
        folders = [f["name"] for f in results.get("files", [])]
        return jsonify({"folders": folders})
    except Exception as e:
        log.warning("Nu pot lista folderele Google Drive: %s", e)
        return jsonify({"folders": []})


# ===== RUTE PRINCIPALE =====

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "Niciun fișier audio trimis"}), 400

    audio_file = request.files["audio"]
    num_speakers = int(request.form.get("num_speakers", 2))

    suffix = ".webm"
    original_name = audio_file.filename or ""
    if "." in original_name:
        suffix = "." + original_name.rsplit(".", 1)[-1].lower()

    log.info("Cerere transcriere — fișier: %s, vorbitori: %d", original_name or "recording", num_speakers)

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    voice_fp_raw = request.form.get("voice_fingerprint", "")
    voice_fingerprint = None
    if voice_fp_raw:
        try:
            voice_fingerprint = json.loads(voice_fp_raw)
        except Exception as e:
            log.warning("Voice fingerprint JSON invalid: %s", e)

    try:
        result = transcribe_audio(tmp_path)

        diarization = diarize_audio(tmp_path, num_speakers, voice_fingerprint)
        if diarization:
            result["segments"] = align_diarization(result["segments"], diarization)
            result["diarization_used"] = True
        else:
            result["segments"] = _fallback_speakers(result["segments"], num_speakers, voice_fingerprint is not None)
            result["diarization_used"] = False

        session_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        result["session_id"] = session_id
        result["timestamp"] = timestamp

        try:
            transcript_path = TRANSCRIPTS_DIR / f"{timestamp}_{session_id}.json"
            with open(transcript_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning("Nu pot salva transcriptul local: %s", e)

        return jsonify(result)

    except Exception as e:
        log.error("Eroare la transcriere: %s\n%s", e, traceback.format_exc())
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
        log.error("Voice register eroare: %s\n%s", e, traceback.format_exc())
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
    transcript_text = "\n".join(f"{s.get('speaker', 'Vorbitor')}: {s.get('text', '')}" for s in segments)
    summary = generate_summary(transcript_text)
    return jsonify(summary)


@app.route("/postprocess", methods=["POST"])
def postprocess():
    data = request.get_json()
    segments = data.get("segments", [])
    if not segments:
        return jsonify({"error": "Transcript gol"}), 400
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "Adaugă ANTHROPIC_API_KEY în .env pentru corecție AI"}), 400
    corrected = post_process_transcript(segments)
    return jsonify({"segments": corrected})


@app.route("/export/word", methods=["POST"])
def export_word():
    data = request.get_json()
    segments = data.get("segments", [])
    summary_data = data.get("summary", {}) or {}
    timestamp = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    duration = data.get("duration", 0)
    buf = build_docx(segments, summary_data, timestamp, duration)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"transcript_{timestamp.replace(':', '-')}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.route("/export/pdf", methods=["POST"])
def export_pdf():
    data = request.get_json()
    segments = data.get("segments", [])
    summary_data = data.get("summary", {}) or {}
    timestamp = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    duration = data.get("duration", 0)
    try:
        buf = build_pdf(segments, summary_data, timestamp, duration)
        return send_file(
            buf,
            as_attachment=True,
            download_name=f"transcript_{timestamp.replace(':', '-')}.pdf",
            mimetype="application/pdf",
        )
    except Exception as e:
        log.error("Export PDF eroare: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": f"Export PDF eșuat: {str(e)}"}), 500


@app.route("/check-api", methods=["GET"])
def check_api():
    return jsonify({
        "has_groq_key": bool(GROQ_API_KEY),
        "has_anthropic_key": bool(ANTHROPIC_API_KEY),
        "google_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "model": GROQ_MODEL,
    })


# ===== LOGS ENDPOINT =====

@app.route("/logs")
def view_logs():
    """Returnează ultimele N linii din log. Accesibil doar din rețeaua internă sau cu cheie."""
    log_key = os.getenv("LOG_ACCESS_KEY", "")
    if log_key and request.args.get("key") != log_key:
        return jsonify({"error": "Acces interzis. Adaugă ?key=<LOG_ACCESS_KEY>"}), 403

    n = min(int(request.args.get("n", 200)), 1000)
    log_file = LOGS_DIR / "app.log" if LOGS_DIR else None

    if not log_file or not log_file.exists():
        return jsonify({"error": "Fișierul de log nu există (posibil pe sistem fără acces la disc)", "lines": []})

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
    log.info("=" * 50)
    log.info("  Transcriere (Groq): %s", "DA" if GROQ_API_KEY else "NU")
    log.info("  Rezumat AI (Claude): %s", "DA" if ANTHROPIC_API_KEY else "NU")
    log.info("  Google Drive: %s", "DA" if (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET) else "NU")
    log.info("  Limba: %s", LANGUAGE)
    log.info("  Deschide: http://localhost:%d", PORT)
    log.info("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=PORT)
