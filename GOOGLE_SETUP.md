# Ghid Setup Google Drive

Aplicatia salveaza fiecare transcript ca un Google Doc separat in Drive-ul tau.
Pentru asta avem nevoie sa creezi o aplicatie OAuth in Google Cloud Console
(gratuit, 5 minute).

---

## Pas 1: Creeaza proiect in Google Cloud

1. Mergi la https://console.cloud.google.com
2. Login cu contul tau Google
3. Click pe dropdown-ul de proiect (sus, langa "Google Cloud")
4. **NEW PROJECT**
5. Nume: `Transcriptor AI`
6. Click **CREATE**
7. Asteapta 30 sec si selecteaza proiectul nou creat

---

## Pas 2: Activeaza Drive API

1. In bara de cautare scrie: `Google Drive API`
2. Click pe **Google Drive API**
3. **ENABLE**

---

## Pas 3: Configureaza OAuth Consent Screen

1. Meniu stanga → **APIs & Services** → **OAuth consent screen**
2. **User Type:** External → **CREATE**
3. Completeaza:
   - **App name:** `Transcriptor AI`
   - **User support email:** emailul tau
   - **Developer contact:** emailul tau
4. **SAVE AND CONTINUE**
5. **Scopes:** SAVE AND CONTINUE (lasa gol, scope-urile se cer in cod)
6. **Test users:** Click **ADD USERS** si adauga emailul tau Google
7. **SAVE AND CONTINUE**

> **Nota:** App-ul ramane in "Testing" mode. Asta inseamna ca doar tu
> (sau utilizatorii pe care i-ai adaugat la "Test users") puteti folosi
> aplicatia. Pentru un singur avocat e perfect.

---

## Pas 4: Creeaza credentialele OAuth

1. Meniu stanga → **APIs & Services** → **Credentials**
2. **CREATE CREDENTIALS** → **OAuth client ID**
3. **Application type:** Web application
4. **Name:** `Transcriptor AI Web`
5. **Authorized redirect URIs** — adauga:

   **Pentru local (Mac):**
   ```
   http://localhost:8080/google/callback
   ```

   **Pentru Render (cloud):**
   ```
   https://NUMELE-APP.onrender.com/google/callback
   ```
   (inlocuieste NUMELE-APP cu numele real)

6. **CREATE**
7. Apare un popup cu:
   - **Client ID** (incepe cu un numar lung)
   - **Client secret**

Copiaza-le si pune-le in fisierul `.env`:

```
GOOGLE_CLIENT_ID=1234567890-xxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxx
```

Sau pe Render → Environment Variables.

---

## Pas 5: Testeaza!

1. Reporneste aplicatia (`Ctrl+C` apoi `python app.py` local)
2. Deschide aplicatia in browser
3. Click pe **Conecteaza Google Drive** in dreapta-sus
4. Login cu Google si accepta permisiunile
5. Vezi avatarul + email-ul tau sus
6. Inregistreaza o convorbire, transcrie-o
7. Apare sectiunea **"Salveaza in Google Drive"**
8. Optional: completeaza titlu / dosar
9. Click **Salveaza** — apare link spre documentul nou creat

---

## Cum sunt organizate fisierele

In Drive-ul tau:
```
Transcripte Clienti/              ← folder root (se creeaza automat)
├── 2026-05-12 - Consultatie initiala.gdoc
├── 2026-05-15 - Discutie token dispute.gdoc
└── SC Crypto SRL/                ← subfolder (daca completezi "Client")
    ├── 2026-05-20 - Audit smart contract.gdoc
    └── 2026-05-25 - Follow-up dispute.gdoc
```

Fiecare convorbire e un Google Doc separat, complet editabil online, cu:
- Rezumat AI
- Puncte cheie discutate
- Actiuni necesare
- Termene
- Transcript complet cu timestamps

---

## Probleme comune

**"Error 403: access_denied"** — Trebuie sa adaugi emailul tau la
**Test users** in OAuth consent screen.

**"redirect_uri_mismatch"** — URL-ul aplicatiei (din browser) nu se
potriveste cu cel din **Authorized redirect URIs**. Adauga-l acolo.

**Nu apare butonul "Conecteaza Google Drive"** — Verifica ca `GOOGLE_CLIENT_ID`
si `GOOGLE_CLIENT_SECRET` sunt setate corect in `.env`.
