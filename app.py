import os
import json
import asyncio
import random
import datetime
import requests
import hashlib
from flask import Flask, render_template, request, jsonify, session
import edge_tts
from groq import Groq
from pymongo import MongoClient

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "jervis-super-secret-2026")

# ══════════════════════════════════════════════════════════════════
#  CONFIGURAZIONE
# ══════════════════════════════════════════════════════════════════
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "QUI_LA_TUA_CHIAVE_GROQ")
groq_client  = Groq(api_key=GROQ_API_KEY)

# ── MONGODB ───────────────────────────────────────────────────────
MONGO_URI = os.environ.get("MONGO_URI", "")  # inserisci su Render come variabile env

_mongo_client = None
_db = None

def get_db():
    global _mongo_client, _db
    if _db is None:
        _mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        _db = _mongo_client["jervisdb"]
    return _db

# Sessioni attive in memoria {username: last_seen_timestamp}
active_sessions = {}

# ══════════════════════════════════════════════════════════════════
#  UTENTI
# ══════════════════════════════════════════════════════════════════
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def load_users():
    try:
        db = get_db()
        docs = list(db.users.find({}, {"_id": 0}))
        if docs:
            return {d["username"]: {"password": d["password"], "role": d["role"]} for d in docs}
    except Exception as e:
        print(f"[MongoDB] load_users error: {e}")

    # Primo avvio: crea admin di default
    default = {"admin": {"password": hash_pw("Jervis2026"), "role": "admin"}}
    save_users(default)
    return default

def save_users(users):
    try:
        db = get_db()
        for username, data in users.items():
            db.users.update_one(
                {"username": username},
                {"$set": {"username": username, "password": data["password"], "role": data["role"]}},
                upsert=True
            )
    except Exception as e:
        print(f"[MongoDB] save_users error: {e}")

def delete_user_db(username):
    try:
        db = get_db()
        db.users.delete_one({"username": username})
    except Exception as e:
        print(f"[MongoDB] delete_user error: {e}")

# ══════════════════════════════════════════════════════════════════
#  PRESENZA
# ══════════════════════════════════════════════════════════════════
def update_presence(username):
    now = datetime.datetime.now().isoformat()
    active_sessions[username] = now
    try:
        db = get_db()
        db.presence.update_one(
            {"username": username},
            {"$set": {"username": username, "last_seen": now}},
            upsert=True
        )
    except Exception as e:
        print(f"[MongoDB] update_presence error: {e}")

def load_presence():
    try:
        db = get_db()
        docs = list(db.presence.find({}, {"_id": 0}))
        return {d["username"]: {"last_seen": d.get("last_seen", "")} for d in docs}
    except Exception as e:
        print(f"[MongoDB] load_presence error: {e}")
        return {}

# ══════════════════════════════════════════════════════════════════
#  MEMORIA
# ══════════════════════════════════════════════════════════════════
def load_memory():
    try:
        db = get_db()
        doc = db.memory.find_one({"_id": "jervis_memory"})
        if doc:
            doc.pop("_id", None)
            return doc
    except Exception as e:
        print(f"[MongoDB] load_memory error: {e}")
    return {"facts": [], "conversations": [], "user_name": "Signore"}

def save_memory(memory):
    try:
        db = get_db()
        data = dict(memory)
        data["_id"] = "jervis_memory"
        db.memory.replace_one({"_id": "jervis_memory"}, data, upsert=True)
    except Exception as e:
        print(f"[MongoDB] save_memory error: {e}")

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
            model="meta-llama/llama-4-scout-17b-16e-instruct",
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
- Sei JERVIS, non un'AI generica

CAPACITÀ:
- Generazione immagini su richiesta
- Analisi foto e file caricati
- Memoria delle conversazioni passate

COSA SAI DELL'UTENTE:
{facts_text}

CONVERSAZIONI RECENTI:
{recent_text}

Oggi è {datetime.datetime.now().strftime('%A %d %B %Y, ore %H:%M')}.
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
#  ROUTE AUTH
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
        return jsonify({"ok": True, "role": user["role"]})
    return jsonify({"ok": False, "error": "Username o password errati"}), 401

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route('/me')
def me():
    if session.get("username"):
        return jsonify({"username": session["username"], "role": session["role"]})
    return jsonify({"error": "Non loggato"}), 401

