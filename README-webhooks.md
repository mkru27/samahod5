# 3 Bots — Webhooks (FastAPI) for Render Free

Этот вариант запускает **3 бота** (Customer / Pro / Dispatcher) через **вебхуки** на одном Web Service в Render.

## Переменные окружения (Render → Environment)
```
CUSTOMER_BOT_TOKEN=...
PRO_BOT_TOKEN=...
DISPATCHER_BOT_TOKEN=...
ADMIN_IDS=123456789,987654321
SUPPORT_PHONE=+37529XXXXXXX
BASE_WEBHOOK_URL=https://<your-app>.onrender.com
WEBHOOK_SECRET=<любая_строка>   # рекомендуется
```

## Деплой (Render → Web Service)
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main_webhooks:app --host 0.0.0.0 --port $PORT`
- Region: любой
- Free plan: OK (сервис может «усыпать» при простое)

После первого деплоя вебхуки выставятся автоматически (startup hook).
Если меняли домен — можно вручную дернуть: `POST https://<your-app>.onrender.com/setup?key=<WEBHOOK_SECRET>`

## Проверка
1) Откройте CustomerBot → `/start` → Создать заявку → заполните.
2) В ProBot перейдите по диплинку: `https://t.me/<username_ProBot>?start=exec`, пройдите регистрацию, попросите диспетчера одобрить (`/exec_approve <id>` в DispatcherBot).
3) Проверьте, что карточка пришла **в личку** одобренным исполнителям нужной категории.
4) Нажмите «👍 Беру» → в DispatcherBot придёт уведомление.

## Важно
- Вебхуки требуют внешне доступный HTTPS-URL — домен Render подходит.
- При «холодном старте» Telegram может повторить запрос: это нормально.
- Если ничего не приходит — проверьте логи Render и что `BASE_WEBHOOK_URL` правильный.
