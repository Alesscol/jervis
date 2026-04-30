# J.E.R.V.I.S.
**Just Extremely Responsive Virtual Intelligence System**

---

## Avvio rapido (PC locale)

1. Ottieni la chiave API Groq **GRATIS** su https://console.groq.com
2. Apri `app.py` e sostituisci `QUI_LA_TUA_CHIAVE_GROQ` con la tua chiave
3. Fai doppio clic su `avvia_jervis.bat`
4. Il browser si apre da solo su http://127.0.0.1:5000

---

## Deploy online GRATIS su Railway

1. Crea un account su https://railway.app (gratis)
2. Clicca **"New Project" → "Deploy from GitHub"**
3. Carica questa cartella su un repository GitHub
4. In Railway vai su **Variables** e aggiungi:
   - `GROQ_API_KEY` = la tua chiave Groq
5. Railway avvia tutto automaticamente — ti dà un link pubblico!

---

## Funzionalità

- 💬 Chat testuale con J.E.R.V.I.S.
- 🎙 Riconoscimento vocale (dici "Jervis..." + comando)
- 🔊 Risposta vocale in italiano (edge-tts)
- 🧠 **Memoria persistente** — ricorda il tuo nome e i fatti importanti tra una sessione e l'altra
- ⬡ Pannello memoria — vedi e cancella cosa sa di te
- 🌐 Funziona online

---

## Quota Groq (gratuita)

- ~6000 richieste/giorno
- Modello: llama-3.3-70b (molto intelligente)
- Nessuna carta di credito richiesta

---

## Struttura file

```
jervis/
├── app.py              ← Cervello principale
├── requirements.txt    ← Dipendenze Python
├── Procfile            ← Per deploy online
├── avvia_jervis.bat    ← Avvio Windows
├── jervis_memory.json  ← Memoria (si crea automaticamente)
├── templates/
│   └── index.html      ← Interfaccia
└── static/             ← File audio temporanei
```
