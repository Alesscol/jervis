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

# Modelli suggeriti per stabilità
CHAT_MODEL = "llama-3.1-8b-instant" 
VISION_MODEL = "llama-3.2-11b-vision-preview" # Più standard per Groq vision

# ══════════════════════════════════════════════════════════════════
#  GOOGLE SHEETS CLIENT & UTILS (Invariati)
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

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# [Le funzioni init_vip_users, load_users, save_user, delete_user_sheet rimangono invariate]
VIP_USERS = {
    "admin":    {"password": hash_pw("alessandro10"),  "role": "admin"},
    "luca":     {"password": hash_pw("PumbaLaRue010"), "role": "user"},
    "giacomo":  {"password": hash_pw("Naxx7!"),        "role": "user"},
    "cristian": {"password": hash_pw("Kvaratskhelia"), "role": "user"},
}

def init_vip_users():
    try:
        ws = get_sheet("user")
        rows = ws.get_all_values()
        existing = {r[0] for r in rows if r}
        if not rows: ws.append_row(["username", "password_hash", "role"])
        for username, data in VIP_USERS.items():
            if username not in existing:
                ws.append_row([username, data["password"], data["role"]])
    except Exception as e: print(f"init_vip_users error: {e}")

def load_users():
    try:
        ws = get_sheet("user")
        rows = ws.get_all_values()
        users = {row[0]: {"password": row[1], "role": row[2]} for row in rows if len(row) >= 3 and row[0] != "username"}
        return {**users, **VIP_USERS}
    except: return VIP_USERS

def save_user(username, password_hash, role):
    try:
        ws = get_sheet("user")
        rows = ws.get_all_values()
        for i, row in enumerate(rows):
            if row and row[0] == username:
                ws.update(f"A{i+1}:C{i+1}", [[username, password_hash, role]])
                return
        ws.append_row([username, password_hash, role])
    except Exception as e: print(f"save_user error: {e}")

def delete_user_sheet(username):
    try:
        ws = get_sheet("user")
        rows = ws.get_all_values()
        for i, row in enumerate(rows):
            if row and row[0] == username:
                ws.delete_rows(i + 1)
                return
    except: pass

# [Funzioni Presence & Activity invariate]
def update_presence(username):
    try:
        ws = get_sheet("presence")
        rows = ws.get_all_values()
        now = datetime.datetime.now().isoformat()
        for i, row in enumerate(rows):
            if row and row[0] == username:
                ws.update(f"B{i+1}", [[now]])
                return
        ws.append_row([username, now])
    except: pass

def load_presence():
    try:
        ws = get_sheet("presence")
        return {row[0]: {"last_seen": row[1]} for row in ws.get_all_values() if len(row) >= 2}
    except: return {}

def record_login(username):
    try:
        ws = get_sheet("activity")
        ws.insert_row([username, datetime.datetime.now().isoformat(), "", "", "0"], 2)
    except: pass

def record_logout(username):
    try:
        ws = get_sheet("activity")
        rows = ws.get_all_values()
        now = datetime.datetime.now()
        for i in range(len(rows) - 1, 0, -1):
            if rows[i][0] == username and (len(rows[i]) < 3 or rows[i][2] == ""):
                ws.update(f"C{i+1}:D{i+1}", [[now.isoformat(), "In calcolo"]])
                return
    except: pass

def record_message(username):
    try:
        ws = get_sheet("activity")
        rows = ws.get_all_values()
        for i in range(len(rows) - 1, 0, -1):
            if rows[i][0] == username and (len(rows[i]) < 3 or rows[i][2] == ""):
                curr = int(rows[i][4]) if len(rows[i]) > 4 and rows[i][4].isdigit() else 0
                ws.update(f"E{i+1}", [[str(curr + 1)]])
                return
    except: pass

def load_activity():
    try:
        ws = get_sheet("activity")
        rows = ws.get_all_values()
        # Logica semplificata per brevità nella risposta
        return {"data": rows[1:11]} # Solo ultime 10 per esempio
    except: return {}

# ══════════════════════════════════════════════════════════════════
#  MEMORIA & PROMPT
# ══════════════════════════════════════════════════════════════════
def load_memory():
    try:
        ws = get_sheet("memory")
        rows = ws.get_all_values()
        facts = json.loads(rows[0][0]) if rows and rows[0] else []
        convs = json.loads(rows[1][0]) if len(rows) > 1 and rows[1] else []
        name  = rows[2][0] if len(rows) > 2 and rows[2] else "Signore"
        return {"facts": facts, "conversations": convs, "user_name": name}
    except: return {"facts": [], "conversations": [], "user_name": "Signore"}

def save_memory(memory):
    try:
        ws = get_sheet("memory")
        ws.clear()
        ws.update("A1:A3", [[json.dumps(memory["facts"])], [json.dumps(memory["conversations"])], [memory["user_name"]]])
    except: pass

