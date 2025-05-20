import flask
from flask import (Flask, render_template, request, redirect, url_for, current_app,
                   send_from_directory, jsonify, flash, session, get_flashed_messages, g)
import os
import psycopg2
import psycopg2.extras
from psycopg2 import OperationalError, InterfaceError, DatabaseError, Error as Psycopg2Error
import datetime

import math
import gspread
from google.oauth2.service_account import Credentials
import re
import uuid
from email.header import Header
import time
import re
from celery import Celery, Task
import fitz
import google.generativeai as genai
import docx
from werkzeug.utils import secure_filename
import markdown
import html
import requests
import json
import smtplib
import pathlib
from dateutil import parser
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from flask_bcrypt import Bcrypt
from google.api_core.exceptions import DeadlineExceeded, ServiceUnavailable, GoogleAPIError
from requests.exceptions import ConnectionError, Timeout, RequestException
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


app = Flask(__name__)
redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
app.config['CELERY_BROKER_URL'] = redis_url
app.config['CELERY_RESULT_BACKEND'] = redis_url

celery_app = Celery(app.name, broker=app.config['CELERY_BROKER_URL'], backend=app.config.get('CELERY_RESULT_BACKEND', app.config['CELERY_BROKER_URL']))
celery_app.conf.update(broker_connection_retry_on_startup=True, task_acks_late=True, worker_prefetch_multiplier=1)

app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev_secret_key_MUST_BE_CHANGED_123!@#XYZABC')
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['AI_SERVICE'] = os.environ.get('AI_SERVICE', 'gemini').lower()
app.config['GEMINI_API_KEY'] = os.environ.get("GEMINI_API_KEY")
app.config['DEEPSEEK_API_KEY'] = os.environ.get("DEEPSEEK_API_KEY")
app.config['GEMINI_MODEL_NAME'] = os.environ.get('GEMINI_MODEL_NAME', 'gemini-2.0-flash')
app.config['DEEPSEEK_MODEL_NAME'] = os.environ.get('DEEPSEEK_MODEL_NAME', 'deepseek-chat')

MAIL_SERVER = os.environ.get('MAIL_SERVER')
MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() in ('true', '1', 't')
MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'False').lower() in ('true', '1', 't')
MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
MAIL_DEFAULT_SENDER = MAIL_USERNAME

DB_USER = os.environ.get('DB_USER', 'hr_analyses')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'arckol')
DB_NAME = os.environ.get('DB_NAME', 'analyses_data')
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = os.environ.get('DB_PORT', '5432')

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
app.config['POSTGRES_URI'] = DATABASE_URL

def get_db_pg():
    if 'db_pg' not in g:
        try:
            g.db_pg = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        except psycopg2.OperationalError as e:
            print(f"Ошибка подключения к базе данных PostgreSQL: {e}")
            g.db_pg = None
    return g.db_pg

@app.teardown_appcontext
def close_db_pg(e=None):
    db = g.pop('db_pg', None)
    if db is not None:
        db.close()

bcrypt = Bcrypt(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Пожалуйста, войдите в систему для доступа."
login_manager.login_message_category = "info"


@app.context_processor
def inject_now():
    return {'now': datetime.datetime.utcnow()}

class User(UserMixin):
    def __init__(self, id, username): self.id = id; self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_pg()
    user = None
    if conn:
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username FROM users WHERE id = %s", (int(user_id),))
            user_data = cursor.fetchone()

            if user_data:
                 user = User(id=user_data[0], username=user_data[1])

        except psycopg2.Error as e:
            print(f"Ошибка БД при загрузке пользователя {user_id}: {e}")
            user = None
        finally:
            if cursor:
                cursor.close()
    else:
        print("Не удалось загрузить пользователя: нет соединения с БД.")
        user = None

    return user


def init_db():

    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

    conn_pg = None
    try:
        conn_pg = psycopg2.connect(DATABASE_URL)
        cursor_pg = conn_pg.cursor()

        cursor_pg.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        )
        """)

        cursor_pg.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
           id SERIAL PRIMARY KEY,
           batch_id TEXT NOT NULL,
           batch_timestamp TIMESTAMP NOT NULL,
           timestamp TIMESTAMP NOT NULL,
           original_filename TEXT NOT NULL,
           stored_filename TEXT UNIQUE,
           candidate_name TEXT,
           contacts_extracted TEXT,
           age INTEGER,
           gender TEXT,
           summary_conclusion TEXT,
           error_info TEXT,
           job_description TEXT,
           analysis_text TEXT,
           score INTEGER,
           user_id INTEGER NOT NULL,

           re_evaluation_score INTEGER,
           re_evaluation_text TEXT,
           re_evaluation_timestamp TIMESTAMP,
           re_evaluation_summary_conclusion TEXT,
           re_evaluation_error TEXT,
           interview_recommendations TEXT,
           salary_expectation TEXT,
           imported_resume_id INTEGER,

           FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
           FOREIGN KEY (imported_resume_id) REFERENCES imported_resumes (id) ON DELETE SET NULL
        )
        """)

        cursor_pg.execute("""
        CREATE TABLE IF NOT EXISTS requests_for_info (
           id SERIAL PRIMARY KEY,
           analysis_id INTEGER NOT NULL,
           token TEXT UNIQUE NOT NULL,
           status TEXT NOT NULL DEFAULT 'sent',
           created_at TIMESTAMP NOT NULL,
           completed_at TIMESTAMP,
           additional_info_text TEXT,
           user_id INTEGER NOT NULL,
           re_evaluation_error TEXT,

           FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE,
           FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """)

        cursor_pg.execute("""
        CREATE TABLE IF NOT EXISTS interview_questions (
           id SERIAL PRIMARY KEY,
           analysis_id INTEGER NOT NULL,
           question_text TEXT NOT NULL,
           source TEXT NOT NULL,
           created_at TIMESTAMP NOT NULL,
           user_id INTEGER,
           FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE,
           FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """)

        cursor_pg.execute("""
        CREATE TABLE IF NOT EXISTS imported_resumes (
            id SERIAL PRIMARY KEY,
            fio TEXT,
            vozrast INTEGER,
            pol TEXT,
            zhelaemaya_dolzhnost TEXT,
            klyuchevye_navyki TEXT,
            opyt_raboty TEXT,
            mesta_raboty TEXT,
            obrazovanie TEXT,
            telefon TEXT,
            email TEXT,
            gorod TEXT,
            zarplatnye_ozhidaniya TEXT,
            relokatsiya_komandirovki TEXT,
            format_raboty TEXT,
            yazyki TEXT,
            imported_at TIMESTAMP,
            original_row_number INTEGER
        )
        """)

        def pg_add_column_if_not_exists(cursor, table, column_name, column_type):
            try:
                cursor.execute(f"""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                      AND column_name = %s;
                """, (table, column_name))
                column_exists = cursor.fetchone()
                if not column_exists:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}")
            except Exception as alter_e:
                 print(f"Warning: Не удалось добавить колонку {column_name} в таблицу {table}: {alter_e}")


        cursor_pg.execute("""
        CREATE INDEX IF NOT EXISTS idx_analyses_batch_id ON analyses (batch_id);
        """)
        cursor_pg.execute("""
        CREATE INDEX IF NOT EXISTS idx_analyses_user_id ON analyses (user_id);
        """)
        cursor_pg.execute("""
        CREATE INDEX IF NOT EXISTS idx_analyses_imported_resume_id ON analyses (imported_resume_id);
        """)
        cursor_pg.execute("""
        CREATE INDEX IF NOT EXISTS idx_requests_for_info_analysis_id ON requests_for_info (analysis_id);
        """)
        cursor_pg.execute("""
        CREATE INDEX IF NOT EXISTS idx_requests_for_info_token ON requests_for_info (token);
        """)
        cursor_pg.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
        """)


        conn_pg.commit()

    except psycopg2.OperationalError as e:
        print(f"Ошибка подключения к БД PostgreSQL при инициализации: {e}")
        print("Убедитесь, что PostgreSQL сервер запущен и доступен, а настройки подключения верны.")
    except psycopg2.Error as e:
        print(f"Ошибка БД PostgreSQL при инициализации: {e}")
        if conn_pg:
            conn_pg.rollback()
    except Exception as e:
        print(f"Неизвестная ошибка при инициализации основной БД: {e}")
        if conn_pg:
            conn_pg.rollback()
    finally:
        if conn_pg:
            cursor_pg.close()
            conn_pg.close()

def parse_pdf_pymupdf(filepath):
    """Извлекает текст из PDF файла с использованием PyMuPDF."""
    text = ""
    try:
        doc = fitz.open(filepath)
        for page in doc:
            text += page.get_text()
        doc.close()
    except Exception as e:
        print(f"[ERROR] Ошибка парсинга PDF файла {filepath}: {e}")
        # В зависимости от требований, можно вернуть пустую строку или вызвать исключение
        raise e # Перевыбрасываем ошибку, чтобы задача Celery завершилась с ошибкой
    return text

def parse_docx(filepath):
    """Извлекает текст из DOCX файла."""
    text = ""
    try:
        doc = docx.Document(filepath)
        for para in doc.paragraphs:
            text += para.text + "\n"
    except Exception as e:
        print(f"[ERROR] Ошибка парсинга DOCX файла {filepath}: {e}")
        raise e
    return text

def get_file_text(filepath, original_filename):
    """Определяет тип файла и извлекает текст."""
    if original_filename.lower().endswith('.pdf'):
        return parse_pdf_pymupdf(filepath)
    elif original_filename.lower().endswith('.docx'):
        return parse_docx(filepath)
    else:
        raise ValueError(f"Неподдерживаемый тип файла: {original_filename}")

analysis_instructions = """
Твоя роль: Ты — высококвалифицированный и внимательный HR-аналитик с многолетним успешным опытом в подборе персонала для различных отраслей. Твоя экспертиза позволяет точно оценивать соответствие кандидата требованиям вакансии.

Твоя задача: Провести профессиональный и объективный анализ анонимизированного резюме кандидата на соответствие требованиям предоставленного описания вакансии.

Ключевые инструкции и ограничения:
1.  Анализируй ИСКЛЮЧИТЕЛЬНО содержание анонимизированного резюме и текста описания вакансии.
2.  Строго запрещено делать любые предположения, догадки или пытаться вывести личные данные кандидата (такие как ФИО, возраст, пол, контакты, место жительства и т.п.), даже если в тексте присутствуют плейсхолдеры или кажутся очевидными намеки. Работай только с профессиональными качествами, опытом и навыками, описанными в анонимизированном тексте.
3.  Не придумывай информацию, которой нет ни в вакансии, ни в резюме.
4.  Строго следуй запрашиваемому формату и структуре ответа, используй Markdown.
5.  Ответ должен быть предоставлен ИСКЛЮЧИТЕЛЬНО на русском языке.

Выполняй следующие шаги анализа строго по порядку:

1.  **Краткая сводка по кандидату (для HR):**
    Составь краткое резюме ключевых профессиональных навыков, опыта работы и квалификаций кандидата, которые наиболее релевантны данной конкретной вакансии, основываясь только на анонимизированном тексте.
    (Объем: 2-4 содержательных предложения).

2.  **Зарплатные ожидания (если указаны):**
    Просмотри анонимизированное резюме на наличие информации о зарплатных ожиданиях кандидата. Если информация найдена, извлеки ее максимально точно. Если зарплатные ожидания не указаны или неочевидны, пропусти этот пункт и ничего не выводи.
    **Представь эту информацию СТРОГО начиная с ТОЧНО такого маркера: `### ЗАРПЛАТНЫЕ ОЖИДАНИЯ:` (с тремя хештегами, пробелом и двоеточием).**
    Укажи извлеченные ожидания сразу после маркера.

3.  **Детальный анализ соответствия требованиям вакансии:**
    Проведи построчное или поэтапное сравнение требований вакансии с информацией, представленной в резюме.
    а) **Требования вакансии, которым кандидат СООТВЕТСТВУЕТ:** Перечисли основные навыки, опыт, образование, сертификаты или другие квалификации, явно упомянутые в анонимизированном резюме и соответствующие требованиям вакансии. Предоставь краткое подтверждение из текста резюме (например, "опыт работы с SQL", "упомянуто знание Python").
    б) **Требования вакансии, которым кандидат НЕ соответствует или по которым ОТСУТСТВУЕТ информация:** Перечисли ключевые требования вакансии, которые явно не упомянуты в анонимизированном резюме, или по которым квалификации кандидата недостаточны/нерелевантны согласно тексту резюме.

    Используй четкие маркированные списки (`* ` или `- `) для обоих подпунктов (а и б).

4.  **Общий вывод и рекомендация:**
    На основе анализа (пункт 3) предоставь следующий вывод:
    -   **Краткий итог для отображения (1-2 предложения):** **Начни этот пункт с ТОЧНО такого маркера: `ИТОГ_КРАТКО:` (с двоеточием и пробелом).** Сформулируй самый главный вывод о пригодности кандидата для данной вакансии (в объеме 1-2 предложений).
    -   **Категория соответствия:** Выбери ОДНУ соответствующую категорию из списка: Высокое соответствие, Среднее соответствие, Низкое соответствие, Не соответствует.

5.  **Числовая Оценка (от 1 до 10) по детализированной шкале соответствия:**
    Присвой финальную числовую оценку соответствия кандидата вакансии, используя целое число от 1 до 10. **СТРОГО руководствуйся приведенной ниже детализированной шкалой соответствия**

    **Детализированная Шкала Оценки Соответствия Резюме Вакансии (от 1 до 10 баллов):**
    * 1: Полное отсутствие соответствия основным требованиям вакансии. Резюме не содержит релевантного опыта, навыков или образования.
    * 2: Крайне низкое соответствие. Присутствуют единичные, очень поверхностные совпадения с требованиями, но в целом кандидат совершенно не подходит.
    * 3: Минимальное соответствие. Есть очень ограниченные совпадения по некоторым пунктам, но отсутствуют ключевой опыт и необходимые навыки для выполнения работы.
    * 4: Небольшое минимальное соответствие. Кандидат обладает некоторыми базовыми знаниями или опытом, связанными с вакансией, но этого явно недостаточно. Есть значительные пробелы.
    * 5: Среднее соответствие. Есть базовое совпадение по нескольким ключевым пунктам вакансии, но при этом присутствуют существенные пробелы в опыте или навыках. Требуются серьезные уточнения.
    * 6: Уверенное среднее соответствие. Кандидат соответствует основным требованиям по большинству базовых пунктов, но есть заметные области, где опыт или навыки недостаточны или требуют прояснения.
    * 7: Хорошее соответствие. Кандидат обладает большинством необходимых навыков и релевантного опыта. Есть небольшие расхождения с идеальным профилем или пункты, которые желательно уточнить на интервью.
    * 8: Очень хорошее соответствие. Кандидат обладает почти всеми ключевыми навыками и значительным релевантным опытом. Требуется лишь минимальное уточнение деталей или оценка соответствия корпоративной культуре.
    * 9: Высокое соответствие. Кандидат полностью соответствует всем ключевым требованиям вакансии, обладает сильными сторонами и релевантным опытом, который может быть очень ценным.
    * 10: Идеальное соответствие. Кандидат не только полностью соответствует всем требованиям вакансии, но и превосходит ожидания по ряду пунктов. Обладает уникальным или очень ценным опытом/навыками, которые делают его идеальным кандидатом.

6. Уточняющие вопросы К КАНДИДАТУ (для интервью):
        На основе анализа резюме кандидата и требований вакансии, сгенерируй список из 5-10 вопросов, **которые рекрутер может задать кандидату на интервью**. Эти вопросы должны быть направлены на **углубление информации из резюме, выявление неочевидных навыков, оценку опыта решения конкретных задач, понимание мотивации кандидата и проверку соответствия ключевым требованиям вакансии**. Избегай вопросов о самой компании или вакансии, если они не касаются того, как опыт кандидата соотносится с этими аспектами.
        **Представь этот список вопросов СТРОГО после раздела "Общий вывод и рекомендация" (Пункта 4). Начни список с ТОЧНО такого маркера: `### УТОЧНЯЮЩИЕ ВОПРОСЫ:` (с тремя хештегами, пробелом и двоеточием).**
        Каждый вопрос в списке начинай с новой строки с числа и точки  (например, `1. Расскажите подробнее о Вашем опыте...?`).

7.  **Рекомендации по интервью:**
На основе всего проведенного анализа предоставленных данных кандидата относительно вакансии, дай рекомендации для рекрутера по проведению интервью. На что стоит обратить особое внимание? Какие аспекты стоит проверить глубже?
**Представь этот раздел СТРОГО после раздела "Уточняющие вопросы к резюме". Начни его с ТОЧНО такого маркера: `### РЕКОМЕНДАЦИИ ПО ИНТЕРВЬЮ:` (с тремя хештегами, пробелом и двоеточием).**
Предоставь рекомендации в формате четкого маркированного списка или кратких, связных абзацев.

Структура финального ответа:
Твой полный ответ должен быть в Markdown формате и строго включать в себя, в указанном порядке:
-   Заголовок Пункта 1: `## Краткая сводка по кандидату` и содержание пункта 1.
-   **Раздел зарплатных ожиданий (с маркером `### ЗАРПЛАТНЫЕ ОЖИДАНИЯ:`)**
-   Заголовок Пункта 3: `## Детальный анализ соответствия требованиям вакансии`.
-   Подзаголовок `Требования, которым кандидат соответствует:` и маркированный список для 3а.
-   Подзаголовок `Требования, которым кандидат НЕ соответствует или по которым ОТСУТСТВУЕТ информация:` и маркированный список для 3б.
-   Заголовок Пункта 4: `## Общий вывод и рекомендация`.
-   Содержание пункта 4: Краткий итог (с маркером `ИТОГ_КРАТКО:`), затем Категория соответствия.
-   **Раздел уточняющих вопросов (с маркером `### УТОЧНЯЮЩИЕ ВОПРОСЫ:`)**
-   **Раздел рекомендаций по интервью (с маркером `### РЕКОМЕНДАЦИИ ПО ИНТЕРВЬЮ:`)**
-   В самом конце твоего ответа, после всего перечисленного выше, должна идти **ТОЛЬКО ОДНА** строка с финальной числовой оценкой в **ТОЧНО таком** формате:
    `ИТОГОВАЯ ОЦЕНКА: [ЧИСЛО ОТ 1 ДО 10]`
