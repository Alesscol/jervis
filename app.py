import os
import json
import asyncio
import random
import datetime
import requests
import hashlib
import re
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

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet(tab_name):
    client = get_sheets_client()
    sh = client.open_by_key(SHEET_ID)
    try:
        return sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=1000, cols=20)
        return ws

# ══════════════════════════════════════════════════════════════════
#  UTILITY
# ══════════════════════════════════════════════════════════════════
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ══════════════════════════════════════════════════════════════════
#  GESTIONE UTENTI — Google Sheets (tab "user")
#  Colonne: username | password_hash | role
# ══════════════════════════════════════════════════════════════════
VIP_USERS = {
    "admin":    {"password": hash_pw("alessandro10"),  "role": "admin"},
    "luca":     {"password": hash_pw("PumbaLaRue010"), "role": "user"},
    "giacomo":  {"password": hash_pw("Naxx7!"),        "role": "user"},
    "cristian": {"password": hash_pw("Kvaratskhelia"), "role": "user"},
}

def init_vip_users():
    """Scrive i VIP nel foglio user se non ci sono già — chiamata all'avvio."""
    try:
        ws = get_sheet("user")
        rows = ws.get_all_values()
        existing = {r[0] for r in rows if r}
        if not rows:
            ws.append_row(["username", "password_hash", "role"])
        for username, data in VIP_USERS.items():
            if username not in existing:
                ws.append_row([username, data["password"], data["role"]])
                print(f"[Sheets] VIP aggiunto: {username}")
    except Exception as e:
        print(f"[Sheets] init_vip_users error: {e}")

def load_users():
    try:
        ws = get_sheet("user")
        rows = ws.get_all_values()
        users = {}
        for row in rows:
            if len(row) >= 3 and row[0] and row[0] != "username":
                users[row[0]] = {"password": row[1], "role": row[2]}
        return {**users, **VIP_USERS}
    except Exception as e:
        print(f"[Sheets] load_users error: {e}")
        return VIP_USERS

def save_user(username, password_hash, role):
    """Aggiunge o aggiorna un utente nel foglio."""
    try:
        ws = get_sheet("user")
        rows = ws.get_all_values()
        # Controlla se esiste già
        for i, row in enumerate(rows):
            if row and row[0] == username:
                ws.update(f"A{i+1}:C{i+1}", [[username, password_hash, role]])
                return
        # Non esiste, aggiunge
        if not rows or rows[0] != ["username", "password_hash", "role"]:
            if not rows:
                ws.update("A1:C1", [["username", "password_hash", "role"]])
        ws.append_row([username, password_hash, role])
    except Exception as e:
        print(f"[Sheets] save_user error: {e}")

def delete_user_sheet(username):
    try:
        ws = get_sheet("user")
        rows = ws.get_all_values()
        for i, row in enumerate(rows):
            if row and row[0] == username:
                ws.delete_rows(i + 1)
                return
    except Exception as e:
        print(f"[Sheets] delete_user error: {e}")

# ══════════════════════════════════════════════════════════════════
#  PRESENZA — Google Sheets (tab "presence")
#  Colonne: username | last_seen
# ══════════════════════════════════════════════════════════════════
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
    except Exception as e:
        print(f"[Sheets] update_presence error: {e}")

def load_presence():
    try:
        ws = get_sheet("presence")
        rows = ws.get_all_values()
        result = {}
        for row in rows:
            if len(row) >= 2 and row[0]:
                result[row[0]] = {"last_seen": row[1]}
        return result
    except Exception as e:
        print(f"[Sheets] load_presence error: {e}")
        return {}

# ══════════════════════════════════════════════════════════════════
#  ATTIVITÀ — Google Sheets (tab "activity")
#  Colonne: username | login | logout | duration | messages | total_messages
# ══════════════════════════════════════════════════════════════════
def record_login(username):
    try:
        ws = get_sheet("activity")
        now = datetime.datetime.now().isoformat()
        ws.insert_row([username, now, "", "", "0"], 2)
        print(f"[Sheets] record_login: riga inserita per {username}")
    except Exception as e:
        print(f"[Sheets] record_login ERRORE: {type(e).__name__}: {e}")

