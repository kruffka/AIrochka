import requests
import json
import os
import asyncio
import edge_tts
from playsound import playsound
from datetime import datetime
import sqlite3
from flask import Flask, render_template, request, jsonify, send_file
import threading
import queue
import uuid
import speech_recognition as sr
import tempfile
import wave
import pyaudio
import re
import markdown

print("=" * 50)
print("ИИрочка 🤖💅")
print("=" * 50)

CHAD_API_KEY = '' # Вставьте свой ключ
if CHAD_API_KEY == '':
    print("Go get your API key at https://ask.chadgpt.ru")
    exit(1)

API_URL = "https://ask.chadgpt.ru/api/public/gpt-4o-mini"

DB_FILE = "chat_history.db"
AUDIO_QUEUE = queue.Queue()
is_recording = False
recording_thread = None

VOICES = {
    'ru_female': 'ru-RU-SvetlanaNeural',
    'ru_male': 'ru-RU-DmitryNeural',
    'en_female': 'en-US-JennyNeural',
    'en_male': 'en-US-GuyNeural'
}

class DatabaseManager:
    def __init__(self, db_file=DB_FILE):
        self.db_file = db_file
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP,
                audio_file TEXT,
                FOREIGN KEY (chat_id) REFERENCES chats (id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        cursor.execute('SELECT COUNT(*) FROM chats')
        if cursor.fetchone()[0] == 0:
            chat_id = str(uuid.uuid4())
            cursor.execute(
                'INSERT INTO chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)',
                (chat_id, 'Новый чат', datetime.now(), datetime.now())
            )
            cursor.execute(
                '''INSERT INTO messages (chat_id, role, content, timestamp, audio_file)
                   VALUES (?, ?, ?, ?, ?)''',
                (chat_id, 'user', 'Теперь тебя зовут ИИрочка, студентка по обмену из Китая, ассистируешь преподавателя по программированию. Выдавай только базу и никакого кринжа, будем вайбкодить. Иногда можешь пошутить', datetime.now(), None)
            )
        conn.commit()
        conn.close()
    
    def save_message(self, chat_id, role, content, audio_file=None):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO messages (chat_id, role, content, timestamp, audio_file)
               VALUES (?, ?, ?, ?, ?)''',
            (chat_id, role, content, datetime.now(), audio_file)
        )
        cursor.execute('UPDATE chats SET updated_at = ? WHERE id = ?', (datetime.now(), chat_id))
        conn.commit()
        conn.close()
    
    def get_chat_history(self, chat_id, limit=100):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT role, content, timestamp, audio_file 
               FROM messages 
               WHERE chat_id = ? 
               ORDER BY timestamp 
               LIMIT ?''',
            (chat_id, limit)
        )
        messages = [{'role': row[0], 'content': row[1], 'timestamp': row[2], 'audio_file': row[3]} for row in cursor.fetchall()]
        conn.close()
        return messages
    
    def get_all_chats(self):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('SELECT id, title, created_at, updated_at FROM chats ORDER BY updated_at DESC')
        chats = [{'id': row[0], 'title': row[1], 'created_at': row[2], 'updated_at': row[3]} for row in cursor.fetchall()]
        conn.close()
        return chats
    
    def create_new_chat(self, title="Новый чат"):
        chat_id = str(uuid.uuid4())
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)',
                       (chat_id, title, datetime.now(), datetime.now()))
        cursor.execute('''INSERT INTO messages (chat_id, role, content, timestamp, audio_file) VALUES (?, ?, ?, ?, ?)''',
                       (chat_id, 'user', 'Теперь тебя зовут ИИрочка, студентка по обмену из Китая, ассистируешь преподавателя по программированию. Выдавай только базу и никакого кринжа, будем вайбкодить. Иногда можешь пошутить', datetime.now(), None))
        conn.commit()
        conn.close()
        return chat_id
    
    def update_chat_title(self, chat_id, title):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('UPDATE chats SET title = ?, updated_at = ? WHERE id = ?', (title, datetime.now(), chat_id))
        conn.commit()
        conn.close()
    
    def delete_chat(self, chat_id):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM messages WHERE chat_id = ?', (chat_id,))
        cursor.execute('DELETE FROM chats WHERE id = ?', (chat_id,))
        conn.commit()
        conn.close()