# ══════════════════════════════════════════════════════════════════
#  ROUTE ADMIN — GESTIONE UTENTI
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
    if not username or not password:
        return jsonify({"error": "Username e password obbligatori"}), 400
    users = load_users()
    if username in users:
        return jsonify({"error": "Utente già esistente"}), 400
    users[username] = {"password": hash_pw(password), "role": role}
    save_users(users)
    return jsonify({"ok": True})

@app.route('/admin/users/<username>', methods=['DELETE'])
@require_login
@require_admin
def delete_user(username):
    if username == session["username"]:
        return jsonify({"error": "Non puoi eliminare te stesso"}), 400
    users = load_users()
    if username not in users:
        return jsonify({"error": "Utente non trovato"}), 404
    delete_user_db(username)
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
    save_users({username: users[username]})
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
    save_users({session["username"]: users[session["username"]]})
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
            if diff < 60:
                ago = "adesso"
            elif diff < 3600:
                ago = f"{int(diff/60)} min fa"
            elif diff < 86400:
                ago = f"{int(diff/3600)}h fa"
            else:
                ago = last_seen.strftime("%d/%m %H:%M")
        except:
            online = False
            ago = "mai"
        result[user] = {"online": online, "last_seen": ago}
    return jsonify(result)

# ══════════════════════════════════════════════════════════════════
#  ROUTE CHAT
# ══════════════════════════════════════════════════════════════════
@app.route('/chat', methods=['POST'])
@require_login
def chat():
    try:
        data = request.get_json()
        user_input    = data.get('msg', '').strip()
        session_history = data.get('history', [])
        image_b64     = data.get('image_b64', None)
        image_type    = data.get('image_type', 'image/jpeg')
        username      = session.get("username", "Signore")
        if username != "Signore":
            update_presence(username)

        if not user_input and not image_b64:
            return jsonify({'response': "Non ho rilevato alcun comando."})

        memory = load_memory()

        if image_b64:
            domanda = user_input if user_input else "Descrivi questa immagine in dettaglio."
            answer = analizza_immagine(image_b64, image_type, domanda)
            extract_facts(user_input or "[immagine]", answer, memory)
            return jsonify({'response': answer})

        gen_kw = ["genera un'immagine", "crea un'immagine", "disegna", "genera una foto",
                  "crea una foto", "fai un'immagine", "crea un disegno", "genera un"]
        if any(k in user_input.lower() for k in gen_kw):
            prompt = user_input
            for k in sorted(gen_kw, key=len, reverse=True):
                prompt = prompt.lower().replace(k, "").strip()
            img_url = genera_immagine(prompt or user_input)
            answer = "Ecco l'immagine generata, Signore."
            extract_facts(user_input, answer, memory)
            return jsonify({'response': answer, 'image_url': img_url})

        messages = [{"role": "system", "content": build_system_prompt(memory, username)}]
        for turn in session_history[-10:]:
            messages.append({"role": "user",      "content": turn['user']})
            messages.append({"role": "assistant",  "content": turn['jervis']})
        messages.append({"role": "user", "content": user_input})

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=300,
                temperature=0.7,
            )
            answer = response.choices[0].message.content.strip()
        except Exception as e:
            answer = "Sistemi temporaneamente irraggiungibili, Signore."

        extract_facts(user_input, answer, memory)

        audio_text = answer.replace("JERVIS", "Giervis").replace("Jervis", "Giervis")
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        os.makedirs(static_dir, exist_ok=True)
        for f in os.listdir(static_dir):
            if f.startswith("voice_"):
                try: os.remove(os.path.join(static_dir, f))
                except: pass
        filename = f"voice_{random.randint(10000,99999)}.mp3"
        run_async(generate_voice(audio_text, os.path.join(static_dir, filename)))

        return jsonify({'response': answer, 'audio_url': f'/static/{filename}'})

    except Exception as e:
        print(f"ERRORE: {e}")
        return jsonify({'response': "Errore critico nei sistemi."})

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
    app.run(debug=False, host='0.0.0.0', port=port)