def record_logout(username):
    try:
        ws = get_sheet("activity")
        rows = ws.get_all_values()
        now = datetime.datetime.now()
        for i in range(len(rows) - 1, 0, -1):
            row = rows[i]
            if len(row) >= 2 and row[0] == username and (len(row) < 3 or row[2] == ""):
                logout_str = now.isoformat()
                try:
                    login_dt = datetime.datetime.fromisoformat(row[1])
                    diff = (now - login_dt).total_seconds()
                    mins, secs = divmod(int(diff), 60)
                    hrs, mins = divmod(mins, 60)
                    duration = f"{hrs:02d}:{mins:02d}:{secs:02d}"
                except:
                    duration = "—"
                ws.update(f"C{i+1}:D{i+1}", [[logout_str, duration]])
                return
    except Exception as e:
        print(f"[Sheets] record_logout error: {e}")

def record_message(username):
    try:
        ws = get_sheet("activity")
        rows = ws.get_all_values()
        for i in range(len(rows) - 1, 0, -1):
            row = rows[i]
            if len(row) >= 2 and row[0] == username and (len(row) < 3 or row[2] == ""):
                current_msgs = int(row[4]) if len(row) > 4 and row[4].isdigit() else 0
                ws.update(f"E{i+1}", [[str(current_msgs + 1)]])
                return
    except Exception as e:
        print(f"[Sheets] record_message error: {e}")

def load_activity():
    """Restituisce dizionario utente → {sessions, total_messages, online, last_seen}"""
    try:
        ws = get_sheet("activity")
        rows = ws.get_all_values()
        presence = load_presence()
        now = datetime.datetime.now()
        all_users = load_users()
        result = {}

        # Raggruppa sessioni per utente
        sessions_by_user = {}
        for row in rows[1:]:
            if not row or not row[0]:
                continue
            uname = row[0]
            if uname not in sessions_by_user:
                sessions_by_user[uname] = []
            sessions_by_user[uname].append(row)

        for username in all_users:
            p = presence.get(username, {})
            online = False
            last_seen_str = "Mai connesso"
            if p.get("last_seen"):
                try:
                    ls = datetime.datetime.fromisoformat(p["last_seen"])
                    diff = (now - ls).total_seconds()
                    online = diff < 300
                    if diff < 60:      last_seen_str = "adesso"
                    elif diff < 3600:  last_seen_str = f"{int(diff/60)} min fa"
                    elif diff < 86400: last_seen_str = f"{int(diff/3600)}h fa"
                    else:              last_seen_str = ls.strftime("%d/%m/%Y %H:%M")
                except:
                    pass

            user_sessions = sessions_by_user.get(username, [])
            total_msgs = sum(int(r[5]) if len(r) > 5 and r[5].isdigit() else 0 for r in user_sessions)

            recent = []
            for row in reversed(user_sessions[-10:]):
                try:
                    login_fmt = datetime.datetime.fromisoformat(row[1]).strftime("%d/%m/%Y %H:%M")
                except:
                    login_fmt = row[1][:16] if len(row) > 1 else "—"
                logout_val = row[2][:16].replace("T", " ") if len(row) > 2 and row[2] else "Sessione aperta"
                duration   = row[3] if len(row) > 3 and row[3] else "—"
                messages   = int(row[4]) if len(row) > 4 and row[4].isdigit() else 0
                recent.append({"login": login_fmt, "logout": logout_val, "duration": duration, "messages": messages})

            result[username] = {
                "online": online,
                "last_seen": last_seen_str,
                "total_sessions": len(user_sessions),
                "total_messages": sum(int(r[4]) if len(r) > 4 and r[4].isdigit() else 0 for r in user_sessions),
                "sessions": recent
            }
        return result
    except Exception as e:
        print(f"[Sheets] load_activity error: {e}")
        return {}

# ══════════════════════════════════════════════════════════════════
#  MEMORIA — Google Sheets (tab "memory")
#  Riga 1: facts (JSON) | Riga 2: conversations (JSON) | Riga 3: user_name
# ══════════════════════════════════════════════════════════════════
def load_memory():
    try:
        ws = get_sheet("memory")
        rows = ws.get_all_values()
        facts = json.loads(rows[0][0]) if rows and rows[0] else []
        convs = json.loads(rows[1][0]) if len(rows) > 1 and rows[1] else []
        name  = rows[2][0] if len(rows) > 2 and rows[2] else "Signore"
        return {"facts": facts, "conversations": convs, "user_name": name}
    except Exception as e:
        print(f"[Sheets] load_memory error: {e}")
        return {"facts": [], "conversations": [], "user_name": "Signore"}

