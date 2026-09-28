# 🔌 API-server — эндпоинты

Базовый адрес: `https://<PUBLIC_BASE_URL>`
Автодокументация (только при `ENV=development`): `/docs`

---

## Публичные

### `GET /sub/{token}`
Ссылка-подписка, которую импортирует клиентское приложение.
Проксирует подписку из Marzban и **всегда отдаёт актуальные конфиги** — это
основа Dynamic Subscription и Auto-Healing.

- `200` — тело подписки (base64 или clash/sing-box по заголовку `User-Agent`)
- `404` — токен не найден
- `410` — подписка истекла (тело содержит конфиг-заглушку с текстом-подсказкой)

### `GET /i/{token}`
Страница импорта подписки в приложение.

Нужна потому, что Telegram разрешает в inline-кнопках только `http(s)`-адреса:
поставить `happ://import/...` прямо в кнопку нельзя. Кнопка ведёт сюда, а
страница сама открывает приложение и показывает запасные варианты (Hiddify,
v2RayTun, Streisand) и ссылку для ручного добавления.

Параметр `?app=hiddify|v2raytun|streisand` меняет приложение, которое
открывается автоматически.

### `GET /health`
Проверка живости для мониторинга. `{"status": "ok", "db": true, "redis": true}`

---

## Вебхуки платежей

### `POST /webhook/platega`
Заголовок `X-Signature` — HMAC-SHA256 от тела запроса на `PLATEGA_WEBHOOK_SECRET`.

```json
{ "id": "pay_abc123", "status": "CONFIRMED", "amount": 249.00, "currency": "RUB",
  "metadata": { "payment_id": 42 } }
```
Ответ: `200 {"ok": true}` всегда при валидной подписи (в т.ч. на дубль — идемпотентно).
`401` — подпись неверна.

### `POST /webhook/cryptopay`
Заголовок `crypto-pay-api-signature` — HMAC-SHA256 от тела на SHA256 от токена.
Обрабатывается `update_type: "invoice_paid"`.

### Telegram Stars
Отдельного вебхука нет: `pre_checkout_query` и `successful_payment` приходят
в бота как обычные апдейты.

---

## Mini App API

Все эндпоинты требуют заголовок `X-Telegram-Init-Data` с `initData` из
`window.Telegram.WebApp`. Сервер проверяет HMAC-подпись по `BOT_TOKEN`
(см. `app/utils/security.py`). При невалидной подписи — `401`.

### `GET /api/miniapp/me`
```json
{
  "user": { "telegram_id": 123, "first_name": "Maksim", "referral_code": "a1b2c3" },
  "subscription": {
    "tariff": "multi", "tariff_title": "🌍 Мульти", "status": "active",
    "expires_at": "2026-10-17T12:00:00Z", "days_left": 30,
    "device_limit": 3, "devices_online": 1,
    "traffic_used_gb": 14.7, "streak_count": 4, "next_streak_bonus_days": 5,
    "pause_days_left": 22, "paused_until": null
  },
  "subscription_url": "https://vpn.example.com/sub/xxx",
  "deeplinks": { "happ": "happ://import/...", "hiddify": "hiddify://import/..." },
  "referrals": { "total": 3, "qualified": 2, "slots_earned": 1, "next_slot_in": 1 }
}
```

### `GET /api/miniapp/tariffs`
Тарифы, периоды и цены со скидками из `config/tariffs.yml` + персональный оффер,
если он активен.

### `POST /api/miniapp/order`
```json
{ "tariff_code": "multi", "period_months": 3, "provider": "platega", "promo_code": null }
```
→ `{ "payment_url": "https://...", "payment_id": 42, "amount": 672.0 }`

### `POST /api/miniapp/pause`
`{ "days": 14 }` → `{ "ok": true, "paused_until": "..." }`
`409` — подписка на триале, уже на паузе или лимит дней исчерпан.

### `POST /api/miniapp/resume`
Досрочная разморозка. → `{ "ok": true, "expires_at": "..." }`

### `POST /api/miniapp/heal`
Кнопка «Не работает»: персональное переключение на резервный inbound.
→ `{ "ok": true, "action": "sni_switch", "subscription_url": "..." }`

---

## Внутренние (для admin-panel)

Требуют заголовок `X-Internal-Token: <API_INTERNAL_TOKEN>`.

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/internal/stats` | юзеры, выручка, конверсия, активные подписки |
| GET | `/internal/users?query=` | поиск пользователей |
| POST | `/internal/users/{tg_id}/grant` | выдать дни вручную |
| GET | `/internal/nodes` | статус нод и история инцидентов |
| POST | `/internal/nodes/{code}/heal` | ручной запуск восстановления |
| POST | `/internal/broadcast` | рассылка |

---

## Коды ошибок

| Код | Значение |
|---|---|
| 400 | Неверные параметры запроса |
| 401 | Подпись вебхука / `initData` не прошли проверку |
| 402 | Платёж не создан (провайдер недоступен) |
| 404 | Пользователь, подписка или токен не найдены |
| 409 | Действие невозможно в текущем состоянии (напр., пауза на триале) |
| 429 | Rate-limit |
| 503 | Marzban недоступен, операция поставлена в очередь повтора |
