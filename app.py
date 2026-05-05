import os
import json
import asyncio
import random
import datetime
import requests
import hashlib
import re
import base64
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, render_template, request, jsonify, session
import edge_tts
from groq import Groq

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "jervis-super-secret-2026")

# ══════════════════════════════════════════════════════════════════
#  CONFIGURAZIONE
# ══════════════════════════════════════════════════════════════════
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
groq_client = Groq(api_key=GROQ_API_KEY)
SHEET_ID = "1Iz1e8_Vgl6X6HlmPqSWx-j5oIo7G5nFxRSzLVfftd9c"

# ══════════════════════════════════════════════════════════════════
#  GOOGLE SHEETS CLIENT
# ══════════════════════════════════════════════════════════════════
def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDS_JSON")
    if creds_json:
        creds_json = creds_json.replace("\\n", "\n")
        creds_dict = json.loads(creds_json, strict=False)
    else:
        with open("jervis-credentials.json", "r") as f:
            creds_dict = json.load(f)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet(tab_name):
    client = get_sheets_client()
    sh = client.open_by_key(SHEET_ID)
    try:
        return sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=tab_name, rows=1000, cols=20)

# ══════════════════════════════════════════════════════════════════
#  STRUMENTO RICERCA WEB
# ══════════════════════════════════════════════════════════════════
def web_search(query):
    """Esegue una ricerca rapida online se Jervis ne ha bisogno."""
    try:
        # Utilizziamo DuckDuckGo via API libera per rapidità
        url = f"https://api.duckduckgo.com/?q={requests.utils.quote(query)}&format=json&no_html=1"
        res = requests.get(url).json()
        abstract = res.get("AbstractText", "")
        if abstract:
            return f"\n[Informazioni dal Web]: {abstract}\n"
        return ""
    except:
        return ""

# ══════════════════════════════════════════════════════════════════
#  NUOVA GESTIONE MEMORIA PER UTENTE (Tab "memory")
#  Struttura: Col A (username) | Col B (JSON Facts) | Col C (JSON History)
# ══════════════════════════════════════════════════════════════════
def load_user_memory(username):
    try:
        ws = get_sheet("memory")
        rows = ws.get_all_values()
        for row in rows:
            if row[0] == username:
                return {
                    "facts": json.loads(row[1]) if len(row) > 1 else [],
                    "conversations": json.loads(row[2]) if len(row) > 2 else []
                }
        return {"facts": [], "conversations": []}
    except:
        return {"facts": [], "conversations": []}

def save_user_memory(username, memory):
    try:
        ws = get_sheet("memory")
        rows = ws.get_all_values()
        facts_json = json.dumps(memory.get("facts", []), ensure_ascii=False)
        convs_json = json.dumps(memory.get("conversations", []), ensure_ascii=False)
        
        for i, row in enumerate(rows):
            if row[0] == username:
                ws.update(f"B{i+1}:C{i+1}", [[facts_json, convs_json]])
                return
        ws.append_row([username, facts_json, convs_json])
    except Exception as e:
        print(f"Errore salvataggio memoria: {e}")

# ══════════════════════════════════════════════════════════════════
#  LOGICA ESTRAZIONE FATTI (Migliorata con IA)
# ══════════════════════════════════════════════════════════════════
def extract_facts_ai(user_msg, memory, username):
    # Analisi rapida per vedere se l'utente ha rivelato preferenze o info
    prompt = f"Estrai info personali brevi da: '{user_msg}'. Rispondi solo con il fatto o 'null'."
    try:
        res = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50
        )
        fact = res.choices[0].message.content.strip()
        if "null" not in fact.lower() and len(fact) > 3:
            if fact not in memory["facts"]:
                memory["facts"].append(fact)
    except: pass
    
    # Mantieni cronologia
    memory["conversations"].append({"t": datetime.datetime.now().isoformat()[:16], "m": user_msg[:100]})
    if len(memory["conversations"]) > 10: memory["conversations"].pop(0)

# ══════════════════════════════════════════════════════════════════
#  TTS & UTILS (Mantenuti dal tuo codice)
# ══════════════════════════════════════════════════════════════════
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

async def generate_voice(text, filepath):
    try:
        communicate = edge_tts.Communicate(text, "it-IT-GiuseppeNeural")
        await communicate.save(filepath)
    except: pass

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try: return loop.run_until_complete(coro)
    finally: loop.close()

# ══════════════════════════════════════════════════════════════════
#  ROUTE CHAT (IL CUORE AGGIORNATO)
# ══════════════════════════════════════════════════════════════════
@app.route('/chat', methods=['POST'])
def chat():
    if not session.get("username"): return jsonify({"error": "Auth required"}), 401
    
    data = request.get_json()
    user_input = data.get('msg', '').strip()
    username = session["username"]
    image_mode = data.get('image_mode', False)

    # 1. Carica Memoria specifica di questo utente
    memory = load_user_memory(username)
    
    # 2. Controllo Intent Web (Serve cercare online?)
    web_info = ""
    intent_check = f"L'utente vuole info recenti o dati che un'IA del 2024 non sa? Rispondi SI o NO. Messaggio: {user_input}"
    try:
        check = groq_client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role":"user", "content":intent_check}])
        if "SI" in check.choices[0].message.content.upper():
            web_info = web_search(user_input)
    except: pass

    # 3. Costruzione Prompt con Memoria e Web
    facts_str = ", ".join(memory["facts"]) if memory["facts"] else "Nessuna info salvata."
    system_prompt = f"""Sei J.E.R.V.I.S., assistente di {username}.
    Info note su {username}: {facts_str}.
    Dati web aggiornati: {web_info}
    Sii elegante, formale ma ironico. Risposte brevi (max 2 frasi)."""

    # 4. Generazione Risposta
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_input}]
        )
        answer = response.choices[0].message.content.strip()
    except:
        answer = "Sistemi in sovraccarico, Signore."

    # 5. Salvataggio Memoria e Audio
    extract_facts_ai(user_input, memory, username)
    save_user_memory(username, memory)

    static_dir = os.path.join(app.root_path, 'static')
    os.makedirs(static_dir, exist_ok=True)
    audio_file = f"voice_{random.randint(1000,9999)}.mp3"
    run_async(generate_voice(answer, os.path.join(static_dir, audio_file)))

    return jsonify({'response': answer, 'audio_url': f'/static/{audio_file}'})

# [IL RESTO DELLE TUE ROTTE LOGIN/ADMIN RIMANE INVARIATO...]
# Copia qui le tue funzioni: init_vip_users, login, logout, get_activity, ecc.