def save_memory(memory):
    try:
        ws = get_sheet("memory")
        ws.clear()
        ws.update("A1", [[json.dumps(memory.get("facts", []), ensure_ascii=False)]])
        ws.update("A2", [[json.dumps(memory.get("conversations", []), ensure_ascii=False)]])
        ws.update("A3", [[memory.get("user_name", "Signore")]])
    except Exception as e:
        print(f"[Sheets] save_memory error: {e}")

# ══════════════════════════════════════════════════════════════════
#  DECORATORI AUTH
# ══════════════════════════════════════════════════════════════════
def require_login(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("username"):
            return jsonify({"error": "Non autorizzato"}), 401
        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            return jsonify({"error": "Solo gli admin possono fare questo"}), 403
        return f(*args, **kwargs)
    return decorated

# ══════════════════════════════════════════════════════════════════
#  IMMAGINI & AI
# ══════════════════════════════════════════════════════════════════
def genera_immagine(prompt):
    prompt_enc = requests.utils.quote(prompt)
    seed = random.randint(1, 99999)
    return f"https://image.pollinations.ai/prompt/{prompt_enc}?width=768&height=512&nologo=true&seed={seed}"

def analizza_immagine(image_b64, media_type, domanda):
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                {"type": "text", "text": f"Sei JERVIS. Rispondi in italiano, breve e preciso. {domanda}"}
            ]}],
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Non riesco ad analizzare l'immagine: {e}"

# ══════════════════════════════════════════════════════════════════
#  LOGICA MEMORIA / PROMPT
# ══════════════════════════════════════════════════════════════════
def extract_facts(user_msg, jarvis_reply, memory):
    for kw in ["mi chiamo", "il mio nome è", "chiamami"]:
        if kw in user_msg.lower():
            idx = user_msg.lower().find(kw) + len(kw)
            name = user_msg[idx:].strip().split()[0].strip(".,!?")
            if name and len(name) > 1:
                memory["user_name"] = name.capitalize()
                fact = f"Il nome dell'utente è {name.capitalize()}"
                if fact not in memory["facts"]:
                    memory["facts"].append(fact)
    memory["conversations"].append({
        "timestamp": datetime.datetime.now().isoformat(),
        "user": user_msg[:200], "jervis": jarvis_reply[:200]
    })
    if len(memory["conversations"]) > 100:
        memory["conversations"] = memory["conversations"][-100:]
    save_memory(memory)

def build_system_prompt(memory, username):
    name = memory.get("user_name", username or "Signore")
    facts_text = "\n".join(f"- {f}" for f in memory.get("facts", [])[-20:]) or "Nessun fatto noto."
    recent = memory.get("conversations", [])[-3:]
    recent_text = "".join(
        f"  [{c.get('timestamp','')[:10]}] Utente: {c['user'][:100]}\n  Jervis: {c['jervis'][:100]}\n"
        for c in recent
    ) or "Nessuna conversazione precedente."
    return f"""Sei J.E.R.V.I.S. (Just Extremely Responsive Virtual Intelligence System).
Sei l'IA personale dell'utente. Intelligente, elegante, leggermente ironico.

REGOLE:
- Chiama l'utente "{name}"
- Rispondi SEMPRE in italiano
- Risposte brevi e precise (max 2-3 frasi)

CONVERSAZIONI RECENTI:
{recent_text}
"""

# ══════════════════════════════════════════════════════════════════
#  TTS
# ══════════════════════════════════════════════════════════════════
async def generate_voice(text, filepath):
    try:
        communicate = edge_tts.Communicate(text, "it-IT-GiuseppeNeural")
        await communicate.save(filepath)
    except Exception as e:
        print(f"Errore TTS: {e}")

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

init_vip_users_pending = True

@app.before_request
def startup():
    global init_vip_users_pending
    if init_vip_users_pending:
        init_vip_users_pending = False
        try:
            init_vip_users()
        except Exception as e:
            print(f"[startup] init_vip_users error: {e}")

# ══════════════════════════════════════════════════════════════════
#  ROUTES AUTH
# ══════════════════════════════════════════════════════════════════
@app.route('/')
def index():
    if session.get("username"):
        return render_template('index.html')
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    users = load_users()
    user = users.get(username)
    if user and user["password"] == hash_pw(password):
        session["username"] = username
        session["role"] = user["role"]
        update_presence(username)
        record_login(username)
        return jsonify({"ok": True, "role": user["role"]})
    return jsonify({"ok": False, "error": "Username o password errati"}), 401

@app.route('/logout', methods=['POST'])
def logout():
    username = session.get("username")
    if username:
        record_logout(username)
    session.clear()
    return jsonify({"ok": True})

