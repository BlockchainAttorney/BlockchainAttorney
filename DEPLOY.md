# Ghid Deploy — Transcriptor AI pe Render.com

Acest ghid te invata sa publici aplicatia pe internet, accesibila de pe orice
device (tableta, telefon, laptop), gratuit.

---

## Pas 1: Obtine cheile API gratuite

### A. Groq (transcriere - OBLIGATORIU)

1. Mergi la https://console.groq.com/keys
2. Sign up cu Google (gratuit, fara card)
3. **Create API Key** → copiaza cheia (incepe cu `gsk_...`)
4. Salveaz-o intr-un loc sigur

> **Free tier:** ~2 ore audio/zi gratuit. Suficient pentru avocatura normala.

### B. Anthropic (rezumat AI - OBLIGATORIU pentru analiza)

1. Mergi la https://console.anthropic.com
2. Sign up
3. Adauga $5 credit (suficient pentru ~5000 rezumate)
4. **API Keys** → **Create Key** → copiaza cheia (incepe cu `sk-ant-...`)

### C. Google Cloud (Drive - OPTIONAL dar recomandat)

Vezi `GOOGLE_SETUP.md` pentru pasi detaliati.

---

## Pas 2: Pune codul pe GitHub (daca nu e deja)

Codul deja exista la `BlockchainAttorney/BlockchainAttorney`, branch
`claude/ai-transcription-app-qZpyB`.

Daca vrei propriul tau repository:

1. Mergi la https://github.com/new
2. Nume: `transcriptor-ai`, **Private**
3. Click **Create**
4. In Terminal pe Mac:
   ```bash
   cd ~/Desktop/TranscriptorAI
   git remote set-url origin https://github.com/USERNAME-TAU/transcriptor-ai.git
   git push -u origin claude/ai-transcription-app-qZpyB:main
   ```

---

## Pas 3: Deploy pe Render.com

### 3.1 Creeaza cont Render

1. Mergi la https://render.com
2. **Sign up** (cu GitHub - mai usor)
3. Conecteaza-ti contul GitHub

### 3.2 Creeaza Web Service

1. Click **New +** → **Web Service**
2. Conecteaza repo-ul `transcriptor-ai`
3. Render va detecta automat `render.yaml` si configureaza totul

### 3.3 Adauga variabile de mediu

In Dashboard-ul Render, mergi la **Environment** si adauga:

| Cheie | Valoare |
|-------|---------|
| `GROQ_API_KEY` | `gsk_...` (de la pasul 1A) |
| `ANTHROPIC_API_KEY` | `sk-ant-...` (de la pasul 1B) |
| `GOOGLE_CLIENT_ID` | (de la GOOGLE_SETUP.md, optional) |
| `GOOGLE_CLIENT_SECRET` | (de la GOOGLE_SETUP.md, optional) |
| `APP_URL` | URL-ul aplicatiei (ex: `https://transcriptor-ai.onrender.com`) |

### 3.4 Deploy!

Click **Manual Deploy** → **Deploy latest commit**.

Astepta 2-5 minute. Cand vezi "Live", aplicatia e online!

---

## Pas 4: Foloseste aplicatia

Deschide URL-ul (ex: `https://transcriptor-ai.onrender.com`) **pe orice device**:
- Pe iPad/tableta Android in Safari/Chrome
- Pe iPhone in Safari
- Pe orice laptop

> **Important:** HTTPS-ul automat de la Render permite accesul la microfon
> de pe orice device fara probleme. Aici e diferenta majora fata de varianta locala.

---

## Costuri totale

- Render Free tier: **$0/luna** (cu o limitare: aplicatia "doarme" dupa 15 min
  inactivitate, prima cerere ia ~30 sec sa porneasca)
- Groq: **$0** (in limita free tier)
- Anthropic: **~$0.001 per convorbire** (5000 convorbiri = $5)
- Google Drive: **$0**

**Total estimat: $0-2/luna** pentru un avocat normal.

---

## Upgrade Render (optional, $7/luna)

Daca vrei sa nu mai "doarma" aplicatia (instant la fiecare deschidere):
- Render Starter Plan: $7/luna
- Stay always-on, mai rapid, RAM dublu

---

## Probleme comune

**"Address already in use"** — Nu apare pe Render (only macOS local).

**"Microfonul nu functioneaza"** — Render foloseste HTTPS automat, asa ca
microfonul ar trebui sa mearga de pe orice device.

**"Application failed to respond"** — App-ul a adormit, asteapta 30 sec si
reincarca pagina.

**Logs si debugging** — Render Dashboard → Logs tab. Vezi orice eroare aici.