---
"""

def extract_pii_and_anonymize(text):
    """
    Извлекает ФИО, возраст, пол, контакты (email/телефон) из текста ЛОКАЛЬНО.
    Анонимизирует текст, заменяя найденные ПДн плейсхолдерами.
    Возвращает словарь с анонимизированным текстом и извлеченными данными.
    Включает ключи 'extracted_name', 'extracted_contacts', 'extracted_age', 'extracted_gender'.
    """

    # Инициализация результатов
    anonymized_text = str(text) if text else ""
    extracted_name = {'surname': None, 'name': None, 'patronymic': None} # Имя как словарь
    extracted_age = None # Переменная для возраста
    extracted_gender = None # Переменная для пола
    extracted_contacts = [] # Используем extracted_contacts как список для контактов


    if not text:
        # Возвращаем словарь с ожидаемыми ключами, даже если текст пустой
        return {
            'anonymized_text': "",
            'extracted_name': {'surname': None, 'name': None, 'patronymic': None},
            'extracted_contacts': None, # Контакты возвращаем как строку или None
            'extracted_age': None,
            'extracted_gender': None # Включаем в словарь результатов
        }
        #  Шаг 1: Извлечение Контактов (Regex) 
    
    print("Шаг 1: Поиск контактов (Email, Телефон)...")
    # Email
    email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'
    emails = list(set(re.findall(email_pattern, anonymized_text)))
    if emails:
        print(f"  Найдены email: {emails}")
        extracted_contacts.extend(emails)
        anonymized_text = re.sub(email_pattern, '[EMAIL]', anonymized_text, flags=re.IGNORECASE)

    # Телефоны
    phone_pattern = r'(\+?\d{1,3}[-\s\(\)]?)?(\(?\d{3,5}\)?[\s\-\)\.]?)([\d][\d\s\-\.]{4,9}\d)\b'
    potential_phones = re.findall(phone_pattern, anonymized_text)

    if potential_phones:
        # print(f"  Потенциальные телефоны (сырые): {potential_phones}") # Можно закомментировать после отладки
        for phone_tuple in potential_phones:
             phone_str_raw = "".join(phone_tuple)
             digits_only = re.sub(r'\D', '', phone_str_raw)
             if 7 <= len(digits_only) <= 15:
                  extracted_contacts.append(phone_str_raw.strip())
                  # print(f"  Найден и добавлен телефон: {phone_str_raw.strip()}") # Можно закомментировать после отладки

        anonymized_text = re.sub(phone_pattern, '[ТЕЛЕФОН]', anonymized_text)

    contacts_str = "; ".join(list(set(extracted_contacts))) if extracted_contacts else None

    #  Шаг 2: Извлечение ФИО (Regex) 
    print("Шаг 2: Поиск ФИО...")

    name_pattern = r'(?:^|\b(?:ФИО|Полное имя)\s*[:\-]?\s*)?([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)(?:\s+([А-ЯЁ][а-яё]+))?'
    lines = text.split('\n')
    found_name_match = None
    for line in lines[:15]:
        match = re.search(name_pattern, line.strip())
        if match:
            # Проверяем, что захвачено как минимум 2 части (Имя + Фамилия) и что они похожи на ФИО.
            # match.groups() возвращает кортеж всех захваченных групп (1, 2, 3). Фильтруем None.
            potential_name_parts = [p for p in match.groups() if p]
            # Дополнительная проверка: убедиться, что найдено хотя бы 2 части и они начинаются с заглавной буквы,
            # и не являются слишком короткими (одна буква).
            if len(potential_name_parts) >= 2 and all(len(p) > 1 and p[0].isupper() for p in potential_name_parts):
                 # Можно добавить более строгую проверку на стоп-слова или другие ложные срабатывания, если возникают.
                 found_name_match = match
                 break # Нашли первое хорошее совпадение, можно остановиться

    if found_name_match: # Этот блок выполнится, если найдено совпадение
        # Извлекаем группы по индексу, зная структуру паттерна. group(3) (отчество) может быть None.
        # Группы 1, 2, 3 соответствуют ([А-ЯЁ][а-яё]+), ([А-ЯЁ][а-яё]+), ([А-ЯЁ][а-яё]+)?
        surname = found_name_match.group(1)
        name = found_name_match.group(2)
        patronymic = found_name_match.group(3) # Может быть None

        extracted_name = {
            'surname': surname,
            'name': name,
            'patronymic': patronymic
        }
        print(f"  Извлечено ФИО: {extracted_name}") # Этот print теперь будет вызываться при успешном извлечении

        # Анонимизация - заменяем каждую часть имени отдельно, если она найдена
        name_parts_to_anonymize = sorted([p for p in [surname, name, patronymic] if p], key=len, reverse=True)

        for part in name_parts_to_anonymize:
             anonymized_text = re.sub(rf'\b{re.escape(part)}\b', '[ЧАСТЬ_ИМЕНИ]', anonymized_text, flags=re.IGNORECASE)

    else:
        print("  ФИО не найдено по паттернам.") # Если ФИО не найдено в первых 15 строках

    #  Шаг 3: Извлечение Возраста (Regex) 
    print("Шаг 3: Поиск возраста...")
    age_patterns = [
        r'\b(\d{1,2})\s*(?:лет|год(?:а|у)?)\b',
        r'(?:возраст|возраста)\s*[:\-]?\s*(\d{1,2})\b',
        r'\((\d{1,2})\s*(?:лет|год(?:а|у)?)\)'
    ]

    found_age_text = None
    for pattern in age_patterns:
        try:
            match_obj = re.search(pattern, anonymized_text, re.IGNORECASE)
            if match_obj:
                potential_age_str = match_obj.group(1)
                try:
                    potential_age = int(potential_age_str)
                    if 16 <= potential_age <= 90: # Валидация возраста
                        extracted_age = potential_age
                        found_age_text = match_obj.group(0)
                        print(f"  Найден возраст: {extracted_age} (текст: '{found_age_text}')")
                        anonymized_text = anonymized_text.replace(found_age_text, '[ВОЗРАСТ]', 1)
                        break
                except ValueError:
                    pass # Число найдено, но не парсится как int или вне диапазона
        except re.error as e:
            print(f"  Предупреждение: Ошибка regex при поиске возраста (паттерн: {pattern}): {e}")

    if extracted_age is None:
        print("  Возраст не найден по паттернам.")


    #  Шаг 4: Анонимизация Даты рождения (Regex) 
    print("Шаг 4: Анонимизация даты рождения...")
    birth_date_patterns_for_anonymization = [
        r'(?:родилась|родился)\s+день\s+\d{1,2}\s+месяца?\s+\d{4}\s+года?',
        r'(?:родилась|родился)\s+\d{1,2}\s+[^,\s]+\s+\d{4}',
        r'\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4}',
        r'\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(?:19|20)\d{2}\s+года?\b',
        r'\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(?:19|20)\d{2}\b'
    ]

    for pattern in birth_date_patterns_for_anonymization:
         try:
             anonymized_text = re.sub(pattern, '[ДАТА РОЖДЕНИЯ]', anonymized_text, flags=re.IGNORECASE)
         except re.error as e:
             print(f"  Предупреждение: Ошибка regex при анонимизации даты рождения (паттерн: {pattern}): {e}")


    #  Шаг 5: Анонимизация фразы "Резюме обновлено" 
    print("Шаг 5: Анонимизация даты обновления...")
    update_phrase_pattern = r'Резюме обновлено\s+\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\s+в\s+\d{2}:\d{2}'
    try:
        anonymized_text = re.sub(update_phrase_pattern, '[ДАТА ОБНОВЛЕНИЯ РЕЗЮМЕ]', anonymized_text, flags=re.IGNORECASE)
    except re.error as e:
        print(f"  Предупреждение: Ошибка regex при анонимизации даты обновления: {e}")

    #  Шаг 6: Извлечение Пола (Regex)  
    print("Шаг 6: Поиск пола...")
    # Паттерны и группы для извлечения пола
    gender_patterns_with_groups = [
        (r'^(Мужчина|Женщина)[,\s]', 1), # Ищем в начале текста "Мужчина," или "Женщина,"
        (r'\b(Мужчина|Женщина)\b', 1),    # Ищем отдельно стоящие слова "Мужчина", "Женщина"
        (r'\b(?:пол|gender)\s*[:\-]?\s*(муж(?:ской)?|муж\.?|м|male|жен(?:ский)?|жен\.?|ж|female)\b', 1) # Ищем "пол: Мужской", "gender: Female", "пол: М"
    ]

    found_gender_text = None
    for pattern, group_index in gender_patterns_with_groups:
        try:
            match_obj = re.search(pattern, anonymized_text, re.IGNORECASE)
            if match_obj:
                # Получаем строку, содержащую пол, из нужной группы
                gender_str_raw = match_obj.group(group_index)
                gender_str_lower = gender_str_raw.lower()
                found_gender_text = match_obj.group(0) # Полный найденный текст

                if 'муж' in gender_str_lower or gender_str_lower == 'м' or gender_str_lower == 'male':
                    extracted_gender = 'Мужской'
                elif 'жен' in gender_str_lower or gender_str_lower == 'ж' or gender_str_lower == 'female':
                     extracted_gender = 'Женский'

                if extracted_gender:
                    print(f"  Найден пол: {extracted_gender} (текст: '{found_gender_text}')")
                    # Анонимизируем найденное упоминание пола в тексте
                    anonymized_text = anonymized_text.replace(found_gender_text, '[ПОЛ]', 1)
                    break # Пол найден, прекращаем поиск по паттернам
        except re.error as e:
            print(f"  Предупреждение: Ошибка regex при поиске пола (паттерн: {pattern}): {e}")

    if extracted_gender is None:
        print("  Пол не найден по паттернам.")

    #  Финальная очистка и сборка результата 
    anonymized_text = re.sub(r'\s+', ' ', anonymized_text).strip()
    contacts_str = "; ".join(list(set(extracted_contacts))) if extracted_contacts else None

    print("-" * 20)
    print("Локальное извлечение ПДн завершено.")
    print(f"  Извлеченное Имя: {extracted_name}") # Это словарь
    print(f"  Извлеченный Возраст: {extracted_age}") # Число или None
    print(f"  Извлеченный Пол: {extracted_gender}") # Теперь может быть 'Мужской'/'Женский' или None
    print(f"  Извлеченные Контакты: {contacts_str}")
    print("-" * 20)
    print(anonymized_text)
    print("-" * 20)

    return {
        'anonymized_text': anonymized_text,
        'extracted_name': extracted_name,       # Имя как словарь
        'extracted_contacts': contacts_str,     # Контакты как строка (или None)
        'extracted_age': extracted_age,         # Возраст как число (или None)
        'extracted_gender': extracted_gender    # Пол как строка ('Мужской'/'Женский' или None)
    }

#  Вспомогательная функция экранирования HTML 
def escapeHTML(text): return html.escape(str(text)) if text else ""

def call_ai_service(ai_service, model_name, api_key, complete_prompt_string):
    """Вызывает AI с ПОЛНЫМ текстом промта."""
    print(f"--- DEBUG [AI Service Call]: Function called with ai_service='{ai_service}', model='{model_name}'")

    #  ЛОГИКА ДЛЯ GEMINI 
    if ai_service == 'gemini':
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)

            prompt = complete_prompt_string
            response = model.generate_content(prompt)

            if not response.parts:
                print(f"Предупреждение: Получен пустой ответ от Gemini для промта. Попытка с temperature=1.0")

            if response.parts:
                print("--- НАЧАЛО ПОЛНОГО ОТВЕТА ОТ AI ---")
                print(response.text)
                print("--- КОНЕЦ ПОЛНОГО ОТВЕТА ОТ AI ---")
                return response.text

            else:
                error_reason = response.prompt_feedback or 'Нет данных в ответе AI'
                print(f"ОШИБКА AI ({ai_service}): {error_reason}")
                return f"ОШИБКА AI ({ai_service}): {error_reason}"

        except (DeadlineExceeded, ServiceUnavailable, GoogleAPIError) as e:
            print(f"Ошибка Google API: {e}")
            raise
        except Exception as e:
            print(f"Ошибка вызова {ai_service} API: {e}")
            return f"ОШИБКА AI ({ai_service}): ({type(e).__name__})."

    #  ЛОГИКА ДЛЯ DEEPSEEK 
    elif ai_service == 'deepseek':
        try:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
            user_prompt = complete_prompt_string
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": user_prompt}],
                "temperature": 0.7,
                "max_tokens": 2048,
                "stream": False
            }

            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            result_json = response.json()

            if result_json.get("choices"):
                content = result_json["choices"][0].get("message", {}).get("content")
                print("--- НАЧАЛО ПОЛНОГО ОТВЕТА ОТ AI ---")
                print(content)
                print("--- КОНЕЦ ПОЛНОГО ОТВЕТА ОТ AI ---")
                return content.strip() if content else f"ОШИБКА AI ({ai_service}): Пустой ответ."
            else:
                print(f"ОШИБКА AI ({ai_service}): Некорректный формат ответа.")
                print(f"Ответ API: {result_json}")
                return f"ОШИБКА AI ({ai_service}): Некорректный формат ответа."

        except (Timeout, ConnectionError, RequestException) as e:
            print(f"Ошибка сети {ai_service}: {e}")
            raise
        except Exception as e:
            print(f"Ошибка вызова {ai_service} API: {e}")
            return f"ОШИБКА AI ({ai_service}): ({type(e).__name__})."

    else:
        return f"ОШИБКА КОНФИГУРАЦИИ: Неизвестный AI сервис '{ai_service}'."

celery_retry_exceptions = (DeadlineExceeded, ServiceUnavailable, ConnectionError, GoogleAPIError, Timeout, RequestException, OperationalError, InterfaceError, DatabaseError)
@celery_app.task(bind=True, autoretry_for=celery_retry_exceptions, retry_backoff=True, max_retries=3, task_acks_late=True)
def process_resume_task(self, filepath, job_description, ai_service, gemini_api_key, deepseek_api_key, gemini_model, deepseek_model, batch_id, batch_timestamp, original_filename, stored_filename, user_id, imported_resume_id=None, text_data=None):
    """
    Задача Celery для анализа резюме с помощью AI.
    Может анализировать из файла (передается filepath) или из текста (передается text_data).
    Сохраняет результаты в БД analyses.
    При наличии imported_resume_id, использует данные из imported_resumes для заполнения
    основных полей кандидата (ФИО, контакты, возраст, пол) в записи analyses.
    Адаптировано для PostgreSQL.
    """
    task_id = self.request.id
    # Определяем источник анализа для логов и отображения. original_filename для импорта будет установлен ниже.
    source_info = f"File: {original_filename} (Stored: {stored_filename})" if filepath else f"Imported ID: {imported_resume_id}" if imported_resume_id is not None else "Unknown Source"
    print(f"[Task:{task_id}] Старт анализа: {source_info} (User:{user_id}) Попытка: {self.request.retries + 1}")

    resume_text_original = None # Исходный текст до анонимизации (из файла или сформированный из imported_data)
    analysis_result_raw = None # Сырой ответ от AI
    score = None # Оценка от AI
    db_id = None # ID записи в таблице analyses после сохранения
    analysis_error = None # Текст ошибки анализа/парсинга/вызова AI (накапливается)

    # Переменные для данных PII, которые будут сохранены в БД analyses.
    # Эти переменные заполняются в блоках ниже, в зависимости от источника данных.
    candidate_name_for_db = None
    contacts_extracted_for_db = None
    age_for_db = None
    gender_for_db = None

    anonymized_text_for_ai = None # Текст после анонимизации, который отправляется в AI

    local_summary_conclusion = None # Краткий итог
    extracted_questions = [] # Список вопросов
    extracted_interview_recommendations = None # Рекомендации по интервью
    extracted_salary_expectation = None # Зарплатные ожидания
    analysis_text_clean = None # Основной текст анализа от AI (без извлеченных частей)

    conn = None # Инициализируем основное соединение как None для основного finally блока
    read_conn = None # Инициализируем отдельное соединение для чтения как None

    try:

        #  1. Получение исходных данных кандидата и текста для AI 
        if imported_resume_id is not None:
             #  АНАЛИЗ ИМПОРТИРОВАННОГО КАНДИДАТА ИЗ БД 
             print(f"[Task:{task_id}] Источник: Импортированный кандидат ID: {imported_resume_id}. Чтение данных из imported_resumes...")
             try:
                  # Используем отдельное соединение для чтения из imported_resumes
                  read_conn = psycopg2.connect(DATABASE_URL)
                  # Используем DictCursor для удобного доступа по имени колонки
                  cursor_dict = read_conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                  # Используем %s для плейсхолдеров в psycopg2
                  cursor_dict.execute("SELECT * FROM imported_resumes WHERE id = %s", (imported_resume_id,))
                  imported_data_dict = cursor_dict.fetchone()
                  cursor_dict.close()
                  # Нет необходимости закрывать read_conn здесь, это сделает finally

                  if imported_data_dict:
                       # Заполняем PII переменные ДЛЯ ЗАПИСИ В БД analyses НАПРЯМУЮ из imported_data_dict
                       candidate_name_for_db = imported_data_dict['fio'] if imported_data_dict['fio'] else None
                       age_for_db = imported_data_dict['vozrast'] if imported_data_dict['vozrast'] is not None else None # Возраст может быть 0 или None
                       gender_for_db = imported_data_dict['pol'] if imported_data_dict['pol'] else None

                       # Объединяем телефон и email для contacts_extracted_for_db
                       contacts_list = []
                       if imported_data_dict['telefon']: contacts_list.append(imported_data_dict['telefon'])
                       if imported_data_dict['email']: contacts_list.append(imported_data_dict['email'])
                       contacts_extracted_for_db = "; ".join(contacts_list) if contacts_list else None

                       # Устанавливаем original_filename и stored_filename для записи в analyses
                       original_filename = f"Импорт ID {imported_resume_id}" # Имя для отображения в истории
                       stored_filename = None # Для импортированных нет хранимого файла на диске

                       print(f"[Task:{task_id}] PII из Imported Data (для БД): Имя='{candidate_name_for_db}', Возраст={age_for_db}, Пол='{gender_for_db}', Контакты='{contacts_extracted_for_db}'")

                       #  Формируем текст для отправки в AI из всех полей imported_data_dict 
                       # Этот текст будет анонимизирован и отправлен в AI для анализа
                       print(f"[Task:{task_id}] Формирую текст для AI из imported_data_dict...")
                       formatted_text_parts = []
                       # Добавляем поля только если они не пустые
                       if imported_data_dict['fio']: formatted_text_parts.append(f"ФИО: {imported_data_dict['fio']}")
                       if imported_data_dict['vozrast'] is not None: formatted_text_parts.append(f"Возраст: {imported_data_dict['vozrast']}")
                       if imported_data_dict['pol']: formatted_text_parts.append(f"Пол: {imported_data_dict['pol']}")
                       if imported_data_dict['zhelaemaya_dolzhnost']: formatted_text_parts.append(f"Желаемая должность: {imported_data_dict['zhelaemaya_dolzhnost']}")
                       if imported_data_dict['klyuchevye_navyki']: formatted_text_parts.append(f"Ключевые навыки: {imported_data_dict['klyuchevye_navyki']}")
                       if imported_data_dict['opyt_raboty']: formatted_text_parts.append(f"Опыт работы: {imported_data_dict['opyt_raboty']}")
                       if imported_data_dict['mesta_raboty']: formatted_text_parts.append(f"Места работы: {imported_data_dict['mesta_raboty']}")
                       if imported_data_dict['obrazovanie']: formatted_text_parts.append(f"Образование: {imported_data_dict['obrazovanie']}")
                       if imported_data_dict['telefon']: formatted_text_parts.append(f"Телефон: {imported_data_dict['telefon']}")
                       if imported_data_dict['email']: formatted_text_parts.append(f"Email: {imported_data_dict['email']}")
                       if imported_data_dict['gorod']: formatted_text_parts.append(f"Город: {imported_data_dict['gorod']}")
                       if imported_data_dict['zarplatnye_ozhidaniya']: formatted_text_parts.append(f"Зарплатные ожидания: {imported_data_dict['zarplatnye_ozhidaniya']}")
                       if imported_data_dict['relokatsiya_komandirovki']: formatted_text_parts.append(f"Релокация/Командировки: {imported_data_dict['relokatsiya_komandirovki']}")
                       if imported_data_dict['format_raboty']: formatted_text_parts.append(f"Формат работы: {imported_data_dict['format_raboty']}")
                       if imported_data_dict['yazyki']: formatted_text_parts.append(f"Языки: {imported_data_dict['yazyki']}")

                       resume_text_original = "\n".join(formatted_text_parts)
                       if not resume_text_original:
                           analysis_error = "ОШИБКА ОБРАБОТКИ: Не удалось сформировать текст для анализа из данных импортированного кандидата."
                           print(f"[Task:{task_id}] {analysis_error}")


                       # Анонимизируем сформированный текст ТОЛЬКО для AI
                       if resume_text_original:
                           print(f"[Task:{task_id}] Анонимизация текста для AI (импортированный кандидат)...")
                           # Вызываем анонимизатор, но его результаты для PII (имя, контакты, возраст, пол)
                           pii_data_for_anon = extract_pii_and_anonymize(resume_text_original)
                           anonymized_text_for_ai = pii_data_for_anon.get('anonymized_text')
                           if not anonymized_text_for_ai:
                                print(f"[Task:{task_id}] Предупреждение: Анонимизатор вернул пустой текст для AI. Использую исходный или заглушку.")
                                anonymized_text_for_ai = resume_text_original or "Нет текста резюме для анализа AI."
                       else:
                           anonymized_text_for_ai = "Нет текста резюме для анализа AI." # Заглушка для AI


                  else:
                       analysis_error = f"ОШИБКА БД: Кандидат с ID {imported_resume_id} не найден в таблице imported_resumes."
                       print(f"[Task:{task_id}] {analysis_error}")


             except Exception as db_read_e:
                  analysis_error = f"ОШИБКА БД: Не удалось прочитать данные кандидата ID {imported_resume_id} из imported_resumes: {type(db_read_e).__name__}: {db_read_e}"
                  print(f"[Task:{task_id}] {analysis_error}")
                  if read_conn is not None:
                      try: read_conn.close()
                      except Exception as ce: print(f"[Task:{task_id}] Ошибка закрытия read_conn после ошибки чтения: {ce}")


        elif filepath:
            #  АНАЛИЗ ИЗ ФАЙЛА 
            print(f"[Task:{task_id}] Источник: Файл {original_filename}. Парсинг файла {filepath}...")
            if not stored_filename or not os.path.exists(filepath):
                analysis_error = "ОШИБКА ФАЙЛА: Файл не найден или имя файла отсутствует."
                stored_filename = None # Убедимся, что stored_filename NULL
            elif stored_filename.lower().endswith('.pdf'):
                resume_text_original = parse_pdf_pymupdf(filepath)
            elif stored_filename.lower().endswith('.docx'):
                resume_text_original = parse_docx(filepath)
            else:
                analysis_error = "ОШИБКА ФАЙЛА: Неподдерживаемое расширение файла."

            if not resume_text_original and not analysis_error and stored_filename:
                analysis_error = "ОШИБКА ФАЙЛА: Не удалось извлечь текст из файла."
                print(f"[Task:{task_id}] {analysis_error}")

            #  Извлечение PII и Анонимизация для анализа из файла 
            # При анализе файла, PII переменные для БД ЗАПОЛНЯЮТСЯ ИЗ РЕЗУЛЬТАТОВ АНОНИМИЗАТОРА.
            if resume_text_original and not analysis_error:
                print(f"[Task:{task_id}] Локальное извлечение ПДн и анонимизация (анализ файла)...")
                pii_data = extract_pii_and_anonymize(resume_text_original)
                anonymized_text_for_ai = pii_data.get('anonymized_text')

                # PII ПЕРЕМЕННЫЕ ДЛЯ БД БЕРУТСЯ ИЗ РЕЗУЛЬТАТОВ АНОНИМИЗАТОРА
                local_candidate_name_dict = pii_data.get('extracted_name') # Извлекаем словарь имени
                contacts_extracted_for_db = pii_data.get('extracted_contacts')
                age_for_db = pii_data.get('extracted_age')
                gender_for_db = pii_data.get('extracted_gender')

                # Формируем полное имя кандидата из словаря, если он был извлечен
                full_candidate_name_string = None
                if isinstance(local_candidate_name_dict, dict):
                    surname = local_candidate_name_dict.get('surname')
                    name = local_candidate_name_dict.get('name')
                    patronymic = local_candidate_name_dict.get('patronymic')
                    name_parts = [surname, name, patronymic]
                    full_candidate_name_string = " ".join(filter(None, name_parts)).strip() or None

                candidate_name_for_db = full_candidate_name_string # Используем это для БД analyses

                # Логирование извлеченных данных (для файла)
                if candidate_name_for_db is not None: print(f"[Task:{task_id}] Извлечено и собрано ФИО (файл): '{candidate_name_for_db}'")
                if age_for_db is not None: print(f"[Task:{task_id}] Извлечен возраст (файл): {age_for_db}")
                if gender_for_db is not None: print(f"[Task:{task_id}] Извлечен пол (файл): {gender_for_db}")
                if contacts_extracted_for_db is not None: print(f"[Task:{task_id}] Извлечены контакты (файл): '{contacts_extracted_for_db}'")


            elif not analysis_error:
                analysis_error = "ОШИБКА ОБРАБОТКИ: Исходный текст из файла пуст или не прочитан для анонимизации."
                print(f"[Task:{task_id}] {analysis_error}")
                anonymized_text_for_ai = "Нет текста резюме для анализа AI." # Заглушка для AI


        else:
            # Если не предоставлен ни filepath, ни imported_resume_id
            analysis_error = "ОШИБКА ВХОДНЫХ ДАННЫХ: Не предоставлены ни путь к файлу, ни ID импортированного резюме."
            print(f"[Task:{task_id}] {analysis_error}")
            anonymized_text_for_ai = "Нет текста резюме для анализа AI." # Заглушка для AI


        #  2. Вызов AI с АНОНИМИЗИРОВАННЫМ текстом 
        if not (analysis_error and ("ОШИБКА КОНФИГУРАЦИИ AI:" in analysis_error or "КРИТИЧЕСКАЯ ОШИБКА ЗАДАЧИ (ДО СОХРАНЕНИЯ):" in analysis_error or "ОШИБКА БД:" in analysis_error or "ОШИБКА ФАЙЛА:" in analysis_error)):
             # Убедимся, что есть текст для AI или хотя бы заглушка
             if anonymized_text_for_ai is None:
                  anonymized_text_for_ai = "Нет текста резюме для анализа AI."

             api_key_to_use = gemini_api_key if ai_service == 'gemini' else deepseek_api_key
             model_name_to_use = gemini_model if ai_service == 'gemini' else deepseek_model

             if not api_key_to_use:
                 ai_config_error = f"ОШИБКА КОНФИГУРАЦИИ AI: Нет API ключа для '{ai_service}'."
                 analysis_error = (analysis_error + "; " + ai_config_error) if analysis_error else ai_config_error
                 print(f"[Task:{task_id}] {ai_config_error}")
             else:
                job_description_for_ai = job_description if job_description is not None else ""

                # Выбираем инструкции для AI в зависимости от источника (imported vs file)
                if imported_resume_id is not None:
                     full_prompt = f"""{analysis_instructions_db_import}\n\nОписание вакансии:\n---\n{job_description_for_ai}\n---\n\nАноним. данные кандидата:\n---\n{anonymized_text_for_ai}\n---"""
                     print(f"[Task:{task_id}] Использую инструкции для AI: analysis_instructions_db_import")
                else:
                    full_prompt = f"""{analysis_instructions}\n\nОписание вакансии:\n---\n{job_description_for_ai}\n---\n\nАноним. резюме:\n---\n{anonymized_text_for_ai}\n---"""
                    print(f"[Task:{task_id}] Использую инструкции для AI: analysis_instructions")

                try:
                    print(f"[Task:{task_id}] Вызов AI сервиса: {ai_service}, модель: {model_name_to_use}")
                    analysis_result_raw = call_ai_service(ai_service, model_name_to_use, api_key_to_use, full_prompt)
                    print(f"[Task:{task_id}] AI вернул ответ (первые 100 символов): {str(analysis_result_raw)[:100]}...")
                except Exception as ai_call_e:
                     ai_api_error = f"ОШИБКА AI ({ai_service}): Критическая ошибка при вызове API - {type(ai_call_e).__name__}: {ai_call_e}"
                     analysis_error = (analysis_error + "; " + ai_api_error) if analysis_error else ai_api_error
                     print(f"[Task:{task_id}] {ai_api_error}")

        else:
             # AI не будет вызван из-за предыдущих критических ошибок (например, файл не прочитался, ошибка БД и т.д.)
             print(f"[Task:{task_id}] Пропускаю вызов AI из-за предыдущих ошибок: {analysis_error}")
             analysis_result_raw = None # Убедимся, что raw результат AI пуст


        #  3. Парсинг ответа AI и извлечение разделов 
        # Этот блок выполняется, только если AI вернул ответ, который не является сообщением об ошибке от самого AI.
        if analysis_result_raw is not None and isinstance(analysis_result_raw, str) and not analysis_result_raw.startswith(("ОШИБКА AI:", "ОШИБКА КОНФИГУРАЦИИ AI:")):
            temp_analysis_text = analysis_result_raw
            parsing_error = None # Инициализация ошибки парсинга

            # Парсинг разделов из ответа AI
            try:
                score, temp_analysis_text, score_parsing_error = parse_ai_score(temp_analysis_text)
                if score_parsing_error: parsing_error = (parsing_error + "; Score: " + score_parsing_error) if parsing_error else ("Score: " + score_parsing_error)

                extracted_interview_recommendations, temp_analysis_text, rec_parsing_error = parse_ai_recommendations(temp_analysis_text)
                if rec_parsing_error: parsing_error = (parsing_error + "; Recommendations: " + rec_parsing_error) if parsing_error else ("Recommendations: " + rec_parsing_error)

                extracted_questions, temp_analysis_text, q_parsing_error = parse_ai_questions(temp_analysis_text)
                if q_parsing_error: parsing_error = (parsing_error + "; Questions: " + q_parsing_error) if parsing_error else ("Questions: " + q_parsing_error)

                extracted_salary_expectation, temp_analysis_text, salary_parsing_error = parse_ai_salary(temp_analysis_text)
                if salary_parsing_error: parsing_error = (parsing_error + "; Salary: " + salary_parsing_error) if parsing_error else ("Salary: " + salary_parsing_error)

                local_summary_conclusion, temp_analysis_text, summary_parsing_error = parse_ai_summary(temp_analysis_text)
                if summary_parsing_error: parsing_error = (parsing_error + "; Summary: " + summary_parsing_error) if parsing_error else ("Summary: " + summary_parsing_error)

                # Оставшийся текст - это детальный анализ
                analysis_text_clean = temp_analysis_text.strip()
                print(f"[Task:{task_id}] Парсинг ответа AI завершен. Ошибки парсинга: {parsing_error}")

                # Добавляем ошибки парсинга к общей ошибке анализа
                if parsing_error:
                     analysis_error = (analysis_error + "; Парсинг: " + parsing_error) if analysis_error else ("Парсинг: " + parsing_error)


            except Exception as parse_e:
                 parse_critical_error = f"КРИТИЧЕСКАЯ ОШИБКА ПАРСИНГА ответа AI: {type(parse_e).__name__}: {parse_e}"
                 analysis_error = (analysis_error + "; " + parse_critical_error) if analysis_error else parse_critical_error
                 analysis_text_clean = analysis_result_raw # Сохраняем сырой ответ при критической ошибке парсинга
                 # Сбрасываем извлеченные переменные, так как парсинг не удался полностью
                 local_summary_conclusion = None
                 score = None
                 extracted_questions = []
                 extracted_interview_recommendations = None
                 extracted_salary_expectation = None
                 print(f"[Task:{task_id}] {analysis_error}")

        elif analysis_result_raw is not None and isinstance(analysis_result_raw, str):
            # Если AI вернул сообщение об ошибке (начинается с "ОШИБКА AI:" и т.п.)
            ai_response_error_msg = analysis_result_raw
            analysis_error = (analysis_error + "; AI Response: " + ai_response_error_msg) if analysis_error else ("AI Response: " + ai_response_error_msg)
            analysis_text_clean = ai_response_error_msg # Сохраняем текст ошибки от AI в поле анализа
            # Сбрасываем извлеченные переменные
            local_summary_conclusion = None
            score = None
            extracted_questions = []
            extracted_interview_recommendations = None
            extracted_salary_expectation = None
            print(f"[Task:{task_id}] AI вернул сообщение об ошибке или конфигурации.")

        else:
            # AI не вернул ответ или вернул unexpected, и нет явных ошибок до вызова AI.
            # Это может произойти, если anonymized_text_for_ai был пуст и AI не был вызван.
            print(f"[Task:{task_id}] AI не сгенерировал осмысленный ответ или не был вызван.")
            if not analysis_text_clean and analysis_error:
                 # Если analysis_text_clean пуст, но есть ошибка, используем текст ошибки
                 analysis_text_clean = analysis_error
            elif not analysis_text_clean:
                 # Если ни текста анализа, ни ошибки нет, ставим заглушку
                 analysis_text_clean = "Нет данных анализа."
                 if not analysis_error: analysis_error = analysis_text_clean # Установим ошибку, если ее нет


        #  4. Сохранение результатов в БД analyses 
        conn = None # Инициализируем соединение для этого блока сохранения
        try:
            conn = psycopg2.connect(DATABASE_URL)
            cursor = conn.cursor()
            timestamp = datetime.datetime.now()
            # Текст для сохранения в поле analysis_text: либо чистый анализ, либо информация об ошибке
            final_analysis_text_to_db = analysis_text_clean if analysis_text_clean is not None else "Нет данных анализа"
            print(f"[Task:{task_id}] Сохранение в БД. Imported ID: {imported_resume_id}, File: {original_filename}. Ошибка: {analysis_error}. Имя: '{candidate_name_for_db}'")

            # Используем %s для плейсхолдеров в psycopg2
            cursor.execute("""
            INSERT INTO analyses (batch_id, batch_timestamp, timestamp, original_filename, stored_filename, candidate_name, contacts_extracted, age, gender, summary_conclusion, job_description, analysis_text, score, user_id, interview_recommendations, salary_expectation, imported_resume_id, error_info)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id; -- Возвращаем ID вставленной записи
            """, (batch_id, batch_timestamp, timestamp, original_filename, stored_filename,
                  candidate_name_for_db,
                  contacts_extracted_for_db,
                  age_for_db,
                  gender_for_db,
                  local_summary_conclusion,
                  job_description,
                  final_analysis_text_to_db,
                  score,
                  user_id,
                  extracted_interview_recommendations,
                  extracted_salary_expectation,
                  imported_resume_id,
                  analysis_error
                  ))
            db_id = cursor.fetchone()[0] # Получаем возвращенный ID
            conn.commit()
            print(f"[Task:{task_id}] Анализ сохранен в БД (ID:{db_id}). Source: {source_info}. Error: {analysis_error is not None}. Name: '{candidate_name_for_db}'.")


            # Сохраняем вопросы интервью (если есть и если основная запись анализа была сохранена)
            if extracted_questions and db_id is not None:
                print(f"[Task:{task_id}] Сохраняю {len(extracted_questions)} вопросов в БД для analysis_id {db_id}...")
                # Формируем список кортежей для executemany
                questions_to_insert = [(db_id, q, 'AI', timestamp, user_id) for q in extracted_questions]
                try:
                    # Используем executemany для вставки нескольких строк
                    cursor.executemany("""
                        INSERT INTO interview_questions (analysis_id, question_text, source, created_at, user_id)
                        VALUES (%s, %s, %s, %s, %s)
                     """, questions_to_insert)
                    conn.commit()
                    print(f"[Task:{task_id}] Вопросы сохранены.")
                except Exception as q_save_e:
                    print(f"[Task:{task_id}] ОШИБКА СОХРАНЕНИЯ ВОПРОСОВ для analysis_id {db_id}: {type(q_save_e).__name__}: {q_save_e}")
                    # Пока просто логируем ошибку сохранения вопросов (она не блокирует сохранение основной записи анализа)

            else:
                print(f"[Task:{task_id}] Вопросы для сохранения не извлечены или анализ не был сохранен.")

        except Exception as e:
            db_save_error = f"ОШИБКА СОХРАНЕНИЯ В БД (анализа или связанных данных): {type(e).__name__}: {e}"
            # Добавляем ошибку сохранения к общей ошибке анализа (если она была ранее)
            final_error_for_log_and_retry = (analysis_error + "; " + db_save_error) if analysis_error else db_save_error
            print(f"[Task:{task_id}] {final_error_for_log_and_retry}")
            if conn:
                try:
                    conn.rollback()
                    print(f"[Task:{task_id}] Роллбэк транзакции БД после ошибки сохранения.")
                except Exception as rb_e:
                    print(f"[Task:{task_id}] Ошибка роллбэка БД: {rb_e}")

            # !!! Если это последняя попытка, пытаемся сохранить минимальную запись об ошибке !!!
            if self.request.retries >= (self.max_retries or 3) - 1:
                conn_error = None # Отдельное соединение для записи ошибки
                try:
                    conn_error = psycopg2.connect(DATABASE_URL)
                    cursor_error = conn_error.cursor()
                    timestamp = datetime.datetime.now()
                     # Убедимся, что original_filename и imported_resume_id корректны для отображения
                    display_original_filename = original_filename if original_filename else (f"Импорт ID {imported_resume_id}" if imported_resume_id is not None else "Неизвестный источник")
                    display_stored_filename = stored_filename if (filepath and os.path.exists(filepath) and imported_resume_id is None) else None # stored_filename только для файлов

                    # Данные для записи при ошибке сохранения: используем собранные PII, если есть, иначе заглушки
                    name_on_error = candidate_name_for_db if candidate_name_for_db is not None else "Ошибка сохранения"
                    contacts_on_error = contacts_extracted_for_db if contacts_extracted_for_db is not None else None
                    age_on_error = age_for_db if age_for_db is not None else None
                    gender_on_error = gender_for_db if gender_for_db is not None else None
                    # Сводка и другие результаты AI могут быть недоступны или частичны
                    summary_on_error = local_summary_conclusion if local_summary_conclusion is not None else "Нет сводки (ошибка сохранения)"
                    # В поле analysis_text записываем финальное сообщение об ошибке
                    analysis_text_on_error = final_error_for_log_and_retry


                    print(f"[Task:{task_id}] Последняя попытка, пытаюсь сохранить запись об ошибке сохранения для {source_info}...")
                    # Используем %s для плейсхолдеров в psycopg2
                    cursor_error.execute("""
                    INSERT INTO analyses (batch_id, batch_timestamp, timestamp, original_filename, stored_filename, candidate_name, contacts_extracted, age, gender, summary_conclusion, job_description, analysis_text, score, user_id, interview_recommendations, salary_expectation, imported_resume_id, error_info)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (batch_id, batch_timestamp, timestamp, display_original_filename, display_stored_filename,
                          name_on_error, contacts_on_error, # < Используем собранные PII
                          age_on_error, gender_on_error, # < Используем собранные PII
                          summary_on_error,
                          job_description, analysis_text_on_error, None, user_id, # score = None при ошибке сохранения
                          None, # interview_recommendations = None при ошибке сохранения
                          None, # salary_expectation = None при ошибке сохранения
                          imported_resume_id, # <-- СОХРАНЯЕМ imported_resume_id при ошибке
                          final_error_for_log_and_retry # <-- Сохраняем финальную ошибку
                          ))
                    conn_error.commit()
                    final_db_id = cursor_error.lastrowid
                    print(f"[Task:{task_id}] Информация об ошибке сохранения сохранена в БД (ID:{final_db_id}).")

                    # Удаляем файл, если это последняя попытка и был файл
                    if display_stored_filename and filepath and os.path.exists(filepath) and imported_resume_id is None:
                         print(f"[Task:{task_id}] Удаляю файл {display_stored_filename} после ошибки сохранения (последняя попытка): {filepath}")
                         try:
                              os.remove(filepath)
                              print(f"[Task:{task_id}] Файл удален.")
                         except OSError as rm_e:
                              print(f"[Task:{task_id}] Ошибка удаления файла {display_stored_filename} после ошибки сохранения: {rm_e}")
                         except Exception as rm_e_unk:
                              print(f"[Task:{task_id}] Неизвестная ошибка удаления файла {display_stored_filename}: {rm_e_unk}")

                except Exception as db_e_final:
                    print(f"[Task:{task_id}] Не удалось сохранить информацию о КРИТИЧЕСКОЙ ОШИБКЕ сохранения в БД: {type(db_e_final).__name__}: {db_e_final}. Source: {source_info}.")
                finally:
                    if conn_error:
                         try:
                              conn_error.close()
                         except Exception as ce:
                             print(f"[Task:{task_id}] Ошибка закрытия соединения при ошибке сохранения: {ce}")

            # Важно: после обработки ошибки сохранения в Celery, повторно возбуждаем исключение
            raise e

    except Exception as e:
        # Эта ошибка ловит все, что произошло до блока сохранения в БД или после него,
        # если это не ошибка сохранения.
        critical_error = f"КРИТИЧЕСКАЯ ОШИБКА ЗАДАЧИ (ДО/ПОСЛЕ СОХРАНЕНИЯ) (Попытка {self.request.retries + 1}) для {source_info}: {type(e).__name__}: {e}"
        # Добавляем эту критическую ошибку к общей ошибке анализа (если она была ранее)
        final_error_for_log_and_retry = (analysis_error + "; " + critical_error) if analysis_error else critical_error
        print(f"[Task:{task_id}] {final_error_for_log_and_retry}")

        # !!! Если это последняя попытка, пытаемся сохранить минимальную запись об ошибке !!!
        if self.request.retries >= (self.max_retries or 3) - 1:
            conn_error = None # Отдельное соединение для записи ошибки
            try:
                conn_error = psycopg2.connect(DATABASE_URL)
                cursor_error = conn_error.cursor()
                timestamp = datetime.datetime.now()
                 # Убедимся, что original_filename и imported_resume_id корректны для отображения
                display_original_filename = original_filename if original_filename else (f"Импорт ID {imported_resume_id}" if imported_resume_id is not None else "Неизвестный источник")
                display_stored_filename = stored_filename if (filepath and os.path.exists(filepath) and imported_resume_id is None) else None # stored_filename только для файлов

                # Данные для записи при критической ошибке: используем собранные PII, если есть, иначе заглушки
                # candidate_name_for_db и другие *_for_db уже содержат данные либо из импорта, либо из PII файла (до ошибки)
                name_on_error = candidate_name_for_db if candidate_name_for_db is not None else "Крит. ошибка"
                contacts_on_error = contacts_extracted_for_db if contacts_extracted_for_db is not None else None
                age_on_error = age_for_db if age_for_db is not None else None
                gender_on_error = gender_for_db if gender_for_db is not None else None
                # Сводка и другие результаты AI могут быть недоступны
                summary_on_error = local_summary_conclusion if local_summary_conclusion is not None else "Нет сводки (крит. ошибка задачи)"
                # В поле analysis_text записываем финальное сообщение об ошибке
                analysis_text_on_error = final_error_for_log_and_retry


                print(f"[Task:{task_id}] Последняя попытка, пытаюсь сохранить запись о критической ошибке задачи для {source_info}...")
                # Используем %s для плейсхолдеров в psycopg2
                cursor_error.execute("""
                INSERT INTO analyses (batch_id, batch_timestamp, timestamp, original_filename, stored_filename, candidate_name, contacts_extracted, age, gender, summary_conclusion, job_description, analysis_text, score, user_id, interview_recommendations, salary_expectation, imported_resume_id, error_info)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (batch_id, batch_timestamp, timestamp, display_original_filename, display_stored_filename,
                      name_on_error, contacts_on_error,
                      age_on_error, gender_on_error,
                      summary_on_error,
                      job_description, analysis_text_on_error, None, user_id,
                      None,
                      None,
                      imported_resume_id,
                      final_error_for_log_and_retry
                      ))
                conn_error.commit()
                final_db_id = cursor_error.lastrowid # lastrowid доступен после commit
                print(f"[Task:{task_id}] Информация о критической ошибке задачи сохранена в БД (ID:{final_db_id}).")


                # Удаляем файл, если это последняя попытка и был файл
                if display_stored_filename and filepath and os.path.exists(filepath) and imported_resume_id is None:
                    print(f"[Task:{task_id}] Удаляю файл {display_stored_filename} после критической ошибки (последняя попытка): {filepath}")
                    try:
                         os.remove(filepath)
                         print(f"[Task:{task_id}] Файл удален.")
                    except OSError as rm_e:
                         print(f"[Task:{task_id}] Ошибка удаления файла {display_stored_filename} после критической ошибки: {rm_e}")
                    except Exception as rm_e_unk:
                         print(f"[Task:{task_id}] Неизвестная ошибка удаления файла {display_stored_filename}: {rm_e_unk}")

            except Exception as db_e:
                print(f"[Task:{task_id}] Не удалось сохранить информацию о критической ошибке в БД: {type(db_e).__name__}: {db_e}. Source: {source_info}.")
            finally:
                if conn_error:
                    try:
                        conn_error.close()
                    except Exception as ce:
                        print(f"[Task:{task_id}] Ошибка закрытия соединения при ошибке: {ce}")
        raise e # Перевыбрасываем исключение для Celery

    finally:
        # Закрываем основное соединение, если оно было открыто
        if conn:
            try:
                conn.close()
                print(f"[Task:{task_id}] Основное соединение БД закрыто в блоке finally.")
            except Exception as ce:
                print(f"[Task:{task_id}] Ошибка закрытия основного соединения в блоке finally: {ce}")
        # Закрываем соединение для чтения, если оно было открыто
        if read_conn:
            try:
                read_conn.close()
                print(f"[Task:{task_id}] Соединение для чтения БД закрыто в блоке finally.")
            except Exception as ce:
                print(f"[Task:{task_id}] Ошибка закрытия соединения для чтения в блоке finally: {ce}")


    final_status = 'SUCCESS' if analysis_error is None else 'FINISHED_WITH_ERRORS'
    print(f"[Task:{task_id}] Задача process_resume_task завершена со статусом {final_status} для {source_info}. Analysis ID в БД: {db_id}. Финальная ошибка: {analysis_error}")

    return {'status': final_status, 'analysis_id': db_id, 'imported_resume_id': imported_resume_id, 'error': analysis_error}

@celery_app.task(bind=True)
def task_re_evaluate(self, analysis_id, ai_service, ai_model, api_key, upload_folder):
    """
    Задача Celery для повторной оценки резюме с учетом ответов кандидата.
    Собирает данные, формирует промт, вызывает AI и сохраняет результаты в БД.
    """
    task_id = self.request.id
    print(f"Запущена задача повторной оценки для analysis_id: {analysis_id} (Task ID: {task_id})")
    print(f"Получены настройки AI: service={ai_service}, model={ai_model}")
    print(f"Получен UPLOAD_FOLDER: {upload_folder}")


    # Переменные для управления соединениями и курсорами. Инициализируем как None.
    conn_read = None
    cursor_read = None
    conn_save = None
    cursor_save = None
    conn_error_update = None # Соединение для обновления статуса ошибки RFI (в блоках except)
    cursor_error_update = None
    conn_save_critical = None # Соединение для критической ошибки сохранения RFI
    cursor_save_critical = None
    conn_critical = None # Соединение для общей критической ошибки RFI
    cursor_critical = None
    # Переменные conn и cursor, если они использовались где-то еще
    conn = None
    cursor = None


    # Переменные для отслеживания состояния задачи и ошибок
    rfi_id = None
    re_evaluation_error_text = None # Накапливаем текст ошибок
    task_status = 'PENDING' # Статус задачи Celery
    final_rfi_status = 'unknown' # Финальный статус запроса доп. инфо в БД

    # Переменные для результатов переоценки (инициализируем как None или пустые значения)
    new_score = None
    new_analysis_text = None
    new_summary_conclusion = None
    new_questions = None # Список вопросов
    re_evaluation_timestamp = datetime.datetime.utcnow() # Время выполнения переоценки


    # Переменные для данных, полученных из БД и обработанных
    analysis_data = None
    original_resume_text = None
    text_acquisition_error = None
    anonymized_resume_text = None
    anonymization_error = None
    analysis_result_raw = None
    ai_call_error = None


    try:
        #  1. Загружаем исходные данные анализа и ответы кандидата 
        conn_read = psycopg2.connect(DATABASE_URL)
        cursor_read = conn_read.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cursor_read.execute("""
            SELECT
                a.id, a.job_description, a.stored_filename, a.original_filename, a.imported_resume_id,
                a.score AS original_score, a.analysis_text AS original_analysis_text,
                rfi.id as rfi_id, rfi.additional_info_text as candidate_answers, rfi.status as rfi_status
                -- Если вы добавили questions_json и answers_json в requests_for_info, раскомментируйте:
                -- , rfi.questions_json, rfi.answers_json
            FROM analyses a
            JOIN requests_for_info rfi ON a.id = rfi.analysis_id
            WHERE a.id = %s AND rfi.status = 'completed';
        """, (analysis_id,))
        analysis_data = cursor_read.fetchone()

        if cursor_read:
            try: cursor_read.close()
            except Exception as ce: print(f"Ошибка закрытия cursor_read: {ce}")
            finally: cursor_read = None
        if conn_read:
            try: conn_read.close()
            except Exception as ce: print(f"Ошибка закрытия conn_read: {ce}")
            finally: conn_read = None


        if analysis_data is None:
            re_evaluation_error_text = "Не удалось найти анализ с completed запросом доп. инфо для переоценки."
            print(f"Ошибка: {re_evaluation_error_text}")
            task_status = 'ERROR'
            return {"status": task_status, "message": re_evaluation_error_text}

        original_job_description = analysis_data.get('job_description')
        stored_filename = analysis_data.get('stored_filename')
        original_filename = analysis_data.get('original_filename')
        imported_resume_id = analysis_data.get('imported_resume_id')
        candidate_answers = analysis_data.get('candidate_answers')
        rfi_id = analysis_data.get('rfi_id')
        original_score = analysis_data.get('original_score')
        original_analysis_text = analysis_data.get('original_analysis_text')


        # Проверяем, что ответы кандидата не пустые
        if not candidate_answers or not str(candidate_answers).strip():
             re_evaluation_error_text = "Ответы кандидата были пусты, переоценка не выполнена."
             print(f"Предупреждение: Для анализа ID {analysis_id} запрос RFI ID {rfi_id} имеет статус 'completed', но ответы пусты. {re_evaluation_error_text}")
             task_status = 'WARNING'
             final_rfi_status = 're_evaluation_skipped_empty_answers'


        else: # Ответы кандидата не пустые
             print(f"Ответы кандидата получены. Переходим к обработке и вызову AI.")
             task_status = 'PROCESSING'

             #  2. Получаем оригинальный текст резюме из файла или из БД 
             source_determined = False

             if stored_filename and stored_filename != 'N/A':
                  print(f"Источник текста: файл ({stored_filename})")
                  source_determined = True
                  file_path = pathlib.Path(upload_folder) / stored_filename

                  if not file_path.exists():
                       text_acquisition_error = f"Ошибка: Файл резюме не найден: {stored_filename} по пути {file_path}"
                       print(f"Ошибка: {text_acquisition_error}")
                  else:
                      file_extension = pathlib.Path(original_filename).suffix.lower() if original_filename and pathlib.Path(original_filename).suffix else pathlib.Path(stored_filename).suffix.lower()
                      try:
                          if file_extension == '.pdf':
                               original_resume_text = parse_pdf_pymupdf(str(file_path))
                          elif file_extension == '.docx':
                               original_resume_text = parse_docx(str(file_path))
                          else:
                               text_acquisition_error = f"Неподдерживаемый формат файла резюме: {original_filename or stored_filename}"
                               print(f"Ошибка: {text_acquisition_error}")
                      except Exception as file_read_e:
                           text_acquisition_error = f"Критическая ошибка при чтении файла резюме {original_filename or stored_filename}: {type(file_read_e).__name__} - {file_read_e}"
                           print(f"Ошибка: {text_acquisition_error}")


             elif imported_resume_id is not None:
                  print(f"Источник текста: импорт из БД (ID: {imported_resume_id})")
                  source_determined = True
                  temp_conn = None
                  temp_cursor = None
                  try:
                      temp_conn = psycopg2.connect(DATABASE_URL)
                      temp_cursor = temp_conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                      temp_cursor.execute("SELECT * FROM imported_resumes WHERE id = %s", (imported_resume_id,))
                      imported_data = temp_cursor.fetchone()
                      if temp_cursor:
                          try: temp_cursor.close()
                          except Exception as ce: print(f"Ошибка закрытия temp_cursor: {ce}")
                          finally: temp_cursor = None

                      if not imported_data:
                          text_acquisition_error = f"Ошибка: Данные импортированного кандидата ID {imported_resume_id} не найдены."
                          print(f"Ошибка: {text_acquisition_error}")
                      else:
                          print(f"Реконструкция текста из imported_resumes ID {imported_resume_id}...")
                          original_resume_text = ""
                          fields_to_include = [
                              'fio', 'vozrast', 'pol', 'zhelaemaya_dolzhnost', 'klyuchevye_navyki',
                              'opyt_raboty', 'mesta_raboty', 'obrazovanie', 'gorod',
                              'zarplatnye_ozhidaniya', 'relokatsiya_komandirovki', 'format_raboty',
                              'yazyki', 'telefon', 'email'
                          ]
                          field_prefixes = {
                               'fio': 'ФИО: ', 'vozrast': 'Возраст: ', 'pol': 'Пол: ', 'zhelaemaya_dolzhnost': 'Желаемая должность: ',
                               'klyuchevye_navyki': 'Ключевые навыки: ', 'opyt_raboty': 'Опыт работы: ', 'mesta_raboty': 'Места работы: ',
                               'obrazovanie': 'Образование: ', 'gorod': 'Город: ', 'zarplatnye_ozhidaniya': 'Зарплатные ожидания: ',
                               'relokatsiya_komandirovki': 'Релокация/Командировки: ', 'format_работы': 'Формат работы: ',
                               'yazyki': 'Языки: ', 'telefon': 'Телефон: ', 'email': 'Email: '
                          }
                          for field_name in fields_to_include:
                              if field_name in imported_data and imported_data[field_name] is not None and str(imported_data[field_name]).strip() != '':
                                  original_resume_text += f"{field_prefixes.get(field_name, field_name)} {imported_data[field_name]}\n"
                          original_resume_text = original_resume_text.strip()

                          if not original_resume_text:
                               text_acquisition_error = f"Ошибка: Текст резюме, реконструированный из imported_resumes ID {imported_resume_id}, оказался пустым."
                               print(f"Ошибка: {text_acquisition_error}")

                  except Exception as db_read_error:
                      text_acquisition_error = f"Ошибка при чтении данных импортированного кандидата ID {imported_resume_id} из imported_resumes: {type(db_read_error).__name__} - {db_read_error}"
                      print(f"Ошибка: {text_acquisition_error}")

                  finally:
                      if temp_cursor:
                          try: temp_cursor.close()
                          except Exception as ce: print(f"Ошибка закрытия temp_cursor: {ce}")
                      if temp_conn:
                          try: temp_conn.close()
                          except Exception as ce: print(f"Ошибка закрытия temp_conn: {ce}")


             else:
                  print(f"Источник текста: не определен в записи анализа (ID: {analysis_id}).")
                  source_determined = False
                  text_acquisition_error = "Ошибка: Не удалось определить источник оригинального резюме для повторной оценки."
                  print(f"Ошибка: {text_acquisition_error}")


             #  Финальная проверка: удалось ли получить текст резюме перед анонимизацией и AI 
             if not original_resume_text or not original_resume_text.strip():
                 if not text_acquisition_error:
                      text_acquisition_error = "Ошибка: Оригинальный текст резюме был получен, но оказался пустым."

                 re_evaluation_error_text = (re_evaluation_error_text + "; ") if re_evaluation_error_text else ""
                 re_evaluation_error_text += f"Ошибка получения текста резюме: {text_acquisition_error}"

                 print(f"Финальная ошибка получения текста резюме: {text_acquisition_error}")

                 task_status = 'ERROR'
                 final_rfi_status = 're_evaluation_failed'


             else: # Текст резюме успешно получен
                  print(f"Оригинальный текст резюме успешно получен/реконструирован.")

                  #  3. АНОНИМИЗИРУЕМ ТЕКСТ РЕЗЮМЕ ПЕРЕД ОТПРАВКОЙ В AI 
                  try:
                      print(f"Выполняется анонимизация текста резюме...")
                      anonymization_result = extract_pii_and_anonymize(original_resume_text)

                      if isinstance(anonymization_result, dict):
                           anonymized_resume_text = anonymization_result.get('anonymized_text')
                           # extracted_pii_data = anonymization_result.get('pii_data')
                           if anonymized_resume_text is None:
                                anonymization_error = "Ошибка анонимизации: Функция extract_pii_and_anonymize вернула словарь, но без ключа 'anonymized_text'."
                                print(f"Ошибка: {anonymization_error}")
                                anonymized_resume_text = ""
                           else:
                                print(f"Анонимизация успешно выполнена.")
                      else:
                           anonymization_error = f"Ошибка анонимизации: Функция extract_pii_and_anonymize вернула неожиданный тип данных ({type(anonymization_result).__name__}), ожидался словарь."
                           print(f"Ошибка: {anonymization_error}")
                           anonymized_resume_text = ""
                  except Exception as anon_e:
                      anonymization_error = f"Критическая ошибка при анонимизации текста резюме: {type(anon_e).__name__} - {anon_e}"
                      print(f"Ошибка: {anonymization_error}")
                      anonymized_resume_text = ""


                  # Если произошла ошибка анонимизации, обрабатываем ее ДО вызова AI
                  if anonymization_error:
                       re_evaluation_error_text = (re_evaluation_error_text + "; ") if re_evaluation_error_text else ""
                       re_evaluation_error_text += f"Ошибка анонимизации: {anonymization_error}"

                       print(f"Ошибка перед вызовом AI (анонимизация): {re_evaluation_error_text}")
                       task_status = 'ERROR'
                       final_rfi_status = 're_evaluation_failed'

                  else: # Анонимизация успешна, ошибок нет
                       print(f"Текст резюме анонимизирован. Переходим к вызову AI.")

                       #  4. Формируем финальный промт для AI и вызываем AI сервис 
                       # 4.1. Формируем промт для AI с анонимизированным резюме и ответами
                       prompt_job_description = original_job_description if original_job_description is not None else "Не предоставлено"
                       prompt_candidate_answers = candidate_answers if candidate_answers is not None else "Не предоставлены"
                       prompt_anonymized_resume = anonymized_resume_text if anonymized_resume_text is not None else "Не получен"


                       prompt = f"""
                       Твоя роль: Ты — высококвалифицированный и внимательный HR-аналитик с многолетним успешным опытом в подборе персонала для различных отраслей. Твоя экспертиза позволяет точно оценивать соответствие кандидата требованиям вакансии.

                       Твоя ЗАДАЧА (Повторный Анализ): Провести повторный профессиональный и объективный анализ анонимизированного резюме кандидата на соответствие требованиям предоставленного описания вакансии, ОБЯЗАТЕЛЬНО УЧИТЫВАЯ предоставленные ОТВЕТЫ кандидата на дополнительные вопросы. Оцени, как ответы повлияли на общее соответствие.

                       Ключевые инструкции и ограничения:
                       1.  Анализируй ИСКЛЮЧИТЕЛЬНО содержание АНОНИМИЗИРОВАННОГО резюме, ОПИСАНИЯ вакансии и ОТВЕТОВ кандидата.
                       2.  Строго запрещено делать любые предположения, догадки или пытаться вывести личные данные кандидата. Работай только с профессиональными качествами, опытом и навыками, описанными в тексте.
                       3.  Не придумывай информацию, которой нет ни в вакансии, ни в резюме, ни в ответах кандидата.
                       4.  **СТРОГО СЛЕДУЙ запрашиваемому Формату ответа.** Не добавляй лишний текст или markdown вокруг указанных маркеров.
                       5.  Ответ должен быть предоставлен ИСКЛЮЧИТЕЛЬНО на русском языке.

                       АНОНИМИЗИРОВАННОЕ РЕЗЮМЕ КАНДИДАТА:
                       ---
                       {prompt_anonymized_resume}
                       ---

                       ОПИСАНИЕ ВАКАНСИИ:
                       ---
                       {prompt_job_description}
                       ---

                       ОТВЕТЫ КАНДИДАТА НА ДОПОЛНИТЕЛЬНЫЕ ВОПРОСЫ:
                       ---
                       {prompt_candidate_answers}
                       ---

                       На основе АНОНИМИЗИРОВАННОГО РЕЗЮМЕ, ОПИСАНИЯ ВАКАНСИИ и ОТВЕТОВ КАНДИДАТА выполни следующие шаги:
                       1. Внимательно проанализируй ОТВЕТЫ кандидата на дополнительные вопросы. **Для каждого вопроса из списка и соответствующего ответа кандидата напиши 1-3 предложения комментария**, оценивающего, как этот ответ дополняет или изменяет понимание соответствия кандидата. Интегрируй эти комментарии в полный текст анализа (шаг 5).
                       2. Проанализируй, как вся совокупность данных (резюме, вакансия, ответы) влияет на общую оценку соответствия.
                       3. Сформулируй КРАТКИЙ ИТОГ повторного анализа, отражающий основные выводы с учетом ответов.
                       4. Присвой КОРРЕКТИРОВАННУЮ ИТОГОВУЮ ОЦЕНКУ соответствия кандидата по шкале от 1 до 10, учитывая все предоставленные данные.
                       **Детализированная Шкала Оценки Соответствия Резюме Вакансии (от 1 до 10 баллов):**
                        * 1: Полное отсутствие соответствия основным требованиям вакансии. Резюме не содержит релевантного опыта, навыков или образования.
                        * 2: Крайне низкое соответствие. Присутствуют единичные, очень поверхностные совпадения с требованиями, но в целом кандидат совершенно не подходит.
                        * 3: Минимальное соответствие. Есть очень ограниченные совпадения по некоторым пунктам, но отсутствуют ключевой опыт и необходимые навыки для выполнения работы.
                        * 4: Небольшое минимальное соответствие. Кандидат обладает некоторыми базовыми знаниями или опытом, связанными с вакансией, но этого явно недостаточно. Есть значительные пробелы.
                        * 5: Среднее соответствие. Есть базовое совпадение по нескольким ключевым пунктам вакансии, но при этом присутствуют существенные пробелы в опыте или навыках. Требуются серьезные уточнения.
                        * 6: Уверенное среднее соответствие. Кандидат соответствует основным требованиям по большинству базовых пунктов, но есть заметные области, где опыт или навыки недостаточны или требуют прояснения.
                        * 7: Хорошее соответствие. Кандидат обладает большинством необходимых навыков и релевантного опыта. Есть небольшие расхождения с идеальным профилем или пункты, которые желательно уточнить на интервью.
                        * 8: Очень хорошее соответствие. Кандидат обладает почти всеми ключевыми навыками и значительным релевантным опытом. Требуется лишь минимальное уточнение деталей или оценка соответствия корпоративной культуре.
                        * 9: Высокое соответствие. Кандидат полностью соответствует всем ключевым требованиям вакансии, обладает сильными сторонами и релевантным опытом, который может быть очень ценным.
                        * 10: Идеальное соответствие. Кандидат не только полностью соответствует всем требованиям вакансии, но и превосходит ожидания по ряду пунктов. Обладает уникальным или очень ценным опытом/навыками, которые делают его идеальным кандидатом.

                       5. Сгенерируй ПОЛНЫЙ ТЕКСТ повторного анализа. **Этот текст должен начинать ответ**. Он должен включать в себя детальный анализ с учетом ответов, **а также комментарии по каждому вопросу и ответу кандидата, как описано в шаге 1.**
                       6. **Включай  анализ и комментарии по ответам кандидата**. Используй markdown (заголовки, списки и т.д.) для структурирования текста анализа, но не для маркеров.
                       **Формат ответа: СТРОГО СОБЛЮДАЙ СЛЕДУЮЩУЮ СТРУКТУРУ И МАРКЕРЫ БЕЗ ЛИШНЕГО ТЕКСТА. СТРОГО СОБЛЮДАЙ ПОРЯДОК:**

                       Анализ ответов кандидата

                       ИТОГ_КРАТКО: [Краткий итог повторного анализа в 1-2 предложения]

                       ИТОГОВАЯ ОЦЕНКА: [Число от 1 до 10 в квадратных скобках]
                       """
                       print(f"Сформирован промт для AI.")


                       # 4.2. Вызываем AI сервис с промтом
                       analysis_result_raw = None
                       ai_call_error = None
                       if not api_key or not ai_model:
                            ai_call_error = f"ОШИБКА AI (повторная оценка): Настройки AI неполные."
                            print(f"{ai_call_error}")
                            task_status = 'ERROR'
                            final_rfi_status = 're_evaluation_failed'

                       else:
                           try:
                               print(f"Вызов call_ai_service...")
                               analysis_result_raw = call_ai_service(
                                   ai_service=ai_service,
                                   model_name=ai_model,
                                   api_key=api_key,
                                   complete_prompt_string=prompt
                               )

                               if isinstance(analysis_result_raw, str) and analysis_result_raw.startswith("ОШИБКА AI:"):
                                    ai_call_error = analysis_result_raw
                                    print(f"Вызов call_ai_service вернул ошибку: {ai_call_error}")
                                    task_status = 'ERROR_AI_CALL'
                                    final_rfi_status = 're_evaluation_failed'

                               elif not analysis_result_raw or not analysis_result_raw.strip():
                                    ai_call_error = f"ОШИБКА AI (повторная оценка): AI вернул пустой ответ."
                                    print(f"{ai_call_error}")
                                    analysis_result_raw = ""
                                    task_status = 'ERROR_AI_EMPTY'
                                    final_rfi_status = 're_evaluation_failed'
                               else:
                                    print(f"Ответ AI получен.")


                           except Exception as e:
                                ai_call_error = f"ОШИБКА AI (повторная оценка): Критическая ошибка при вызове call_ai_service: {type(e).__name__} - {e}"
                                print(f"{ai_call_error}")
                                analysis_result_raw = ""
                                task_status = 'CRITICAL_AI_CALL_ERROR'
                                final_rfi_status = 're_evaluation_failed'


                       #  Обработка ответа AI (после вызова call_ai_service) 
                       if ai_call_error:
                            re_evaluation_error_text = (re_evaluation_error_text + "; ") if re_evaluation_error_text else ""
                            re_evaluation_error_text += ai_call_error
                            new_analysis_text = f"Ошибка при получении результата от AI: {ai_call_error}"
                            new_score = None
                            new_summary_conclusion = None
                            new_questions = None


                       elif analysis_result_raw:
                           print(f"Начинаем парсинг ответа AI.")
                           text_to_parse = str(analysis_result_raw)

                           local_new_score = None
                           local_new_summary = None
                           local_new_questions = None
                           local_new_recommendations = None
                           local_new_analysis_text = None

                           score_parsing_error = None
                           summary_parsing_error = None
                           questions_parsing_error = None
                           recommendations_parsing_error = None
                           overall_parsing_error = None


                           #  Парсим Оценку 
                           local_new_score, text_after_score_parsing, score_parsing_error = parse_ai_score(text_to_parse)
                           if score_parsing_error:
                               overall_parsing_error = (overall_parsing_error + "; ") if overall_parsing_error else ""
                               overall_parsing_error += f"Ошибка парсинга оценки: {score_parsing_error}"


                           #  Парсим Краткий Итог 
                           text_for_summary_parsing = text_after_score_parsing if text_after_score_parsing is not None else ""
                           local_new_summary, text_after_summary_parsing, summary_parsing_error = parse_ai_summary(text_for_summary_parsing)
                           if summary_parsing_error:
                                overall_parsing_error = (overall_parsing_error + "; ") if overall_parsing_error else ""
                                overall_parsing_error += f"Ошибка парсинга итога: {summary_parsing_error}"

                           #  Парсим Уточняющие Вопросы 
                           text_for_questions_parsing = text_after_summary_parsing if text_after_summary_parsing is not None else ""
                           local_new_questions, text_after_questions_parsing, questions_parsing_error = parse_ai_questions(text_for_questions_parsing)
                           if questions_parsing_error:
                                overall_parsing_error = (overall_parsing_error + "; ") if overall_parsing_error else ""
                                overall_parsing_error += f"Ошибка парсинга вопросов: {questions_parsing_error}"
                           if not isinstance(local_new_questions, list):
                                local_new_questions = []


                           #  Парсим Рекомендации 
                           text_for_recommendations_parsing = text_after_questions_parsing if text_after_questions_parsing is not None else ""
                           local_new_recommendations, text_after_recommendations_parsing, recommendations_parsing_error = parse_ai_recommendations(text_for_recommendations_parsing)
                           if recommendations_parsing_error:
                                overall_parsing_error = (overall_parsing_error + "; ") if overall_parsing_error else ""
                                overall_parsing_error += f"Ошибка парсинга рекомендаций: {recommendations_parsing_error}"

                           #  Оставшийся текст - это полный текст анализа влияния ответов 
                           local_new_analysis_text = text_after_recommendations_parsing.strip() if text_after_recommendations_parsing is not None else ""


                           #  Финальная сборка результатов парсинга 
                           new_score = local_new_score
                           new_summary_conclusion = local_new_summary
                           new_questions = local_new_questions # Список вопросов
                           new_analysis_text = local_new_analysis_text # Полный текст анализа влияния ответов


                           #  Обработка ошибок парсинга 
                           if overall_parsing_error:
                                re_evaluation_error_text = (re_evaluation_error_text + "; ") if re_evaluation_error_text else ""
                                re_evaluation_error_text += f"Ошибки парсинга ответа AI: {overall_parsing_error}"
                                task_status = 'WARNING_PARSING'
                                final_rfi_status = 're_evaluated_with_parsing_errors'


                           # Проверяем, что ключевые поля извлечены после парсинга
                           missing_fields = []
                           if new_score is None: missing_fields.append("Оценка")

                           if missing_fields:
                                warning_message = f"Предупреждение парсинга: Не извлечены ключевые поля: {', '.join(missing_fields)}. Проверьте формат ответа AI."
                                re_evaluation_error_text = (re_evaluation_error_text + "; ") if re_evaluation_error_text else ""
                                re_evaluation_error_text += warning_message
                                if task_status not in ['ERROR_AI_CALL', 'ERROR_AI_EMPTY', 'CRITICAL_AI_CALL_ERROR']:
                                     task_status = 'WARNING_PARSING'
                                if final_rfi_status not in ['re_evaluation_failed']:
                                     final_rfi_status = 're_evaluated_with_parsing_errors'


                           # Если ошибок парсинга не было и все ключевые поля (только оценка) извлечены
                           if overall_parsing_error is None and not missing_fields:
                                task_status = 'PARSING_SUCCESS'
                                final_rfi_status = 're_evaluated'

                           print(f"Парсинг Завершён. Финальная ошибка парсинга: '{re_evaluation_error_text}'")

        #  Конец блока обработки, если ответы кандидата не пустые 


        #  5. Сохраняем результаты в базу данных и обновляем статус RFI 
        try:
            conn_save = psycopg2.connect(DATABASE_URL)
            cursor_save = conn_save.cursor()

            sql_update_analysis = """
                UPDATE analyses
                SET
                    re_evaluation_score = %s,
                    re_evaluation_text = %s,
                    re_evaluation_timestamp = %s,
                    re_evaluation_summary_conclusion = %s,
                    re_evaluation_error = %s
                WHERE id = %s;
            """

            params_update_analysis = [
                new_score,
                new_analysis_text,
                re_evaluation_timestamp,
                new_summary_conclusion,
                re_evaluation_error_text,
                analysis_id
            ]

            sql_update_rfi_status = """
                UPDATE requests_for_info
                SET status = %s,
                    re_evaluation_error = %s
                WHERE id = %s;
            """
            error_text_for_db = re_evaluation_error_text if re_evaluation_error_text is not None else None

            params_update_rfi_status = [
                final_rfi_status,
                error_text_for_db,
                rfi_id
            ]


            if conn_save and cursor_save and rfi_id is not None:
                 #  ДОБАВЛЕННЫЕ ОТЛАДОЧНЫЕ ПРИНТЫ 
                 print(f"--- DEBUG SAVE: ANALYSIS UPDATE ---")
                 print(f"SQL (analysis): {sql_update_analysis}")
                 print(f"Params (analysis): {params_update_analysis}")
                 print(f"SQL placeholders count (analysis): {sql_update_analysis.count('%s')}")
                 print(f"Params count (analysis): {len(params_update_analysis)}")
                 print(f"------------------------------------")

                 cursor_save.execute(sql_update_analysis, params_update_analysis)

                 print(f"--- DEBUG SAVE: RFI STATUS UPDATE ---")
                 print(f"SQL (RFI): {sql_update_rfi_status}")
                 print(f"Params (RFI): {params_update_rfi_status}")
                 print(f"SQL placeholders count (RFI): {sql_update_rfi_status.count('%s')}")
                 print(f"Params count (RFI): {len(params_update_rfi_status)}")
                 print(f"------------------------------------")
                 #  КОНЕЦ ОТЛАДОЧНЫХ ПРИНТОВ 
                 cursor_save.execute(sql_update_rfi_status, params_update_rfi_status)


                 conn_save.commit()
                 print(f"Результаты сохранены. RFI ID: {rfi_id}. Финальный статус RFI: {final_rfi_status}.")
                 if task_status in ['PENDING', 'PROCESSING', 'PARSING_SUCCESS']:
                      task_status = 'SUCCESS'
                 elif task_status == 'WARNING_PARSING':
                      task_status = 'WARNING'
                 elif task_status == 'WARNING':
                      task_status = 'SUCCESS'
            else:
                 print(f"Ошибка: Соединение для сохранения conn_save не было открыто или rfi_id неизвестен ({rfi_id}) перед выполнением UPDATE запросов.")
                 task_status = 'ERROR_SAVE_CONNECTION'
                 re_evaluation_error_text = (re_evaluation_error_text + "; ") if re_evaluation_error_text else ""
                 re_evaluation_error_text += "Ошибка: Соединение БД для сохранения не установлено или RFI ID неизвестен."


        except (Psycopg2Error, Exception) as db_e:
            print(f"Ошибка БД при сохранении результатов: {type(db_e).__name__} - {db_e}. RFI ID: {rfi_id}.")
            task_status = 'DB_SAVE_ERROR'
            re_evaluation_error_text = (re_evaluation_error_text + "; ") if re_evaluation_error_text else ""
            re_evaluation_error_text += f"Ошибка БД при сохранении: {type(db_e).__name__} - {db_e}"

            if conn_save:
                 try: conn_save.rollback()
                 except Exception as rb_e: print(f"Ошибка роллбэка транзакции сохранения: {rb_e}")

            if rfi_id is not None:
                 try:
                      conn_save_critical = psycopg2.connect(DATABASE_URL)
                      cursor_save_critical = conn_save_critical.cursor()
                      cursor_save_critical.execute("UPDATE requests_for_info SET status = %s, re_evaluation_error = %s WHERE id = %s", ('re_evaluation_save_failed', re_evaluation_error_text, rfi_id))
                      conn_save_critical.commit()
                      print(f"Статус RFI {rfi_id} обновлен на 're_evaluation_save_failed'.")
                 except Exception as update_status_e:
                      print(f"Критическая ошибка при обновлении статуса RFI {rfi_id} на 're_evaluation_save_failed' после ошибки сохранения: {update_status_e}")
                 finally:
                      if cursor_save_critical:
                          try: cursor_save_critical.close()
                          except Exception as ce: print(f"Ошибка закрытия cursor_save_critical: {ce}")
                      if conn_save_critical:
                          try: conn_save_critical.close()
                          except Exception as ce: print(f"Ошибка закрытия conn_save_critical: {ce}")
            else:
                 print(f"Не удалось обновить статус RFI, ID был неизвестен во время ошибки сохранения.")

            raise db_e


    except Exception as e:
        re_evaluation_error_text_critical = f"Критическая ошибка в задаче: {type(e).__name__} - {e}."
        print(f"{re_evaluation_error_text_critical}")

        task_status = 'CRITICAL_ERROR'
        re_evaluation_error_text = (re_evaluation_error_text + "; ") if re_evaluation_error_text else ""
        re_evaluation_error_text += re_evaluation_error_text_critical

        final_rfi_status = 're_evaluation_failed'

        if 'rfi_id' in locals() and rfi_id is not None:
             try:
                 conn_critical = psycopg2.connect(DATABASE_URL)
                 cursor_critical = conn_critical.cursor()
                 cursor_critical.execute("UPDATE requests_for_info SET status = %s, re_evaluation_error = %s WHERE id = %s", (final_rfi_status, re_evaluation_error_text, rfi_id))
                 conn_critical.commit()
                 print(f"Статус RFI {rfi_id} обновлен на '{final_rfi_status}' из-за критической ошибки.")
             except Exception as e_rollback:
                 print(f"Критическая ошибка при обновлении статуса ошибки RFI {rfi_id} из-за критической ошибки: {e_rollback}")
             finally:
                if cursor_critical:
                    try: cursor_critical.close()
                    except Exception as ce: print(f"Ошибка закрытия cursor_critical: {ce}")
                if conn_critical:
                    try: conn_critical.close()
                    except Exception as ce: print(f"Ошибка закрытия conn_critical: {ce}")
        else:
             print(f"Не удалось обновить статус RFI, ID был неизвестен.")

        raise e


    finally:
        # Корректное закрытие ВСЕХ возможных соединений и курсоров
        if cursor_read:
            try: cursor_read.close()
            except Exception as e: print(f"Ошибка закрытия cursor_read: {e}")
        if conn_read:
            try: conn_read.close()
            except Exception as e: print(f"Ошибка закрытия conn_read: {e}")

        if cursor_save:
            try: cursor_save.close()
            except Exception as e: print(f"Ошибка закрытия cursor_save: {e}")
        if conn_save:
            try: conn_save.close()
            except Exception as e: print(f"Ошибка закрытия conn_save: {e}")

        if cursor_error_update:
            try: cursor_error_update.close()
            except Exception as e: print(f"Ошибка закрытия cursor_error_update: {e}")
        if conn_error_update:
            try: conn_error_update.close()
            except Exception as e: print(f"Ошибка закрытия conn_error_update: {ce}")

        if cursor_save_critical:
            try: cursor_save_critical.close()
            except Exception as e: print(f"Ошибка закрытия cursor_save_critical: {e}")
        if conn_save_critical:
            try: conn_save_critical.close()
            except Exception as ce: print(f"Ошибка закрытия conn_save_critical: {ce}")

        if cursor_critical:
            try: cursor_critical.close()
            except Exception as e: print(f"Ошибка закрытия cursor_critical: {e}")
        if conn_critical:
            try: conn_critical.close()
            except Exception as ce: print(f"Ошибка закрытия conn_critical: {ce}")

        print(f"Все соединения БД закрыты в блоке finally.")


    # Возвращаем результаты задачи
    rfi_id_for_return = rfi_id if 'rfi_id' in locals() else None

    if 'final_rfi_status' not in locals():
         final_rfi_status_for_return = 're_evaluation_failed'
    else:
         final_rfi_status_for_return = final_rfi_status

    print(f"Задача завершена. Статус Celery: {task_status}. Финальный статус RFI {rfi_id_for_return}: {final_rfi_status_for_return}. Ошибка: {re_evaluation_error_text}")

    return {"status": task_status,
            "analysis_id": analysis_id,
            "rfi_id": rfi_id_for_return,
            "new_score": new_score if task_status in ['SUCCESS', 'PARSING_SUCCESS', 'WARNING_PARSING', 'WARNING'] else None,
            "re_evaluation_timestamp": re_evaluation_timestamp.isoformat() if task_status in ['SUCCESS', 'PARSING_SUCCESS', 'WARNING_PARSING', 'WARNING'] and re_evaluation_timestamp else None,
            "new_summary_conclusion": new_summary_conclusion if task_status in ['SUCCESS', 'PARSING_SUCCESS', 'WARNING_PARSING', 'WARNING'] else None,
            "new_questions_count": len(new_questions) if new_questions is not None and isinstance(new_questions, list) and task_status in ['SUCCESS', 'PARSING_SUCCESS', 'WARNING_PARSING', 'WARNING'] else 0,
            "final_rfi_status": final_rfi_status_for_return,
            "re_evaluation_error": re_evaluation_error_text
            }

#  Маршруты Flask 
@app.route('/')
@login_required
def index():
    # Просто рендерим главную страницу, если пользователь авторизован
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    # Если пользователь уже вошел, перенаправляем на главную
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        # Проверяем заполнение всех полей
        if not username or not password or not confirm_password:
            flash('Все поля обязательны.', 'warning')
            return render_template('register.html', username=username)

        # Проверяем длину пароля
        if len(password) < 6:
            flash('Пароль должен быть не менее 6 символов.', 'warning')
            return render_template('register.html', username=username)

        # Проверяем совпадение паролей
        if password != confirm_password:
            flash('Пароли не совпадают.', 'warning')
            return render_template('register.html', username=username)

        conn = None
        cursor = None
        try:
            # Подключаемся к БД для проверки и регистрации
            conn = psycopg2.connect(DATABASE_URL)
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            # Проверяем, существует ли уже пользователь с таким именем
            cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()

            if user:
                flash('Имя пользователя уже занято.', 'warning')
                return render_template('register.html', username=username)

            # Хешируем пароль
            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

            # Вставляем нового пользователя в таблицу users
            cursor.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, hashed_password))
            conn.commit()

            # Если нет ошибок, показываем сообщение об успехе и перенаправляем на логин
            if not get_flashed_messages(category_filter=["danger", "warning"]):
                 flash('Регистрация успешна! Теперь вы можете войти.', 'success')
                 return redirect(url_for('login'))

        except (Psycopg2Error, Exception) as e: # Ловим ошибки БД и общие исключения
            print(f"Ошибка регистрации пользователя {username}: {e}")
            if conn:
                conn.rollback() # Откатываем изменения при ошибке
            flash(f'Произошла ошибка регистрации.', 'danger') # Более общее сообщение для пользователя
            return render_template('register.html', username=username)
        finally:
            # Закрываем курсор и соединение
            if cursor: cursor.close()
            if conn: conn.close()

    # Если метод GET, просто показываем страницу регистрации
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    # Если пользователь уже вошел, перенаправляем на главную
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Проверяем заполнение полей
        if not username or not password:
            flash('Пожалуйста, введите имя пользователя и пароль.', 'warning')
            return render_template('login.html', username=username)

        conn = None
        cursor = None
        try:
            # Подключаемся к БД для проверки учетных данных
            conn = psycopg2.connect(DATABASE_URL)
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

            # Ищем пользователя по имени
            cursor.execute("SELECT id, username, password_hash FROM users WHERE username = %s", (username,))
            user_data = cursor.fetchone()

            # Закрываем курсор и соединение сразу после получения данных
            cursor.close()
            conn.close()
            conn = None # Сбрасываем, чтобы finally не пытался закрыть уже закрытое

            # Проверяем, найден ли пользователь и совпадает ли хеш пароля
            if user_data and bcrypt.check_password_hash(user_data['password_hash'], password):
                # Создаем объект пользователя и логиним его
                user = User(id=user_data['id'], username=user_data['username'])
                login_user(user, remember=request.form.get('remember')) # Учитываем "запомнить меня"

                flash('Вход выполнен успешно!', 'success')
                # Перенаправляем на страницу, с которой пришел пользователь, или на главную
                next_page = request.args.get('next')
                return redirect(next_page or url_for('index'))
            else:
                # Если пользователь не найден или пароль неверный
                flash('Неверное имя пользователя или пароль.', 'danger')

        except (Psycopg2Error, Exception) as e: # Ловим ошибки БД и общие исключения
            print(f"Ошибка входа пользователя {username}: {e}")
            flash('Произошла ошибка при попытке входа.', 'danger') # Более общее сообщение для пользователя
        finally:
            if conn:
                conn.close()

    # Если метод GET или вход не удался, показываем страницу входа
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    # Выходим из системы
    logout_user()
    flash('Вы вышли из системы.', 'info')
    # Перенаправляем на страницу входа
    return redirect(url_for('login'))

@app.route('/delete_analysis/<int:analysis_id>', methods=['POST'])
@login_required
def delete_analysis(analysis_id):
    """
    Обрабатывает POST-запрос на удаление анализа и связанного файла.
    """
    user_id = current_user.id
    print(f"[INFO] Запрос на удаление анализа ID: {analysis_id} от пользователя: {user_id}")

    conn = None
    cursor = None
    try:
        # Подключаемся к БД
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Получаем информацию об анализе и файле, убеждаемся, что он принадлежит пользователю
        cursor.execute("""
            SELECT stored_filename, original_filename FROM analyses
            WHERE id = %s AND user_id = %s
        """, (analysis_id, user_id))
        analysis_data = cursor.fetchone()

        if analysis_data is None:
            flash("Анализ не найден или не принадлежит вам.", "danger")
            print(f"[WARN] Попытка удаления несуществующего анализа ID: {analysis_id} или не своего.")
            return redirect(url_for('history'))

        stored_filename = analysis_data['stored_filename']
        original_filename = analysis_data['original_filename']

        # Удаляем запись из таблицы analyses
        cursor.execute("DELETE FROM analyses WHERE id = %s", (analysis_id,))
        conn.commit()
        print(f"[INFO] Анализ ID: {analysis_id} удален из БД.")

        # Закрываем курсор после операции с БД
        cursor.close()
        cursor = None

        # Удаляем связанный файл резюме с диска, если он есть
        if stored_filename:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], stored_filename)
            # Дополнительная проверка безопасности: убеждаемся, что путь файла находится внутри UPLOAD_FOLDER
            if os.path.abspath(file_path).startswith(os.path.abspath(app.config['UPLOAD_FOLDER'])):
                 try:
                     os.remove(file_path)
                     print(f"[INFO] Файл резюме удален с диска: {stored_filename}")
                 except OSError as e:
                     print(f"[ERROR] Не удалось удалить файл {stored_filename} с диска: {e}")
                     # Ошибка удаления файла не должна блокировать удаление из БД
                     flash(f"Анализ удален, но не удалось удалить файл резюме '{original_filename or stored_filename}'.", "warning")
                 except Exception as e:
                      print(f"[ERROR] Неизвестная ошибка при удалении файла {stored_filename}: {e}")
                      flash(f"Анализ удален, но при удалении файла возникла ошибка.", "warning")
            else:
                 print(f"[SECURITY ALERT] Попытка удалить файл вне UPLOAD_FOLDER: {file_path}")
                 flash("Анализ удален, но возникла ошибка безопасности при попытке удалить файл.", "warning")


        flash(f"Анализ '{original_filename or 'без имени файла'}' успешно удален.", "success")

    except (Psycopg2Error, Exception) as e: # Ловим ошибки БД и общие исключения
        print(f"[ERROR] Ошибка при удалении анализа {analysis_id}: {e}")
        flash(f"Произошла ошибка при удалении анализа.", "danger")
        if conn:
            conn.rollback() # Откатываем изменения при ошибке
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

    # Перенаправляем пользователя обратно на страницу истории
    return redirect(url_for('history'))

@app.route('/analysis/<int:analysis_id>')
@login_required
def analysis_details(analysis_id):
    conn = None
    cursor = None
    analysis = None
    error_message = None


    try:
        conn = get_db_pg()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("""
            SELECT
                a.id, a.batch_id, a.user_id, a.original_filename, a.stored_filename,
                a.batch_timestamp AS upload_time,
                a.timestamp AS analysis_time,
                a.candidate_name, a.contacts_extracted, a.score,
                a.summary_conclusion AS summary,
                a.analysis_text AS raw_analysis_output,
                a.error_info,
                a.re_evaluation_score, a.re_evaluation_text, a.re_evaluation_timestamp,
                a.re_evaluation_summary_conclusion, a.re_evaluation_error,
                a.age, a.gender, a.salary_expectation,
                a.summary_conclusion,
                a.interview_recommendations AS interview_recommendations_html,
                a.job_description,

                -- Данные из requests_for_info (rfi)
                rfi.id AS rfi_id,
                rfi.status AS rfi_status,
                -- rfi.questions_json,
                -- rfi.answers_json,
                rfi.additional_info_text AS rfi_answers,
                rfi.token as rfi_token,
                rfi.re_evaluation_error as rfi_re_evaluation_error

            FROM analyses a
            LEFT JOIN requests_for_info rfi ON a.id = rfi.analysis_id
            WHERE a.id = %s AND a.user_id = %s;
        """, (analysis_id, current_user.id))

        result = cursor.fetchone()

        #  ДОБАВЛЕНЫ ОТЛАДОЧНЫЕ ВЫВОДЫ 
        print(f"DEBUG: analysis_id={analysis_id}")
        print(f"DEBUG: Тип объекта 'result' после fetchone(): {type(result)}")
        if result is not None:
            if hasattr(result, 'keys'):
                 print(f"DEBUG: Ключи в объекте 'result': {list(result.keys())}")
                 # Проверяем ключи, которые теперь должны быть доступны
                 print(f"DEBUG: result['id']: {result.get('id')}")
                 print(f"DEBUG: result['upload_time']: {result.get('upload_time')}")
                 print(f"DEBUG: result['analysis_time']: {result.get('analysis_time')}")
                 print(f"DEBUG: result['summary']: {result.get('summary')}")
                 print(f"DEBUG: result['raw_analysis_output']: {result.get('raw_analysis_output')}")
                 print(f"DEBUG: result['interview_recommendations_html']: {result.get('interview_recommendations_html')}")
                 print(f"DEBUG: result['job_description']: {result.get('job_description')}")
                 print(f"DEBUG: result['rfi_status']: {result.get('rfi_status')}") # Используем rfi_status из запроса
                 print(f"DEBUG: result['rfi_answers']: {result.get('rfi_answers')}")
                 print(f"DEBUG: result.get('questions_json'): {result.get('questions_json')}") # Проверяем, что вернулось (ожидаем None)
                 print(f"DEBUG: result.get('answers_json'): {result.get('answers_json')}") # Проверяем, что вернулось (ожидаем None)

            else:
                 print(f"DEBUG: Объект 'result' (не DictRow): {result}")
        else:
            print("DEBUG: Объект 'result' равен None (анализ не найден).")
        #  КОНЕЦ ОТЛАДОЧНЫХ ВЫВОДОВ 


        if not result:
            flash('Анализ не найден или у вас нет прав доступа.', 'danger')
            analysis = None # analysis останется None, что приведет к редиректу

        else:
            analysis = dict(result)

            re_evaluation_text_raw = analysis.get('re_evaluation_text')
            if re_evaluation_text_raw and isinstance(re_evaluation_text_raw, str):
                 try:
                      # Применяем Markdown с расширениями к тексту повторного анализа
                      analysis['re_evaluation_html'] = markdown.markdown(re_evaluation_text_raw, extensions=['nl2br', 'fenced_code', 'tables', 'extra'])
                 except Exception as md_err:
                      print(f"[WARNING] Ошибка Markdown для повторного анализа ID {analysis_id}: {md_err}")
                      # В случае ошибки Markdown, просто экранируем HTML-символы
                      analysis['re_evaluation_html'] = escapeHTML(re_evaluation_text_raw or "Ошибка форматирования повторного анализа.")
            else:
                 analysis['re_evaluation_html'] = None # Устанавливаем None, если текста нет

            rfi_answers_raw = analysis.get('rfi_answers')
            if rfi_answers_raw and isinstance(rfi_answers_raw, str):
                 try:
                     # Используем Markdown с расширениями для ответов кандидата
                     analysis['rfi_answers_html'] = markdown.markdown(rfi_answers_raw, extensions=['nl2br', 'fenced_code', 'tables', 'extra'])
                 except Exception as rfi_md_err:
                     print(f"[WARNING] Ошибка Markdown для RFI ответов анализа {analysis_id}: {rfi_md_err}")
                     analysis['rfi_answers_html'] = escapeHTML(rfi_answers_raw or "Ошибка форматирования ответов.")
            else:
                 analysis['rfi_answers_html'] = None

            #  Обработка и форматирование контактов 
            contacts_extracted_raw = analysis.get('contacts_extracted')
            contacts_display = 'Не извлечены'

            if contacts_extracted_raw and isinstance(contacts_extracted_raw, str):
                 contacts_display = html.escape(contacts_extracted_raw).replace('\n', '<br>').replace(';', '<br>').replace(',', '<br>')
            elif contacts_extracted_raw is not None:
                 print(f"[WARNING] Неожиданный тип данных для contacts_extracted в analysis_id {analysis_id}: {type(contacts_extracted_raw)}")
                 contacts_display = f'Неизвестный формат контактов ({type(contacts_extracted_raw).__name__})'

            analysis['contacts_display'] = contacts_display
            analysis['candidate_has_email'] = '@' in (contacts_extracted_raw or '')

            questions_json_raw = analysis.get('questions_json') # Получаем значение, может быть None
            answers_json_raw = analysis.get('answers_json') # Получаем значение, может быть None

            analysis['questions_json'] = json.loads(questions_json_raw) if questions_json_raw and isinstance(questions_json_raw, str) else []
            analysis['answers_json'] = json.loads(answers_json_raw) if answers_json_raw and isinstance(answers_json_raw, str) else {}


            # Получаем ошибку RFI из словаря analysis по ключу 'rfi_re_evaluation_error'
            rfi_re_evaluation_error_value = analysis.get('rfi_re_evaluation_error') # Получаем значение, может быть None
            # Вы можете передать эту переменную в render_template или использовать analysis.get() там напрямую

            # Преобразуем rfi_answers (текст) в HTML
            rfi_answers_raw = analysis.get('rfi_answers')
            if rfi_answers_raw and isinstance(rfi_answers_raw, str):
                 analysis['rfi_answers_html'] = html.escape(rfi_answers_raw).replace('\n', '<br>')
            else:
                 analysis['rfi_answers_html'] = None

            analysis_html_output_value = analysis.get('analysis_html_output')
            raw_analysis_output_value = analysis.get('raw_analysis_output')
            error_info_value = analysis.get('error_info')

            if analysis_html_output_value:
                 analysis['analysis_html'] = analysis_html_output_value
                 analysis['analysis_text'] = raw_analysis_output_value
            elif raw_analysis_output_value and not error_info_value:
                 try:
                      # Применяем Markdown к сырому тексту для отображения
                      analysis['analysis_html'] = markdown.markdown(raw_analysis_output_value, extensions=['nl2br', 'fenced_code', 'tables', 'extra'])
                      analysis['analysis_text'] = raw_analysis_output_value
                 except Exception as md_err:
                      print(f"[WARNING] Ошибка Markdown для первичного анализа ID {analysis_id}: {md_err}")
                      analysis['analysis_html'] = escapeHTML(f"Ошибка обработки Markdown: {md_err}\n\n{raw_analysis_output_value}").replace('\n', '<br>')
                      analysis['analysis_text'] = raw_analysis_output_value
            elif error_info_value: # Если есть общая ошибка анализа
                 analysis['analysis_html'] = escapeHTML(f"Ошибка анализа: {error_info_value}").replace('\n', '<br>')
                 analysis['analysis_text'] = error_info_value
            else: # Если нет ни HTML, ни сырого текста, ни ошибки
                 analysis['analysis_html'] = "<p>Нет данных анализа.</p>"
                 analysis['analysis_text'] = "Нет данных анализа."

            #  Преобразование объектов времени в формат строк 
            # Получаем объекты datetime из словаря analysis по ключам 'upload_time' и 'analysis_time'
            upload_time_obj = analysis.get('upload_time')
            analysis_time_obj = analysis.get('analysis_time')

            if upload_time_obj and isinstance(upload_time_obj, datetime.datetime):
                 analysis['upload_time_formatted'] = upload_time_obj.strftime('%Y-%m-%d %H:%M:%S') # Или другой формат
            else:
                 analysis['upload_time_formatted'] = 'N/A' # Или другое значение по умолчанию

            if analysis_time_obj and isinstance(analysis_time_obj, datetime.datetime):
                 analysis['analysis_time_formatted'] = analysis_time_obj.strftime('%Y-%m-%d %H:%M:%S') # Или другой формат
            else:
                 analysis['analysis_time_formatted'] = 'N/A' # Или другое значение по умолчанию
            #  Конец блока форматирования времени 


    except Psycopg2Error as db_err:
        print(f"[ERROR] Ошибка базы данных при загрузке деталей анализа {analysis_id}: {db_err}")
        flash(f"Ошибка базы данных при загрузке деталей анализа: {db_err}", 'danger')
        error_message = f"Ошибка базы данных при загрузке деталей анализа: {db_err}"
        analysis = None

    except Exception as e:
        print(f"[ERROR] Непредвиденная ошибка при загрузке деталей анализа {analysis_id}: {e}")
        flash(f"Произошла ошибка при загрузке деталей анализа: {e}", 'danger')
        error_message = f"Произошла ошибка при загрузке деталей анализа: {e}"
        analysis = None

    finally:
        pass


    # Если анализ не был найден или произошла ошибка
    if analysis is None:
         return redirect(url_for('history'))

    # Передаем данные в шаблон analysis_details.html
    return render_template('analysis_details.html',
                           analysis=analysis, # Словарь analysis содержит все данные под нужными ключами (с учетом алиасов)
                           job_description=analysis.get('job_description'),
                           questions=analysis.get('questions_json', []), # Может быть None, если колонки нет в БД
                           answers=analysis.get('answers_json', {}), # Может быть None, если колонки нет в БД
                           rfi_re_evaluation_error=analysis.get('rfi_re_evaluation_error') # Может быть None, если нет RFI
                           )


@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    # Получаем ID текущего пользователя
    user_id = current_user.id
    # Получаем список загруженных файлов и описание вакансии
    uploaded_files = request.files.getlist('resume_file')
    job_description = request.form.get('job_description', '').strip()

    # Генерируем уникальный ID для этой пачки анализов и запоминаем время
    batch_id = str(uuid.uuid4())
    batch_timestamp = datetime.datetime.now()
    tasks_submitted = 0 # Счетчик успешно поставленных задач

    # Получаем настройки AI из переменных окружения
    ai_service = os.environ.get('AI_SERVICE', 'gemini').lower() # Какой AI сервис используем (gemini или deepseek)
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY")
    gemini_model = os.environ.get('GEMINI_MODEL_NAME', 'gemini-2.0-flash') # Модель для Gemini
    deepseek_model = os.environ.get('DEEPSEEK_MODEL_NAME', 'deepseek-chat') # Модель для DeepSeek

    print(f"--- DEBUG [Analyze Route]: Используется AI сервис: '{ai_service}'")

    # Проверяем, что файлы выбраны и описание вакансии заполнено
    if not uploaded_files or all(f.filename == '' for f in uploaded_files):
        flash("Пожалуйста, выберите файлы резюме для анализа.", "warning")
        return redirect(url_for('index'))

    if not job_description:
        flash("Пожалуйста, заполните описание вакансии.", "warning")
        return redirect(url_for('index'))

    # Проверяем наличие API ключа для выбранного сервиса
    current_api_key = gemini_api_key if ai_service == 'gemini' else deepseek_api_key
    if not current_api_key:
        flash(f"Ошибка: API ключ для выбранного AI сервиса ('{ai_service}') не найден в настройках сервера.", "danger")
        return redirect(url_for('index'))

    # Проверяем, что выбранный AI сервис поддерживается
    if ai_service not in ['gemini', 'deepseek']:
        flash(f"Ошибка: Неизвестный или неподдерживаемый AI сервис '{ai_service}'.", "danger")
        return redirect(url_for('index'))


    # Перебираем все загруженные файлы
    for file in uploaded_files:
        # Проверяем, что файл существует и не пустой
        if file and file.filename != '':
            original_filename = file.filename
            # Очищаем имя файла для безопасности
            safe_original_filename = secure_filename(original_filename)

            # Если имя файла после очистки пустое, пропускаем
            if not safe_original_filename:
                continue

            # Получаем расширение файла
            _, extension = os.path.splitext(safe_original_filename)
            # Генерируем уникальное имя для хранения файла на сервере
            stored_filename = f"{uuid.uuid4()}{extension}"
            # Формируем полный путь к файлу в папке загрузок
            filepath = os.path.abspath(os.path.join(app.config['UPLOAD_FOLDER'], stored_filename))

            # Разрешенные форматы файлов
            allowed_extensions = ['.pdf', '.docx']

            # Проверяем, что расширение файла разрешено
            if extension.lower() in allowed_extensions:
                try:
                    # Сохраняем файл на диск
                    file.save(filepath)
                    print(f"Файл '{original_filename}' сохранен как '{stored_filename}'. Готовим задачу для Celery.")

                    # Отправляем задачу на анализ в Celery.
                    # Передаем все нужные данные, включая оба API ключа и модели,
                    # чтобы задача сама выбрала нужные по ai_service.
                    process_resume_task.delay(
                        filepath=filepath,
                        job_description=job_description,
                        ai_service=ai_service,
                        gemini_api_key=gemini_api_key,
                        deepseek_api_key=deepseek_api_key,
                        gemini_model=gemini_model,
                        deepseek_model=deepseek_model,
                        batch_id=batch_id,
                        batch_timestamp=batch_timestamp,
                        original_filename=original_filename,
                        stored_filename=stored_filename,
                        user_id=user_id
                    )
                    tasks_submitted += 1 # Увеличиваем счетчик успешно поставленных задач

                except Exception as e:
                    # Если что-то пошло не так при сохранении или постановке задачи
                    print(f"Ошибка при обработке файла {original_filename}: {e}")
                    flash(f"Произошла ошибка при обработке файла '{original_filename}'.", "warning")
            else:
                # Если формат файла не разрешен
                print(f"Файл {original_filename} пропущен - неподдерживаемый формат.")
                flash(f"Файл '{original_filename}' пропущен, т.к. имеет неподдерживаемый формат. Разрешены .pdf и .docx.", "info")

    # Если ни одна задача не была поставлена (например, все файлы были неправильного формата)
    if tasks_submitted == 0:
        flash("Не найдено ни одного файла подходящего формата для анализа.", "warning")
        return redirect(url_for('index'))

    # Перенаправляем пользователя на страницу результатов,
    # передавая ID пачки и общее количество задач для отслеживания прогресса.
    # Также передаем job_description, чтобы показать его на странице результатов.
    return redirect(url_for('results_page', batch_id=batch_id, total_tasks=tasks_submitted, job_description=job_description))

@app.route('/results/<batch_id>')
@login_required
def results_page(batch_id):
    """
    Показывает страницу с результатами анализа для конкретной пачки.
    Получает общее количество задач и описание вакансии.
    """
    # Получаем общее количество задач из параметров URL. По умолчанию 0.
    total_tasks = request.args.get('total_tasks', 0, type=int)
    # Получаем описание вакансии из параметров URL. Убираем пробелы по краям.
    job_description = request.args.get('job_description', '').strip()

    conn = None # Переменная для соединения с БД

    try:
        # Подключаемся к базе данных PostgreSQL
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # Пытаемся получить описание вакансии из первой записи этой пачки в БД.
        # Это нужно, если страница результатов открывается не сразу после запуска анализа,
        # и описание не передалось через URL.
        # Фильтруем по batch_id и user_id!
        cursor.execute("""
            SELECT job_description FROM analyses
            WHERE batch_id = %s AND user_id = %s
            ORDER BY timestamp ASC LIMIT 1; -- Берем первое описание по времени
        """, (batch_id, current_user.id)) # Передаем batch_id и user_id
        batch_info = cursor.fetchone()

        # Закрываем курсор
        cursor.close()
        cursor = None

        # Если описание найдено в БД и оно не пустое, используем его
        if batch_info and batch_info[0]:
            job_description = batch_info[0]
            print(f"[INFO] Описание вакансии для пачки {batch_id} получено из БД.")
        elif not job_description:
             # Если описание не было в URL и не найдено в БД
             print(f"[WARN] Описание вакансии для пачки {batch_id} не найдено ни в URL, ни в БД.")


    except (Psycopg2Error, Exception) as e: # Ловим ошибки БД и общие исключения
        print(f"[ERROR] Ошибка при попытке получить job_desc для пачки {batch_id} из БД: {e}")
        # Если произошла ошибка, оставляем job_description как есть (из URL или пустую)
        flash("Не удалось загрузить описание вакансии из базы данных.", "warning")
    finally:
        if conn:
            conn.close()

    # Если описание вакансии все еще пустое (не было в URL и не нашлось в БД),
    # устанавливаем стандартное сообщение
    if not job_description:
        job_description = "Описание вакансии не найдено для этой пачки."

    # Рендерим шаблон страницы результатов, передавая ID пачки,
    # общее количество задач и описание вакансии.
    return render_template('results_async.html', batch_id=batch_id, total_tasks=total_tasks, job_description=job_description)


@app.route('/batch_status/<batch_id>')
@login_required
def batch_status(batch_id):
    """
    Предоставляет JSON-статус выполнения анализа для конкретной пачки.
    Используется для асинхронного обновления страницы результатов.
    """
    results = [] # Список для результатов анализа
    best_candidate = None # Переменная для данных лучшего кандидата
    status = 'PROCESSING' # Изначальный статус пачки
    # Получаем общее ожидаемое количество задач из параметров URL
    total_expected = request.args.get('total_tasks', 0, type=int)
    processed_count = 0 # Счетчик уже обработанных результатов

    conn = None # Переменная для соединения с БД
    cursor = None # Переменная для курсора

    try:
        # Подключаемся к базе данных PostgreSQL
        conn = psycopg2.connect(DATABASE_URL)
        # Используем DictCursor для удобного доступа к данным по именам колонок
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Выбираем все записи анализа для данной пачки и текущего пользователя.
        # Сортируем по оценке (убывание), затем по времени (возрастание) для
        # надежного определения "лучшего" кандидата.
        cursor.execute("""
            SELECT
                id, timestamp, original_filename, stored_filename, candidate_name,
                contacts_extracted, age, gender, summary_conclusion, analysis_text,
                score, error_info, re_evaluation_score, re_evaluation_timestamp,
                re_evaluation_text, re_evaluation_summary_conclusion, re_evaluation_error,
                interview_recommendations -- Добавил все нужные поля
            FROM analyses
            WHERE batch_id = %s AND user_id = %s
            ORDER BY score DESC NULLS LAST, timestamp ASC; -- NULLS LAST для score, чтобы записи без оценки были в конце
        """, (batch_id, current_user.id)) # Передаем batch_id и user_id

        raw_results = cursor.fetchall() # Получаем все строки
        # Закрываем курсор и соединение сразу после получения данных
        cursor.close()
        cursor = None
        conn.close()
        conn = None # Сбрасываем, чтобы finally не пытался закрыть уже закрытое

        processed_results = []
        # Обрабатываем каждую строку результата
        for row in raw_results:
            item = dict(row) # Преобразуем строку DictCursor в обычный словарь

            # Обрабатываем текстовые поля с помощью Markdown для HTML
            # Используем nl2br для переносов строк, fenced_code, tables, extra для расширенного Markdown
            fields_to_markdown = {
                'analysis_text': 'analysis_html',
                're_evaluation_text': 're_evaluation_html',
                'interview_recommendations': 'interview_recommendations_html',
            }
            for text_field, html_field in fields_to_markdown.items():
                 text_content = item.get(text_field)
                 if text_content:
                      try:
                           item[html_field] = markdown.markdown(text_content or "", extensions=['nl2br', 'fenced_code', 'tables', 'extra'])
                      except Exception as md_err:
                           print(f"[WARNING] Ошибка Markdown для поля '{text_field}' анализа {item.get('id')}: {md_err}")
                           # В случае ошибки Markdown, просто экранируем HTML-символы
                           item[html_field] = escapeHTML(text_content or "Ошибка форматирования.")
                 else:
                      item[html_field] = '' # Пустая строка, если текста нет


            # Парсим timestamp'ы в ISO формат для удобства на фронтенде
            for ts_key in ['timestamp', 're_evaluation_timestamp']:
                 ts_value = item.get(ts_key)
                 if isinstance(ts_value, datetime.datetime):
                      item[ts_key] = ts_value.isoformat() # Если уже datetime, конвертируем в ISO
                 elif isinstance(ts_value, str) and ts_value:
                      try:
                           # Пробуем парсить строку, если это строка
                           item[ts_key] = parser.parse(ts_value).isoformat()
                      except Exception:
                           item[ts_key] = None # Если не получилось, ставим None
                 else:
                      item[ts_key] = None # Если None или другой тип, ставим None


            processed_results.append(item) # Добавляем обработанный результат в список

        results = processed_results
        processed_count = len(results) # Обновляем счетчик обработанных результатов

        # Определяем лучшего кандидата
        best_candidate_item = None
        # Фильтруем результаты, чтобы найти кандидата с наивысшей оценкой
        # Учитываем только успешно проанализированные записи (есть оценка и текст анализа)
        valid_results_for_best = [res for res in results
                                 if res.get('score') is not None # Оценка должна быть
                                 and isinstance(res.get('analysis_text'), str) # Текст анализа должен быть строкой
                                 and not res.get('analysis_text', '').startswith(("ОШИБКА", "КРИТИЧЕСКАЯ")) # Не должен быть сообщением об ошибке
                                 and res.get('analysis_html')] # Должен быть сгенерирован HTML

        if valid_results_for_best:
             # Поскольку мы уже отсортировали по score DESC, первый элемент в valid_results_for_best - лучший
             # Находим полный объект лучшего кандидата из исходного списка processed_results по его id
             best_candidate_item = next((res for res in processed_results if res['id'] == valid_results_for_best[0]['id']), None)
             print(f"[INFO] Найден лучший кандидат для пачки {batch_id}: ID {best_candidate_item.get('id')}, Оценка {best_candidate_item.get('score')}")
        else:
             print(f"[INFO] Не найдено подходящих результатов для определения лучшего кандидата в пачке {batch_id}.")


        # Определяем текущий статус пачки
        if total_expected > 0 and processed_count >= total_expected:
             status = 'COMPLETE' # Завершено, если обработано столько же или больше, чем ожидалось
             print(f"[INFO] Статус пачки {batch_id}: COMPLETE (Обработано: {processed_count}, Ожидалось: {total_expected})")
        elif total_expected == 0 and processed_count > 0:
             # Если общее количество задач не было передано (total_expected=0),
             # считаем завершенным, если есть хотя бы один результат.
             status = 'COMPLETE'
             print(f"[INFO] Статус пачки {batch_id}: COMPLETE (total_expected=0, есть результаты)")
        elif total_expected == 0 and processed_count == 0:
             # Если total_expected=0 и нет результатов (все файлы были пропущены или с ошибками парсинга)
             status = 'COMPLETE' # Считаем завершенным, но пустым
             print(f"[INFO] Статус пачки {batch_id}: COMPLETE (total_expected=0, нет результатов)")
        # Если total_expected > 0 и processed_count < total_expected, статус остается 'PROCESSING'


        # Возвращаем JSON-ответ с текущим статусом, количеством обработанных,
        # общим ожидаемым количеством, списком всех результатов и данными лучшего кандидата.
        return jsonify({
            'status': status,
            'processed_count': processed_count,
            'total_tasks': total_expected,
            'results': results,
            'best_candidate': best_candidate_item
        })

    except (Psycopg2Error, Exception) as e: # Ловим ошибки БД и любые другие
        print(f"[ERROR] Критическая ошибка при получении статуса пачки {batch_id}: {e}")
        # В случае ошибки, возвращаем статус ошибки и сообщение
        return jsonify({'status': 'ERROR', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/history', methods=['GET'])
@login_required
def history():
    """
    Показываю историю анализов резюме для текущего пользователя.
    Есть фильтры, сортировка и пагинация.
    Загружаю также данные по запросам доп. инфо (RFI) и повторной оценке.
    """
    user_id = current_user.id # ID текущего пользователя
    conn = None # Переменная для соединения с БД
    cursor = None # Переменная для курсора
    history_data = [] # Список для данных истории
    total_items = 0 # Общее количество записей
    total_pages = 0 # Общее количество страниц
    error_message = None # Сообщение об ошибке, если что-то пошло не так

    try:
        # Подключаемся к базе данных PostgreSQL
        conn = psycopg2.connect(DATABASE_URL)
        # Используем DictCursor, чтобы получать строки как словари - очень удобно
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        #  Собираем параметры фильтрации и сортировки из URL запроса 
        search_term = request.args.get('search', '').strip() # Строка поиска
        date_from = request.args.get('date_from') # Дата "от"
        date_to = request.args.get('date_to') # Дата "до"
        age_from = request.args.get('age_from', type=int) # Возраст "от"
        age_to = request.args.get('age_to', type=int) # Возраст "до"
        gender = request.args.get('gender') # Пол ('Мужской', 'Женской', '__none__', '' - любой)

        sort_by = request.args.get('sort_by', 'batch_timestamp') # Поле для сортировки (по умолчанию по дате пачки)
        sort_dir = request.args.get('sort_dir', 'desc') # Направление сортировки (по умолчанию убывание)

        # Параметры пагинации
        page = request.args.get('page', 1, type=int) # Текущая страница (по умолчанию 1)
        # Убедимся, что номер страницы не меньше 1
        if page < 1:
            page = 1

        per_page = 10 # Сколько записей показывать на одной странице

        #  Формируем условия WHERE для SQL запроса (фильтрация) 
        where_clauses = ["a.user_id = %s"] # Всегда фильтруем по текущему пользователю
        params = [user_id] # Список параметров для запроса

        if search_term:
            # Ищем совпадение в имени кандидата, имени файла или контактах (без учета регистра в PostgreSQL)
            where_clauses.append("(a.candidate_name ILIKE %s OR a.original_filename ILIKE %s OR a.contacts_extracted ILIKE %s)")
            params.extend([f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"])

        if date_from:
            # Фильтр по дате пачки (только по дате, без времени)
            where_clauses.append("a.batch_timestamp::date >= %s")
            params.append(date_from)

        if date_to:
            # Фильтр по дате пачки (только по дате, без времени)
            where_clauses.append("a.batch_timestamp::date <= %s")
            params.append(date_to)

        if age_from is not None: # Проверяем, что age_from не None
             where_clauses.append("a.age >= %s")
             params.append(age_from)

        if age_to is not None: # Проверяем, что age_to не None
             where_clauses.append("a.age <= %s")
             params.append(age_to)

        if gender: # Если пол выбран (не "Любой")
             if gender == '__none__': # Специальное значение для "Не указан"
                  where_clauses.append("(a.gender IS NULL OR a.gender = '')")
             else:
                  where_clauses.append("a.gender = %s")
                  params.append(gender)


        #  Формируем ORDER BY для SQL запроса (сортировка) 
        # Проверяем, что поле сортировки допустимо
        allowed_sort_fields = ['batch_timestamp', 'timestamp', 'candidate_name', 'score', 'age', 'original_filename', 'job_description', 'contacts_extracted', 'gender', 're_evaluation_score', 're_evaluation_timestamp']
        if sort_by not in allowed_sort_fields:
             sort_by = 'batch_timestamp' # Если поле недопустимо, сортируем по умолчанию

        # Определяем направление сортировки в SQL
        sort_dir_sql = 'DESC' if sort_dir.lower() == 'desc' else 'ASC'

        # Составляем clause для ORDER BY.
        # Для полей с оценкой или датой повторной оценки используем NULLS LAST/FIRST
        # чтобы записи без этих данных были в конце (при DESC) или в начале (при ASC)
        # Добавляем вторичные сортировки для стабильного порядка
        if sort_by == 're_evaluation_timestamp':
             # Сортируем по дате повторной оценки, потом по дате пачки, потом по дате первичного анализа
             order_by_clause = f"a.re_evaluation_timestamp {sort_dir_sql} NULLS LAST, a.batch_timestamp DESC, a.timestamp DESC"
        elif sort_by == 're_evaluation_score':
             # Сортируем по оценке повторной оценки, потом по дате повторной оценки, потом по дате пачки, потом по дате первичного анализа
             order_by_clause = f"a.re_evaluation_score {sort_dir_sql} NULLS LAST, a.re_evaluation_timestamp DESC NULLS LAST, a.batch_timestamp DESC, a.timestamp DESC"
        elif sort_by == 'score':
             # Сортируем по первичной оценке, потом по дате пачки, потом по дате первичного анализа
             order_by_clause = f"a.score {sort_dir_sql} NULLS LAST, a.batch_timestamp DESC, a.timestamp DESC"
        else:
             # Для остальных полей сортируем по выбранному полю, потом по дате пачки, потом по дате первичного анализа
             order_by_clause = f"a.{sort_by} {sort_dir_sql}, a.batch_timestamp DESC, a.timestamp DESC"


        #  Подготовка SQL запроса для получения данных с пагинацией 
        # Сначала считаем общее количество записей, удовлетворяющих фильтрам
        count_sql = f"SELECT COUNT(*) FROM analyses a WHERE {' AND '.join(where_clauses)}"
        cursor.execute(count_sql, params) # Выполняем запрос с параметрами фильтрации
        total_items = cursor.fetchone()[0] # Получаем количество

        # Рассчитываем общее количество страниц
        total_pages = math.ceil(total_items / per_page) if total_items > 0 else 1

        # Корректируем номер текущей страницы, если он больше общего количества страниц
        if page > total_pages and total_items > 0:
             print(f"[DEBUG] Запрошена страница {page}, всего страниц {total_pages} ({total_items} записей). Перенаправление на {total_pages}.")
             # Перенаправляем пользователя на последнюю страницу, сохраняя все текущие параметры фильтрации/сортировки
             redirect_url_args = request.args.to_dict() # Получаем все аргументы запроса как словарь
             redirect_url_args['page'] = total_pages # Меняем номер страницы
             # Возвращаем редирект на URL с обновленными параметрами
             return redirect(url_for('history', **redirect_url_args))


        # Рассчитываем смещение (OFFSET) для текущей страницы
        offset = (page - 1) * per_page
        if offset < 0: offset = 0 # На всякий случай, если page стал <= 0


        #  ОСНОВНОЙ SQL ЗАПРОС ДЛЯ ПОЛУЧЕНИЯ ДАННЫХ ИСТОРИИ 
        # Выбираем все нужные поля из таблицы analyses и объединяем с requests_for_info (LEFT JOIN)
        sql = f"""
            SELECT
                a.id, a.batch_id, a.batch_timestamp, a.timestamp, a.original_filename, a.stored_filename,
                a.candidate_name, a.contacts_extracted, a.age, a.gender, a.summary_conclusion,
                a.job_description, a.analysis_text, a.score,
                a.re_evaluation_score,
                a.re_evaluation_text,
                a.re_evaluation_timestamp,
                a.re_evaluation_summary_conclusion,
                a.re_evaluation_error,
                rfi.id as rfi_id,
                rfi.status AS rfi_status,
                rfi.additional_info_text AS rfi_answers,
                rfi.token as rfi_token,
                rfi.re_evaluation_error as rfi_re_evaluation_error -- Ошибка повторной оценки из RFI
            FROM
                analyses a
            LEFT JOIN requests_for_info rfi ON a.id = rfi.analysis_id
            WHERE {' AND '.join(where_clauses)} -- Применяем условия фильтрации
            ORDER BY {order_by_clause} -- Применяем сортировку
            LIMIT %s OFFSET %s; -- Применяем пагинацию
        """
        # Собираем все параметры: сначала параметры фильтрации, потом параметры пагинации
        query_params = params + [per_page, offset]

        cursor.execute(sql, query_params) # Выполняем запрос
        raw_history_data = cursor.fetchall() # Получаем все строки
        # Закрываем курсор и соединение сразу после получения данных
        cursor.close()
        cursor = None
        conn.close()
        conn = None # Сбрасываем, чтобы finally не пытался закрыть уже закрытое


        #  Обработка и форматирование данных для передачи в шаблон 
        history_data = []

        for row in raw_history_data:
            item = dict(row) # Преобразуем строку DictCursor в обычный словарь

            # Форматируем timestamp'ы в объекты datetime
            # PostgreSQL возвращает timestamp как datetime объекты напрямую, если тип колонки timestamp/timestamptz
            # Если вдруг они приходят как строки, то парсим
            for ts_key in ['timestamp', 'batch_timestamp', 're_evaluation_timestamp']:
                 ts_value = item.get(ts_key)
                 if isinstance(ts_value, str) and ts_value:
                      try:
                           # Пробуем парсить строку, если это строка
                           item[ts_key] = parser.parse(ts_value)
                      except Exception:
                           item[ts_key] = None # Если не получилось, ставим None


            # Форматируем contacts_extracted для отображения с переносами строк
            if item.get('contacts_extracted'): # Используем .get() для безопасности
                 # Разбиваем на строки по запятой или точке с запятой
                 contacts_list = re.split(r'[;,]\s*', item['contacts_extracted'])
                 item['contacts_display'] = '<br>'.join(contacts_list)
            else:
                 item['contacts_display'] = '-'

            # Обработка текста анализа для HTML (первичный анализ)
            try:
                analysis_text_raw = item.get('analysis_text', '')
                # Используем Markdown только если текст не начинается с маркеров ошибок
                if analysis_text_raw and not analysis_text_raw.startswith(("ОШИБКА", "КРИТИЧЕСКАЯ")):
                    item['analysis_html'] = markdown.markdown(analysis_text_raw, extensions=['nl2br', 'fenced_code', 'tables', 'extra'])
                else:
                     # Если ошибка или пусто, просто экранируем HTML-символы
                     item['analysis_html'] = escapeHTML(analysis_text_raw or "-")

            except Exception as md_err:
                print(f"[WARNING] Ошибка Markdown для первичного анализа ID {item.get('id')}: {md_err}")
                # В случае ошибки Markdown, выводим сообщение об ошибке и сырой текст
                item['analysis_html'] = escapeHTML(f"Ошибка обработки первичного анализа: {md_err}\n\n{item.get('analysis_text', '')}")


            # Обработка текста повторного анализа для HTML
            try:
                re_evaluation_text_raw = item.get('re_evaluation_text', '')
                re_evaluation_error_item = item.get('re_evaluation_error') # Ошибка из analyses
                rfi_re_evaluation_error_item = item.get('rfi_re_evaluation_error') # Ошибка из requests_for_info (если есть)

                # Приоритет вывода ошибки: сначала ошибка из RFI, потом из analyses
                if rfi_re_evaluation_error_item:
                    item['re_evaluation_html'] = escapeHTML(f"Ошибка повторной оценки (запрос RFI): {rfi_re_evaluation_error_item}")
                elif re_evaluation_error_item:
                    item['re_evaluation_html'] = escapeHTML(f"Ошибка повторной оценки (анализ): {re_evaluation_error_item}")
                elif re_evaluation_text_raw:
                    # Используем Markdown для текста повторного анализа
                    item['re_evaluation_html'] = markdown.markdown(re_evaluation_text_raw, extensions=['nl2br', 'fenced_code', 'tables', 'extra'])
                else:
                     item['re_evaluation_html'] = "-" # Если нет текста и нет ошибок

            except Exception as md_err:
                print(f"[WARNING] Ошибка Markdown для повторного анализа ID {item.get('id')}: {md_err}")
                # В случае ошибки Markdown, выводим сообщение об ошибке и сырой текст или ошибку
                error_text = item.get('re_evaluation_text') or item.get('re_evaluation_error') or item.get('rfi_re_evaluation_error') or ""
                item['re_evaluation_html'] = escapeHTML(f"Ошибка обработки повторного анализа: {md_err}\n\n{error_text}")

            # Обработка ответов кандидата для HTML (если есть)
            item['rfi_answers_html'] = escapeHTML(item.get('rfi_answers') or "-")


            history_data.append(item) # Добавляем обработанный элемент в список истории


    except Psycopg2Error as e: # Ловим специфические ошибки PostgreSQL
        print(f"[ERROR] Ошибка базы данных при загрузке истории: {e}")
        error_message = f"Произошла ошибка базы данных при загрузке истории: {e}"
        history_data = [] # Очищаем данные при ошибке
        total_items = 0
        total_pages = 1
        page = 1 # Сбрасываем пагинацию

    except Exception as e: # Ловим любые другие неожиданные ошибки
        print(f"[ERROR] Критическая ошибка при загрузке истории: {e}")
        error_message = f"Произошла критическая ошибка при загрузке истории: {e}"
        history_data = [] # Очищаем данные при ошибке
        total_items = 0
        total_pages = 1
        page = 1 # Сбрасываем пагинацию

    finally:
        if conn:
             conn.close()


    #  Передаем данные в шаблон 
    # Передаем все текущие аргументы запроса, чтобы сохранить состояние фильтрации/сортировки
    request_args_dict = request.args.to_dict()

    # Рендерим шаблон, передавая все собранные данные
    return render_template('history.html',
                           history_data=history_data,           # Список записей истории
                           page=page,                           # Текущий номер страницы
                           total_pages=total_pages,             # Общее количество страниц
                           total_items=total_items,             # Общее количество записей
                           error_message=error_message,         # Сообщение об ошибке (или None)
                           request_args=request_args_dict       # Словарь с параметрами запроса
                           )


@app.route('/download_resume/<filename>')
@login_required
def download_resume(filename):
    """
    Отдает файл резюме пользователю.
    Проверяет, что файл существует и принадлежит текущему пользователю.
    """
    user_id = current_user.id # ID текущего пользователя
    print(f"[INFO] Запрос на скачивание файла: {filename} пользователем ID: {user_id}")

    conn = None # Переменная для соединения с БД
    cursor = None # Переменная для курсора

    try:
        # Проверяем имя файла на безопасность
        safe_filename = secure_filename(filename)
        # Если имя файла после очистки отличается от исходного, это подозрительно
        if safe_filename != filename:
            print(f"[WARN] Попытка скачать файл с небезопасным именем: {filename}")
            flask.abort(403, description="Недопустимое имя файла.") # Запрещаем доступ

        # Подключаемся к базе данных PostgreSQL
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # Проверяем в БД, существует ли запись с таким именем файла
        # и принадлежит ли она текущему пользователю
        cursor.execute("""
            SELECT user_id FROM analyses
            WHERE stored_filename = %s AND user_id = %s;
        """, (safe_filename, user_id)) # Передаем имя файла и ID пользователя

        file_owner_data = cursor.fetchone() # Получаем одну строку или None

        # Закрываем курсор и соединение
        cursor.close()
        cursor = None
        conn.close()
        conn = None # Сбрасываем

        # Если запись не найдена (либо файла нет, либо он чужой)
        if file_owner_data is None:
            print(f"[WARN] Попытка скачать несуществующий или чужой файл: {safe_filename} пользователем ID: {user_id}")
            flask.abort(404, description="Файл не найден или у вас нет прав доступа к нему.")

        # Формируем полный путь к файлу на сервере
        directory = os.path.abspath(app.config['UPLOAD_FOLDER'])
        file_path = os.path.join(directory, safe_filename)

        # Дополнительная проверка, что путь к файлу находится внутри разрешенной папки загрузок
        # Это защита от атак типа "Path Traversal"
        if not os.path.abspath(file_path).startswith(directory):
            print(f"[CRITICAL] Попытка Path Traversal при скачивании файла: {safe_filename}")
            flask.abort(404, description="Недопустимый путь к файлу.") # Скрываем реальную причину

        # Проверяем, существует ли файл на диске
        if not os.path.exists(file_path):
            print(f"[ERROR] Файл найден в БД, но отсутствует на диске: {file_path}")
            flask.abort(404, description="Файл не найден на диске.") # Файл есть в БД, но нет на сервере

        # Если все проверки пройдены, отдаем файл
        print(f"[INFO] Файл {safe_filename} успешно отдан пользователю ID: {user_id}")
        return send_from_directory(directory, safe_filename, as_attachment=True)

    except FileNotFoundError:
        # Эта ошибка маловероятна после проверки os.path.exists, но на всякий случай
        print(f"[ERROR] FileNotFoundError при отдаче файла {filename}")
        flask.abort(404, description="Файл не найден.")
    except Psycopg2Error as e: # Ловим ошибки БД
        print(f"[ERROR] Ошибка базы данных при проверке файла {filename}: {e}")
        flask.abort(500, description="Ошибка базы данных при скачивании файла.")
    except Exception as e: # Ловим любые другие неожиданные ошибки
        print(f"[ERROR] Непредвиденная ошибка при отдаче файла {filename}: {e}")
        flask.abort(500, description="Произошла внутренняя ошибка сервера.")
    finally:
        if conn:
            conn.close()


@app.route('/search_database', methods=['GET', 'POST'])
@login_required
def search_database():
    """
    Обрабатывает поисковые запросы по импортированной базе резюме (из таблицы imported_resumes).
    Выполняет поиск, фильтрацию, сортировку и пагинацию.
    Явно преобразует imported_at в datetime объекты или None для корректной работы шаблона.
    Возвращает список словарей.
    """
    # Получаем параметры поиска и фильтрации из запроса (GET или POST)
    search_term = request.values.get('q', '').strip() # Общий поисковый термин
    age_from = request.values.get('vozrast_from', type=int) # Возраст "от"
    age_to = request.values.get('vozrast_to', type=int) # Возраст "до"
    gender = request.values.get('pol') # Пол
    zhelaemaya_dolzhnost_filter = request.values.get('zhelaemaya_dolzhnost', '').strip() # Желаемая должность
    gorod_filter = request.values.get('gorod', '').strip() # Город
    klyuchevye_navyki_filter = request.values.get('klyuchevye_navyki', '').strip() # Ключевые навыки
    zarplatnye_ozhidaniya_from = request.values.get('zarplatnye_ozhidaniya_from', '').strip() # Зарплата "от" (строка)
    zarplatnye_ozhidaniya_to = request.values.get('zarplatnye_ozhidaniya_to', '').strip() # Зарплата "до" (строка)
    format_raboty_filter = request.values.get('format_raboty') # Формат работы

    # Параметры сортировки
    sort_by = request.values.get('sort_by', 'imported_at') # Поле для сортировки (по умолчанию дата импорта)
    sort_dir = request.values.get('sort_dir', 'desc') # Направление сортировки (по умолчанию убывание)

    # Параметры пагинации
    page = request.args.get('page', 1, type=int) # Текущая страница (по умолчанию 1)
    per_page = 10 # Количество записей на странице

    search_results = [] # Список для обработанных результатов (словарей)
    search_results_raw = [] # Список для сырых результатов из БД (DictCursor)
    search_performed = False # Флаг, был ли выполнен поиск

    # Определяем, был ли выполнен какой-либо поиск или применена фильтрация
    if search_term or age_from is not None or age_to is not None or (gender is not None and gender not in ['', '__none__']) or \
       zhelaemaya_dolzhnost_filter or gorod_filter or klyuchevye_navyki_filter or \
       zarplatnye_ozhidaniya_from or zarplatnye_ozhidaniya_to or \
       (format_raboty_filter is not None and format_raboty_filter != ''):
        search_performed = True
        print(f"[INFO] Получен поисковый запрос с фильтрами/сортировкой. Термин: '{search_term}'")

    conn = None # Переменная для соединения с БД
    cursor = None # Переменная для курсора

    try:
        # Подключаемся к базе данных PostgreSQL
        conn = psycopg2.connect(DATABASE_URL)
        # Используем DictCursor для получения строк как словарей
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        where_clauses = [] # Список условий для WHERE
        params = [] # Список параметров для запроса

        #  Формируем условия WHERE для фильтрации 
        if search_term:
             # Ищем совпадение в нескольких текстовых полях (без учета регистра в PostgreSQL)
             search_pattern = f"%{search_term}%"
             where_clauses.append("""
                 (fio ILIKE %s OR zhelaemaya_dolzhnost ILIKE %s OR klyuchevye_navyki ILIKE %s
                OR gorod ILIKE %s OR email ILIKE %s OR telefon ILIKE %s OR opyt_raboty ILIKE %s
                OR mesta_raboty ILIKE %s OR obrazovanie ILIKE %s OR yazyki ILIKE %s
                OR zarplatnye_ozhidaniya ILIKE %s OR relokatsiya_komandirovki ILIKE %s
                OR format_raboty ILIKE %s)
             """)
             params.extend([search_pattern] * 13) # Добавляем шаблон поиска 13 раз

        if age_from is not None: # Фильтр по возрасту "от"
             where_clauses.append("vozrast >= %s")
             params.append(age_from)
        if age_to is not None: # Фильтр по возрасту "до"
             where_clauses.append("vozrast <= %s")
             params.append(age_to)

        if gender and gender not in ['', '__none__']: # Фильтр по полу (если выбран конкретный пол)
             where_clauses.append("pol = %s")
             params.append(gender)
        elif gender == '__none__': # Фильтр по полу "Не указан" (NULL или пустая строка)
             where_clauses.append("(pol IS NULL OR pol = '')")

        if zhelaemaya_dolzhnost_filter: # Фильтр по желаемой должности
             where_clauses.append("zhelaemaya_dolzhnost ILIKE %s")
             params.append(f"%{zhelaemaya_dolzhnost_filter}%")
        if gorod_filter: # Фильтр по городу
             where_clauses.append("gorod ILIKE %s")
             params.append(f"%{gorod_filter}%")
        if klyuchevye_navyki_filter: # Фильтр по ключевым навыкам
             where_clauses.append("klyuchevye_navyki ILIKE %s")
             params.append(f"%{klyuchevye_navyki_filter}%")

        # Фильтры по зарплатным ожиданиям (как строки, ищем совпадение)
        if zarplatnye_ozhidaniya_from:
             where_clauses.append("zarplatnye_ozhidaniya ILIKE %s")
             params.append(f"%{zarplatnye_ozhidaniya_from}%")
        if zarplatnye_ozhidaniya_to:
             where_clauses.append("zarplatnye_ozhidaniya ILIKE %s")
             params.append(f"%{zarplatnye_ozhidaniya_to}%")

        if format_raboty_filter and format_raboty_filter != '': # Фильтр по формату работы
             if format_raboty_filter == '__none__': # "Не указан"
                  where_clauses.append("(format_raboty IS NULL OR format_raboty = '')")
             else:
                  where_clauses.append("format_raboty = %s")
                  params.append(format_raboty_filter)

        # Составляем строку WHERE из условий
        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        #  Формируем ORDER BY для сортировки 
        # Проверяем, что поле сортировки допустимо
        allowed_sort_fields = ['fio', 'vozrast', 'pol', 'zhelaemaya_dolzhnost',
                               'klyuchevye_navyki', 'gorod', 'email', 'telefon',
                               'zarplatnye_ozhidaniya', 'relokatsiya_komandirovki',
                               'format_raboty', 'yazyki', 'imported_at', 'original_row_number']
        if sort_by not in allowed_sort_fields:
             sort_by = 'imported_at' # По умолчанию, если недопустимое поле

        # Определяем направление сортировки в SQL
        sort_dir_sql = 'DESC' if sort_dir.lower() == 'desc' else 'ASC'

        # Составляем clause для ORDER BY.
        # Для imported_at используем NULLS LAST, чтобы записи без даты импорта были в конце
        # Добавляем id ASC как вторичную сортировку для стабильного порядка при равных значениях
        if sort_by == 'imported_at':
             order_by_clause = f"imported_at {sort_dir_sql} NULLS LAST, id ASC"
        else:
             # Для остальных полей сортируем по выбранному полю, потом по imported_at (DESC), потом по id
             order_by_clause = f"{sort_by} {sort_dir_sql}, imported_at DESC NULLS LAST, id ASC"


        #  Подготовка SQL запроса для получения данных с пагинацией 
        # Сначала считаем общее количество записей, удовлетворяющих фильтрам
        count_sql = f"SELECT COUNT(*) FROM imported_resumes {where_sql}"
        cursor.execute(count_sql, params) # Выполняем запрос с параметрами фильтрации
        total_items = cursor.fetchone()[0] # Получаем количество

        # Рассчитываем общее количество страниц
        total_pages = math.ceil(total_items / per_page) if total_items > 0 else 1

        # Корректируем номер текущей страницы, если он больше общего количества страниц
        if page > total_pages and total_items > 0:
             print(f"[DEBUG] Запрошена страница {page}, всего страниц {total_pages}. Перенаправление на {total_pages}.")
             # Перенаправляем пользователя на последнюю страницу, сохраняя все текущие параметры фильтрации/сортировки
             redirect_url_args = request.args.to_dict() # Получаем все аргументы запроса как словарь
             redirect_url_args['page'] = total_pages # Меняем номер страницы
             # Возвращаем редирект на URL с обновленными параметрами
             return redirect(url_for('search_database', **redirect_url_args))

        # Рассчитываем смещение (OFFSET) для текущей страницы
        offset = (page - 1) * per_page
        if offset < 0: offset = 0 # На всякий случай, если page стал <= 0


        #  ОСНОВНОЙ SQL ЗАПРОС ДЛЯ ПОЛУЧЕНИЯ ДАННЫХ 
        sql = f"""
            SELECT * FROM imported_resumes
            {where_sql} -- Применяем условия фильтрации
            ORDER BY {order_by_clause} -- Применяем сортировку
            LIMIT %s OFFSET %s; -- Применяем пагинацию
        """
        # Собираем все параметры: сначала параметры фильтрации, потом параметры пагинации
        query_params = params + [per_page, offset]

        cursor.execute(sql, query_params) # Выполняем запрос
        search_results_raw = cursor.fetchall() # Получаем все строки (DictCursor objects)

        # Закрываем курсор и соединение сразу после получения данных
        cursor.close()
        cursor = None
        conn.close()
        conn = None # Сбрасываем, чтобы finally не пытался закрыть уже закрытое

        #  Обработка данных для передачи в шаблон 
        # PostgreSQL с DictCursor обычно возвращает datetime объекты для колонок типа timestamp/timestamptz.
        # Но мы все равно пройдемся по результатам, чтобы убедиться и, если нужно, распарсить строки
        # или установить None для imported_at.
        search_results = [] # Список для ОБРАБОТАННЫХ РЕЗУЛЬТАТОВ (словарей)
        for row in search_results_raw: # <-- Итерируемся по сырым результатам (DictCursor objects)
            # DictCursor уже ведет себя как словарь, но можно скопировать на всякий случай, если планируются изменения
            row_dict = dict(row) # Копируем в обычный словарь

            imported_at_value = row_dict.get('imported_at')

            # Проверяем тип imported_at. Если это строка, пытаемся распарсить.
            # Если это уже datetime или None, оставляем как есть.
            if isinstance(imported_at_value, str) and imported_at_value:
                try:
                    # Используем parser из dateutil для надежного парсинга строк
                    row_dict['imported_at'] = parser.parse(imported_at_value)
                except (ValueError, TypeError, parser.ParserError):
                    row_dict['imported_at'] = None # Устанавливаем None при ошибке парсинга
                    print(f"[DEBUG] Ошибка парсинга строки imported_at: '{imported_at_value}' для записи ID {row_dict.get('id')}. Установлено в None.")
            elif imported_at_value is not None and not isinstance(imported_at_value, datetime.datetime):
                 # Если тип не строка, не datetime и не None (что-то неожиданное)
                 row_dict['imported_at'] = None
                 print(f"[DEBUG] Неожиданный тип данных imported_at: {type(imported_at_value)} для записи ID {row_dict.get('id')}. Установлено в None.")
            # Если imported_at_value уже None или datetime, оставляем его как есть.

            search_results.append(row_dict) # Добавляем ОБРАБОТАННЫЙ СЛОВАРЬ в итоговый список

        print(f"[INFO] Найдено {total_items} записей, получено {len(search_results)} для страницы {page}.")

    except Psycopg2Error as db_e: # Ловим специфические ошибки PostgreSQL
        print(f"[ERROR] Ошибка базы данных в /search_database: {db_e}")
        flash("Ошибка базы данных при поиске.", "danger")
        search_results = [] # Очищаем данные при ошибке
        total_items, total_pages, page = 0, 1, 1 # Сбрасываем пагинацию
        search_performed = True # Все равно считаем, что поиск был попыткой
    except Exception as e: # Ловим любые другие неожиданные ошибки
        print(f"[ERROR] Непредвиденная ошибка в /search_database: {e}")
        flash("Непредвиденная ошибка при поиске.", "danger")
        search_results = [] # Очищаем данные при ошибке
        total_items, total_pages, page = 0, 1, 1 # Сбрасываем пагинацию
        search_performed = True # Все равно считаем, что поиск был попыткой

    finally:
        if conn:
            conn.close()

    #  Передаем данные в шаблон 
    # Передаем все текущие аргументы запроса, чтобы сохранить состояние фильтрации/сортировки
    request_args_dict = request.args.to_dict()

    # Рендерим шаблон, передавая все собранные данные
    return render_template('search_database.html',
                           search_term=search_term, # Передаем поисковый термин
                           search_results=search_results, # Передаем список словарей
                           search_performed=search_performed, # Флаг, был ли поиск
                           page=page, # Текущий номер страницы
                           total_pages=total_pages, # Общее количество страниц
                           total_items=total_items, # Общее количество записей
                           per_page=per_page, # Записей на странице

                           # Параметры фильтрации/сортировки для заполнения формы
                           vozrast_from=age_from, vozrast_to=age_to, pol=gender,
                           zhelaemaya_dolzhnost=zhelaemaya_dolzhnost_filter, gorod=gorod_filter,
                           klyuchevye_navyki=klyuchevye_navyki_filter,
                           zarplatnye_ozhidaniya_from=zarplatnye_ozhidaniya_from,
                           zarplatnye_ozhidaniya_to=zarplatnye_ozhidaniya_to,
                           format_raboty=format_raboty_filter,
                           sort_by=sort_by, sort_dir=sort_dir,

                           # Передаем request.args для использования в шаблоне (для ссылок пагинации)
                           request_args=request.args
                           )

#  Пользовательский фильтр Jinja2 для безопасного форматирования даты/времени 
@app.template_filter('format_datetime_safe')
def format_datetime_filter_safe(value, format_string='%d.%m.%Y'):
    """
    Пользовательский фильтр Jinja2 для безопасного форматирования объектов даты/времени.
    Корректно обрабатывает None, datetime объекты и пытается разобрать строки как запасной вариант.
    Используется для отображения дат в шаблонах.
    """
    if isinstance(value, datetime.datetime):
        # Если значение уже является объектом datetime (как возвращает psycopg2 для timestamp)
        try:
            return value.strftime(format_string)
        except Exception:
            # Если форматирование не удалось (редко), возвращаем строковое представление
            return str(value)
    elif isinstance(value, str) and value:
        # Если значение - строка, пытаемся распарсить ее в datetime объект
        try:
            dt_obj = parser.parse(value)
            return dt_obj.strftime(format_string)
        except (ValueError, TypeError, parser.ParserError):
            # Если парсинг не удался, выводим сообщение об ошибке или дефис
            print(f"[DEBUG] Не удалось распарсить строку даты в шаблоне: '{value}'")
            return f"Неверная дата: {value}" # Или просто return '-'
    else:
        # Если значение None или другой некорректный тип
        return '-' # Выводим дефис




def send_email(recipient_email, subject, body, html_body=None):
    """
    Отправляет email кандидату.

    Args:
        recipient_email (str): Email адрес получателя.
        subject (str): Тема письма.
        body (str): Текст письма (plain text).
        html_body (str, optional): HTML версия текста письма. Defaults to None.

    Returns:
        bool: True если письмо успешно отправлено, False иначе.
    """
    #  ОТЛАДОЧНЫЕ ПРИНТЫ 
    print(f"DEBUG: MAIL_SERVER = {MAIL_SERVER}")
    print(f"DEBUG: MAIL_PORT = {MAIL_PORT}")
    print(f"DEBUG: MAIL_USE_TLS = {MAIL_USE_TLS}")
    print(f"DEBUG: MAIL_USE_SSL = {MAIL_USE_SSL}")
    print(f"DEBUG: MAIL_USERNAME = {MAIL_USERNAME}")
    print(f"DEBUG: MAIL_PASSWORD = {'********' if MAIL_PASSWORD else 'None'}")
    print(f"DEBUG: MAIL_DEFAULT_SENDER = {MAIL_DEFAULT_SENDER}")
    #  КОНЕЦ ОТЛАДОЧНЫХ ПРИНТОВ 


    if not MAIL_SERVER or not MAIL_USERNAME or not MAIL_PASSWORD:
        print("Ошибка отправки email: Не заданы настройки почтового сервера (MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD).")
        return False

    # Создаем сообщение с указанием кодировки UTF-8
    # Если есть HTML, создаем MIMEMultipart
    if html_body:
        msg = MIMEMultipart('alternative')
        part1 = MIMEText(body, 'plain', 'utf-8') # Кодировка UTF-8 для текстовой части
        part2 = MIMEText(html_body, 'html', 'utf-8') # Кодировка UTF-8 для HTML части
        msg.attach(part1)
        msg.attach(part2)
    else:
        # Если только текст
        msg = MIMEText(body, 'plain', 'utf-8') # Кодировка UTF-8 для текстовой части


    msg['From'] = MAIL_DEFAULT_SENDER
    msg['To'] = recipient_email
    msg['Subject'] = Header(subject, 'utf-8')


    try:
        # Подключаемся к SMTP серверу
        if MAIL_USE_TLS:
            print(f"DEBUG: Подключаюсь к {MAIL_SERVER}:{MAIL_PORT} с TLS")
            server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT)
            server.starttls() # Начинаем TLS шифрование
        elif MAIL_USE_SSL:
            print(f"DEBUG: Подключаюсь к {MAIL_SERVER}:{MAIL_PORT} с SSL")
            server = smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT) # Для SSL используем SMTP_SSL
        else:
             print(f"DEBUG: Подключаюсь к {MAIL_SERVER}:{MAIL_PORT} без шифрования (НЕБЕЗОПАСНО)")
             server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT)

        print("DEBUG: Логинюсь на SMTP сервер...")
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        print("DEBUG: Логин успешный.")

        print(f"DEBUG: Отправляю email на {recipient_email}...")
        server.sendmail(MAIL_DEFAULT_SENDER, recipient_email, msg.as_bytes())
        print("DEBUG: sendmail вызвана.")

        server.quit()
        print("DEBUG: SMTP QUIT.")

        print(f"Email успешно отправлен на {recipient_email}")
        return True

    except Exception as e:
        print(f"Ошибка при отправке email на {recipient_email}: {e}")
        return False

def parse_ai_salary(text):
    """
    Извлекает зарплатные ожидания из текста по маркеру '### ЗАРПЛАТНЫЕ ОЖИДАНИЯ:'.
    Возвращает (извлеченные ожидания, оставшийся текст, ошибка парсинга).
    """
    marker = '### ЗАРПЛАТНЫЕ ОЖИДАНИЯ:'
    extracted_salary = None
    remaining_text = text
    error = None

    try:
        # Ищем маркер, начиная с него и до конца строки или до следующего заголовка (## или ###)
        match = re.search(f'{re.escape(marker)}(.*?)(\n##|\n###|\Z)', text, re.DOTALL | re.IGNORECASE)
        if match:
            extracted_salary = match.group(1).strip()
            # Удаляем найденный раздел из текста
            remaining_text = text[:match.start()] + match.group(2) + text[match.end():]
            remaining_text = remaining_text.strip()

        elif marker in text:
             # Если маркер есть, но не удалось спарсить по паттерну (например, он в конце текста без новой строки после)
             start_index = text.find(marker) + len(marker)
             extracted_salary = text[start_index:].strip()
             remaining_text = text[:text.find(marker)].strip()
             error = "Warning: Salary marker found but complex parsing failed."


    except Exception as e:
        error = f"Ошибка парсинга зарплатных ожиданий: {e}"
        print(f"Парсинг зарплаты: {error}")

    # Если ничего не найдено, extracted_salary остается None, error None
    print(f"Парсинг зарплаты: '{extracted_salary}'")
    return extracted_salary, remaining_text, error


def parse_ai_score(text):
    """
    Извлекает числовую оценку из текста по маркеру 'ИТОГОВАЯ ОЦЕНКА: [ЧИСЛО]'.
    Возвращает (извлеченная оценка, оставшийся текст, ошибка парсинга).
    """
    marker = 'ИТОГОВАЯ ОЦЕНКА:'
    extracted_score = None
    remaining_text = text
    error = None

    try:
        # Ищем маркер в конце текста и число в квадратных скобках или просто число
        # Используем rfind для поиска с конца
        marker_index = text.rfind(marker)
        if marker_index != -1:
            score_part = text[marker_index + len(marker):].strip()
            # Ищем число от 1 до 10 в квадратных скобках или просто число
            score_match = re.search(r'\[?\s*(\d+)\s*\]?', score_part)
            if score_match:
                potential_score_str = score_match.group(1)
                try:
                    potential_score = int(potential_score_str)
                    if 1 <= potential_score <= 10:
                        extracted_score = potential_score
                        # Удаляем строку с оценкой из текста (добавляем небольшую очистку вокруг)
                        line_start = text.rfind('\n', 0, marker_index) + 1
                        line_end = text.find('\n', marker_index)
                        if line_end == -1: line_end = len(text) # Если оценка в последней строке

                        remaining_text = text[:line_start].strip() + '\n' + text[line_end:].strip()
                        remaining_text = remaining_text.strip()

                    else:
                        error = f"Найденная оценка {potential_score} вне диапазона 1-10."
                        print(f"Парсинг оценки: {error}")
                        # В этом случае не удаляем текст оценки, оставляем его в remaining_text

                except ValueError:
                    error = f"Не удалось преобразовать '{potential_score_str}' в число."
                    print(f"Парсинг оценки: {error}")
                    # Не удаляем текст оценки

            else:
                error = "Маркер оценки найден, но числовое значение не обнаружено в ожидаемом формате."
                print(f"Парсинг оценки: {error}")

        # Если маркер не найден, extracted_score остается None, error None
        print(f"Парсинг оценки: {extracted_score}")

    except Exception as e:
        error = f"Критическая ошибка парсинга оценки: {e}"
        print(f"Парсинг оценки: {error}")
        # Не удаляем текст оценки

    return extracted_score, remaining_text, error


def parse_ai_recommendations(text):
    """
    Извлекает рекомендации по интервью по маркеру '### РЕКОМЕНДАЦИИ ПО ИНТЕРВЬЮ:'.
    Возвращает (извлеченные рекомендации в виде одной строки текста, оставшийся текст, ошибка парсинга).
    ИСПРАВЛЕНО: Маркер и использование replace для удаления найденного блока.
    """
    marker = '### РЕКОМЕНДАЦИИ ПО ИНТЕРВЬЮ:'
    extracted_recommendations_list = [] # Временный список для элементов рекомендаций
    recommendations_text_for_db = None # Финальный текст для сохранения в БД
    remaining_text = text
    error = None

    try:
        # Ищем маркер и захватываем весь текст до следующего заголовка ### или конца текста.
        # Захватываем ВЕСЬ блок, который нужно удалить, включая маркер.
        pattern = re.compile(f'({re.escape(marker)}\s*(.*?)(?=\n###|\Z))', re.DOTALL | re.IGNORECASE) # Группа 1: ВЕСЬ блок, Группа 2: текст рекомендаций
        match = pattern.search(text)

        if match:
            # ВЕСЬ блок для удаления находится в match.group(1)
            full_block_to_remove = match.group(1)
            # Сам текст рекомендаций находится в match.group(2)
            recommendations_block = match.group(2).strip()

            # Используем replace для удаления ВЕСЬ найденного блока из текста
            remaining_text = text.replace(full_block_to_remove, '', 1).strip()
            # ^\s* : начало строки с необязательными пробелами
            # [\*-+] : звездочка, дефис или плюс
            # \s+ : один или более пробельных символов (включая неразрывные)
            # (.+) : захватываем остаток строки
            rec_lines = re.findall(r'^\s*[\*-+]\s+(.+)', recommendations_block, re.MULTILINE)

            extracted_recommendations_list = [line.strip() for line in rec_lines if line.strip()]

            # Логируем предупреждение, если маркер найден, но список пуст
            if not extracted_recommendations_list and recommendations_block:
                 error = f"Warning: Recommendations block marker found, but no list items parsed from the block using pattern '^\s*[\\*-+]\\s+(.+)'. Block content (first 100 chars): {recommendations_block[:100]}..."
                 print(f"Парсинг рекомендаций: {error}")


            # Объединяем извлеченные элементы списка в одну строку для сохранения в БД, разделяя их переносами строки.
            if extracted_recommendations_list:
                recommendations_text_for_db = "\n".join(extracted_recommendations_list)

        # Если маркер не найден, extracted_recommendations_list остается пустым списком, error None.


    except Exception as e:
        error = f"Ошибка парсинга рекомендаций: {e}"
        print(f"Парсинг рекомендаций: {error}")
        # В случае ошибки парсинга, оставляем original text в remaining_text
        remaining_text = text

    # Логирование результата парсинга рекомендаций
    log_message_value = error
    if not log_message_value: # Если ошибки нет
         log_message_value = f"{len(extracted_recommendations_list)} рекомендаций извлечено"

    print(f"Парсинг рекомендаций: {log_message_value}")

    # Возвращаем объединенный текст рекомендаций, оставшийся текст и ошибку
    return recommendations_text_for_db, remaining_text, error

def parse_ai_questions(text):
    """
    Извлекает список уточняющих вопросов по маркеру '### УТОЧНЯЮЩИЕ ВОПРОСЫ:'.
    Возвращает (список вопросов, оставшийся текст, ошибка парсинга).
    ИСПРАВЛЕНО: Маркер и использование replace для удаления найденного блока.
    """
    marker = '### УТОЧНЯЮЩИЕ ВОПРОСЫ:'
    extracted_questions = []
    remaining_text = text
    error = None

    try:
        # Ищем маркер и захватываем весь текст до следующего заголовка ### или конца текста.
        # Захватываем ВЕСЬ блок, который нужно удалить, включая маркер.
        pattern = re.compile(f'({re.escape(marker)}\s*(.*?)(?=\n###|\Z))', re.DOTALL | re.IGNORECASE) # Группа 1: ВЕСЬ блок, Группа 2: текст вопросов
        match = pattern.search(text)

        if match:
            # ВЕСЬ блок для удаления находится в match.group(1)
            full_block_to_remove = match.group(1)
            # Сам текст вопросов находится в match.group(2)
            questions_block = match.group(2).strip()

            # Используем replace для удаления ВЕСЬ найденного блока из текста
            remaining_text = text.replace(full_block_to_remove, '', 1).strip()
            # ^\s* : начало строки с необязательными пробелами
            # \d+ : одна или более цифр
            # [\.\,] : точка или запятая
            # \s+ : один или более пробельных символов (включая неразрывные)
            # (.+) : захватываем остаток строки
            question_lines = re.findall(r'^\s*\d+[\.\,]\s+(.+)', questions_block, re.MULTILINE)

            extracted_questions = [line.strip() for line in question_lines if line.strip()]

            # Логируем предупреждение, если маркер найден, но список пуст
            if not extracted_questions and questions_block:
                 error = f"Warning: Questions block marker found, but no numbered list items parsed from the block using pattern '^\s*\d+[\.\,]\s+(.+)'. Block content (first 100 chars): {questions_block[:100]}..."
                 print(f"Парсинг вопросов: {error}")


    except Exception as e:
        error = f"Ошибка парсинга вопросов: {e}"
        print(f"Парсинг вопросов: {error}")
        # В случае ошибки парсинга, оставляем original text в remaining_text для дальнейшего парсинга
        remaining_text = text

    # Логирование результата парсинга вопросов
    log_message_value = error
    if not log_message_value: # Если ошибки нет
        log_message_value = f"{len(extracted_questions)} вопросов извлечено"

    print(f"Парсинг вопросов: {log_message_value}")

    return extracted_questions, remaining_text, error

def parse_ai_summary(text):
    """
    Извлекает краткий итог по маркеру 'ИТОГ_КРАТКО:'.
    Возвращает (извлеченный итог, оставшийся текст, ошибка парсинга).
    ИСПРАВЛЕНО: Использование replace для удаления найденного блока.
    """
    marker = 'ИТОГ_КРАТКО:' # Маркер из ответа AI
    extracted_summary = None
    remaining_text = text
    error = None

    try:
        # Ищем маркер и захватываем текст итога до следующего заголовка ## или ### или маркера оценки, или конца текста.
        # Захватываем ВЕСЬ блок, который нужно удалить, включая маркер.
        # Используем поиск (.*?)
        pattern = re.compile(
            f'({re.escape(marker)}\s*(.*?)(?=\n##|\n###|{re.escape("ИТОГОВАЯ ОЦЕНКА:")}|\Z))', # Группа 1: ВЕСЬ блок, Группа 2: текст итога
            re.DOTALL | re.IGNORECASE
        )
        match = pattern.search(text)

        if match:
            # ВЕСЬ блок для удаления находится в match.group(1)
            full_block_to_remove = match.group(1)
            # Сам текст итога находится в match.group(2)
            extracted_summary = match.group(2).strip()

            # Используем replace для удаления ВЕСЬ найденного блока из текста
            remaining_text = text.replace(full_block_to_remove, '', 1).strip()

        # Если маркер не найден, extracted_summary остается None, error None.

    except Exception as e:
        error = f"Ошибка парсинга краткого итога: {e}"
        print(f"Парсинг итога: {error}")
        # В случае критической ошибки парсинга оставляем original text в remaining_text
        remaining_text = text

    # Логирование результата парсинга итога
    log_message_value = error
    if not log_message_value: # Если ошибки нет
        log_message_value = f"'{extracted_summary}'" if extracted_summary is not None else 'Не извлечено'
    print(f"Парсинг итога: {log_message_value}")

    return extracted_summary, remaining_text, error

analysis_instructions_db_import = """
Твоя роль: Ты — высококвалифицированный и внимательный HR-аналитик с многолетним успешным опытом в подборе персонала для различных отраслей. Твоя экспертиза позволяет точно оценивать соответствие кандидата требованиям вакансии.

Твоя задача: Провести профессиональный и объективный анализ предоставленных структурированных данных кандидата (извлеченных из базы данных) на соответствие требованиям предоставленного описания вакансии.

Ключевые инструкции и ограничения:
1.  Анализируй ИСКЛЮЧИТЕЛЬНО содержание предоставленных структурированных данных о кандидате и текста описания вакансии.
2.  Строго запрещено делать любые предположения или догадки, выходящие за рамки предоставленной информации. Работай только с профессиональными качествами, опытом и навыками, описанными в предоставленных данных.
3.  Не придумывай информацию, которой нет ни в вакансии, ни в предоставленных данных кандидата.
4.  Строго следуй запрашиваемому формату и структуре ответа, используй Markdown.
5.  Ответ должен быть предоставлен ИСКЛЮЧИТЕЛЬНО на русском языке.

Выполняй следующие шаги анализа строго по порядку:

1.  **Краткая сводка по кандидату (для HR):**
    Составь краткое резюме ключевых профессиональных навыков, опыта работы и квалификаций кандидата, которые наиболее релевантны данной конкретной вакансии, основываясь только на предоставленных данных.
    (Объем: 2-4 содержательных предложения).

2.  **Зарплатные ожидания (если указаны):**
    Просмотри предоставленные данные кандидата на наличие информации о зарплатных ожиданиях. Если информация найдена, извлеки ее максимально точно. Если зарплатные ожидания не указаны или неочевидны, пропусти этот пункт и ничего не выводи.
    **Представь эту информацию СТРОГО начиная с ТОЧНО такого маркера: `### ЗАРПЛАТНЫЕ ОЖИДАНИЯ:` (с тремя хештегами, пробелом и двоеточием).**
    Укажи извлеченные ожидания сразу после маркера.

3.  **Детальный анализ соответствия требованиям вакансии:**
    Проведи построчное или поэтапное сравнение требований вакансии с информацией, представленной в предоставленных данных о кандидате.
    а) **Требования вакансии, которым кандидат СООТВЕТСТВУЕТ:** Перечисли основные навыки, опыт, образование, сертификаты или другие квалификации, явно упомянутые в предоставленных данных кандидата и соответствующие требованиям вакансии. Предоставь краткое подтверждение из предоставленных данных (например, "в навыках указано знание Python", "опыт работы в сфере продаж").
    б) **Требования вакансии, которым кандидат НЕ соответствует или по которым ОТСУТСТВУЕТ информация:** Перечисли ключевые требования вакансии, которые явно не упомянуты в предоставленных данных кандидата, или по которым квалификации кандидата недостаточны/нерелевантны согласно предоставленным данным.

    Используй четкие маркированные списки (`* ` или `- `) для обоих подпунктов (а и б).