@app.route('/me')
def me():
    if session.get("username"):
        return jsonify({"username": session["username"], "role": session["role"]})
    return jsonify({"error": "Non loggato"}), 401

# ══════════════════════════════════════════════════════════════════
#  ROUTES ADMIN
# ══════════════════════════════════════════════════════════════════
@app.route('/admin/users', methods=['GET'])
@require_login
@require_admin
def get_users():
    users = load_users()
    return jsonify({u: {"role": v["role"]} for u, v in users.items()})

@app.route('/admin/users', methods=['POST'])
@require_login
@require_admin
def add_user():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    role = data.get("role", "user")
    users = load_users()
    if username in users:
        return jsonify({"error": "Utente esistente"}), 400
    save_user(username, hash_pw(password), role)
    return jsonify({"ok": True})

@app.route('/admin/users/<username>', methods=['DELETE'])
@require_login
@require_admin
def delete_user(username):
    if username == session["username"]:
        return jsonify({"error": "Non puoi eliminare te stesso"}), 400
    if username in VIP_USERS:
        return jsonify({"error": "Non puoi eliminare un utente VIP"}), 400
    delete_user_sheet(username)
    return jsonify({"ok": True})

@app.route('/admin/users/<username>/password', methods=['PUT'])
@require_login
@require_admin
def change_password(username):
    data = request.get_json()
    new_pw = data.get("password", "").strip()
    if not new_pw:
        return jsonify({"error": "Password obbligatoria"}), 400
    users = load_users()
    if username not in users:
        return jsonify({"error": "Utente non trovato"}), 404
    save_user(username, hash_pw(new_pw), users[username]["role"])
    return jsonify({"ok": True})

@app.route('/admin/my-password', methods=['PUT'])
@require_login
def change_my_password():
    data = request.get_json()
    new_pw = data.get("password", "").strip()
    if not new_pw:
        return jsonify({"error": "Password obbligatoria"}), 400
    users = load_users()
    save_user(session["username"], hash_pw(new_pw), users[session["username"]]["role"])
    return jsonify({"ok": True})

@app.route('/admin/presence', methods=['GET'])
@require_login
@require_admin
def get_presence():
    presence = load_presence()
    now = datetime.datetime.now()
    result = {}
    for user, data in presence.items():
        last_seen_str = data.get("last_seen", "")
        try:
            last_seen = datetime.datetime.fromisoformat(last_seen_str)
            diff = (now - last_seen).total_seconds()
            online = diff < 300
            if diff < 60:      ago = "adesso"
            elif diff < 3600:  ago = f"{int(diff/60)} min fa"
            elif diff < 86400: ago = f"{int(diff/3600)}h fa"
            else:              ago = last_seen.strftime("%d/%m %H:%M")
        except:
            online = False
            ago = "mai"
        result[user] = {"online": online, "last_seen": ago}
    return jsonify(result)

@app.route('/admin/activity', methods=['GET'])
@require_login
@require_admin
def get_activity():
    return jsonify(load_activity())

