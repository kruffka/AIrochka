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

API_KEY = ""
if (API_KEY == ""):
    print("Go get your API key at https://openrouter.ai/")
    exit(1)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DB_FILE = "chat_history.db"
AUDIO_QUEUE = queue.Queue()
is_recording = False
recording_thread = None

# Список нейронных голосов
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
        """Инициализация базы данных"""
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
        """Сохранить сообщение в БД"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute(
            '''INSERT INTO messages (chat_id, role, content, timestamp, audio_file)
               VALUES (?, ?, ?, ?, ?)''',
            (chat_id, role, content, datetime.now(), audio_file)
        )
        
        cursor.execute(
            'UPDATE chats SET updated_at = ? WHERE id = ?',
            (datetime.now(), chat_id)
        )
        
        conn.commit()
        conn.close()
    
    def get_chat_history(self, chat_id, limit=100):
        """Получить историю чата"""
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
        
        messages = []
        for row in cursor.fetchall():
            messages.append({
                'role': row[0],
                'content': row[1],
                'timestamp': row[2],
                'audio_file': row[3]
            })
        
        conn.close()
        return messages
    
    def get_all_chats(self):
        """Получить все чаты"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute(
            '''SELECT id, title, created_at, updated_at 
               FROM chats 
               ORDER BY updated_at DESC'''
        )
        
        chats = []
        for row in cursor.fetchall():
            chats.append({
                'id': row[0],
                'title': row[1],
                'created_at': row[2],
                'updated_at': row[3]
            })
        
        conn.close()
        return chats
    
    def create_new_chat(self, title="Новый чат"):
        """Создать новый чат"""
        chat_id = str(uuid.uuid4())
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute(
            'INSERT INTO chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)',
            (chat_id, title, datetime.now(), datetime.now())
        )
        cursor.execute(
            '''INSERT INTO messages (chat_id, role, content, timestamp, audio_file) VALUES (?, ?, ?, ?, ?)''',
            (chat_id, 'user', 'Теперь тебя зовут ИИрочка, студентка по обмену из Китая, ассистируешь преподавателя по программированию. Выдавай только базу и никакого кринжа, будем вайбкодить. Иногда можешь пошутить', datetime.now(), None)
        )
        conn.commit()
        conn.close()
        return chat_id
    
    def update_chat_title(self, chat_id, title):
        """Обновить заголовок чата"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute(
            'UPDATE chats SET title = ?, updated_at = ? WHERE id = ?',
            (title, datetime.now(), chat_id)
        )
        
        conn.commit()
        conn.close()
    
    def delete_chat(self, chat_id):
        """Удалить чат и все его сообщения"""
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
        """Получить ID текущего чата"""
        chats = self.db_manager.get_all_chats()
        return chats[0]['id'] if chats else None
    
    def ask(self, question, chat_id=None):
        """Задать вопрос с учетом истории"""
        if chat_id is None:
            chat_id = self.current_chat_id
        
        # Сохраняем вопрос
        self.db_manager.save_message(chat_id, "user", question)
        
        # Получаем историю
        history = self.db_manager.get_chat_history(chat_id, limit=100)

        # Подготавливаем сообщения для API
        messages = [
            {"role": "system", "content": "еперь тебя зовут ИИрочка, студентка по обмену из Китая, ассистируешь преподавателя по программированию. Выдавай только базу и никакого кринжа, будем вайбкодить"}
        ]
        
        for msg in history[-20:]:
            messages.append({"role": msg['role'], "content": msg['content']})
        
        messages.append({"role": "user", "content": question})
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "deepseek/deepseek-chat",
            "messages": messages,
            "max_tokens": 1000,
            "temperature": 0.7
        }
        
        try:
            response = requests.post(API_URL, headers=headers, json=data, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                answer = result['choices'][0]['message']['content']
                text_to_voice = re.sub(r'[^\w\s]', '', answer, flags=re.UNICODE)     

                # Генерируем аудио и сохраняем ответ
                audio_file = asyncio.run(generate_speech(text_to_voice, VOICES['ru_female']))
                self.db_manager.save_message(chat_id, "assistant", answer, audio_file)
                
                # Добавляем в очередь для воспроизведения
                AUDIO_QUEUE.put(audio_file)
                
                return answer, audio_file
            else:
                error_msg = f"❌ Ошибка {response.status_code}: {response.text}"
                return error_msg, None
                
        except Exception as e:
            error_msg = f"❌ Ошибка: {str(e)}"
            return error_msg, None

async def generate_speech(text, voice='ru-RU-SvetlanaNeural'):
    """Генерация речи с Edge TTS"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_filename = f"audio/{timestamp}_{uuid.uuid4().hex[:8]}.mp3"
        
        os.makedirs("audio", exist_ok=True)
        
        communicate = edge_tts.Communicate(
            text,
            voice,
            rate="+10%",
            pitch="+0Hz",
            volume="+100%"
        )
        
        await communicate.save(audio_filename)
        return audio_filename
    except Exception as e:
        print(f"Ошибка генерации речи: {e}")
        return None