4.  **Общий вывод и рекомендация:**
    На основе анализа (пункт 3) предоставь следующий вывод:
    -   **Краткий итог для отображения (1-2 предложения):** **Начни этот пункт с ТОЧНО такого маркера: `ИТОГ_КРАТКО:` (с двоеточием и пробелом).** Сформулируй самый главный вывод о пригодности кандидата для данной вакансии (в объеме 1-2 предложений).
    -   **Категория соответствия:** Выбери ОДНУ соответствующую категорию из списка: Высокое соответствие, Среднее соответствие, Низкое соответствие, Не соответствует.

5.  **Числовая Оценка (от 1 до 10) по детализированной шкале соответствия:**
    Присвой финальную числовую оценку соответствия кандидата вакансии, используя целое число от 1 до 10. **СТРОГО руководствуйся приведенной ниже детализированной шкалой соответствия** (включите сюда текст вашей шкалы оценки).

    **Детализированная Шкала Оценки Соответствия Резюме Вакансии (от 1 до 10 баллов):**
    * 1: Полное отсутствие соответствия основным требованиям вакансии. Резюме не содержит релевантного опыта, навыков или образования.
    * 2: Крайне низкое соответствие. Присутствуют единичные, очень поверхностные совпадения с требованиями, но в целом кандидат совершенно не подходит.
    * 3: Минимальное соответствие. Есть очень ограниченные совпадения по некоторым пунктам, но отсутствуют ключевой опыт и необходимые навыки для выполнения работы.
    * 4: Небольшое минимальное соответствие. Кандидат обладает некоторыми базовыми знаниями или опытом, связанными с вакансией, но этого явно недостаточно. Есть значительные пробелы.
    * 5: Среднее соответствие. Есть базовое совпадение по нескольким ключевым пунктам вакансии, но при этом присутствуют существенные пробелы в опыте или навыках. Требуются серьезные уточнения.
    * 6: Уверенное среднее соответствие. Кандидат соответствует основным требованиям по большинству базовых пунктов, но есть заметные области, где опыт или навыки недостаточны или требуют прояснения.
    * 7: Хорошее соответствие. Кандидат обладает большинством необходимых навыков и релевантного опыта. Есть небольшие расхождения с идеальным профилем или пункты, которые желательно уточнить на интервью.
    * 8: Очень хорошее соответствие. Кандидат обладает почти всеми ключевыми навыками и значительным релевантным опытом. Требуется лишь минимальное уточнение деталей или оценка соответствия корпоративной культуре.
    * 9: Высокое соответствие. Кандидат полностью соответствует всем ключевым требованиям вакансии, обладает сильными сторонами и релевантным опытом, который может быть очень ценным.
    * 10: Идеальное соответствие. Кандидат не только полностью соответствует всем требованиям вакансии, но и превосходит ожидания по ряду пунктов. Обладает уникальным или очень ценным опытом/навыками, которые делают его идеальным кандидатом.

