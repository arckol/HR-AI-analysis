import pandas as pd
import psycopg2
import datetime
import os
import sys
import psycopg2.extras

EXCEL_FILE_PATH = os.environ.get('EXCEL_FILE_PATH', '/Users/arseniikolin/Downloads/Rezume.xlsx')
EXCEL_SHEET_NAME = os.environ.get('EXCEL_SHEET_NAME', 'Rezume')

# Параметры подключения к БД PostgreSQL
DB_USER = os.environ.get('DB_USER', 'HR_analyses')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'arckol')
DB_NAME = os.environ.get('DB_NAME', 'analyses_data')
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = os.environ.get('DB_PORT', '5432')

# Формируем строку подключения для psycopg2
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def import_resumes_from_excel():
    """
    Читает данные из указанного листа Excel файла и импортирует их
    в таблицу imported_resumes базы данных PostgreSQL.
    Добавлена отладка ошибок строк и остановка после первой ошибки БД в цикле.
    """
    print("Старт импорта из файла Excel...")
    print(f"Файл Excel: {EXCEL_FILE_PATH}")
    print(f"Лист Excel: {EXCEL_SHEET_NAME}")
    print(f"Целевая БД (PostgreSQL): {DB_HOST}:{DB_PORT}/{DB_NAME} (пользователь: {DB_USER})")


    # Проверяем существование файла Excel
    if not os.path.exists(EXCEL_FILE_PATH):
        print(f"ОШИБКА: Файл Excel не найден по пути: {EXCEL_FILE_PATH}")
        # Можно завершить скрипт с ошибкой
        sys.exit(f"Ошибка: Файл Excel не найден: {EXCEL_FILE_PATH}")

    try:
        # Читаем данные из файла Excel с помощью pandas
        print("Чтение файла Excel с помощью pandas...")
        df = pd.read_excel(EXCEL_FILE_PATH, sheet_name=EXCEL_SHEET_NAME, header=0)
        print("Файл Excel прочитан.")

        if df.empty:
            print("В указанном листе Excel нет данных или он пуст.")
            return

        column_mapping = {
            'candidate_fio': 'fio',
            'candidate_age': 'vozrast',
            'candidate_gender': 'pol',
            'candidate_position': 'zhelaemaya_dolzhnost',
            'candidate_skills': 'klyuchevye_navyki',
            'candidate_experience_summary': 'opyt_raboty',
            'candidate_work_history': 'mesta_raboty',
            'candidate_education': 'obrazovanie',
            'candidate_contact_phone': 'telefon',
            'candidate_contact_email': 'email',
            'candidate_city': 'gorod',
            'candidate_salary_expectation': 'zarplatnye_ozhidaniya',
            'candidate_relocation_travel': 'relokatsiya_komandirovki',
            'candidate_work_format': 'format_raboty',
            'candidate_languages': 'yazyki',
        }

        # Проверка наличия всех необходимых колонок из column_mapping в файле Excel
        excel_columns = df.columns.tolist()
        missing_excel_cols = [excel_col for excel_col in column_mapping.keys() if excel_col not in excel_columns]
        if missing_excel_cols:
            print(f"ОШИБКА: Следующие колонки, указанные в column_mapping, не найдены в файле Excel: {', '.join(missing_excel_cols)}")
            print("Пожалуйста, проверьте точные заголовки колонок в вашем файле Excel и обновите словарь column_mapping.")
            print(f"Колонки, найденные в Excel: {', '.join(excel_columns)}")
            sys.exit("Ошибка: Не найдены необходимые колонки в файле Excel.")


        # Переименовываем колонки в DataFrame согласно column_mapping
        df_renamed = df.rename(columns=column_mapping).copy()
        print("Колонки DataFrame переименованы.")


        conn = None # Переменная для соединения с БД
        cursor = None # Переменная для курсора
        try:
            print("Подключение к базе данных PostgreSQL...")
            conn = psycopg2.connect(DATABASE_URL)
            # Отключаем автокоммит, чтобы управлять транзакцией вручную
            conn.autocommit = False
            cursor = conn.cursor()
            print("Подключение к БД успешно.")

            imported_count = 0
            errors_count = 0 # Счетчик ошибок при вставке строк
            print("Начало импорта строк в БД...")

            for index, row in df_renamed.iterrows():
                original_row = index + 2 # Номер строки в Excel (начиная с 1, плюс заголовок)

                # Преобразуем строку в обычный словарь для более удобной отладки
                row_dict = row.to_dict()

                try:
                    # Получаем данные из строки DataFrame, используя названия колонок БД
                    fio_val = str(row_dict.get('fio', '')) if pd.notna(row_dict.get('fio')) else None
                    age_val = row_dict.get('vozrast')
                    gender_val = str(row_dict.get('pol', '')) if pd.notna(row_dict.get('pol')) else None
                    position_val = str(row_dict.get('zhelaemaya_dolzhnost', '')) if pd.notna(row_dict.get('zhelaemaya_dolzhnost')) else None
                    skills_val = str(row_dict.get('klyuchevye_navyki', '')) if pd.notna(row_dict.get('klyuchevye_navyki')) else None
                    experience_val = str(row_dict.get('opyt_raboty', '')) if pd.notna(row_dict.get('opyt_raboty')) else None
                    work_history_val = str(row_dict.get('mesta_raboty', '')) if pd.notna(row_dict.get('mesta_raboty')) else None
                    education_val = str(row_dict.get('obrazovanie', '')) if pd.notna(row_dict.get('obrazovanie')) else None
                    phone_val = str(row_dict.get('telefon', '')) if pd.notna(row_dict.get('telefon')) else None
                    email_val = str(row_dict.get('email', '')) if pd.notna(row_dict.get('email')) else None
                    city_val = str(row_dict.get('gorod', '')) if pd.notna(row_dict.get('gorod')) else None
                    salary_val = str(row_dict.get('zarplatnye_ozhidaniya', '')) if pd.notna(row_dict.get('zarplatnye_ozhidaniya')) else None
                    relocation_val = str(row_dict.get('relokatsiya_komandirovki', '')) if pd.notna(row_dict.get('relokatsiya_komandirovki')) else None
                    work_format_val = str(row_dict.get('format_raboty', '')) if pd.notna(row_dict.get('format_raboty')) else None
                    languages_val = str(row_dict.get('yazyki', '')) if pd.notna(row_dict.get('yazyki')) else None

                    if pd.notna(age_val):
                         try:
                             age_val = int(float(age_val))
                         except (ValueError, TypeError):
                             age_val = None
                             print(f"Предупреждение: Не удалось преобразовать возраст '{row_dict.get('vozrast')}' (тип {type(row_dict.get('vozrast'))}) в строке Excel {original_row} в число. Установлено в NULL для БД.")
                    else:
                         age_val = None

                    timestamp = datetime.datetime.now() # Текущее время импорта

                    cursor.execute("""
                    INSERT INTO imported_resumes (fio, vozrast, pol, zhelaemaya_dolzhnost, klyuchevye_navyki, opyt_raboty, mesta_raboty, obrazovanie, telefon, email, gorod, zarplatnye_ozhidaniya, relokatsiya_komandirovki, format_raboty, yazyki, imported_at, original_row_number)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        fio_val, age_val, gender_val, position_val, skills_val, experience_val, work_history_val, education_val,
                        phone_val, email_val, city_val, salary_val, relocation_val, work_format_val, languages_val,
                        timestamp, original_row
                    ))

                    imported_count += 1

                except psycopg2.Error as row_db_e:
                    # Ловим ошибки БД для конкретной строки (например, нарушение ограничений, неверный тип данных для БД)
                    errors_count += 1
                    print(f"ОШИБКА БД при импорте строки Excel {original_row}: {row_db_e}")
                    # Выводим содержимое строки, вызвавшей ошибку, для отладки
                    print(f"  Содержимое строки Excel (сырые значения): {row.to_dict()}")
                    print(f"  Содержимое строки после обработки (для вставки):")
                    # Выводим ключевые поля для быстрой проверки. Можно добавить больше полей при необходимости.
                    print(f"    fio: {fio_val}, vozrast: {age_val}, pol: {gender_val}, zhelaemaya_dolzhnost: {position_val}, email: {email_val}, telefon: {phone_val}, zarplatnye_ozhidaniya: {salary_val}")

                    print("Остановлен импорт после первой ошибки БД в строке.")
                    break # Прерываем цикл for после первой ошибки БД


                except Exception as row_e:
                    # Ловим любые другие ошибки при обработке конкретной строки (например, ошибка парсинга в Python, не связанная напрямую с БД)
                    errors_count += 1
                    print(f"ОБЩАЯ ОШИБКА при импорте строки Excel {original_row}: {row_e}")
                    # Выводим содержимое строки, вызвавшей ошибку, для отладки
                    print(f"  Содержимое строки Excel (сырые значения): {row.to_dict()}")
                    print(f"  Содержимое строки после обработки (для вставки):")
                     # Выводим ключевые поля для быстрой проверки.
                    print(f"    fio: {fio_val}, vozrast: {age_val}, pol: {gender_val}, zhelaemaya_dolzhnost: {position_val}, email: {email_val}, telefon: {phone_val}, zarplatnye_ozhidaniya: {salary_val}")

            if errors_count > 0:
                 print(f"Обнаружены ошибки при импорте {errors_count} строк. Откатываем всю транзакцию.")
                 print("Транзакция БД будет откатана.")
                 print("Импорт завершен с ошибками.")
            else:
                 print("Ошибок не обнаружено. Завершаем транзакцию (COMMIT).")
                 conn.commit() # Коммит, если ошибок не было
                 print("Транзакция БД завершена.")
                 print("Импорт завершен успешно.")


            print(f"Импорт завершен. Всего строк в Excel: {len(df_renamed)}. Успешно обработано {imported_count} записей. Ошибок при вставке: {errors_count}.") # imported_count считает попытки вставки, но не успешные коммиты


        except psycopg2.OperationalError as op_e:
             print(f"КРИТИЧЕСКАЯ ОШИБКА ПОДКЛЮЧЕНИЯ/ОПЕРАЦИИ БД (PostgreSQL): {op_e}")
             print("Убедитесь, что сервер PostgreSQL запущен и доступен, а настройки подключения верны.")
             if conn:
                 try: conn.rollback() # Попытка отката, если соединение было установлено до сбоя
                 except Exception as rb_e: print(f"Ошибка роллбэка БД после операционной ошибки: {rb_e}")

        except psycopg2.Error as db_e:
            print(f"КРИТИЧЕСКАЯ ОБЩАЯ ОШИБКА БД (PostgreSQL): {db_e}")
            if conn:
                try:
                    conn.rollback() # Откатываем транзакцию, если ошибка произошла до цикла строк или в начале
                    print("Транзакция БД откатана из-за критической ошибки.")
                except Exception as rb_e:
                    print(f"Ошибка роллбэка БД после общей ошибки: {rb_e}")
        except Exception as e:
            print(f"КРИТИЧЕСКАЯ ОБЩАЯ ОШИБКА при импорте данных (вне цикла строк): {e}")
            if conn:
                try:
                    conn.rollback() # Откатываем транзакцию
                    print("Транзакция БД откатана из-за общей ошибки.")
                except Exception as rb_e:
                     print(f"Ошибка роллбэка БД после общей ошибки: {rb_e}")
        finally:
            if cursor:
                try: cursor.close()
                except Exception as ce: print(f"Ошибка закрытия курсора БД: {ce}")
            if conn:
                try:
                    conn.close()
                    print("Соединение с БД закрыто.")
                except Exception as ce:
                    print(f"Ошибка закрытия соединения БД: {ce}")


    except FileNotFoundError as fnf_e:
         print(f"Ошибка файла: {fnf_e}")
    except pd.errors.EmptyDataError:
         print("Ошибка pandas: Указанный лист Excel пуст или имеет неверный формат.")
    except pd.errors.ParserError as parse_e:
         print(f"Ошибка pandas (ParserError): Ошибка при чтении файла Excel. Проверьте формат файла: {parse_e}")
    except KeyError as key_e:
         print(f"Ошибка pandas (KeyError): Не удалось получить доступ к колонке после переименования. Проверьте column_mapping и заголовки Excel. Не найден ключ: {key_e}")
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОБЩАЯ ОШИБКА при чтении файла Excel или подготовке данных: {e}")

# Вызов функции импорта
if __name__ == '__main__':
    import_resumes_from_excel()