def audio_player_worker():
    """Рабочий поток для воспроизведения аудио"""
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
        """Начать запись голоса"""
        self.is_recording = True
        self.frames = []
        
        p = pyaudio.PyAudio()
        stream = p.open(format=self.format,
                       channels=self.channels,
                       rate=self.rate,
                       input=True,
                       frames_per_buffer=self.chunk)
        
        print("🎤 Запись началась... Говорите!")
        while self.is_recording:
            data = stream.read(self.chunk)
            self.frames.append(data)
        
        stream.stop_stream()
        stream.close()
        p.terminate()
        
        return self.save_recording()
    
    def stop_recording(self):
        """Остановить запись"""
        self.is_recording = False
    
    def save_recording(self):
        """Сохранить запись в файл"""
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
        """Транскрибировать аудио в текст"""
        try:
            r = sr.Recognizer()
            with sr.AudioFile(audio_file) as source:
                audio_data = r.record(source)
                text = r.recognize_google(audio_data, language="ru-RU")
                return text
        except sr.UnknownValueError:
            return "Не удалось распознать речь"
        except sr.RequestError as e:
            return f"Ошибка сервиса распознавания: {e}"
        except Exception as e:
            return f"Ошибка: {str(e)}"

# Инициализация Flask
app = Flask(__name__, 
            template_folder='templates',
            static_folder='static')

os.makedirs('templates', exist_ok=True)
os.makedirs('static', exist_ok=True)
os.makedirs('audio', exist_ok=True)


db_manager = DatabaseManager()