6. Уточняющие вопросы К КАНДИДАТУ (для интервью):
        На основе анализа предоставленных данных кандидата и требований вакансии, сгенерируй список из 5-10 вопросов, **которые рекрутер может задать кандидату на интервью**. Эти вопросы должны быть направлены на **углубление информации из предоставленных данных, выявление неочевидных навыков, оценку опыта решения конкретных задач, понимание мотивации кандидата и проверку соответствия ключевым требованиям вакансии**. Избегай вопросов, ответы на которые очевидны из предоставленных данных, или вопросов о самой компании или вакансии, если они не касаются того, как опыт кандидата соотносится с этими аспектами.
        **Представь этот список вопросов СТРОГО после раздела "Общий вывод и рекомендация" (Пункта 4). Начни список с ТОЧНО такого маркера: `### УТОЧНЯЮЩИЕ ВОПРОСЫ:` (с тремя хештегами, пробелом и двоеточием).**
        Каждый вопрос в списке начинай с новой строки с числа и точки (например, `1. Расскажите подробнее о Вашем опыте...?`).

7.  **Рекомендации по интервью:**
На основе всего проведенного анализа предоставленных данных кандидата относительно вакансии, дай рекомендации для рекрутера по проведению интервью. На что стоит обратить особое внимание? Какие аспекты стоит проверить глубже?
**Представь этот раздел СТРОГО после раздела "Уточняющие вопросы к кандидату". Начни его с ТОЧНО такого маркера: `### РЕКОМЕНДАЦИИ ПО ИНТЕРВЬЮ:` (с тремя хештегами, пробелом и двоеточием).**
Предоставь рекомендации в формате четкого маркированного списка или кратких, связных абзацев.

