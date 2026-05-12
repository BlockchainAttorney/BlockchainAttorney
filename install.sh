#!/bin/bash
set -e

echo "=================================="
echo " Instalare Transcriptor AI"
echo "=================================="

# Verificare Python
if ! command -v python3 &> /dev/null; then
    echo "EROARE: Python 3 nu este instalat."
    exit 1
fi

# Instalare ffmpeg (necesar pentru procesare audio)
echo ""
echo "[1/4] Instalare ffmpeg..."
if command -v apt-get &> /dev/null; then
    sudo apt-get install -y ffmpeg
elif command -v brew &> /dev/null; then
    brew install ffmpeg
elif command -v choco &> /dev/null; then
    choco install ffmpeg -y
else
    echo "ATENTIE: Instalati ffmpeg manual de la https://ffmpeg.org/download.html"
fi

# Creare environment virtual
echo ""
echo "[2/4] Creare mediu virtual Python..."
python3 -m venv venv
source venv/bin/activate

# Instalare dependente Python
echo ""
echo "[3/4] Instalare librarii Python (poate dura 5-10 minute)..."
pip install --upgrade pip
pip install flask faster-whisper anthropic python-docx fpdf2 python-dotenv pydub

# Configurare .env
echo ""
echo "[4/4] Configurare..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "IMPORTANT: Deschide fisierul .env si adauga cheia ta Anthropic API!"
    echo "Obtine o cheie gratuita la: https://console.anthropic.com"
fi

# Creare folder pentru transcrieri
mkdir -p transcripts

echo ""
echo "=================================="
echo " Instalare completa!"
echo "=================================="
echo ""
echo " Urmatorul pas:"
echo "   1. Editeaza fisierul .env si adauga ANTHROPIC_API_KEY"
echo "   2. Ruleaza: source venv/bin/activate"
echo "   3. Ruleaza: python app.py"
echo "   4. Deschide: http://localhost:5000"
echo ""
