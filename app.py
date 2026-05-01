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

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "jervis-super-secret-2026")

# ══════════════════════════════════════════════════════════════════
#  CONFIGURAZIONE
# ══════════════════════════════════════════════════════════════════
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "QUI_LA_TUA_CHIAVE_GROQ")
groq_client = Groq(api_key=GROQ_API_KEY)

# File locali per i dati (Sostituiscono MongoDB)
USERS_FILE = 'users.json'
MEMORY_FILE = 'memory.json'
PRESENCE_FILE = 'presence.json'

# ══════════════════════════════════════════════════════════════════
#  FUNZIONI DI SUPPORTO LOCALI
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
        json.dump(data, f, indent=4)

# ══════════════════════════════════════════════════════════════════
#  GESTIONE UTENTI
# ══════════════════════════════════════════════════════════════════
def load_users():
    # Questi utenti non verranno MAI cancellati perché sono scritti nel codice
    vip_users = {
        "admin": {"password": hash_pw("alessandro10"), "role": "admin"},
        "luca": {"password": hash_pw("PumbaLaRue010"), "role": "user"},
        "giacomo": {"password": hash_pw("Naxx7!"), "role": "user"},
        "cristian":  {"password": hash_pw("prova"), "role": "user"}\
    }
    
    # Carica anche eventuali utenti aggiunti dal sito (finché Render non resetta)
    creati_dal_sito = load_json(USERS_FILE, {})
    
    # Unisce le due liste (i VIP vincono sempre)
    return {**creati_dal_sito, **vip_users}
def save_users(users):
    save_json(USERS_FILE, users)

# ══════════════════════════════════════════════════════════════════
#  GESTIONE PRESENZA
# ══════════════════════════════════════════════════════════════════
def update_presence(username):
    presence = load_json(PRESENCE_FILE, {})
    now = datetime.datetime.now().isoformat()
    presence[username] = {"last_seen": now}
    save_json(PRESENCE_FILE, presence)

def load_presence():
    return load_json(PRESENCE_FILE, {})

# ══════════════════════════════════════════════════════════════════
#  GESTIONE MEMORIA
# ══════════════════════════════════════════════════════════════════
def load_memory():
    default_memory = {"facts": [], "conversations": [], "user_name": "Signore"}
    return load_json(MEMORY_FILE, default_memory)

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
#  TTS (VOCE)
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
#  ROUTES
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

@app.route('/chat', methods=['POST'])
@require_login
def chat():
    data = request.get_json()
    user_input = data.get('msg', '').strip()
    image_b64 = data.get('image_b64', None)
    image_type = data.get('image_type', 'image/jpeg')
    username = session.get("username", "Signore")
    update_presence(username)

    memory = load_memory()

    if image_b64:
        domanda = user_input if user_input else "Cosa vedi?"
        answer = analizza_immagine(image_b64, image_type, domanda)
        extract_facts("[immagine]", answer, memory)
        return jsonify({'response': answer})

    # Logica immagini
    if any(k in user_input.lower() for k in ["genera", "disegna", "crea immagine"]):
        img_url = genera_immagine(user_input)
        return jsonify({'response': "Certamente, Signore.", 'image_url': img_url})

    # Logica Testo Groq
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

    # Genera Voce
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    os.makedirs(static_dir, exist_ok=True)
    filename = f"voice_{random.randint(1000,9999)}.mp3"
    run_async(generate_voice(answer, os.path.join(static_dir, filename)))

    return jsonify({'response': answer, 'audio_url': f'/static/{filename}'})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