Структура финального ответа:
Твой полный ответ должен быть в Markdown формате и строго включать в себя, в указанном порядке:
-   Заголовок Пункта 1: `## Краткая сводка по кандидату` и содержание пункта 1.
-   **Раздел зарплатных ожиданий (с маркером `### ЗАРПЛАТНЫЕ ОЖИДАНИЯ:`)**
-   Заголовок Пункта 3: `## Детальный анализ соответствия требованиям вакансии`.
-   Подзаголовок `Требования вакансии, которым кандидат СООТВЕТСТВУЕТ:` и маркированный список для 3а.
-   Подзаголовок `Требования вакансии, которым кандидат НЕ соответствует или по которым ОТСУТСТВУЕТ информация:` и маркированный список для 3б.
-   Заголовок Пункта 4: `## Общий вывод и рекомендация`.
-   Содержание пункта 4: Краткий итог (с маркером `ИТОГ_КРАТКО:`), затем Категория соответствия.
-   **Раздел уточняющих вопросов (с маркером `### УТОЧНЯЮЩИЕ ВОПРОСЫ:`)**
-   **Раздел рекомендаций по интервью (с маркером `### РЕКОМЕНДАЦИИ ПО ИНТЕРВЬЮ:`)**
-   В самом конце твоего ответа, после всего перечисленного выше, должна идти **ТОЛЬКО ОДНА** строка с финальной числовой оценкой в **ТОЧНО таком** формате:
    `ИТОГОВАЯ ОЦЕНКА: [ЧИСЛО ОТ 1 ДО 10]`

