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
#  CONFIGURAZIONE API
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
#  GESTIONE UTENTI & ATTIVITÀ (ORIGINALE)
# ══════════════════════════════════════════════════════════════════
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

VIP_USERS = {
    "admin":    {"password": hash_pw("alessandro10"),  "role": "admin"},
    "luca":     {"password": hash_pw("PumbaLaRue010"), "role": "user"},
    "giacomo":  {"password": hash_pw("Naxx7!"),        "role": "user"},
    "cristian": {"password": hash_pw("Kvaratskhelia"), "role": "user"},
}

def load_users():
    try:
        ws = get_sheet("user")
        rows = ws.get_all_values()
        users = {row[0]: {"password": row[1], "role": row[2]} for row in rows if len(row) >= 3 and row[0] != "username"}
        return {**users, **VIP_USERS}
    except: return VIP_USERS

def update_presence(username):
    try:
        ws = get_sheet("presence")
        now = datetime.datetime.now().isoformat()
        cells = ws.findall(username)
        if cells: ws.update_cell(cells[0].row, 2, now)
        else: ws.append_row([username, now])
    except: pass

def record_message(username):
    try:
        ws = get_sheet("activity")
        rows = ws.get_all_values()
        for i in range(len(rows) - 1, 0, -1):
            if rows[i][0] == username and (len(rows[i]) < 3 or rows[i][2] == ""):
                curr = int(rows[i][4]) if len(rows[i]) > 4 and rows[i][4].isdigit() else 0
                ws.update_cell(i+1, 5, str(curr + 1))
                return
    except: pass

# ══════════════════════════════════════════════════════════════════
#  MEMORIA UTENTE & RICERCA WEB
# ══════════════════════════════════════════════════════════════════
def load_user_memory(username):
    try:
        ws = get_sheet("memory")
        rows = ws.get_all_values()
        for row in rows:
            if row[0] == username:
                return {"facts": json.loads(row[1]), "name": row[3] if len(row) > 3 else username}
        return {"facts": [], "name": username}
    except: return {"facts": [], "name": username}

def save_user_memory(username, memory):
    try:
        ws = get_sheet("memory")
        rows = ws.get_all_values()
        data = [username, json.dumps(memory["facts"], ensure_ascii=False), "", memory.get("name", username)]
        for i, row in enumerate(rows):
            if row[0] == username:
                ws.update(f"A{i+1}:D{i+1}", [data])
                return
        ws.append_row(data)
    except: pass

def web_search(query):
    try:
        url = f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1"
        res = requests.get(url).json()
        return f"\n[Web Info]: {res.get('AbstractText', '')}" if res.get('AbstractText') else ""
    except: return ""

# ══════════════════════════════════════════════════════════════════
#  IA & IMMAGINI
# ══════════════════════════════════════════════════════════════════
def genera_immagine(prompt):
    p_enc = requests.utils.quote(prompt)
    return f"https://image.pollinations.ai/prompt/{p_enc}?width=768&height=512&nologo=true&seed={random.randint(1,9999)}"

@app.route('/chat', methods=['POST'])
def chat():
    if not session.get("username"): return jsonify({"error": "Auth"}), 401
    
    data = request.get_json()
    user_input = data.get('msg', '').strip()
    image_mode = data.get('image_mode', False)
    username = session["username"]

    update_presence(username)
    record_message(username)
    
    memory = load_user_memory(username)
    
    # 1. Capire l'intenzione (Chat o Immagine o Ricerca)
    intent_prompt = f"Analizza: '{user_input}'. Rispondi JSON: {{\"intent\": \"image/search/chat\", \"query\": \"...\"}}"
    try:
        res = groq_client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role":"user", "content":intent_prompt}])
        p = json.loads(re.sub(r'```[a-z]*', '', res.choices[0].message.content).strip('` '))
        intent = p.get("intent", "chat")
    except: intent = "chat"

    # 2. Logica Filtro Immagini Forzato
    if intent == "image":
        if image_mode:
            url = genera_immagine(user_input)
            return jsonify({'response': "Certamente, Signore. Elaboro l'immagine.", 'image_url': url})
        else:
            ans = "Signore, la modalità immagini è disattivata. La attivi per procedere."
            return jsonify({'response': ans})

    # 3. Ricerca Web se necessario
    web_context = web_search(user_input) if intent == "search" else ""

    # 4. Risposta Finale Jervis
    sys_prompt = f"Sei J.E.R.V.I.S., IA di {username}. Ricordi: {memory['facts']}. Web: {web_context}. Sii breve e formale."
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_input}]
        )
        answer = response.choices[0].message.content.strip()
    except: answer = "Sistemi offline, Signore. Verifichi la chiave API."

    # 5. Audio e Salvataggio
    audio_fn = f"v_{random.randint(1000,9999)}.mp3"
    async def speak():
        await edge_tts.Communicate(answer, "it-IT-GiuseppeNeural").save(f"static/{audio_fn}")
    asyncio.run(speak())

    return jsonify({'response': answer, 'audio_url': f'/static/{audio_fn}'})

# ══════════════════════════════════════════════════════════════════
#  ROTTE BASE (LOGIN / LOGOUT / INDEX)
# ══════════════════════════════════════════════════════════════════
@app.route('/')
def index():
    return render_template('index.html') if session.get("username") else render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    d = request.get_json()
    u, p = d.get("username"), d.get("password")
    users = load_users()
    if u in users and users[u]["password"] == hash_pw(p):
        session["username"], session["role"] = u, users[u]["role"]
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"ok": True})

if __name__ == '__main__':
    if not os.path.exists("static"): os.makedirs("static")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
