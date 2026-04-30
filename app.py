import os
import json
import asyncio
import random
import datetime
import requests
from flask import Flask, render_template, request, jsonify
import edge_tts

# ── GROQ ────────────────────────────────────────────────────────────────────
from groq import Groq

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ══════════════════════════════════════════════════════════════════
#  CONFIGURAZIONE
# ══════════════════════════════════════════════════════════════════
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY",  "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")
TAPO_EMAIL    = os.environ.get("TAPO_EMAIL",     "colettoalessandro0@gmail.com")
TAPO_PASSWORD = os.environ.get("TAPO_PASSWORD",  "alessandro10")
TAPO_DEVICES  = {
    "sedia": "192.168.1.82",  # IP spina sedia
}
# ══════════════════════════════════════════════════════════════════

groq_client = Groq(api_key=GROQ_API_KEY)

# ── TAPO con plugp100 (async) ─────────────────────────────────────────────
async def _tapo_action_async(ip, email, password, action):
    try:
        import aiohttp
        from plugp100.common.credentials import AuthCredential
        from plugp100.new.device_factory import connect, DeviceConnectConfiguration

        credentials = AuthCredential(email, password)
        config = DeviceConnectConfiguration(host=ip, credentials=credentials)
        async with aiohttp.ClientSession() as session:
            device = await connect(config, session)
            await device.update()
            if action == "on":
                await device.turn_on()
            else:
                await device.turn_off()
            return action
    except Exception as e:
        print(f"Errore Tapo async: {e}")
        return None

def tapo_action(device_name, action):
    ip = TAPO_DEVICES.get(device_name)
    if not ip:
        return None
    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_tapo_action_async(ip, TAPO_EMAIL, TAPO_PASSWORD, action))
        loop.close()
        return result
    except Exception as e:
        print(f"Errore Tapo: {e}")
        return None

# ── PAROLE CHIAVE LUCE ────────────────────────────────────────────────────
LUCE_ON  = ["buio","non ci vedo","accendi","è scuro","scuro","non vedo",
            "accendi la luce","accendi sedia","illumina","fa buio","è buia","luce"]
LUCE_OFF = ["spegni","troppa luce","spegni la luce","spegni sedia","non serve la luce"]

def controlla_tapo(testo):
    t = testo.lower()
    if any(k in t for k in LUCE_OFF):
        return tapo_action("sedia", "off")
    if any(k in t for k in LUCE_ON):
        return tapo_action("sedia", "on")
    return None

# ── GENERAZIONE IMMAGINI ──────────────────────────────────────────────────
def genera_immagine(prompt):
    prompt_enc = requests.utils.quote(prompt)
    seed = random.randint(1, 99999)
    return f"https://image.pollinations.ai/prompt/{prompt_enc}?width=768&height=512&nologo=true&seed={seed}"

# ── ANALISI IMMAGINE ──────────────────────────────────────────────────────
def analizza_immagine(image_b64, media_type, domanda):
    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                {"type": "text", "text": f"Sei JERVIS, assistente AI elegante. Rispondi in italiano, sii preciso e breve. {domanda}"}
            ]}],
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Errore vision: {e}")
        return "Non riesco ad analizzare l'immagine in questo momento, Signore."

# ── MEMORIA ───────────────────────────────────────────────────────────────
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jervis_memory.json")

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"facts": [], "conversations": [], "user_name": "Signore"}

def save_memory(memory):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Errore memoria: {e}")

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

def build_system_prompt(memory):
    name = memory.get("user_name", "Signore")
    facts_text = "\n".join(f"- {f}" for f in memory.get("facts", [])[-20:]) or "Nessun fatto noto."
    recent = memory.get("conversations", [])[-3:]
    recent_text = "".join(
        f"  [{c.get('timestamp','')[:10]}] Utente: {c['user'][:100]}\n  Jervis: {c['jervis'][:100]}\n"
        for c in recent
    ) or "Nessuna conversazione precedente."
    return f"""Sei J.E.R.V.I.S. (Just Extremely Responsive Virtual Intelligence System).
Sei l'IA personale dell'utente. Intelligente, elegante, leggermente ironico.

REGOLE:
- Chiama sempre l'utente "{name}"
- Rispondi SEMPRE in italiano
- Risposte brevi e precise (max 2-3 frasi)
- Sei JERVIS, non un'AI generica

CAPACITÀ:
- Controllo spina smart "sedia"
- Generazione immagini su richiesta
- Analisi foto e file

COSA SAI DELL'UTENTE:
{facts_text}

CONVERSAZIONI RECENTI:
{recent_text}

Oggi è {datetime.datetime.now().strftime('%A %d %B %Y, ore %H:%M')}.
"""

# ── TTS ───────────────────────────────────────────────────────────────────
async def generate_voice(text, filepath):
    try:
        communicate = edge_tts.Communicate(text, "it-IT-GiuseppeNeural")
        await communicate.save(filepath)
    except Exception as e:
        print(f"Errore TTS: {e}")

def run_async(coro):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# ── ROUTE ─────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_input = data.get('msg', '').strip()
        session_history = data.get('history', [])
        image_b64 = data.get('image_b64', None)
        image_type = data.get('image_type', 'image/jpeg')

        if not user_input and not image_b64:
            return jsonify({'response': "Non ho rilevato alcun comando."})

        print(f"[JERVIS] Comando: {user_input or '[immagine]'}")
        memory = load_memory()

        # Immagine caricata
        if image_b64:
            domanda = user_input if user_input else "Descrivi questa immagine in dettaglio."
            answer = analizza_immagine(image_b64, image_type, domanda)
            extract_facts(user_input or "[immagine]", answer, memory)
            return jsonify({'response': answer})

        # Generazione immagine
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

        # Controllo Tapo
        tapo_result = controlla_tapo(user_input)

        # Chat Groq
        messages = [{"role": "system", "content": build_system_prompt(memory)}]
        for turn in session_history[-10:]:
            messages.append({"role": "user", "content": turn['user']})
            messages.append({"role": "assistant", "content": turn['jervis']})
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
            print(f"Errore Groq: {e}")
            answer = "Sistemi temporaneamente irraggiungibili, Signore."

        if tapo_result:
            print(f"[TAPO] sedia: {tapo_result}")

        extract_facts(user_input, answer, memory)

        # TTS
        audio_text = answer.replace("JERVIS","Giervis").replace("Jervis","Giervis")
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        os.makedirs(static_dir, exist_ok=True)
        for f in os.listdir(static_dir):
            if f.startswith("voice_"):
                try: os.remove(os.path.join(static_dir, f))
                except: pass
        filename = f"voice_{random.randint(10000,99999)}.mp3"
        run_async(generate_voice(audio_text, os.path.join(static_dir, filename)))

        result = {'response': answer, 'audio_url': f'/static/{filename}'}
        if tapo_result:
            result['tapo'] = tapo_result
        return jsonify(result)

    except Exception as e:
        print(f"ERRORE CRITICO: {e}")
        return jsonify({'response': "Errore critico nei sistemi."})

@app.route('/memory', methods=['GET'])
def get_memory():
    memory = load_memory()
    return jsonify({'user_name': memory.get('user_name','Signore'),
                    'facts': memory.get('facts',[]),
                    'total_conversations': len(memory.get('conversations',[]))})

@app.route('/memory/clear', methods=['POST'])
def clear_memory():
    save_memory({"facts":[],"conversations":[],"user_name":"Signore"})
    return jsonify({'status':'ok'})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