---
"""


@app.route('/analyze_imported_candidate/<int:resume_id>', methods=['POST'])
@login_required
def analyze_imported_candidate(resume_id):
    """
    Запускает Celery задачу для анализа импортированного кандидата.
    Принимает ID импортированного резюме из URL.
    Извлекает описание вакансии из JSON тела POST запроса.
    Использует psycopg2 для взаимодействия с PostgreSQL.
    Возвращает JSON ответ (успех/ошибка).
    """
    request_data = request.get_json() # Пытаемся получить JSON тело запроса

    if not request_data:
         # Если JSON тело отсутствует или не парсится как JSON
         print(f"[ERROR] Ошибка в маршруте /analyze_imported_candidate: POST запрос без JSON тела или неверный Content-Type.")
         flash("Ошибка: Запрос анализа не содержит необходимых данных или имеет неверный формат (ожидается JSON).", "danger")
         return jsonify({'status': 'error', 'message': 'Неверный формат запроса (ожидается JSON).'}), 400 # Bad Request

    # Безопасно извлекаем описание вакансии из JSON данных
    job_description = request_data.get('job_description', '').strip()
    batch_id = str(uuid.uuid4()) # Генерируем ID пакета
    batch_timestamp = datetime.datetime.now() # Метка времени пакета

    print(f"Получен запрос на анализ импортированного кандидата ID: {resume_id}")

    conn = None
    cursor = None # Добавляем курсор в область видимости try/finally
    try:
        # PostgreSQL - Подключение
        # Используем строку подключения из конфигурации приложения
        conn = psycopg2.connect(current_app.config['POSTGRES_URI'])
        cursor = conn.cursor()

        #  Изменение для PostgreSQL: Запрос и плейсхолдер 
        # Изменяем ? на %s и передаем параметры как кортеж
        cursor.execute("SELECT * FROM imported_resumes WHERE id = %s", (resume_id,))
        imported_resume_data = cursor.fetchone()

        if imported_resume_data:
            # Кандидат найден. Получаем настройки AI и запускаем Celery задачу.
            ai_service_config = current_app.config.get('DEFAULT_AI_SERVICE', 'gemini')
            gemini_api_key = current_app.config.get('GEMINI_API_KEY')
            deepseek_api_key = current_app.config.get('DEEPSEEK_API_KEY')
            gemini_model_name = current_app.config.get('GEMINI_MODEL_NAME')
            deepseek_model_name = current_app.config.get('DEEPSEEK_MODEL_NAME')

            # Запускаем Celery задачу для анализа
            task = process_resume_task.delay(
                filepath=None, # Для импортированных резюме нет файла
                job_description=job_description,
                ai_service=ai_service_config,
                gemini_api_key=gemini_api_key,
                deepseek_api_key=deepseek_api_key,
                gemini_model=gemini_model_name,
                deepseek_model=deepseek_model_name,
                batch_id=batch_id,
                batch_timestamp=batch_timestamp,
                original_filename=f"Импорт ID {resume_id}", # Имя для отображения в истории
                stored_filename=None, # Нет сохраненного файла
                user_id=current_user.id,
                imported_resume_id=resume_id, # Передаем ID импортированного резюме
                text_data=None # Задача сама получит данные из imported_resume_id
            )
            print(f"Задача Celery {task.id} запущена для анализа импортированного кандидата ID: {resume_id}")

            return jsonify({'status': 'success', 'message': 'Задача анализа поставлена в очередь.', 'task_id': task.id})

        else:
            # Кандидат с таким ID не найден в imported_resumes
            print(f"Ошибка: Импортированный кандидат с ID {resume_id} не найден.")
            return jsonify({'status': 'error', 'message': f'Кандидат с ID {resume_id} не найден в базе данных импортированных резюме.'}), 404

    except psycopg2.Error as db_err:
        # Перехват ошибок базы данных PostgreSQL
        print(f"[ERROR] Ошибка базы данных в маршруте /analyze_imported_candidate для ID {resume_id}: {db_err}")
        return jsonify({'status': 'error', 'message': f'Ошибка базы данных: {db_err}'}), 500

    except Exception as e:
        # Перехват любых других ошибок
        print(f"[ERROR] Непредвиденная ошибка в маршруте /analyze_imported_candidate для ID {resume_id}: {e}")
        return jsonify({'status': 'error', 'message': f'Непредвиденная ошибка сервера: {e}'}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/send_final_questions/<token>', methods=['POST'])
@login_required # Рекрутер должен быть авторизован, чтобы отправить вопросы
def send_final_questions(token):
    """
    Принимает финальный список вопросов от рекрутера, сохраняет его,
    формирует email с вопросами и ссылкой на форму ответов, и отправляет email кандидату.
    Использует psycopg2 для взаимодействия с PostgreSQL.
    """
    user_id = current_user.id
    print(f"Получен POST-запрос для отправки вопросов с токеном: {token} пользователем: {user_id}")

    conn = None
    cursor = None # Добавляем курсор в область видимости try/finally
    try:
        conn = psycopg2.connect(current_app.config['POSTGRES_URI'])
        cursor = conn.cursor()

        # Находим запись запроса по токену и убеждаемся, что она принадлежит пользователю

        cursor.execute("""
            SELECT
                rfi.id, rfi.analysis_id, rfi.status, a.candidate_name, a.contacts_extracted
            FROM
                requests_for_info rfi
            JOIN
                analyses a ON rfi.analysis_id = a.id
            WHERE rfi.token = %s AND rfi.user_id = %s
        """, (token, user_id)) # Передаем параметры как кортеж
        request_data = cursor.fetchone()

        if request_data is None:
            flash("Запрос с указанным токеном не найден или не принадлежит вам.", "danger")
            return redirect(url_for('history')) # Или на страницу ошибки

        # Извлекаем данные из кортежа
        request_id, analysis_id, current_status, candidate_name, contacts_extracted = request_data

        # Если запрос уже завершен кандидатом, не даем отправить вопросы повторно
        if current_status == 'completed':
             flash("Этот запрос уже был завершен кандидатом. Отправка повторных вопросов невозможна.", "warning")
             return redirect(url_for('history')) # Или на страницу истории


        #  Получаем email кандидата 
        candidate_email = None
        if contacts_extracted:
             emails_found = re.findall(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', contacts_extracted)
             if emails_found: candidate_email = emails_found[0]

        if not candidate_email:
            flash(f"Не удалось найти email для кандидата '{candidate_name or 'N/A'}'. Отправка невозможна.", "warning")
            # Возвращаемся на страницу редактирования, если не смогли найти email
            return redirect(url_for('history'))


        # Получаем список вопросов из формы
        final_questions_list = request.form.getlist('final_questions[]')
        final_questions_text_plain = "\n".join([f"- {q}" for q in final_questions_list if q.strip()]) # Форматируем в текст для письма (с дефисом)

        if not final_questions_list:
            flash("Нечего отправлять: список вопросов пуст.", "warning")
            # Возвращаемся на страницу редактирования, передавая токен для сохранения контекста
            return redirect(url_for('request_additional_info', analysis_id=analysis_id))


        #  Сохраняем/обновляем список вопросов в БД 
        try:
            # Удаляем все предыдущие вопросы для этого analysis_id
            # Используем %s и кортеж для параметров
            cursor.execute("DELETE FROM interview_questions WHERE analysis_id = %s", (analysis_id,))

            print(f"Удалены старые вопросы для analysis_id {analysis_id}")

            # Вставляем финальный список вопросов
            timestamp_now = datetime.datetime.now() # Время сохранения
            questions_to_insert = []
            for q_text in final_questions_list:
                 if q_text.strip(): # Убедимся, что вопрос не пустой
                     questions_to_insert.append((analysis_id, q_text.strip(), 'Sent', timestamp_now, user_id)) # source = 'Sent' для отправленных вопросов


            if questions_to_insert:
                 cursor.executemany("""
                    INSERT INTO interview_questions (analysis_id, question_text, source, created_at, user_id)
                    VALUES (%s, %s, %s, %s, %s)
                 """, questions_to_insert)
                 print(f"Сохранен финальный список из {len(questions_to_insert)} вопросов для analysis_id {analysis_id} с source='Sent'.")
            else:
                 print(f"Финализированный список вопросов пуст, ничего не сохранено.")
                 conn.rollback() # Откатываем удаление, если вставлять нечего
                 flash("Нечего отправлять: список вопросов пуст.", "warning") # Повторяем на всякий случай
                 # Возвращаемся на страницу редактирования
                 return redirect(url_for('request_additional_info', analysis_id=analysis_id))

            # Если вставка произошла успешно, делаем commit
            conn.commit()


        except Exception as db_e:
             print(f"Ошибка БД при сохранении финальных вопросов: {db_e}")
             flash("Произошла ошибка при сохранении финального списка вопросов.", "danger")
             if conn: conn.rollback() # Откат изменений в случае ошибки сохранения
             return redirect(url_for('history')) # Или обратно на страницу редактирования


        #  Формируем текст Email с вопросами и ссылкой на форму ответов 
        # URL формы для ответов кандидата
        request_form_url = url_for('submit_additional_info', token=token, _external=True)

        subject = f"Уточняющие вопросы по вашему резюме - {candidate_name or 'Кандидат'}"

        # Форматируем текст вопросов для HTML ДО создания f-строки
        final_questions_text_html = final_questions_text_plain.replace('\n', '<br>')


        # Формируем тело письма
        email_body_text = f"""Здравствуйте, {candidate_name or 'Кандидат'}!

