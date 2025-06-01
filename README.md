# HR AI Анализатор Резюме

Сервис для автоматического анализа резюме кандидатов с использованием AI (Gemini/DeepSeek).

## ✨ Возможности

- 📄 Анализ резюме в форматах PDF и DOCX
- 🔍 Оценка соответствия требованиям вакансии
- ❓ Генерация вопросов для собеседования и повторный анализ
- 🗃️ Импорт данных из Excel
- 📊 Визуализация результатов анализа
- 🔎 Поиск по базе кандидатов

## 🛠 Технологии

- **Backend**: Python 3.10+, Flask
- **База данных**: PostgreSQL
- **Очереди задач**: Celery + Redis
- **AI анализ**: Google Gemini API / DeepSeek API
- **Парсинг резюме**: PyMuPDF, python-docx

## 🚀 Быстрый старт

### Предварительные требования
- PostgreSQL 14+
- Redis 6+
- Python 3.8+

```bash
# 1. Клонировать репозиторий
git clone https://github.com/arckol/HR-AI-analysis.git
cd HR-AI-analysis

# 2. Создать и активировать окружение
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# 3. Установить зависимости
pip install -r requirements.txt
```

## ⚙️ Настройка

1. Настройте PostgreSQL:
```sql
CREATE DATABASE hr_analyzer;
CREATE USER hr_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE hr_analyzer TO hr_user;
```

2. Создайте файл `.env`:
```ini
# PostgreSQL
DB_USER='hr_user'
DB_PASSWORD='your_password'
DB_NAME='hr_analyzer'
DB_HOST='localhost'
DB_PORT='5432'

# API Keys
GEMINI_API_KEY='your_gemini_key'
DEEPSEEK_API_KEY='your_deepseek_key'

# Security
SECRET_KEY='your_secret_key'

# Redis
REDIS_URL='redis://localhost:6379/0'

# Email (optional)
MAIL_SERVER='smtp.example.com'
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME='your_email@example.com'
MAIL_PASSWORD='your_email_password'
```

## 🏃 Запуск

```bash
# Запустить Redis (в отдельном терминале)
redis-server

# Запустить Celery worker (в новом терминале)
celery -A app.celery_app worker -l info

# Запустить Flask приложение (в новом терминале)
flask run
python app.py
```

Приложение будет доступно по адресу: [http://localhost:5000](http://localhost:5000)

## 📂 Структура проекта

```
HR-AI-analysis/
├── uploads/               # Загруженные резюме
├── venv/                  # Виртуальное окружение
├── .env                   # Конфигурация
├── init_db.py             # Инициализация БД
├── import_excel_resumes.py # Импорт из Excel
├── Rezume.xlsx            # Пример данных
├── templates/             # Шаблоны
│   ├── analysis_details.html
│   ├── base.html
│   ├── edit_questions.html
│   ├── history.html
│   ├── index.html
│   ├── info_message.html
│   ├── login.html
│   ├── register.html
│   ├── results_async.html
│   ├── search_database.html
│   └── submit_additional_info.html
└── app.py                 # Основное приложение
```
