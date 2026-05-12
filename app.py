import os
import io
import uuid
import json
import tempfile
import traceback
import re
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for, session
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB max upload
app.secret_key = os.getenv("FLASK_SECRET_KEY", "schimba-aceasta-cheie-in-productie-" + str(uuid.uuid4()))

# Allow OAuth over HTTP for local development
if os.getenv("FLASK_ENV", "development") == "development":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

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


# ===== TRANSCRIERE =====

# Vocabular juridic general — ghideaza Whisper spre termeni specifici domeniului
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

# Post-procesare Claude — corector strict, fara adaugiri sau interpretari
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
        raise RuntimeError("GROQ_API_KEY lipseste. Adauga cheia in .env (https://console.groq.com)")
    from groq import Groq
    return Groq(api_key=GROQ_API_KEY)


def transcribe_audio(audio_path: str) -> dict:
    client = get_groq_client()

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
            start = seg.get("start", 0)
            end = seg.get("end", 0)
            text = seg.get("text", "")
        else:
            start = getattr(seg, "start", 0)
            end = getattr(seg, "end", 0)
            text = getattr(seg, "text", "")
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


def extract_voice_fingerprint(audio_path: str) -> list:
    """Extract MFCC-based voice fingerprint (20 coefficients mean vector)."""
    import librosa
    import numpy as np
    y, sr = librosa.load(audio_path, sr=16000, mono=True, duration=30)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20)
    return np.mean(mfcc, axis=1).tolist()


def diarize_audio(audio_path: str, num_speakers: int, voice_fingerprint: list = None) -> list:
    """
    Speaker diarization via MFCC + KMeans clustering.
    Returns list of (start_sec, end_sec, speaker_label) tuples.
    If voice_fingerprint provided, auto-labels lawyer's voice as 'Avocat'.
    """
    import librosa
    import numpy as np
    from sklearn.cluster import KMeans

    try:
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
    except Exception as e:
        print(f"[Diarize] Nu pot incarca audio: {e}")
        return []

    hop_length = int(sr * 0.4)  # ~400ms frames
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop_length)
    mfcc = mfcc.T  # [n_frames, 20]

    if len(mfcc) < num_speakers * 4:
        return []

    kmeans = KMeans(n_clusters=num_speakers, random_state=42, n_init=10)
    labels = kmeans.fit_predict(mfcc)

    # Group consecutive frames into segments
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

    # Merge very short segments (< 0.8s) with neighbours
    merged = []
    for seg in raw_segs:
        if merged and (seg[1] - seg[0]) < 0.8:
            prev = merged[-1]
            if prev[2] == seg[2]:
                prev[1] = seg[1]
                continue
        merged.append(seg)

    # Build speaker label map
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

    return [(s[0], s[1], label_map.get(s[2], "Vorbitor")) for s in merged]


def post_process_transcript(segments: list) -> list:
    """Use Claude to fix transcription errors — strictly no additions or rewriting."""
    if not ANTHROPIC_API_KEY or not segments:
        return segments
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

        # Extract JSON array from response
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            corrected = json.loads(match.group(0))
            if isinstance(corrected, list) and len(corrected) == len(segments):
                return [{**s, "text": corrected[i]} for i, s in enumerate(segments)]
    except Exception as e:
        print(f"[PostProcess] Eroare: {e}")
    return segments  # Return original on any failure