Спасибо за ваше резюме. Мы рассмотрели вашу кандидатуру и хотели бы задать несколько уточняющих вопросов, чтобы лучше понять ваш опыт для открытой вакансии.

Пожалуйста, ответьте на следующие вопросы:

{final_questions_text_plain}

Просим предоставить ваши ответы, заполнив форму по ссылке ниже:
{request_form_url}

Спасибо за уделенное время!

С уважением,
Команда HR Analyzer

"""
        email_body_html = f"""
<html>
<head></head>
<body>
<p>Здравствуйте, {candidate_name or 'Кандидат'}!</p>
<p>Спасибо за ваше резюме. Мы рассмотрели вашу кандидатуру и хотели бы задать несколько уточняющих вопросов, чтобы лучше понять ваш опыт для открытой вакансии.</p>
<p>Пожалуйста, ответьте на следующие вопросы:</p>
<p>
{final_questions_text_html}
</p>
<p>Просим предоставить ваши ответы, заполнив форму по ссылке ниже:</p>
<p><a href="{request_form_url}">{request_form_url}</a></p>
<p>Спасибо за уделенное время!</p>
<p>С уважением,<br>
Команда HR Analyzer</p>
</body>
</html>
"""

        #  Отправляем письмо 
        email_sent_successfully = send_email(candidate_email, subject, email_body_text, email_body_html)

        #  Обновляем статус запроса в БД 
        if email_sent_successfully:
            try:
                # Обновляем статус записи в requests_for_info на 'sent' и сохраняем текст отправленных вопросов как доп. инфо
                cursor.execute("""
                    UPDATE requests_for_info
                    SET status = 'sent', completed_at = %s, additional_info_text = %s -- Сохраняем отправленные вопросы в additional_info_text
                    WHERE id = %s -- Используем request_id из SELECT в начале
                """, (datetime.datetime.utcnow(), final_questions_text_plain, request_id)) # additional_info_text = текст отправленных вопросов
                conn.commit()
                print(f"Статус запроса {request_id} обновлен на 'sent'. Сохранен текст отправленных вопросов.")

            except Exception as status_update_e:
                print(f"Ошибка БД при обновлении статуса запроса {request_id} на 'sent' и сохранении вопросов: {status_update_e}")
                flash(f"Вопросы отправлены, но не удалось полностью обновить статус запроса в базе данных.", "warning")

        else:
            # Если отправка email не удалась, показываем сообщение об ошибке
            print(f"Не удалось отправить email после сохранения финальных вопросов.")
            try:
                 cursor.execute("""
                     UPDATE requests_for_info
                     SET status = 'pending_recruiter_edit', completed_at = NULL, additional_info_text = NULL
                     WHERE id = %s
                 """, (request_id,))
                 conn.commit()
                 print(f"Статус запроса {request_id} возвращен на 'pending_recruiter_edit'.")
            except Exception as reset_status_e:
                 print(f"Ошибка БД при сбросе статуса запроса {request_id} на 'pending_recruiter_edit': {reset_status_e}")


            flash(f"Не удалось отправить email кандидату '{candidate_name or 'N/A'}'. Проверьте настройки почты и логи сервера.", "danger")
            # Возвращаем на страницу редактирования
            return redirect(url_for('request_additional_info', analysis_id=analysis_id))

    except psycopg2.Error as db_err:
        # Перехват ошибок базы данных PostgreSQL
        print(f"[ERROR] Ошибка базы данных при обработке отправки финальных вопросов по токену {token}: {db_err}")
        flash(f"Ошибка базы данных при отправке вопросов: {db_err}", "danger")
        if conn: conn.rollback() # Откат изменений в случае ошибки БД

    except Exception as e:
        print(f"Критическая ошибка при обработке отправки финальных вопросов по токену {token}: {e}")
        flash("Произошла критическая ошибка при отправке вопросов.", "danger")
        if conn: conn.rollback() # Откат изменений в случае критической ошибки

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    # Перенаправляем пользователя обратно на страницу истории или на страницу подтверждения
    # Проверяем флаг email_sent_successfully, который устанавливается только при успешной отправке и обновлении статуса
    if 'email_sent_successfully' in locals() and email_sent_successfully:
         return redirect(url_for('history'))
    else:
        return redirect(url_for('history'))
    
@app.route('/submit_additional_info/<token>', methods=['GET', 'POST'])
def submit_additional_info(token):
    """
    Обрабатывает запрос от кандидата.
    GET: Показывает форму для ответа.
    POST: Принимает и сохраняет ответ.
    Использует psycopg2 для взаимодействия с PostgreSQL.
    """
    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(current_app.config['POSTGRES_URI'])
        cursor = conn.cursor()

        # Получаем данные запроса по токену
        cursor.execute("""
            SELECT rfi.id, rfi.analysis_id, rfi.status, a.candidate_name, a.user_id
            FROM requests_for_info rfi
            JOIN analyses a ON rfi.analysis_id = a.id
            WHERE rfi.token = %s
        """, (token,)) # Передаем параметры как кортеж
        request_data = cursor.fetchone()

        if request_data is None:
            flash("Неверная ссылка или запрос не найден.", "danger")
            return render_template('message_for_candidate.html', message="Неверная ссылка или запрос не найден.")

        # Извлекаем данные из кортежа
        request_id, analysis_id, current_status, candidate_name, recruiter_user_id = request_data

        if current_status == 'completed':
            flash("Вы уже предоставили информацию.", "info")
            return render_template('message_for_candidate.html')

        #  POST-запрос: обработка формы 
        if request.method == 'POST':
            # Сначала получаем список вопросов, чтобы связать их с ответами
            cursor.execute("""
                SELECT question_text
                FROM interview_questions
                WHERE analysis_id = %s AND source = 'Sent'
                ORDER BY created_at ASC
            """, (analysis_id,)) # Передаем параметры как кортеж
            sent_questions = [q[0] for q in cursor.fetchall()] # fetchall возвращает список кортежей

            # Сбор всех ответов по вопросам
            answers = []
            for index, question in enumerate(sent_questions, start=1):
                answer = request.form.get(f'answer_{index}', '').strip()
                if not answer:
                    flash(f"Пожалуйста, ответьте на все вопросы. Вопрос {index} остался без ответа.", "warning")
                    # При ошибке возвращаем форму с уже введенными данными
                    return render_template('submit_additional_info.html',
                                           token=token,
                                           candidate_name=candidate_name,
                                           questions=sent_questions,
                                           )
                answers.append(f"Вопрос {index}: {question}\nОтвет: {answer}")

            full_response = "\n\n".join(answers)
            print(f"[INFO] Получена доп. информация:\n{full_response}")

            # Сохраняем ответы в базу
            try:
                cursor.execute("""
                    UPDATE requests_for_info
                    SET status = 'completed', completed_at = %s, additional_info_text = %s
                    WHERE id = %s
                """, (datetime.datetime.utcnow(), full_response, request_id))
                conn.commit()
                print(f"[INFO] Доп. информация сохранена, ID запроса: {request_id}")
                flash("Информация успешно отправлена!", "success")
                return render_template('message_for_candidate.html')
            except Exception as db_e:
                print(f"[ERROR] Ошибка при сохранении информации: {db_e}")
                flash("Ошибка при сохранении.", "danger")
                if conn:
                    conn.rollback()
                # При ошибке сохранения возвращаем форму с вопросами
                return render_template('submit_additional_info.html',
                                       token=token,
                                       candidate_name=candidate_name,
                                       questions=sent_questions,
                                       )


        #  GET-запрос: показ формы с вопросами 
        else:
            if current_status != 'sent':
                flash("Этот запрос еще не был отправлен вам.", "warning")
                return render_template('message_for_candidate.html', message="Запрос еще не активен.")

            # Получаем вопросы для отображения
            cursor.execute("""
                SELECT question_text
                FROM interview_questions
                WHERE analysis_id = %s AND source = 'Sent'
                ORDER BY created_at ASC
            """, (analysis_id,))
            sent_questions = [q[0] for q in cursor.fetchall()] # fetchall возвращает список кортежей
            print(f"[INFO] Загружено вопросов для отображения: {len(sent_questions)}")

            return render_template('submit_additional_info.html',
                                   token=token,
                                   candidate_name=candidate_name,
                                   questions=sent_questions)

    except psycopg2.Error as db_err:
        # Перехват ошибок базы данных PostgreSQL
        print(f"[ERROR] Ошибка базы данных при обработке запроса submit_additional_info по токену {token}: {db_err}")
        flash(f"Ошибка базы данных: {db_err}", "danger")
        if conn: conn.rollback() # Откат изменений в случае ошибки БД
        return render_template('message_for_candidate.html', message="Ошибка при обработке запроса.")

    except Exception as e:
        print(f"[ERROR] Критическая ошибка при обработке запроса submit_additional_info по токену {token}: {e}")
        flash("Произошла критическая ошибка.", "danger")
        if conn: conn.rollback() # Откат изменений в случае критической ошибки
        return render_template('message_for_candidate.html', message="Произошла ошибка.")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/request_additional_info/<int:analysis_id>', methods=['GET'], endpoint='request_additional_info')
@login_required
def request_additional_info(analysis_id):
    """
    Инициирует процесс запроса доп. информации:
    загружает AI-вопросы (если есть) и показывает форму для их редактирования перед отправкой.
    Позволяет начать новый запрос, даже если AI-вопросов нет, для добавления вручную.
    Использует psycopg2 для взаимодействия с PostgreSQL.
    (GET-only версия)
    """
    user_id = current_user.id
    print(f"[INFO] Запрос редактирования вопросов для анализа ID: {analysis_id}, пользователь ID: {user_id}")

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(current_app.config['POSTGRES_URI'])
        cursor = conn.cursor()

        # Получаем данные анализа
        cursor.execute("SELECT id, candidate_name, contacts_extracted FROM analyses WHERE id = %s AND user_id = %s", (analysis_id, user_id))
        analysis_data = cursor.fetchone()

        if analysis_data is None:
            flash("Анализ не найден или не принадлежит вам.", "danger")
            return redirect(url_for('history'))

        # Извлекаем данные из кортежа
        analysis_id, candidate_name, contacts_extracted = analysis_data

        # Проверка наличия email
        candidate_email = None
        if contacts_extracted:
            emails_found = re.findall(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', contacts_extracted)
            if emails_found:
                candidate_email = emails_found[0]
                print(f"[INFO] Найден email: {candidate_email}")
            else:
                print(f"[WARN] Email не найден в: {contacts_extracted}")

        if not candidate_email:
            flash(f"Email кандидата не найден. Невозможно запросить доп. информацию.", "warning")
            # Перенаправляем на детали анализа
            return redirect(url_for('analysis_details', analysis_id=analysis_id))

        # Ищем существующий запрос RFI со статусами 'pending_recruiter_edit' или 'sent'
        cursor.execute("""
            SELECT token FROM requests_for_info
            WHERE analysis_id = %s AND user_id = %s
            AND status IN ('pending_recruiter_edit', 'sent')
            ORDER BY created_at DESC
            LIMIT 1
        """, (analysis_id, user_id)) # Передаем параметры как кортеж
        existing_request = cursor.fetchone()

        if existing_request:
            request_token = existing_request[0]
            print(f"[INFO] Найден существующий НЕзавершенный/НЕотправленный токен: {request_token}. Продолжаем редактирование.")

            # Для редактирования существующего запроса, все равно загружаем AI вопросы как основу
            cursor.execute("""
                SELECT id, question_text
                FROM interview_questions
                WHERE analysis_id = %s AND source = 'AI'
                ORDER BY created_at ASC
            """, (analysis_id,)) # Передаем параметры как кортеж
            ai_questions = cursor.fetchall()
            ai_questions_list = [{'id': q[0], 'text': q[1]} for q in ai_questions]
            print(f"[INFO] Загружено AI-вопросов для формы редактирования: {len(ai_questions_list)}")


            flash("Продолжите редактирование вопросов для кандидата.", "info")
            # Рендерим форму редактирования с найденным токеном и загруженными AI вопросами
            return render_template('edit_questions.html',
                                   analysis_id=analysis_id,
                                   candidate_name=candidate_name,
                                   ai_questions=ai_questions_list,
                                   request_token=request_token)

        else:
            print(f"[INFO] Не найден активный pending/sent запрос. Инициируем НОВЫЙ цикл RFI.")

            #  ШАГ 1: Удалить старые вопросы с source='Sent' для этого анализа 
            # Это важно, чтобы при повторном запросе не накапливались старые вопросы от предыдущих циклов
            try:
                cursor.execute("DELETE FROM interview_questions WHERE analysis_id = %s AND source = 'Sent'", (analysis_id,))
                print(f"[INFO] Удалены старые вопросы с source='Sent' для analysis_id {analysis_id} (если были).")
                conn.commit() # Сохраняем удаление сразу
            except Exception as delete_e:
                 print(f"[ERROR] Ошибка при удалении старых 'Sent' вопросов для analysis_id {analysis_id}: {delete_e}")
                 flash("Ошибка базы данных при подготовке запроса (очистка старых вопросов).", "danger")
                 if conn: conn.rollback()
                 return redirect(url_for('analysis_details', analysis_id=analysis_id))


            #  ШАГ 2: Получить вопросы AI 
            cursor.execute("""
                SELECT id, question_text
                FROM interview_questions
                WHERE analysis_id = %s AND source = 'AI'
                ORDER BY created_at ASC
            """, (analysis_id,)) # Передаем параметры как кортеж
            ai_questions = cursor.fetchall()
            ai_questions_list = [{'id': q[0], 'text': q[1]} for q in ai_questions]
            print(f"[INFO] Загружено AI-вопросов ({len(ai_questions_list)}) для нового запроса.")

            #  ШАГ 3: Создать новую запись в requests_for_info 
            new_token = str(uuid.uuid4())
            created_at = datetime.datetime.utcnow()
            initial_status = 'pending_recruiter_edit' # Новый запрос начинается со статуса редактирования рекрутером

            try:
                cursor.execute("""
                    INSERT INTO requests_for_info (analysis_id, token, status, created_at, user_id)
                    VALUES (%s, %s, %s, %s, %s)
                """, (analysis_id, new_token, initial_status, created_at, user_id)) # Передаем параметры как кортеж
                print(f"[INFO] Создана новая запись RFI с токеном: {new_token}")
                conn.commit() # Сохраняем новую запись RFI
            except psycopg2.IntegrityError:
                 print(f"[ERROR] IntegrityError при создании новой записи RFI.")
                 flash("Ошибка при создании запроса (Integrity Error).", "danger")
                 if conn: conn.rollback()
                 return redirect(url_for('analysis_details', analysis_id=analysis_id))
            except Exception as insert_e:
                 print(f"[ERROR] Ошибка при создании новой записи RFI: {insert_e}")
                 flash("Ошибка базы данных при создании запроса.", "danger")
                 if conn: conn.rollback()
                 return redirect(url_for('analysis_details', analysis_id=analysis_id))


            #  ШАГ 4: Перенаправить на форму редактирования с AI-вопросами (возможно, пустым списком) 
            flash("Подготовьте вопросы для отправки кандидату.", "info")
            # Рендерим форму редактирования с НОВЫМ токеном и загруженными AI вопросами (список может быть пустым)
            return render_template('edit_questions.html',
                                   analysis_id=analysis_id,
                                   candidate_name=candidate_name,
                                   ai_questions=ai_questions_list, # Передаем AI вопросы (может быть пустым списком)
                                   request_token=new_token) # Передаем токен НОВОГО запроса


    except psycopg2.Error as db_err: # Изменение: Перехват ошибок базы данных PostgreSQL
        print(f"[ERROR] Ошибка базы данных при запросе редактирования для analysis_id {analysis_id}: {db_err}")
        flash(f"Ошибка базы данных при подготовке запроса: {db_err}", "danger")
        if conn: conn.rollback() # Откат изменений в случае ошибки БД
        return redirect(url_for('analysis_details', analysis_id=analysis_id))

    except Exception as e:
        print(f"[ERROR] Критическая ошибка при запросе редактирования для analysis_id {analysis_id}: {e}")
        flash("Произошла критическая ошибка при подготовке запроса.", "danger")
        if conn: conn.rollback() # Откат изменений в случае критической ошибки
        return redirect(url_for('analysis_details', analysis_id=analysis_id))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/re_evaluate/<int:analysis_id>', methods=['POST'])
@login_required
def re_evaluate(analysis_id):
    """
    Обрабатывает запрос на повторную оценку анализа с учетом ответов кандидата.
    Запускает новую задачу Celery для выполнения переоценки.
    Читает настройки AI и UPLOAD_FOLDER из конфига и передает их в задачу.
    Использует psycopg2 для взаимодействия с PostgreSQL.
    """
    user_id = current_user.id
    print(f"[INFO] Получен запрос на повторную оценку для analysis_id: {analysis_id} от пользователя: {user_id}")

    conn = None
    cursor = None
    rfi_id = None # Инициализируем rfi_id

    try:
        conn = psycopg2.connect(current_app.config['POSTGRES_URI'])
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Убедимся, что анализ существует, принадлежит текущему пользователю
        # и для этого анализа есть ЗАВЕРШЕННЫЙ запрос доп. инфо (ответы получены)
        cursor.execute("""
            SELECT a.id, rfi.id as rfi_id, rfi.status as rfi_status
            FROM analyses a
            JOIN requests_for_info rfi ON a.id = rfi.analysis_id
            WHERE a.id = %s AND a.user_id = %s AND rfi.status = 'completed'
        """, (analysis_id, user_id)) # Передаем параметры как кортеж
        validation_data = cursor.fetchone()

        if validation_data is None:
            flash("Не удалось запустить повторную оценку. Анализ не найден, не принадлежит вам, или ответы кандидата еще не получены.", "danger")
            return redirect(url_for('history'))

        rfi_id = validation_data['rfi_id']


        #  Получаем настройки AI и UPLOAD_FOLDER 
        try:
            ai_service = current_app.config.get('AI_SERVICE', 'gemini').lower()
            gemini_api_key = current_app.config.get("GEMINI_API_KEY")
            deepseek_api_key = current_app.config.get("DEEPSEEK_API_KEY")
            gemini_model = current_app.config.get('GEMINI_MODEL_NAME', 'gemini-2.0-flash')
            deepseek_model = current_app.config.get('DEEPSEEK_MODEL_NAME', 'deepseek-chat')
            upload_folder = current_app.config.get('UPLOAD_FOLDER') # Получаем UPLOAD_FOLDER

            # Выбираем активный API ключ и модель
            current_ai_key = gemini_api_key if ai_service == 'gemini' else deepseek_api_key
            current_ai_model = gemini_model if ai_service == 'gemini' else deepseek_model

            if not current_ai_key:
                raise ValueError(f"API ключ для '{ai_service}' не найден в переменных окружения или конфиге.")
            if not upload_folder:
                 raise ValueError("UPLOAD_FOLDER не настроен в конфиге.")

        except Exception as config_e:
             flash(f"Ошибка конфигурации AI/загрузки: {config_e}", "danger")
             print(f"[ERROR] Ошибка конфигурации при запросе повторной оценки для analysis_id {analysis_id}: {config_e}")
             # Если ошибка конфигурации, пытаемся обновить статус RFI
             try:
                 if rfi_id: # Проверяем, что rfi_id был найден
                    cursor.execute("UPDATE requests_for_info SET status = 're_evaluation_failed', re_evaluation_error = %s WHERE id = %s", (f"Ошибка конфигурации: {config_e}", rfi_id))
                    conn.commit()
                 else:
                    print(f"[ERROR] Не удалось обновить статус RFI, RFI ID не был найден до ошибки конфигурации.")
             except Exception as e_rollback:
                 print(f"[ERROR] Критическая ошибка при обновлении статуса ошибки RFI {rfi_id} после ошибки конфигурации: {e_rollback}")

             return redirect(url_for('history'))


        #  Запускаем Celery задачу, ПЕРЕДАВАЯ настройки AI и UPLOAD_FOLDER 
        try:
            task = task_re_evaluate.delay(
                analysis_id=analysis_id,
                ai_service=ai_service,
                ai_model=current_ai_model,
                api_key=current_ai_key,
                upload_folder=upload_folder # Передаем UPLOAD_FOLDER
            )
            print(f"[INFO] Задача повторной оценки запущена: {task.id} для analysis_id {analysis_id}")
            flash("Повторная оценка запущена. Результаты появятся в истории анализа.", "success") # Добавляем сообщение об успешном запуске

        except Exception as task_e:
            print(f"[ERROR] Ошибка при запуске Celery задачи повторной оценки для analysis_id {analysis_id}: {task_e}")
            flash("Произошла ошибка при запуске повторной оценки.", "danger")
            # В случае ошибки запуска задачи, пытаемся обновить статус запроса доп. инфо на ошибку запуска
            try:
                 if rfi_id: # Проверяем, что rfi_id был найден
                    cursor.execute("UPDATE requests_for_info SET status = 're_evaluation_failed', re_evaluation_error = %s WHERE id = %s", (f"Ошибка запуска задачи: {task_e}", rfi_id))
                    conn.commit()
                 else:
                     print(f"[ERROR] Не удалось обновить статус RFI, RFI ID не был найден до ошибки запуска задачи.")

            except Exception as e_rollback:
                 print(f"[ERROR] Критическая ошибка при обновлении статуса ошибки RFI {rfi_id} после ошибки запуска задачи: {e_rollback}")


    except psycopg2.Error as db_err: # Перехват ошибок базы данных PostgreSQL
        print(f"[ERROR] Ошибка базы данных при запросе повторной оценки для analysis_id {analysis_id}: {db_err}")
        flash(f"Ошибка базы данных: {db_err}", "danger")
        if conn: conn.rollback() # Откат изменений в случае ошибки БД

    except Exception as e:
        print(f"[ERROR] Общая ошибка в маршруте повторной оценки для analysis_id {analysis_id}: {e}")
        flash("Произошла ошибка при запросе повторной оценки.", "danger")
        if conn: conn.rollback() # Откат изменений в случае критической ошибки

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for('history'))

#  Запуск приложения 
if __name__ == '__main__':
    init_db()
    app.run(debug=True)
