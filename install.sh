#!/bin/bash
set -e

echo "=================================="
echo " Instalare Transcriptor AI (local)"
echo "=================================="

if ! command -v python3 &> /dev/null; then
    echo "EROARE: Python 3 nu este instalat."
    exit 1
fi

echo ""
echo "[1/3] Creare mediu virtual Python..."
python3 -m venv venv
source venv/bin/activate

echo ""
echo "[2/3] Instalare librarii Python..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "[3/3] Configurare..."
if [ ! -f .env ]; then
    cp .env.example .env
fi

mkdir -p transcripts

echo ""
echo "=================================="
echo " Instalare completa!"
echo "=================================="
echo ""
echo " Pasii urmatori:"
echo "   1. Editeaza fisierul .env (open -a TextEdit .env)"
echo "   2. Adauga cheia GROQ_API_KEY (gratuit la https://console.groq.com)"
echo "   3. Adauga cheia ANTHROPIC_API_KEY (https://console.anthropic.com)"
echo "   4. Optional: Google Drive (vezi GOOGLE_SETUP.md)"
echo ""
echo " Pornire:"
echo "   source venv/bin/activate"
echo "   python app.py"
echo ""
echo " Deschide: http://localhost:8080"
echo ""
