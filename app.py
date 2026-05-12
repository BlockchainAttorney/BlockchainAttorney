import os
import uuid
import json
import tempfile
import traceback
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, render_template, send_file
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB max upload

TRANSCRIPTS_DIR = Path("transcripts")
TRANSCRIPTS_DIR.mkdir(exist_ok=True)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
LANGUAGE = os.getenv("LANGUAGE", "ro")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print(f"[Whisper] Se incarca modelul '{WHISPER_MODEL}'... (prima data dureaza mai mult)")
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        print("[Whisper] Model incarcat.")
    return _whisper_model


def transcribe_audio(audio_path: str) -> dict:
    model = get_whisper_model()
    segments, info = model.transcribe(
        audio_path,
        language=LANGUAGE,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    result_segments = []
    full_text_parts = []

    for seg in segments:
        result_segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
            "speaker": "Vorbitor",
        })
        full_text_parts.append(seg.text.strip())

    full_text = " ".join(full_text_parts)
    return {
        "segments": result_segments,
        "full_text": full_text,
        "language": info.language,
        "duration": round(info.duration, 2) if info.duration else 0,
    }


def assign_speakers(segments: list, num_speakers: int = 2) -> list:
    """
    Simplified speaker assignment based on pauses between segments.
    Alternates speakers when a pause > 1.5s is detected.
    """
    if not segments:
        return segments

    speaker_labels = ["Avocat", "Client"]
    current_speaker = 0

    updated = []
    for i, seg in enumerate(segments):
        if i > 0:
            pause = seg["start"] - segments[i - 1]["end"]
            if pause > 1.5:
                current_speaker = 1 - current_speaker

        updated.append({**seg, "speaker": speaker_labels[current_speaker]})

    return updated


def generate_summary(transcript_text: str) -> str:
    if not ANTHROPIC_API_KEY:
        return ""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Esti asistentul unui avocat specializat in blockchain si criptomonede. "
                        "Analizeaza urmatorul transcript al unei convorbiri cu un client si ofera:\n"
                        "1. Rezumat scurt (3-5 propozitii)\n"
                        "2. Puncte cheie discutate (lista)\n"
                        "3. Actiuni necesare (daca exista)\n\n"
                        f"TRANSCRIPT:\n{transcript_text}"
                    ),
                }
            ],
        )
        return message.content[0].text
    except Exception as e:
        print(f"[Summary] Eroare: {e}")
        return f"Rezumatul nu a putut fi generat: {str(e)}"


def format_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


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

    try:
        result = transcribe_audio(tmp_path)
        result["segments"] = assign_speakers(result["segments"], num_speakers)

        session_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        result["session_id"] = session_id
        result["timestamp"] = timestamp

        transcript_path = TRANSCRIPTS_DIR / f"{timestamp}_{session_id}.json"
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        os.unlink(tmp_path)


@app.route("/summarize", methods=["POST"])
def summarize():
    data = request.get_json()
    transcript_text = data.get("text", "")
    if not transcript_text:
        return jsonify({"error": "Text lipsa"}), 400

    summary = generate_summary(transcript_text)
    return jsonify({"summary": summary})


@app.route("/export/word", methods=["POST"])
def export_word():
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    data = request.get_json()
    segments = data.get("segments", [])
    summary = data.get("summary", "")
    timestamp = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    duration = data.get("duration", 0)

    doc = Document()

    title = doc.add_heading("Transcript Convorbire Client", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    info = doc.add_paragraph()
    info.add_run(f"Data: {timestamp}    |    Durata: {format_time(duration)}").bold = True
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph()

    if summary:
        doc.add_heading("Rezumat AI", level=1)
        doc.add_paragraph(summary)
        doc.add_paragraph()

    doc.add_heading("Transcript", level=1)

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

    out_path = TRANSCRIPTS_DIR / f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    doc.save(str(out_path))

    return send_file(
        str(out_path),
        as_attachment=True,
        download_name=f"transcript_{timestamp.replace(':', '-')}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.route("/export/pdf", methods=["POST"])
def export_pdf():
    from fpdf import FPDF

    data = request.get_json()
    segments = data.get("segments", [])
    summary = data.get("summary", "")
    timestamp = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    duration = data.get("duration", 0)

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

    if summary:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Rezumat AI", ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_fill_color(245, 245, 245)
        pdf.multi_cell(0, 6, summary, fill=True)
        pdf.ln(5)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Transcript", ln=True)
    pdf.ln(2)

    speaker_colors = {
        "Avocat": (0, 83, 159),
        "Client": (46, 125, 50),
    }

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

    out_path = TRANSCRIPTS_DIR / f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf.output(str(out_path))

    return send_file(
        str(out_path),
        as_attachment=True,
        download_name=f"transcript_{timestamp.replace(':', '-')}.pdf",
        mimetype="application/pdf",
    )


@app.route("/check-api", methods=["GET"])
def check_api():
    has_key = bool(ANTHROPIC_API_KEY)
    return jsonify({"has_anthropic_key": has_key, "whisper_model": WHISPER_MODEL})


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  Transcriptor AI - BlockchainAttorney")
    print("=" * 50)
    print(f"  Model Whisper: {WHISPER_MODEL}")
    print(f"  Limba: {LANGUAGE}")
    print(f"  Rezumat AI: {'DA' if ANTHROPIC_API_KEY else 'NU (adauga ANTHROPIC_API_KEY in .env)'}")
    print(f"\n  Deschide: http://localhost:5000")
    print("=" * 50 + "\n")
    app.run(debug=False, host="0.0.0.0", port=5000)