# HTML шаблон
with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write('''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ИИрочка 🤖💅</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            gap: 20px;
            height: calc(100vh - 40px);
        }
        
        /* Сайдбар с чатами */
        .sidebar {
            width: 300px;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
            display: flex;
            flex-direction: column;
        }
        
        .sidebar-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        
        .sidebar h2 {
            color: #333;
            font-size: 1.5rem;
        }
        
        #new-chat-btn {
            background: #667eea;
            color: white;
            border: none;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            cursor: pointer;
            font-size: 1.2rem;
            transition: all 0.3s ease;
        }
        
        #new-chat-btn:hover {
            background: #764ba2;
            transform: rotate(90deg);
        }
        
        .chats-list {
            flex: 1;
            overflow-y: auto;
            margin-bottom: 20px;
        }
        
        .chat-item {
            padding: 12px 15px;
            margin-bottom: 10px;
            background: #f5f5f5;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s ease;
            position: relative;
        }
        
        .chat-item:hover {
            background: #e9e9e9;
            transform: translateX(5px);
        }
        
        .chat-item.active {
            background: #667eea;
            color: white;
        }
        
        .chat-title {
            font-weight: 600;
            margin-bottom: 5px;
        }
        
        .chat-date {
            font-size: 0.8rem;
            opacity: 0.7;
        }
        
        .delete-chat {
            position: absolute;
            right: 10px;
            top: 50%;
            transform: translateY(-50%);
            background: #ff4757;
            color: white;
            border: none;
            border-radius: 5px;
            padding: 5px 10px;
            cursor: pointer;
            font-size: 0.8rem;
            opacity: 0;
            transition: opacity 0.3s;
        }
        
        .chat-item:hover .delete-chat {
            opacity: 1;
        }
        
        /* Основная область чата */
        .chat-area {
            flex: 1;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 20px;
            display: flex;
            flex-direction: column;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
            overflow: hidden;
        }
        
        .chat-header {
            padding: 20px;
            border-bottom: 1px solid #eee;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .chat-header h2 {
            color: #333;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .voice-controls {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        .voice-btn {
            background: #667eea;
            color: white;
            border: none;
            padding: 10px 15px;
            border-radius: 10px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: all 0.3s ease;
        }
        
        .voice-btn:hover {
            background: #764ba2;
        }
        
        .voice-btn.active {
            background: #4CAF50;
        }
        
        .voice-btn.recording {
            background: #ff4757;
            animation: pulse 1.5s infinite;
        }
        
        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(255, 71, 87, 0.7); }
            70% { box-shadow: 0 0 0 10px rgba(255, 71, 87, 0); }
            100% { box-shadow: 0 0 0 0 rgba(255, 71, 87, 0); }
        }
        
        .chat-messages {
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        
        .message {
            max-width: 80%;
            padding: 15px 20px;
            border-radius: 18px;
            position: relative;
            animation: fadeIn 0.3s ease;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .user-message {
            background: #667eea;
            color: white;
            align-self: flex-end;
            border-bottom-right-radius: 5px;
        }
        
        .bot-message {
            background: #f0f0f0;
            color: #333;
            align-self: flex-start;
            border-bottom-left-radius: 5px;
        }
        
        .message-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 5px;
            font-size: 0.9rem;
        }
        
        .message-content {
            line-height: 1.5;
            word-break: break-word;
        }
        
        .message-actions {
            margin-top: 10px;
            display: flex;
            gap: 10px;
        }
        
        .action-btn {
            background: none;
            border: none;
            color: #666;
            cursor: pointer;
            font-size: 0.9rem;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: color 0.3s;
        }
        
        .action-btn:hover {
            color: #667eea;
        }
        
        .chat-input-area {
            padding: 20px;
            border-top: 1px solid #eee;
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        #message-input {
            flex: 1;
            padding: 15px 20px;
            border: 2px solid #eee;
            border-radius: 15px;
            font-size: 1rem;
            outline: none;
            transition: border-color 0.3s;
        }
        
        #message-input:focus {
            border-color: #667eea;
        }
        
        #send-btn {
            background: #667eea;
            color: white;
            border: none;
            border-radius: 15px;
            padding: 15px 30px;
            cursor: pointer;
            font-size: 1rem;
            font-weight: 600;
            transition: all 0.3s ease;
            min-width: 120px;
        }
        
        #send-btn:hover {
            background: #764ba2;
            transform: scale(1.05);
        }
        
        #send-btn:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }
        
        #voice-input-btn {
            background: #667eea;
            color: white;
            border: none;
            border-radius: 50%;
            width: 60px;
            height: 60px;
            cursor: pointer;
            font-size: 1.5rem;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        #voice-input-btn:hover {
            background: #764ba2;
        }
        
        #voice-input-btn.recording {
            background: #ff4757;
            animation: pulse 1.5s infinite;
        }
        
        .typing-indicator {
            display: none;
            align-self: flex-start;
            padding: 15px 20px;
            background: #f0f0f0;
            border-radius: 18px;
            margin-bottom: 15px;
        }
        
        .typing-dots {
            display: flex;
            gap: 5px;
        }
        
        .typing-dots span {
            width: 8px;
            height: 8px;
            background: #999;
            border-radius: 50%;
            animation: typing 1.4s infinite;
        }
        
        .typing-dots span:nth-child(2) { animation-delay: 0.2s; }
        .typing-dots span:nth-child(3) { animation-delay: 0.4s; }
        
        @keyframes typing {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-10px); }
        }
        
        .recording-indicator {
            display: none;
            color: #ff4757;
            font-weight: bold;
            margin-left: 10px;
        }
        
        /* Адаптивность */
        @media (max-width: 768px) {
            .container {
                flex-direction: column;
                height: auto;
            }
            
            .sidebar {
                width: 100%;
                margin-bottom: 20px;
            }
            
            .message {
                max-width: 90%;
            }
            
            .chat-input-area {
                flex-wrap: wrap;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Сайдбар с чатами -->
        <div class="sidebar">
            <div class="sidebar-header">
                <h2><i class="fas fa-comments"></i> Мои чаты</h2>
                <button id="new-chat-btn" title="Новый чат">
                    <i class="fas fa-plus"></i>
                </button>
            </div>
            <div class="chats-list" id="chats-list">
                <!-- Список чатов загружается динамически -->
            </div>
            <div class="status-info">
                <p id="api-status">🔴 API не подключен</p>
                <p id="recording-status"></p>
            </div>
        </div>
        <!-- Основная область чата -->
        <div class="chat-area">
            <div class="chat-header">
                <h2><i class="fas fa-robot"></i> <span id="current-chat-title">Новый чат</span></h2>
                <div class="voice-controls">
                    <button id="voice-toggle" class="voice-btn">
                        <i class="fas fa-volume-up"></i> Голос
                    </button>
                    <button id="clear-chat-btn" class="voice-btn" title="Очистить чат">
                        <i class="fas fa-trash"></i> Очистить
                    </button>
                </div>
            </div>
            
            <div class="chat-messages" id="chat-messages">
                <div class="welcome-message message bot-message">
                    <div class="message-content">
                        👋 Привет! меня зовут ИИрочка 🤖💅и я студентка по обмену из Китая. Задавайте вопросы, а я буду отвечать
                    </div>
                </div>
            </div>
            
            <div class="typing-indicator" id="typing-indicator">
                <div class="typing-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
            
            <div class="chat-input-area">
                <button id="voice-input-btn" title="Голосовой ввод">
                    <i class="fas fa-microphone"></i>
                </button>
                <span class="recording-indicator" id="recording-indicator">
                    <i class="fas fa-circle"></i> Запись...
                </span>
                <input type="text" id="message-input" placeholder="Введите сообщение или нажмите микрофон..." autocomplete="off">
                <button id="send-btn">Отправить</button>
            </div>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>

    <script>
        let currentChatId = null;
        let voiceEnabled = true;
        let currentAudio = null;
        let isRecording = false;
        let mediaRecorder = null;
        let audioChunks = [];
        
        // Загрузка при запуске
        document.addEventListener('DOMContentLoaded', function() {
            loadChats();
            checkAPIStatus();
            
            // Настройка горячих клавиш
            document.getElementById('message-input').focus();
            
            document.getElementById('message-input').addEventListener('keypress', function(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });
            
            // Обработчик для голосового ввода
            document.getElementById('voice-input-btn').addEventListener('click', toggleVoiceRecording);

            document.getElementById('send-btn').addEventListener('click', sendButton);
        });
        
        
        // Проверка статуса API
        async function checkAPIStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                document.getElementById('api-status').innerHTML = data.connected ? 
                    '🟢 API подключен' : '🔴 API не подключен';
            } catch (error) {
                console.error('Ошибка проверки API:', error);
            }
        }
        
        // Загрузка списка чатов
        async function loadChats() {
            try {
                const response = await fetch('/api/chats');
                const chats = await response.json();
                
                const chatsList = document.getElementById('chats-list');
                chatsList.innerHTML = '';
                
                chats.forEach(chat => {
                    const chatElement = document.createElement('div');
                    chatElement.className = 'chat-item';
                    chatElement.dataset.chatId = chat.id;
                    // Обрезаем дату для отображения
                    const date = new Date(chat.updated_at);
                    const formattedDate = date.toLocaleDateString('ru-RU', {
                        day: 'numeric',
                        month: 'short',
                        hour: '2-digit',
                        minute: '2-digit'
                    });
                    
                    chatElement.innerHTML = `
                        <div class="chat-title">${chat.title}</div>
                        <div class="chat-date">${formattedDate}</div>
                        <button class="delete-chat" onclick="deleteChat('${chat.id}')">
                            <i class="fas fa-trash"></i>
                        </button>
                    `;
                    
                    chatElement.addEventListener('click', () => loadChat(chat.id));
                    
                    if (currentChatId === null) {
                        currentChatId = chat.id;
                        loadChat(chat.id);
                    }
                    
                    chatsList.appendChild(chatElement);
                });

                // Помечаем активный чат
                updateActiveChat();
            } catch (error) {
                console.error('Ошибка загрузки чатов:', error);
            }
        }
        
        // Загрузка конкретного чата
        async function loadChat(chatId) {
            currentChatId = chatId;
            updateActiveChat();
            
            try {
                const response = await fetch(`/api/chat/${chatId}`);
                const data = await response.json();
                
                document.getElementById('current-chat-title').textContent = data.title;
                
                const messagesContainer = document.getElementById('chat-messages');
                messagesContainer.innerHTML = '';
                
                // Если чат пустой
                if (data.messages.length === 0) {
                    const welcomeMsg = document.createElement('div');
                    welcomeMsg.className = 'welcome-message message bot-message';
                    welcomeMsg.innerHTML = `
                        <div class="message-content">
                            👋 Привет! меня зовут ИИрочка 🤖💅и я студентка по обмену из Китая. Задавайте вопросы, а я буду отвечать
                        </div>
                    `;
                    messagesContainer.appendChild(welcomeMsg);
                }
                
                data.messages.forEach(msg => {
                    const content = (msg.role === 'assistant' && msg.content_html) 
                        ? msg.content_html 
                        : msg.content;
                    
                    addMessageToChat(content, msg.role === 'user', msg.timestamp);
                });
                
                messagesContainer.scrollTop = messagesContainer.scrollHeight;
            } catch (error) {
                console.error('Ошибка загрузки чата:', error);
            }
        }
        
        // Обновление активного чата
        function updateActiveChat() {
            document.querySelectorAll('.chat-item').forEach(item => {
                if (item.dataset.chatId === currentChatId) {
                    item.classList.add('active');
                } else {
                    item.classList.remove('active');
                }
            });
        }
        
        // Добавление сообщения в чат
        function addMessageToChat(content, isUser, timestamp = null) {
            const messagesContainer = document.getElementById('chat-messages');
            const messageDiv = document.createElement('div');
            
            const time = timestamp ? new Date(timestamp).toLocaleTimeString('ru-RU', {
                hour: '2-digit',
                minute: '2-digit'
            }) : new Date().toLocaleTimeString('ru-RU', {
                hour: '2-digit',
                minute: '2-digit'
            });
            
            messageDiv.className = `message ${isUser ? 'user-message' : 'bot-message'}`;

            // Преобразуем Markdown в HTML только для ответов бота, используя marked
            const displayContent = isUser ? content : marked.parse(content);
            
            messageDiv.innerHTML = `
                <div class="message-header">
                    <span>${isUser ? '👤 Вы' : '🤖💅 ИИрочка'}</span>
                    <span class="message-time">${time}</span>
                </div>
                <div class="message-content">${displayContent}</div>
                ${!isUser ? `
                <div class="message-actions">
                    <button class="action-btn" onclick="playAudioMessage(this)" title="Воспроизвести">
                        <i class="fas fa-play"></i> Аудио
                    </button>
                    <button class="action-btn" onclick="copyToClipboard(this)" title="Копировать">
                        <i class="fas fa-copy"></i> Копировать
                    </button>
                </div>
                ` : ''}
            `;
            
            // Сохраняем оригинальный текст для копирования (удаляем HTML теги)
            if (!isUser) {
                messageDiv.dataset.originalText = content;
            }
            
            messagesContainer.appendChild(messageDiv);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }

        // Обновленная функция копирования
        async function copyToClipboard(button) {
            const messageDiv = button.closest('.message');
            const originalText = messageDiv.dataset.originalText || 
                                messageDiv.querySelector('.message-content').textContent;
            
            try {
                await navigator.clipboard.writeText(originalText);
                showNotification('Текст скопирован в буфер обмена!');
            } catch (error) {
                console.error('Ошибка копирования:', error);
            }
        }
        
        // Отправка сообщения
        async function sendMessage() {
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            
            if (!message || !currentChatId) return;
            
            input.value = '';
            
            addMessageToChat(message, true);
            
            const typingIndicator = document.getElementById('typing-indicator');
            typingIndicator.style.display = 'block';
            
            try {
                const response = await fetch('/api/ask', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        chat_id: currentChatId,
                        question: message
                    })
                });
                
                const data = await response.json();
                
                typingIndicator.style.display = 'none';
                
                if (data.success) {
                    addMessageToChat(data.answer, false);
                    
                    // Воспроизводим аудио если включено
                    if (voiceEnabled && data.audio_url) {
                        playAudio(data.audio_url);
                    }
                } else {
                    addMessageToChat(`❌ ${data.error}`, false);
                }
                
                loadChats();
            } catch (error) {
                typingIndicator.style.display = 'none';
                addMessageToChat('❌ Ошибка сети. Попробуйте еще раз.', false);
                console.error('Ошибка отправки:', error);
            }
        }
        
        // Переключение записи голоса
        async function toggleVoiceRecording() {
            if (isRecording) {
                stopVoiceRecording();
            } else {
                startVoiceRecording();
            }
        }

        async function sendButton() {
            setTimeout(sendMessage, 500);
        }
        
        // Начать запись голоса
        async function startVoiceRecording() {
            const voiceBtn = document.getElementById('voice-input-btn');
            const indicator = document.getElementById('recording-indicator');
            
            try {
                const response = await fetch('/api/start-recording', {
                    method: 'POST'
                });
                
                if (response.ok) {
                    isRecording = true;
                    voiceBtn.classList.add('recording');
                    voiceBtn.innerHTML = '<i class="fas fa-stop"></i>';
                    indicator.style.display = 'inline';
                    document.getElementById('recording-status').textContent = '🎤 Запись...';
                }
            } catch (error) {
                console.error('Ошибка начала записи:', error);
            }
        }
        
        // Остановить запись голоса
        async function stopVoiceRecording() {
            const voiceBtn = document.getElementById('voice-input-btn');
            const indicator = document.getElementById('recording-indicator');
            const input = document.getElementById('message-input');
            
            try {
                const response = await fetch('/api/stop-recording');
                const data = await response.json();
                
                isRecording = false;
                voiceBtn.classList.remove('recording');
                voiceBtn.innerHTML = '<i class="fas fa-microphone"></i>';
                indicator.style.display = 'none';
                document.getElementById('recording-status').textContent = '';
                
                if (data.success && data.text) {
                    // Вставляем распознанный текст в поле ввода
                    input.value = data.text;
                    // Автоматически отправляем сообщение
                    setTimeout(sendMessage, 500);
                } else if (data.text) {
                    // Показываем ошибку распознавания
                    input.value = data.text;
                }
            } catch (error) {
                console.error('Ошибка остановки записи:', error);
                isRecording = false;
                voiceBtn.classList.remove('recording');
                voiceBtn.innerHTML = '<i class="fas fa-microphone"></i>';
                indicator.style.display = 'none';
            }
        }
        
        // Воспроизведение аудио
        function playAudio(audioUrl) {
            if (currentAudio) {
                currentAudio.pause();
                currentAudio.currentTime = 0;
            }
            
            currentAudio = new Audio(audioUrl);
            currentAudio.play().catch(e => {
                console.error('Ошибка воспроизведения:', e);
                // Fallback: если не удалось воспроизвести, показываем уведомление
                showNotification('Не удалось воспроизвести аудио. Проверьте звук.');
            });
        }
        
        // Воспроизведение аудио сообщения
        async function playAudioMessage(button) {
            const messageDiv = button.closest('.message');
            const audioUrl = messageDiv.dataset.audioUrl;
            
            if (audioUrl) {
                playAudio(audioUrl);
            } else {
                const content = messageDiv.querySelector('.message-content').textContent;
                
                try {
                    const response = await fetch('/api/generate-audio', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ text: content })
                    });
                    
                    const data = await response.json();
                    if (data.audio_url) {
                        messageDiv.dataset.audioUrl = data.audio_url;
                        playAudio(data.audio_url);
                    }
                } catch (error) {
                    console.error('Ошибка генерации аудио:', error);
                }
            }
        }
        
        function showNotification(message) {
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                background: #4CAF50;
                color: white;
                padding: 15px 20px;
                border-radius: 10px;
                z-index: 1000;
                animation: slideIn 0.3s ease;
            `;
            notification.textContent = message;
            document.body.appendChild(notification);
            
            setTimeout(() => {
                notification.style.animation = 'slideOut 0.3s ease';
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }
        
        // Создание нового чата
        document.getElementById('new-chat-btn').addEventListener('click', async function() {
            const title = prompt('Введите название нового чата:', `Чат от ${new Date().toLocaleDateString('ru-RU')}`);
            
            if (title) {
                try {
                    const response = await fetch('/api/new-chat', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ title: title })
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        loadChats();
                        loadChat(data.chat_id);
                    }
                } catch (error) {
                    console.error('Ошибка создания чата:', error);
                }
            }
        });
        
        // Удаление чата
        async function deleteChat(chatId) {
            if (!confirm('Вы уверены, что хотите удалить этот чат? Все сообщения будут потеряны.')) {
                return;
            }
            
            try {
                const response = await fetch(`/api/chat/${chatId}`, {
                    method: 'DELETE'
                });
                
                const data = await response.json();
                
                if (data.success) {
                    loadChats();
                }
            } catch (error) {
                console.error('Ошибка удаления чата:', error);
            }
        }
        
        // Переключение голоса
        document.getElementById('voice-toggle').addEventListener('click', function() {
            voiceEnabled = !voiceEnabled;
            this.classList.toggle('active');
            this.innerHTML = voiceEnabled ? 
                '<i class="fas fa-volume-up"></i> Голос включен' : 
                '<i class="fas fa-volume-mute"></i> Голос выключен';
        });
        
        // Очистка текущего чата
        document.getElementById('clear-chat-btn').addEventListener('click', async function() {
            if (!confirm('Очистить историю текущего чата?')) return;
            
            try {
                const response = await fetch(`/api/clear-chat/${currentChatId}`, {
                    method: 'POST'
                });
                
                const data = await response.json();
                
                if (data.success) {
                    const messagesContainer = document.getElementById('chat-messages');
                    messagesContainer.innerHTML = `
                        <div class="welcome-message message bot-message">
                            <div class="message-content">
                                👋 История чата очищена. Задавайте вопросы, а я буду отвечать голосом и текстом.
                            </div>
                        </div>
                    `;
                }
            } catch (error) {
                console.error('Ошибка очистки чата:', error);
            }
        });
        
        // Экспорт функций
        window.sendMessage = sendMessage;
        window.playAudioMessage = playAudioMessage;
        window.copyToClipboard = copyToClipboard;
        window.deleteChat = deleteChat;
        window.toggleVoiceRecording = toggleVoiceRecording;
    </script>
</body>
</html>
''')

