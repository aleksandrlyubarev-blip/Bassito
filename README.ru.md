# Bassito Remote Agent

Удалённое управление конвейером видеопроизводства Bassito через Telegram с автоматической загрузкой на Google Drive.

## Архитектура

```
Вы (из любого места) → Telegram-бот → Очередь задач → Исполнитель конвейера
  → Фаза 1: Генерация сценария (Grok API)
  → Фаза 2: Генерация фонов (Veo/Grok)
  → Фаза 3: Синтез голоса
  → Фаза 4: Генерация синхронизации губ
  → Фаза 5: Рендер в CTA5
  → Фаза 6: Компоновка в FFmpeg
  → Загрузка на Google Drive → Ссылка обратно в Telegram
```

## Быстрый старт

### 1. Клонирование и установка

```bash
git clone https://github.com/youruser/bassito-remote.git
cd bassito-remote
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Настройка

```bash
cp .env.example .env
# Отредактируйте .env, добавив свои ключи (см. раздел «Конфигурация» ниже)
```

### 3. Настройка Google Drive

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/) → создайте проект → включите Drive API
2. Создайте **Сервисный аккаунт** → скачайте JSON-ключ → сохраните как `service_account.json`
3. В Google Drive поделитесь целевой папкой с email сервисного аккаунта
4. Скопируйте ID папки в `.env` → `GOOGLE_DRIVE_FOLDER_ID`

### 4. Настройка Telegram-бота

1. Напишите [@BotFather](https://t.me/BotFather) → `/newbot` → скопируйте токен
2. Узнайте свой Telegram ID через [@userinfobot](https://t.me/userinfobot)
3. Укажите оба значения в `.env`

### 5. Запуск

```bash
python bassito_telegram_orchestrator.py
```

Затем напишите своему боту: `/generate Bassito спорит с котом о философии`

## Команды бота

| Команда | Описание |
|---------|----------|
| `/generate <промпт>` | Запустить создание нового эпизода |
| `/status` | Статус агента и текущей задачи |
| `/queue` | Просмотр очереди задач |
| `/stop` | Отменить текущую задачу |
| `/last` | Ссылка на последнее готовое видео |
| `/help` | Показать все команды |

## Автоматизация CTA5

Система автоматически определяет оптимальную стратегию управления Cartoon Animator 5:

| Стратегия | Требования | Без GUI? |
|-----------|-----------|----------|
| **A: CLI Pipeline** | CTA5 v5.2+ с `CTA5Pipeline.exe` | ✅ Да |
| **B: Script API** | CTA5 со включённым RLPy-скриптингом | ❌ Нужен GUI |
| **C: UI Automation** | `pyautogui` + `pywinauto` + разблокированный экран | ❌ Нестабильно |

Принудительный выбор стратегии через окружение или код:

```python
from cta5_controller import CTA5Controller
controller = CTA5Controller.force("cli")  # "cli", "script" или "ui"
```

## Конфигурация

Все настройки задаются в `.env`:

| Переменная | Обяз. | Описание |
|------------|-------|----------|
| `BOT_TOKEN` | ✅ | Токен Telegram-бота от BotFather |
| `ALLOWED_TELEGRAM_IDS` | ✅ | Telegram ID пользователей через запятую |
| `XAI_API_KEY` | ✅ | Ключ Grok API |
| `GEMINI_API_KEY` | ✅ | Ключ Google Gemini API |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | ✅ | Путь к JSON сервисного аккаунта |
| `GOOGLE_DRIVE_FOLDER_ID` | ✅ | ID целевой папки на Drive |
| `CTA5_INSTALL_DIR` | ⬚ | Путь установки CTA5 (определяется автоматически) |
| `CTA5_RENDER_TIMEOUT_MINUTES` | ⬚ | Таймаут рендера, по умолчанию 30 |
| `MAX_QUEUE_SIZE` | ⬚ | Макс. задач в очереди, по умолчанию 10 |
| `JOB_TIMEOUT_MINUTES` | ⬚ | Общий таймаут задачи, по умолчанию 60 |

## Запуск как служба

### Linux (systemd)

```bash
# Отредактируйте bassito.service — обновите пути User, WorkingDirectory, ExecStart
sudo cp bassito.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bassito.service

# Проверка статуса
sudo systemctl status bassito.service
journalctl -u bassito.service -f
```

### Windows (NSSM)

```cmd
# Установите NSSM: https://nssm.cc/download
nssm install BassitoAgent "C:\path\to\venv\Scripts\python.exe" "C:\path\to\bassito_telegram_orchestrator.py"
nssm set BassitoAgent AppDirectory "C:\path\to\bassito-remote"
nssm start BassitoAgent
```

## Структура проекта

```
bassito-remote/
├── bassito_telegram_orchestrator.py   # Telegram-бот + очередь задач
├── bassito_core.py                    # 6-фазный конвейер (ваш существующий код)
├── bassito_drive.py                   # Загрузчик на Google Drive (Service Account)
├── cta5_controller.py                 # Автоматизация CTA5 (3 стратегии)
├── cta5_scripts/                      # Автоматически генерируемые JS-скрипты CTA5
├── logs/                              # Журналы работы
├── output/                            # Готовые видео
├── tests/                             # Тесты
├── .env.example                       # Шаблон конфигурации
├── .gitignore
├── bassito.service                    # Юнит-файл systemd
├── requirements.txt
└── README.md
```

## Интеграция с существующим Bassito

Подключите ваш существующий 6-фазный конвейер в `bassito_core.py`, реализовав `run_phase()` для каждого `PipelinePhase`. Оркестратор вызывает фазы последовательно с колбэками прогресса — точка интеграции описана в `PipelineRunner._execute_phase()` внутри оркестратора.

## Лицензия

Частная собственность / Все права защищены.