# ══════════════════════════════════════════════════════════════════
#  ROUTE CHAT
# ══════════════════════════════════════════════════════════════════
@app.route('/chat', methods=['POST'])
@require_login
def chat():
    data = request.get_json()
    user_input = data.get('msg', '').strip()
    image_b64  = data.get('image_b64', None)
    image_type = data.get('image_type', 'image/jpeg')
    username   = session.get("username", "Signore")

    update_presence(username)
    record_message(username)

    memory = load_memory()

    if image_b64:
        domanda = user_input if user_input else "Cosa vedi?"
        answer = analizza_immagine(image_b64, image_type, domanda)
        extract_facts("[immagine]", answer, memory)
        return jsonify({'response': answer})

    intent_prompt = f"""Analizza questo comando utente e rispondi SOLO con un JSON valido, niente altro.

Comando: "{user_input}"

Devi capire l'intenzione anche con errori di battitura, sinonimi o frasi incomplete.
Restituisci SOLO questo JSON (senza markdown, senza backtick):

{{
  "intent": "<uno tra: open_site | youtube_search | youtube_video | google_search | spotify_search | generate_image | chat>",
  "query": "<cosa cercare o aprire, vuoto se non serve>",
  "url": "<url diretto se intent=open_site, altrimenti vuoto>"
}}

REGOLE (in ordine di priorità):
- "genera/disegna/crea/fai un'immagine di X", "crea X", "disegna X", "illustra X" → intent=generate_image, query=X  ← PRIORITÀ MASSIMA
- "avvia youtube", "apri yt", "lancia youtube", "youtub", "youtbe" → intent=open_site, url=https://www.youtube.com
- "cerca X su youtube", "fammi vedere X", "metti il video di X", "ultimo video di X", "novita di X su youtube" → intent=youtube_video, query=X
- "metti musica X", "ascolta X", "metti X su spotify", "canzone X" → intent=spotify_search, query=X
- "cerca su google X", "googla X", "cerca X" → intent=google_search, query=X
- "apri/avvia/lancia/vai su SITO" → intent=open_site, url=url corretto
- tutto il resto → intent=chat, query=vuoto

IMPORTANTE: se il messaggio contiene "crea", "disegna", "genera" riferito a un oggetto/animale/persona/cosa → è SEMPRE generate_image, MAI youtube_video.

Siti noti: youtube=https://www.youtube.com, google=https://www.google.com, netflix=https://www.netflix.com,
spotify=https://open.spotify.com, gmail=https://mail.google.com, whatsapp=https://web.whatsapp.com,
instagram=https://www.instagram.com, twitter/x=https://www.x.com, facebook=https://www.facebook.com,
twitch=https://www.twitch.tv, github=https://www.github.com, reddit=https://www.reddit.com,
amazon=https://www.amazon.it, maps=https://maps.google.com, wikipedia=https://www.wikipedia.org,
chatgpt=https://chat.openai.com, claude=https://claude.ai
"""

    intent = "chat"
    query  = ""
    url    = ""
    try:
        intent_resp = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": intent_prompt}],
            max_tokens=150,
            temperature=0.0
        )
        raw = intent_resp.choices[0].message.content.strip()
        raw = re.sub(r'```[a-z]*', '', raw).strip().strip('`')
        parsed = json.loads(raw)
        intent = parsed.get("intent", "chat")
        query  = parsed.get("query", "")
        url    = parsed.get("url", "")
    except Exception as e:
        print(f"[Intent] parsing fallito: {e}")
        intent = "chat"

    if intent == "open_site" and url:
        extract_facts(user_input, "Apertura sito.", memory)
        return jsonify({'response': "Certamente, Signore. Apro subito.", 'open_url': url})

    if intent in ("youtube_video", "youtube_search") and query:
        search_url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}"
        extract_facts(user_input, f"Ricerca YouTube: {query}", memory)
        return jsonify({'response': f"Cerco «{query}» su YouTube, Signore.", 'open_url': search_url})

    if intent == "google_search" and query:
        search_url = f"https://www.google.com/search?q={requests.utils.quote(query)}"
        extract_facts(user_input, f"Ricerca Google: {query}", memory)
        return jsonify({'response': f"Cerco «{query}» su Google, Signore.", 'open_url': search_url})

    if intent == "spotify_search" and query:
        search_url = f"https://open.spotify.com/search/{requests.utils.quote(query)}"
        extract_facts(user_input, f"Spotify: {query}", memory)
        return jsonify({'response': f"Metto «{query}» su Spotify, Signore.", 'open_url': search_url})

    if intent == "generate_image":
        img_url = genera_immagine(query or user_input)
        extract_facts(user_input, "Immagine generata.", memory)
        return jsonify({'response': "Ecco l'immagine, Signore.", 'image_url': img_url})

    messages = [{"role": "system", "content": build_system_prompt(memory, username)}]
    messages.append({"role": "user", "content": user_input})
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=300
        )
        answer = response.choices[0].message.content.strip()
    except:
        answer = "Sistemi offline, Signore."

    extract_facts(user_input, answer, memory)

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    os.makedirs(static_dir, exist_ok=True)
    filename = f"voice_{random.randint(1000,9999)}.mp3"
    run_async(generate_voice(answer, os.path.join(static_dir, filename)))

    return jsonify({'response': answer, 'audio_url': f'/static/{filename}'})

# ══════════════════════════════════════════════════════════════════
#  ROUTE MEMORIA
# ══════════════════════════════════════════════════════════════════
@app.route('/memory', methods=['GET'])
@require_login
def get_memory():
    memory = load_memory()
    return jsonify({
        'user_name': memory.get('user_name', 'Signore'),
        'facts': memory.get('facts', []),
        'total_conversations': len(memory.get('conversations', []))
    })

@app.route('/memory/clear', methods=['POST'])
@require_login
def clear_memory():
    save_memory({"facts": [], "conversations": [], "user_name": "Signore"})
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    init_vip_users()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