# Инициализация рекордера
voice_recorder = VoiceRecorder()

# Маршруты Flask
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def api_status():
    """Проверка статуса API"""
    status = {
        'connected': API_KEY != "ВАШ_API_КЛЮЧ_ЗДЕСЬ",
        'chats_count': len(db_manager.get_all_chats()),
        'voices_available': list(VOICES.keys())
    }
    return jsonify(status)

@app.route('/api/chats', methods=['GET'])
def get_chats():
    """Получить список всех чатов"""
    chats = db_manager.get_all_chats()
    return jsonify(chats)

@app.route('/api/chat/<chat_id>', methods=['GET'])
def get_chat(chat_id):
    """Получить конкретный чат с сообщениями"""
    messages = db_manager.get_chat_history(chat_id)
    
    if len(messages) > 0:
        messages = messages[1:]  # Пропускаем первый элемент

    # Конвертируем Markdown в HTML для сообщений от бота
    processed_messages = []
    for msg in messages:
        if msg['role'] == 'assistant':
            # Сохраняем оригинальный Markdown и добавляем HTML версию
            processed_msg = msg.copy()
            processed_msg['content_html'] = markdown.markdown(
                msg['content'],
                extensions=['fenced_code', 'tables', 'nl2br']
            )
            processed_messages.append(processed_msg)
        else:
            # Для пользователя просто копируем
            processed_messages.append(msg.copy())

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT title FROM chats WHERE id = ?', (chat_id,))
    result = cursor.fetchone()
    conn.close()
    
    title = result[0] if result else 'Безымянный чат'
    
    return jsonify({
        'id': chat_id,
        'title': title,
        'messages': processed_messages  # Используем обработанные сообщения
    })