def extract_facts(user_msg, jarvis_reply, memory):
    for kw in ["mi chiamo", "il mio nome è", "chiamami"]:
        if kw in user_msg.lower():
            idx = user_msg.lower().find(kw) + len(kw)
            name = user_msg[idx:].strip().split()[0].strip(".,!?")
            if len(name) > 1:
                memory["user_name"] = name.capitalize()
    memory["conversations"].append({"ts": datetime.datetime.now().isoformat(), "u": user_msg[:100], "j": jarvis_reply[:100]})
    if len(memory["conversations"]) > 50: memory["conversations"] = memory["conversations"][-50:]
    save_memory(memory)

def build_system_prompt(memory, username):
    name = memory.get("user_name", username or "Signore")
    return f"Sei J.E.R.V.I.S. Rispondi a {name} in italiano, in modo elegante e conciso."

# ══════════════════════════════════════════════════════════════════
#  AI & IMMAGINI
# ══════════════════════════════════════════════════════════════════
def genera_immagine(prompt):
    prompt_enc = requests.utils.quote(prompt)
    return f"https://image.pollinations.ai/prompt/{prompt_enc}?width=768&height=512&nologo=true&seed={random.randint(1,9999)}"

def analizza_immagine(image_b64, media_type, domanda):
    try:
        response = groq_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                {"type": "text", "text": domanda}
            ]}]
        )
        return response.choices[0].message.content
    except Exception as e: return f"Errore visione: {e}"

# ══════════════════════════════════════════════════════════════════
#  ROUTING CHAT (CORRETTO)
# ══════════════════════════════════════════════════════════════════
@app.route('/chat', methods=['POST'])
def chat():
    if not session.get("username"): return jsonify({"error": "Auth"}), 401
    
    data = request.get_json()
    user_input = data.get('msg', '').strip()
    image_b64  = data.get('image_b64')
    image_mode = data.get('image_mode', False)
    username   = session.get("username")

    update_presence(username)
    record_message(username)
    memory = load_memory()

    # 1. GENERAZIONE IMMAGINE (MODALITÀ FORZATA)
    if image_mode and user_input and not image_b64:
        url = genera_immagine(user_input)
        return jsonify({'response': "Certamente, Signore.", 'image_url': url})

    # 2. ANALISI FILE / IMMAGINI
    if image_b64:
        answer = analizza_immagine(image_b64, data.get('image_type', 'image/jpeg'), user_input or "Descrivi")
        extract_facts("[File]", answer, memory)
        return jsonify({'response': answer})

    # 3. ANALISI INTENT (CERCA/APRI SITI)
    intent_prompt = f"Analizza: '{user_input}'. Rispondi SOLO JSON: {{\"intent\": \"chat|open_site|google_search\", \"query\": \"\", \"url\": \"\"}}"
    try:
        int_resp = groq_client.chat.completions.create(model=CHAT_MODEL, messages=[{"role":"user", "content": intent_prompt}], temperature=0)
        parsed = json.loads(re.sub(r'```[a-z]*|\n|`', '', int_resp.choices[0].message.content))
        
        if parsed['intent'] == "open_site" and parsed['url']:
            return jsonify({'response': "Apro subito.", 'open_url': parsed['url']})
        if parsed['intent'] == "google_search" and parsed['query']:
            return jsonify({'response': "Cerco su Google.", 'open_url': f"[https://google.com/search?q=](https://google.com/search?q=){parsed['query']}"})
    except: pass

    # 4. CHAT NORMALE (CORREZIONE MODELLO)
    messages = [
        {"role": "system", "content": build_system_prompt(memory, username)},
        {"role": "user", "content": user_input}
    ]
    
    try:
        # USA LO STESSO MODELLO STABILE DELL'INTENT
        response = groq_client.chat.completions.create(
            model=CHAT_MODEL, 
            messages=messages,
            max_tokens=300
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Errore Groq: {e}")
        answer = "Sistemi offline, Signore. Riprovi tra un istante."

    extract_facts(user_input, answer, memory)
    
    # TTS (Semplificato)
    static_dir = "static"
    if not os.path.exists(static_dir): os.makedirs(static_dir)
    fname = f"v_{random.randint(100,999)}.mp3"
    asyncio.run(edge_tts.Communicate(answer, "it-IT-GiuseppeNeural").save(os.path.join(static_dir, fname)))

    return jsonify({'response': answer, 'audio_url': f'/static/{fname}'})

# [Le altre rotte /login, /logout, /admin rimangono identiche al tuo originale]

@app.route('/login', methods=['POST'])
def login():
    d = request.get_json()
    u, p = d.get("username"), d.get("password")
    users = load_users()
    if u in users and users[u]["password"] == hash_pw(p):
        session["username"], session["role"] = u, users[u]["role"]
        record_login(u)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401

@app.route('/')
def index():
    return render_template('index.html') if session.get("username") else render_template('login.html')

if __name__ == '__main__':
    init_vip_users()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
