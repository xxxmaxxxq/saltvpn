# 🛡️ VPN Service — Telegram-бот + Marzban

Коммерческий VPN-сервис на VLESS+Reality: Telegram-бот с оплатой, личный кабинет
(Mini App), автоматическая выдача ключей через панель Marzban и самовосстановление
при блокировке нод.

> **Версия 1.0** · Python 3.12 · aiogram 3 · FastAPI · PostgreSQL 16 · Redis 7 · Docker

---

## 🎯 Что умеет сервис

| Возможность | Описание |
|---|---|
| 🎁 Триал в 1 клик | 3 дня бесплатно, без карты, все локации |
| ⚡ Deep Link | `happ://import/...` — подписка ставится в приложение одной кнопкой |
| 🌍 3 тарифа | Нидерланды / Россия / Мульти (обе локации в одном ключе) |
| 💳 3 платёжки | Platega (СБП, МИР/Visa/MC), CryptoPay, Telegram Stars |
| 🩺 Auto-Healing | Ноду заблокировали → бэкенд сам переключает протокол/SNI, ключ у клиента не меняется |
| ⏸ Пауза подписки | Заморозка до 30 дней в год — дни не сгорают |
| 🔥 Streak-бонусы | Продлил без разрыва → +1…+7 бонусных дней |
| 👥 Рефералы на слотах | 2 оплативших друга = +1 постоянное устройство |
| ⏰ Таймер-оффер | За 2 часа до конца триала — персональная скидка 40% |
| 📱 Mini App | Личный кабинет: дни, трафик, устройства, продление |

---

## 🏗 Архитектура (кратко)

```
[Пользователь]
     │  Telegram
     ▼
[Bot (aiogram 3)] ──┐
                    ├──► [PostgreSQL] биллинг, юзеры, платежи, рефералы
[FastAPI]  ─────────┤
 ├ вебхуки платежей ├──► [Redis] FSM, кэш, антифрод, rate-limit
 ├ Mini App API     │
 └ /sub/{token}     └──► [Marzban API] ──► [Node NL 🇳🇱] Xray: VLESS/Reality, Hysteria2
                                        └► [Node RU 🇷🇺] Xray: VLESS/Reality, Hysteria2
[Workers] notifier (уведомления) + health (Auto-Healing)
```

Подробно — в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Полное техническое задание — в [`docs/TZ.md`](docs/TZ.md).

---

## 🚀 Посмотреть бота прямо сейчас (без сервера)

Нужен только Python и токен бота — ни VPS, ни домена, ни Docker:

```bash
cp .env.example .env     # впишите BOT_TOKEN, BOT_USERNAME, ADMIN_IDS
bash scripts/run-local.sh
```

Бот поднимется на SQLite в режиме polling, и весь интерфейс будет работать.
Пошагово, включая Windows — [`docs/QUICKSTART_LOCAL.md`](docs/QUICKSTART_LOCAL.md).

---

## 🐳 Полный стек локально (Docker, 3 команды)

```bash
git clone https://github.com/xxxmaxxxq/my-life.git
cd practice/vpn-service-project

cp .env.example .env        # заполните BOT_TOKEN и пароли
make up                     # поднимет postgres, redis, api, bot
make migrate                # создаст таблицы в БД
```

Развёртывание на боевом сервере — пошагово в [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

---

## 📂 Структура репозитория

```text
vpn-service-project/
├── .github/workflows/     # CI/CD: тесты + автодеплой по SSH
├── apps/
│   ├── api-server/        # бот, API, биллинг, воркеры  ← основной код
│   ├── admin-panel/       # веб-панель администратора
│   └── client-apps/       # Telegram Mini App
├── config/                # тарифы, тексты бота, шаблоны Xray
├── scripts/               # установка мастера и нод, бэкапы
├── docker/                # docker-compose, Dockerfile, Caddy
└── docs/                  # ТЗ, архитектура, API, runbook, безопасность
```

---

## 🔧 Что менять без программирования

| Хочу изменить | Файл |
|---|---|
| Цены, периоды, скидки | `config/tariffs.yml` |
| Любой текст бота | `config/messages.yml` |
| Дни триала, лимит устройств | `config/tariffs.yml` → секция `limits` |
| Токены, пароли, домены | `.env` |

После правки: `make restart`.

---

## 🔒 Безопасность

- Логирование трафика пользователей выключено (`log.loglevel: none` в Xray).
- Приватные ключи Reality генерируются на нодах и не хранятся в БД сервиса.
- Все вебхуки платёжек проверяются по подписи (HMAC), Mini App — по `initData`.
- Подробности и чек-лист — [`docs/SECURITY.md`](docs/SECURITY.md).

---

## 🌿 Git-flow

`main` (прод) ← `test` (стенд) ← `dev` (разработка). Схема — [`docs/GIT_FLOW.md`](docs/GIT_FLOW.md).

---

## ⚖️ Дисклеймер

Сервис предназначен для легального использования (защита трафика в публичных сетях,
доступ к собственным ресурсам, приватность). Ответственность за соблюдение
законодательства своей юрисдикции — на операторе сервиса и его пользователях.

---

## Автор

[@xxxmaxxxq](https://github.com/xxxmaxxxq) — Python, автоматизация, AI-интеграции.
Бот проекта: [@getSaltVPN_bot](https://t.me/getSaltVPN_bot)