def align_diarization(groq_segments: list, diarization: list) -> list:
    """Map speaker labels from diarization onto Groq transcript segments by overlap."""
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
        return {"error": "Adauga ANTHROPIC_API_KEY in .env pentru rezumat AI"}

    raw = ""
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
        for key in ["titlu_sugerat", "rezumat", "puncte_cheie", "intrebari_juridice", "actiuni", "termene_importante", "informatii_client"]:
            if key not in data:
                data[key] = [] if key not in ["rezumat", "titlu_sugerat"] else ""
        return data

    except json.JSONDecodeError as e:
        return {"error": f"AI nu a returnat JSON valid: {str(e)}", "raw": raw}
    except Exception as e:
        print(f"[Summary] Eroare: {e}")
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
    pdf.cell(0, 12, "Transcript Convorbire Client", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, f"Data: {timestamp}  |  Durata: {format_time(duration)}", ln=True, align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    if isinstance(summary_data, dict) and not summary_data.get("error"):
        if summary_data.get("rezumat"):
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(0, 8, "Rezumat", ln=True)
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
                pdf.cell(0, 7, title_ro, ln=True)
                pdf.set_font("Helvetica", "", 10)
                for item in items:
                    pdf.multi_cell(0, 5, f"  - {item}")
                pdf.ln(2)

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Transcript complet", ln=True)
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

    out = bytes(pdf.output(dest="S"))
    return io.BytesIO(out)


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
        return jsonify({"error": "Google OAuth nu este configurat. Adauga GOOGLE_CLIENT_ID si GOOGLE_CLIENT_SECRET in .env"}), 400
    flow = build_flow()
    auth_url, state = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
    session["google_oauth_state"] = state
    return redirect(auth_url)


@app.route("/google/callback")
def google_callback():
    state = session.get("google_oauth_state")
    if not state:
        return redirect("/")
    flow = build_flow(state=state)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session["google_creds"] = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    # Get user info
    try:
        from googleapiclient.discovery import build
        oauth2 = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        user_info = oauth2.userinfo().get().execute()
        session["google_user"] = {
            "email": user_info.get("email"),
            "name": user_info.get("name"),
            "picture": user_info.get("picture"),
        }
    except Exception:
        session["google_user"] = {}
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
        return jsonify({"error": "Nu esti conectat la Google. Conecteaza-te mai intai."}), 401

    data = request.get_json()
    segments = data.get("segments", [])
    summary_data = data.get("summary", {}) or {}
    timestamp = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    duration = data.get("duration", 0)
    client_folder = (data.get("client_folder") or "").strip()
    custom_title = (data.get("title") or "").strip()

    try:
        # Get/create root folder
        root_folder_id = get_or_create_folder(service, GOOGLE_DRIVE_FOLDER_NAME)

        # Get/create client subfolder if specified
        target_folder = root_folder_id
        if client_folder:
            target_folder = get_or_create_folder(service, client_folder, parent_id=root_folder_id)

        # Build docx
        docx_buf = build_docx(segments, summary_data, timestamp, duration)

        # Determine filename
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
            "mimeType": "application/vnd.google-apps.document",  # Convert to Google Doc
            "parents": [target_folder],
        }
        file = service.files().create(body=metadata, media_body=media, fields="id, webViewLink, name").execute()
        return jsonify({
            "ok": True,
            "id": file.get("id"),
            "name": file.get("name"),
            "url": file.get("webViewLink"),
            "folder_url": f"https://drive.google.com/drive/folders/{target_folder}",
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/google/folders")
def google_folders():
    """List existing client subfolders for auto-complete."""
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
    except Exception:
        return jsonify({"folders": []})


# ===== RUTE PRINCIPALE =====

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "Niciun fisier audio trimis"}), 400

    audio_file = request.files["audio"]
    num_speakers = int(request.form.get("num_speakers", 2))

    suffix = ".webm"
    original_name = audio_file.filename or ""
    if "." in original_name:
        suffix = "." + original_name.rsplit(".", 1)[-1]

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    # Optional voice fingerprint for lawyer auto-detection
    voice_fp_raw = request.form.get("voice_fingerprint", "")
    voice_fingerprint = None
    if voice_fp_raw:
        try:
            voice_fingerprint = json.loads(voice_fp_raw)
        except Exception:
            pass

    try:
        result = transcribe_audio(tmp_path)

        # Speaker diarization (voice-based)
        diarization = diarize_audio(tmp_path, num_speakers, voice_fingerprint)
        if diarization:
            result["segments"] = align_diarization(result["segments"], diarization)
            result["diarization_used"] = True
        else:
            # Fallback: simple pause-based if diarization failed
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
        except Exception:
            pass

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _fallback_speakers(segments: list, num_speakers: int, has_profile: bool) -> list:
    """Simple pause-based fallback when diarization fails."""
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
    """Extract voice fingerprint from a recorded sample."""
    if "audio" not in request.files:
        return jsonify({"error": "Niciun fisier audio"}), 400

    audio_file = request.files["audio"]
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        fingerprint = extract_voice_fingerprint(tmp_path)
        return jsonify({"fingerprint": fingerprint, "ok": True})
    except Exception as e:
        traceback.print_exc()
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
        return jsonify({"error": "Adauga ANTHROPIC_API_KEY in .env pentru corectie AI"}), 400
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
    buf = build_pdf(segments, summary_data, timestamp, duration)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"transcript_{timestamp.replace(':', '-')}.pdf",
        mimetype="application/pdf",
    )


@app.route("/check-api", methods=["GET"])
def check_api():
    return jsonify({
        "has_groq_key": bool(GROQ_API_KEY),
        "has_anthropic_key": bool(ANTHROPIC_API_KEY),
        "google_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "model": GROQ_MODEL,
    })


if __name__ == "__main__":
    PORT = int(os.getenv("PORT", 8080))
    print("\n" + "=" * 50)
    print("  Transcriptor AI - BlockchainAttorney")
    print("=" * 50)
    print(f"  Transcriere (Groq): {'DA' if GROQ_API_KEY else 'NU'}")
    print(f"  Rezumat AI (Claude): {'DA' if ANTHROPIC_API_KEY else 'NU'}")
    print(f"  Google Drive: {'DA' if (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET) else 'NU'}")
    print(f"  Limba: {LANGUAGE}")
    print(f"\n  Deschide: http://localhost:{PORT}")
    print("=" * 50 + "\n")
    app.run(debug=False, host="0.0.0.0", port=PORT)