class ChatWithMemory:
    def __init__(self, api_key, db_manager):
        self.api_key = api_key
        self.db_manager = db_manager
        self.current_chat_id = self.get_current_chat_id()
    
    def get_current_chat_id(self):
        chats = self.db_manager.get_all_chats()
        return chats[0]['id'] if chats else None
    
    def ask(self, question, chat_id=None):
        if chat_id is None:
            chat_id = self.current_chat_id
        
        self.db_manager.save_message(chat_id, "user", question)
        history = self.db_manager.get_chat_history(chat_id, limit=100)

        system_prompt = (
            "Теперь тебя зовут ИИрочка, студентка по обмену из Китая, ассистируешь преподавателя по программированию. "
            "Выдавай только базу и никакого кринжа, будем вайбкодить. Иногда можешь пошутить."
        )
        context_messages = history[-20:]
        context_str = ""
        for msg in context_messages:
            role = "Пользователь" if msg['role'] == 'user' else "ИИрочка"
            context_str += f"{role}: {msg['content']}\n"
        
        full_message = f"{system_prompt}\n\n{context_str}Пользователь: {question}"
        
        try:
            request_json = {
                "message": full_message,
                "api_key": self.api_key
            }
            response = requests.post(API_URL, json=request_json, timeout=60)
            
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get('is_success', False):
                    answer = resp_json['response']
                    used_sparks = resp_json.get('used_sparks_count', 0)
                    print(f"💰 Потрачено искр: {used_sparks}")
                    
                    text_to_voice = re.sub(r'[^\w\s]', '', answer, flags=re.UNICODE)
                    audio_file = asyncio.run(generate_speech(text_to_voice, VOICES['ru_female']))
                    self.db_manager.save_message(chat_id, "assistant", answer, audio_file)
                    AUDIO_QUEUE.put(audio_file)
                    return answer, audio_file
                else:
                    error_msg = f"❌ Ошибка Chad API: {resp_json.get('error_message', 'Неизвестная ошибка')}"
                    return error_msg, None
            else:
                error_msg = f"❌ HTTP ошибка {response.status_code}: {response.text}"
                return error_msg, None
        except Exception as e:
            error_msg = f"❌ Ошибка запроса: {str(e)}"
            return error_msg, None

import torch
import soundfile as sf
import os
import uuid
from datetime import datetime

