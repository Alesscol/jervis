import os
import json
import asyncio
import random
import datetime
import requests
import hashlib
import re
from flask import Flask, render_template, request, jsonify, session
import edge_tts
from groq import Groq

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "jervis-super-secret-2026")

# ══════════════════════════════════════════════════════════════════
#  CONFIGURAZIONE
# ══════════════════════════════════════════════════════════════════
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
groq_client = Groq(api_key=GROQ_API_KEY)

USERS_FILE    = 'users.json'
MEMORY_FILE   = 'memory.json'
PRESENCE_FILE = 'presence.json'
ACTIVITY_FILE = 'activity.json'   # ← NUOVO: statistiche per utente

# ══════════════════════════════════════════════════════════════════
#  UTILITY JSON
# ══════════════════════════════════════════════════════════════════
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def load_json(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ══════════════════════════════════════════════════════════════════
#  GESTIONE UTENTI
# ══════════════════════════════════════════════════════════════════
def load_users():
    vip_users = {
        "admin":   {"password": hash_pw("alessandro10"),   "role": "admin"},
        "luca":    {"password": hash_pw("PumbaLaRue010"),  "role": "user"},
        "giacomo": {"password": hash_pw("Naxx7!"),         "role": "user"},
        "cristian":{"password": hash_pw("Kvaratskhelia"),  "role": "user"},
    }
    creati_dal_sito = load_json(USERS_FILE, {})
    return {**creati_dal_sito, **vip_users}

def save_users(users):
    save_json(USERS_FILE, users)

# ══════════════════════════════════════════════════════════════════
#  ATTIVITÀ UTENTI  (sessioni, messaggi, durata)
# ══════════════════════════════════════════════════════════════════
def load_activity():
    return load_json(ACTIVITY_FILE, {})

def save_activity(activity):
    save_json(ACTIVITY_FILE, activity)

def record_login(username):
    """Registra l'inizio di una sessione."""
    activity = load_activity()
    if username not in activity:
        activity[username] = {"sessions": [], "total_messages": 0}
    # Apre una nuova sessione
    activity[username]["sessions"].append({
        "login":    datetime.datetime.now().isoformat(),
        "logout":   None,
        "duration": None,
        "messages": 0
    })
    save_activity(activity)

def record_logout(username):
    """Chiude l'ultima sessione aperta e calcola la durata."""
    activity = load_activity()
    if username not in activity or not activity[username]["sessions"]:
        return
    sessions = activity[username]["sessions"]
    # Trova l'ultima sessione ancora aperta
    for s in reversed(sessions):
        if s["logout"] is None:
            now = datetime.datetime.now()
            s["logout"] = now.isoformat()
            try:
                login_dt = datetime.datetime.fromisoformat(s["login"])
                diff = (now - login_dt).total_seconds()
                mins, secs = divmod(int(diff), 60)
                hrs,  mins = divmod(mins, 60)
                s["duration"] = f"{hrs:02d}:{mins:02d}:{secs:02d}"
            except:
                s["duration"] = "—"
            break
    save_activity(activity)

def record_message(username):
    """Incrementa il contatore messaggi dell'utente nella sessione corrente."""
    activity = load_activity()
    if username not in activity:
        activity[username] = {"sessions": [], "total_messages": 0}
    activity[username]["total_messages"] = activity[username].get("total_messages", 0) + 1
    # Incrementa anche nella sessione corrente
    sessions = activity[username]["sessions"]
    for s in reversed(sessions):
        if s["logout"] is None:
            s["messages"] = s.get("messages", 0) + 1
            break
    save_activity(activity)

# ══════════════════════════════════════════════════════════════════
#  PRESENZA (last seen)
# ══════════════════════════════════════════════════════════════════
def update_presence(username):
    presence = load_json(PRESENCE_FILE, {})
    presence[username] = {"last_seen": datetime.datetime.now().isoformat()}
    save_json(PRESENCE_FILE, presence)

def load_presence():
    return load_json(PRESENCE_FILE, {})

# ══════════════════════════════════════════════════════════════════
#  MEMORIA
# ══════════════════════════════════════════════════════════════════
def load_memory():
    return load_json(MEMORY_FILE, {"facts": [], "conversations": [], "user_name": "Signore"})

def save_memory(memory):
    save_json(MEMORY_FILE, memory)

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
        record_login(username)          # ← registra sessione
        return jsonify({"ok": True, "role": user["role"]})
    return jsonify({"ok": False, "error": "Username o password errati"}), 401

@app.route('/logout', methods=['POST'])
def logout():
    username = session.get("username")
    if username:
        record_logout(username)         # ← chiude sessione
    session.clear()
    return jsonify({"ok": True})

@app.route('/me')
def me():
    if session.get("username"):
        return jsonify({"username": session["username"], "role": session["role"]})
    return jsonify({"error": "Non loggato"}), 401

# ══════════════════════════════════════════════════════════════════
#  ROUTES ADMIN — UTENTI
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
    users[username] = {"password": hash_pw(password), "role": role}
    save_users(users)
    return jsonify({"ok": True})

@app.route('/admin/users/<username>', methods=['DELETE'])
@require_login
@require_admin
def delete_user(username):
    if username == session["username"]:
        return jsonify({"error": "Non puoi eliminare te stesso"}), 400
    users = load_json(USERS_FILE, {})
    if username in users:
        del users[username]
        save_users(users)
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
    users[username]["password"] = hash_pw(new_pw)
    save_users(users)
    return jsonify({"ok": True})

@app.route('/admin/my-password', methods=['PUT'])
@require_login
def change_my_password():
    data = request.get_json()
    new_pw = data.get("password", "").strip()
    if not new_pw:
        return jsonify({"error": "Password obbligatoria"}), 400
    users = load_users()
    users[session["username"]]["password"] = hash_pw(new_pw)
    save_users(users)
    return jsonify({"ok": True})

# ── PRESENZA ──────────────────────────────────────────────────────
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

# ── ATTIVITÀ DETTAGLIATA ──────────────────────────────────────────
@app.route('/admin/activity', methods=['GET'])
@require_login
@require_admin
def get_activity():
    activity = load_activity()
    presence = load_presence()
    now = datetime.datetime.now()
    result = {}

    # Includi tutti gli utenti conosciuti, anche senza attività
    all_users = load_users()
    for username in all_users:
        data = activity.get(username, {"sessions": [], "total_messages": 0})
        sessions = data.get("sessions", [])
        total_msgs = data.get("total_messages", 0)

        # Stato online
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

        # Ultime 10 sessioni (più recenti prima)
        recent_sessions = []
        for s in reversed(sessions[-10:]):
            try:
                login_dt = datetime.datetime.fromisoformat(s["login"])
                login_fmt = login_dt.strftime("%d/%m/%Y %H:%M")
            except:
                login_fmt = s.get("login", "—")[:16]
            recent_sessions.append({
                "login":    login_fmt,
                "logout":   s["logout"][:16].replace("T", " ") if s.get("logout") else "Sessione aperta",
                "duration": s.get("duration", "—"),
                "messages": s.get("messages", 0)
            })

        result[username] = {
            "online":          online,
            "last_seen":       last_seen_str,
            "total_sessions":  len(sessions),
            "total_messages":  total_msgs,
            "sessions":        recent_sessions
        }

    return jsonify(result)

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
    record_message(username)            # ← conta messaggio

    memory = load_memory()

    # ── ANALISI IMMAGINE ──────────────────────────────────────────
    if image_b64:
        domanda = user_input if user_input else "Cosa vedi?"
        answer = analizza_immagine(image_b64, image_type, domanda)
        extract_facts("[immagine]", answer, memory)
        return jsonify({'response': answer})

    # ══════════════════════════════════════════════════════════════
    #  INTENT DETECTION — L'AI capisce il comando da sola
    #  Capisce errori di battitura, sinonimi, frasi naturali
    # ══════════════════════════════════════════════════════════════
    intent_prompt = f"""Analizza questo comando utente e rispondi SOLO con un JSON valido, niente altro.

Comando: "{user_input}"

Devi capire l'intenzione anche con errori di battitura, sinonimi o frasi incomplete.
Restituisci SOLO questo JSON (senza markdown, senza backtick):

{{
  "intent": "<uno tra: open_site | youtube_search | youtube_video | google_search | spotify_search | generate_image | chat>",
  "query": "<cosa cercare o aprire, vuoto se non serve>",
  "url": "<url diretto se intent=open_site, altrimenti vuoto>"
}}

REGOLE:
- "avvia youtube", "apri yt", "lancia youtube", "youtub", "youtbe" → intent=open_site, url=https://www.youtube.com
- "cerca X su youtube", "fammi vedere X", "metti il video di X", "ultimo video di X" → intent=youtube_video, query=X
- "metti musica X", "ascolta X", "metti X su spotify", "canzone X" → intent=spotify_search, query=X
- "cerca su google X", "googla X", "cerca X" → intent=google_search, query=X
- "apri/avvia/lancia/vai su SITO" → intent=open_site, url=url corretto
- "genera/disegna/crea immagine di X" → intent=generate_image, query=X
- tutto il resto → intent=chat, query=vuoto

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
        # Pulisce eventuale markdown residuo
        raw = re.sub(r'```[a-z]*', '', raw).strip().strip('`')
        parsed = json.loads(raw)
        intent = parsed.get("intent", "chat")
        query  = parsed.get("query", "")
        url    = parsed.get("url", "")
    except Exception as e:
        print(f"[Intent] parsing fallito: {e} — fallback a chat")
        intent = "chat"

    # ── GESTIONE INTENT ──────────────────────────────────────────
    if intent == "open_site" and url:
        extract_facts(user_input, "Apertura sito.", memory)
        return jsonify({'response': "Certamente, Signore. Apro subito.", 'open_url': url})

    if intent == "youtube_video" and query:
        search_url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}"
        extract_facts(user_input, f"Ricerca YouTube: {query}", memory)
        return jsonify({
            'response': f"Cerco subito «{query}» su YouTube, Signore.",
            'open_url': search_url
        })

    if intent == "youtube_search" and query:
        search_url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}"
        extract_facts(user_input, f"Ricerca YouTube: {query}", memory)
        return jsonify({
            'response': f"Ecco i risultati per «{query}» su YouTube, Signore.",
            'open_url': search_url
        })

    if intent == "google_search" and query:
        search_url = f"https://www.google.com/search?q={requests.utils.quote(query)}"
        extract_facts(user_input, f"Ricerca Google: {query}", memory)
        return jsonify({
            'response': f"Cerco «{query}» su Google, Signore.",
            'open_url': search_url
        })

    if intent == "spotify_search" and query:
        search_url = f"https://open.spotify.com/search/{requests.utils.quote(query)}"
        extract_facts(user_input, f"Spotify: {query}", memory)
        return jsonify({
            'response': f"Metto «{query}» su Spotify, Signore.",
            'open_url': search_url
        })

    if intent == "generate_image":
        img_url = genera_immagine(query or user_input)
        extract_facts(user_input, "Immagine generata.", memory)
        return jsonify({'response': "Ecco l'immagine, Signore.", 'image_url': img_url})

    # ── CHAT GROQ (risposta normale) ─────────────────────────────
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
