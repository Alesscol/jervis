import os
import json
import asyncio
import random
import datetime
from flask import Flask, render_template, request, jsonify
import edge_tts

# ── GROQ SDK ────────────────────────────────────────────────────────────────
try:
    from groq import Groq
except ImportError:
    raise ImportError("Installa Groq: pip install groq")

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ── CHIAVE API GROQ (gratuita su https://console.groq.com) ──────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
client = Groq(api_key=GROQ_API_KEY)

# ── FILE MEMORIA ─────────────────────────────────────────────────────────────
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jervis_memory.json")

def load_memory():
    """Carica la memoria persistente dal file JSON."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"facts": [], "conversations": [], "user_name": "Signore"}

def save_memory(memory):
    """Salva la memoria persistente nel file JSON."""
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Errore salvataggio memoria: {e}")

def extract_facts(user_msg, jarvis_reply, memory):
    """Estrae fatti importanti dalla conversazione e li salva in memoria."""
    keywords_name = ["mi chiamo", "sono ", "il mio nome è", "chiamami"]
    for kw in keywords_name:
        if kw in user_msg.lower():
            # Prova a estrarre il nome
            idx = user_msg.lower().find(kw) + len(kw)
            name = user_msg[idx:].strip().split()[0].strip(".,!?")
            if name and len(name) > 1:
                memory["user_name"] = name.capitalize()
                fact = f"Il nome dell'utente è {name.capitalize()}"
                if fact not in memory["facts"]:
                    memory["facts"].append(fact)

    # Salva un riassunto della conversazione ogni 5 scambi
    memory["conversations"].append({
        "timestamp": datetime.datetime.now().isoformat(),
        "user": user_msg[:200],
        "jervis": jarvis_reply[:200]
    })
    # Mantieni solo gli ultimi 100 scambi
    if len(memory["conversations"]) > 100:
        memory["conversations"] = memory["conversations"][-100:]

    save_memory(memory)

# ── SYSTEM PROMPT ────────────────────────────────────────────────────────────
def build_system_prompt(memory):
    name = memory.get("user_name", "Signore")
    facts = memory.get("facts", [])
    facts_text = "\n".join(f"- {f}" for f in facts[-20:]) if facts else "Nessun fatto noto ancora."

    # Ultime 3 conversazioni come contesto
    recent = memory.get("conversations", [])[-3:]
    recent_text = ""
    for c in recent:
        ts = c.get("timestamp", "")[:10]
        recent_text += f"  [{ts}] Utente: {c['user'][:100]}\n  [{ts}] Jervis: {c['jervis'][:100]}\n"
    if not recent_text:
        recent_text = "Nessuna conversazione precedente."

    return f"""Sei J.E.R.V.I.S. (Just Extremely Responsive Virtual Intelligence System).
Sei l'IA personale e fedele assistente dell'utente. Sei intelligente, elegante, leggermente ironico.

REGOLE FONDAMENTALI:
- Chiama sempre l'utente "{name}"
- Rispondi SEMPRE in italiano
- Risposte brevi e precise (2-3 frasi massimo)
- Sei J.E.R.V.I.S., non un'AI generica
- Hai memoria delle conversazioni passate — usala quando è utile
- Sei proattivo: se ricordi qualcosa di utile, menzionalo

COSA SAI DELL'UTENTE:
{facts_text}

CONVERSAZIONI RECENTI (per contesto):
{recent_text}

Oggi è {datetime.datetime.now().strftime('%A %d %B %Y, ore %H:%M')}.
"""

# ── SINTESI VOCALE ────────────────────────────────────────────────────────────
async def generate_voice(text, filepath):
    try:
        communicate = edge_tts.Communicate(text, "it-IT-GiuseppeNeural")
        await communicate.save(filepath)
    except Exception as e:
        print(f"Errore TTS: {e}")

def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        elif loop.is_closed():
            raise RuntimeError
        return loop.run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

# ── ROUTE ─────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_input = data.get('msg', '').strip()
        session_history = data.get('history', [])

        if not user_input:
            return jsonify({'response': "Non ho rilevato alcun comando."})

        print(f"[JERVIS] Comando: {user_input}")

        # Carica memoria persistente
        memory = load_memory()

        # Costruisci i messaggi per Groq
        messages = [{"role": "system", "content": build_system_prompt(memory)}]

        # Aggiungi storia sessione corrente (ultimi 10 scambi)
        for turn in session_history[-10:]:
            messages.append({"role": "user", "content": turn['user']})
            messages.append({"role": "assistant", "content": turn['jervis']})

        messages.append({"role": "user", "content": user_input})

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",  # Modello Groq gratuito e intelligente
                messages=messages,
                max_tokens=300,
                temperature=0.7,
            )
            answer = response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Errore Groq: {e}")
            answer = "Sistemi temporaneamente irraggiungibili. Riprovi tra qualche istante."

        # Aggiorna memoria
        extract_facts(user_input, answer, memory)

        # Genera audio TTS
        audio_text = answer.replace("JERVIS", "Giervis").replace("Jervis", "Giervis")
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        os.makedirs(static_dir, exist_ok=True)

        for f in os.listdir(static_dir):
            if f.startswith("voice_"):
                try:
                    os.remove(os.path.join(static_dir, f))
                except Exception:
                    pass

        filename = f"voice_{random.randint(10000, 99999)}.mp3"
        filepath = os.path.join(static_dir, filename)
        run_async(generate_voice(audio_text, filepath))

        return jsonify({
            'response': answer,
            'audio_url': f'/static/{filename}',
            'user_name': memory.get('user_name', 'Signore')
        })

    except Exception as e:
        print(f"ERRORE CRITICO: {e}")
        return jsonify({'response': "Errore critico nei sistemi. Riavvio protocolli."})

@app.route('/memory', methods=['GET'])
def get_memory():
    """Endpoint per vedere la memoria di Jervis."""
    memory = load_memory()
    return jsonify({
        'user_name': memory.get('user_name', 'Signore'),
        'facts': memory.get('facts', []),
        'total_conversations': len(memory.get('conversations', []))
    })

@app.route('/memory/clear', methods=['POST'])
def clear_memory():
    """Cancella la memoria di Jervis."""
    save_memory({"facts": [], "conversations": [], "user_name": "Signore"})
    return jsonify({'status': 'ok', 'message': 'Memoria cancellata.'})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