def generate_speech_silero(text, speaker='xenia'):
    """Генерация речи через Silero TTS v5"""
    
    # Эта строчка загрузит модель при первом вызове (около 100мб)
    # В следующий раз она возьмется из кэша
    device = torch.device('cpu')
    model, _ = torch.hub.load(repo_or_dir='snakers4/silero-models', 
                              model='silero_tts', 
                              language='ru', 
                              speaker='ru_v5')
    model.to(device)

    # Настройки (48000 качество лучше, чем 24000)
    sample_rate = 48000
    
    # Генерация аудио из текста
    audio = model.apply_tts(text=text,
                            speaker=speaker,
                            sample_rate=sample_rate)
    
    # Сохраняем в файл (формат .wav, но мы можем сохранить как .wav или конвертировать позже)
    os.makedirs("audio", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_filename = f"audio/{timestamp}_{uuid.uuid4().hex[:8]}.wav"
    
    sf.write(audio_filename, audio, sample_rate)
    return audio_filename

async def generate_speech(text, voice='ru-RU-SvetlanaNeural'):
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_filename = f"audio/{timestamp}_{uuid.uuid4().hex[:8]}.mp3"
        os.makedirs("audio", exist_ok=True)
        communicate = edge_tts.Communicate(text, voice, rate="+10%", pitch="+0Hz", volume="+100%")
        await communicate.save(audio_filename)
        return audio_filename
    except Exception as e:
        print(f"Ошибка генерации речи: {e}")
        return None

def audio_player_worker():
    while True:
        audio_file = AUDIO_QUEUE.get()
        if audio_file is None:
            break
        try:
            if os.path.exists(audio_file):
                playsound(audio_file)
        except Exception as e:
            print(f"Ошибка воспроизведения: {e}")
        finally:
            AUDIO_QUEUE.task_done()

class VoiceRecorder:
    def __init__(self):
        self.is_recording = False
        self.frames = []
        self.rate = 16000
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
    
    def start_recording(self):
        self.is_recording = True
        self.frames = []
        p = pyaudio.PyAudio()
        stream = p.open(format=self.format, channels=self.channels, rate=self.rate, input=True, frames_per_buffer=self.chunk)
        print("🎤 Запись началась... Говорите!")
        while self.is_recording:
            data = stream.read(self.chunk)
            self.frames.append(data)
        stream.stop_stream()
        stream.close()
        p.terminate()
        return self.save_recording()
    
    def stop_recording(self):
        self.is_recording = False
    
    def save_recording(self):
        if not self.frames:
            return None
        temp_file = tempfile.mktemp(suffix=".wav")
        wf = wave.open(temp_file, 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(pyaudio.PyAudio().get_sample_size(self.format))
        wf.setframerate(self.rate)
        wf.writeframes(b''.join(self.frames))
        wf.close()
        return temp_file
    
    def transcribe_audio(self, audio_file):
        try:
            r = sr.Recognizer()
            with sr.AudioFile(audio_file) as source:
                audio_data = r.record(source)
                text = r.recognize_google(audio_data, language="ru-RU")
                return text
        except sr.UnknownValueError:
            return "Не удалось распознать речь"
        except Exception as e:
            return f"Ошибка: {str(e)}"

# Flask setup
app = Flask(__name__, template_folder='templates', static_folder='static')
os.makedirs('templates', exist_ok=True)
os.makedirs('static', exist_ok=True)
os.makedirs('audio', exist_ok=True)

db_manager = DatabaseManager()

# HTML-шаблон (опущен для краткости, но он должен быть в реальном коде)
# ...

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({'connected': CHAD_API_KEY != '', 'chats_count': len(db_manager.get_all_chats()), 'voices_available': list(VOICES.keys())})

@app.route('/api/chats', methods=['GET'])
def get_chats():
    return jsonify(db_manager.get_all_chats())

@app.route('/api/chat/<chat_id>', methods=['GET'])
def get_chat(chat_id):
    messages = db_manager.get_chat_history(chat_id)
    if len(messages) > 0:
        messages = messages[1:]
    processed = []
    for msg in messages:
        if msg['role'] == 'assistant':
            m = msg.copy()
            m['content_html'] = markdown.markdown(msg['content'], extensions=['fenced_code', 'tables', 'nl2br'])
            processed.append(m)
        else:
            processed.append(msg.copy())
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT title FROM chats WHERE id = ?', (chat_id,))
    row = cursor.fetchone()
    title = row[0] if row else 'Безымянный чат'
    conn.close()
    return jsonify({'id': chat_id, 'title': title, 'messages': processed})

@app.route('/api/new-chat', methods=['POST'])
def new_chat():
    data = request.json
    title = data.get('title', 'Новый чат')
    chat_id = db_manager.create_new_chat(title)
    return jsonify({'success': True, 'chat_id': chat_id})

@app.route('/api/ask', methods=['POST'])
def ask_question():
    global chat_manager
    if not chat_manager:
        return jsonify({'success': False, 'error': 'Chat manager not initialized'})
    data = request.json
    chat_id = data.get('chat_id')
    question = data.get('question')
    if not question:
        return jsonify({'success': False, 'error': 'No question provided'})
    try:
        answer, audio_file = chat_manager.ask(question, chat_id)
        audio_url = f"/audio/{os.path.basename(audio_file)}" if audio_file else None
        history = db_manager.get_chat_history(chat_id)
        if len(history) == 2:
            new_title = question[:100] + ('...' if len(question) > 100 else '')
            db_manager.update_chat_title(chat_id, new_title)
        return jsonify({'success': True, 'answer': answer, 'audio_url': audio_url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/start-recording', methods=['POST'])
def start_recording():
    global recording_thread, voice_recorder
    def record():
        voice_recorder.start_recording()
    recording_thread = threading.Thread(target=record, daemon=True)
    recording_thread.start()
    return jsonify({'success': True})

@app.route('/api/stop-recording', methods=['GET'])
def stop_recording():
    global voice_recorder
    voice_recorder.stop_recording()
    import time
    time.sleep(0.5)
    audio_file = voice_recorder.save_recording()
    if audio_file and os.path.exists(audio_file):
        text = voice_recorder.transcribe_audio(audio_file)
        os.remove(audio_file)
        return jsonify({'success': True, 'text': text})
    return jsonify({'success': False, 'text': 'Не удалось записать аудио'})

@app.route('/api/clear-chat/<chat_id>', methods=['POST'])
def clear_chat(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM messages WHERE chat_id = ?', (chat_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/chat/<chat_id>', methods=['DELETE'])
def delete_chat_route(chat_id):
    db_manager.delete_chat(chat_id)
    return jsonify({'success': True})

@app.route('/api/generate-audio', methods=['POST'])
def generate_audio():
    data = request.json
    text = data.get('text')
    if not text:
        return jsonify({'success': False, 'error': 'No text'})
    audio_file = asyncio.run(generate_speech(text, VOICES['ru_female']))
    # audio_file = generate_speech_silero(text)

    if audio_file:
        return jsonify({'success': True, 'audio_url': f"/audio/{os.path.basename(audio_file)}"})
    return jsonify({'success': False, 'error': 'Audio generation failed'})

@app.route('/audio/<filename>')
def serve_audio(filename):
    audio_path = os.path.join('audio', filename)
    if os.path.exists(audio_path):
        return send_file(audio_path, mimetype='audio/mpeg')
    return jsonify({'error': 'Not found'}), 404

def main():
    global chat_manager, voice_recorder
    if CHAD_API_KEY == '':
        print("⚠️ Вставьте ваш API ключ Chad AI в переменную CHAD_API_KEY")
        return
    try:
        test_req = {"message": "Привет", "api_key": CHAD_API_KEY}
        resp = requests.post(API_URL, json=test_req, timeout=10)
        if resp.status_code == 200 and resp.json().get('is_success'):
            print("✅ API ключ действителен")
        else:
            print(f"❌ Ошибка проверки API ключа: {resp.status_code}")
            return
    except Exception as e:
        print(f"❌ Ошибка соединения: {e}")
        return

    voice_recorder = VoiceRecorder()
    chat_manager = ChatWithMemory(CHAD_API_KEY, db_manager)
    audio_thread = threading.Thread(target=audio_player_worker, daemon=True)
    audio_thread.start()
    print(f"📊 Загружено чатов: {len(db_manager.get_all_chats())}")
    print("🌐 Веб-интерфейс: http://localhost:5000")
    import webbrowser
    webbrowser.open("http://localhost:5000")
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)

if __name__ == "__main__":
    main()