@app.route('/api/new-chat', methods=['POST'])
def new_chat():
    """Создать новый чат"""
    data = request.json
    title = data.get('title', 'Новый чат')
    
    chat_id = db_manager.create_new_chat(title)
    
    return jsonify({
        'success': True,
        'chat_id': chat_id,
        'message': 'Чат создан'
    })

@app.route('/api/ask', methods=['POST'])
def ask_question():
    """Задать вопрос ИИрочке"""
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
        
        # Обновляем заголовок чата
        history = db_manager.get_chat_history(chat_id)
        if len(history) == 2:
            new_title = question[:100] + ('...' if len(question) > 100 else '')
            db_manager.update_chat_title(chat_id, new_title)
        
        return jsonify({
            'success': True,
            'answer': answer,
            'audio_url': audio_url
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/start-recording', methods=['POST'])
def start_recording():
    """Начать запись голоса"""
    global recording_thread, voice_recorder
    
    try:
        # Запускаем запись в отдельном потоке
        def record():
            voice_recorder.start_recording()
        
        recording_thread = threading.Thread(target=record, daemon=True)
        recording_thread.start()
        
        return jsonify({'success': True, 'message': 'Recording started'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/stop-recording', methods=['GET'])
def stop_recording():
    """Остановить запись и распознать речь"""
    global voice_recorder
    
    try:
        voice_recorder.stop_recording()
        
        # Даем время для сохранения файла
        import time
        time.sleep(0.5)
        
        # Получаем последний записанный файл
        audio_file = voice_recorder.save_recording()
        
        if audio_file and os.path.exists(audio_file):
            # Распознаем речь
            text = voice_recorder.transcribe_audio(audio_file)
            
            # Удаляем временный файл
            os.remove(audio_file)
            
            return jsonify({
                'success': True,
                'text': text,
                'message': 'Speech recognized'
            })
        else:
            return jsonify({
                'success': False,
                'text': 'Не удалось записать аудио',
                'message': 'Recording failed'
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'text': 'Ошибка распознавания'})

@app.route('/api/clear-chat/<chat_id>', methods=['POST'])
def clear_chat(chat_id):
    """Очистить историю чата"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM messages WHERE chat_id = ?', (chat_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/chat/<chat_id>', methods=['DELETE'])
def delete_chat_route(chat_id):
    """Удалить чат"""
    try:
        db_manager.delete_chat(chat_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/generate-audio', methods=['POST'])
def generate_audio():
    """Сгенерировать аудио для текста"""
    data = request.json
    text = data.get('text')
    
    if not text:
        return jsonify({'success': False, 'error': 'No text provided'})
    
    try:
        audio_file = asyncio.run(generate_speech(text, VOICES['ru_female']))
        
        if audio_file:
            return jsonify({
                'success': True,
                'audio_url': f"/audio/{os.path.basename(audio_file)}"
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to generate audio'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/audio/<filename>')
def serve_audio(filename):
    """Отдача аудио файлов"""
    audio_path = os.path.join('audio', filename)
    if os.path.exists(audio_path):
        return send_file(audio_path, mimetype='audio/mpeg')
    return jsonify({'error': 'Audio file not found'}), 404

def main():
    """Запуск приложения"""
    global chat_manager
    
    if API_KEY == "ВАШ_API_КЛЮЧ_ЗДЕСЬ":
        print("⚠️  Вставьте ваш API ключ OpenRouter в переменную API_KEY")
        print("🔗 Получить ключ: https://openrouter.ai/keys")
        return
    
    # Проверка API ключа
    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {API_KEY}"}
        )
        
        if response.status_code == 200:
            print("✅ API ключ действителен")
            data = response.json()
            print(f"💰 Использовано: ${data['data']['usage']:.6f}")
            print(f"📊 Бесплатный тариф: {data['data']['is_free_tier']}")
        else:
            print(f"❌ Ошибка API ключа: {response.status_code}")
            return
    except Exception as e:
        print(f"❌ Ошибка проверки API: {e}")
        return
    
    # Инициализация менеджера чата
    chat_manager = ChatWithMemory(API_KEY, db_manager)
    
    # Запуск аудио потока
    audio_thread = threading.Thread(target=audio_player_worker, daemon=True)
    audio_thread.start()
    
    print(f"📊 Загружено чатов: {len(db_manager.get_all_chats())}")
    print(f"🔊 Голосов доступно: {len(VOICES)}")
    print(f"🎤 Голосовой ввод: доступен")
    print(f"🌐 Веб-интерфейс запущен по адресу: http://localhost:5000")
    print("=" * 50)
    
    # Запуск Flask
    
    import webbrowser
    webbrowser.open("http://localhost:5000")
    
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)

if __name__ == "__main__":
    main()