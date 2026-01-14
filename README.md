# AI Telegram Channel Bot

Асинхронный Telegram-бот на aiogram 3, использующий Google Gemini (google-generativeai) для улучшения текстов и публикации их в целевой канал. Принимает текстовые сообщения и видео‑кружочки; кружочки пересылает напрямую, тексты пропускает через AI перед публикацией.

## Как это работает
- Пользователь пишет боту (личка/чат) текст или видео‑кружок.
- Текст: [src/ai/service.py](src/ai/service.py) отправляет его в Gemini (модель `gemini-pro`), получает улучшенную версию и публикует в канал `TARGET_CHANNEL_ID` через [src/handlers/publication.py](src/handlers/publication.py).
- Кружочки: [src/handlers/publication.py](src/handlers/publication.py) пересылает `video_note.file_id` в канал.
- Конфиг: [src/config/settings.py](src/config/settings.py) читает `.env` (токен, ключ Gemini, канал, задел под RSS-шаблон).

## Настройка
1. Скопируйте пример окружения:
   ```bash
   cp .env.example .env
   ```
2. Заполните `.env`:
   - `BOT_TOKEN` — токен BotFather
   - `GOOGLE_API_KEY` — ключ Google Gemini
   - `TARGET_CHANNEL_ID` — @username или -100… для канала (бот должен быть админом)
   - `NEWS_RSS_URLS` — (опционально) через запятую список RSS для будущего планировщика
   - `POST_TEMPLATE` — (опционально) шаблон формата поста для RSS (например `"Заголовок: {title}\nКратко: {summary}\nСсылка: {link}"`)

## Запуск
- Локально:
  ```bash
  python -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  PYTHONPATH=. python -m src.main
  ```

- Docker:
  ```bash
  docker build -t tg-ai-channel-bot .
  docker run --env-file .env tg-ai-channel-bot
  ```

## Архитектура
```
tg_AIChannel/
├── src/
│   ├── ai/                 # Интеграция с Google Gemini (Gemini Pro)
│   ├── config/             # Конфиг через pydantic-settings
│   ├── handlers/           # Хендлеры aiogram (start, publication)
│   ├── utils/              # Утилиты (rss заготовка)
│   └── main.py             # Точка входа, регистрация роутеров
├── .env.example            # Шаблон переменных окружения
├── Dockerfile              # Образ с PYTHONPATH=/app и запуском -m src.main
├── requirements.txt        # Зависимости
└── README.md               # Документация
```

## План на RSS (задел)
- В `.env` можно задать `NEWS_RSS_URLS` и `POST_TEMPLATE`.
- В [src/utils/rss.py](src/utils/rss.py) заготовлена функция `fetch_rss_entries()`; нужно дописать асинхронный парсер (aiohttp + feedparser) и повесить планировщик (cron/apscheduler/K8s CronJob).
- Публикация в канал может использовать тот же маршрут, что и пользовательские тексты, с подстановкой данных в шаблон перед отправкой в Gemini (или сразу в канал, если AI не нужен).
