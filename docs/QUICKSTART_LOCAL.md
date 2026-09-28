# ⚡ Запуск бота на своём компьютере за 5 минут

Инструкция для того, чтобы **увидеть и потрогать интерфейс бота прямо сейчас**:
без сервера, без домена, без Docker, без оплаты хостинга.

> Что заработает: все экраны и кнопки — онбординг, триал, тарифы, оплата
> (создание счетов), кабинет, заморозка, рефералы, админ-команды.
> Что НЕ заработает: выдача настоящих VPN-ключей — для этого нужна панель
> Marzban с нодами, это отдельный этап (`docs/RUNBOOK.md`).

---

## Шаг 1. Поставьте Python 3.12

- **Windows:** https://www.python.org/downloads/ → при установке обязательно
  отметьте галочку **«Add Python to PATH»**.
- **macOS:** `brew install python@3.12`
- **Linux:** `sudo apt install python3.12 python3.12-venv`

Проверка — откройте терминал (на Windows: *PowerShell*) и введите:
```bash
python --version
```
Должно показать `Python 3.12.x` (на macOS/Linux может быть `python3 --version`).

---

## Шаг 2. Скачайте проект

```bash
git clone https://github.com/xxxmaxxxq/my-life.git
cd practice/vpn-service-project
```

Если `git` не установлен — скачайте ZIP кнопкой *Code → Download ZIP* на GitHub
и распакуйте.

---

## Шаг 3. Впишите токен бота

```bash
cp .env.example .env
```
(на Windows в PowerShell: `copy .env.example .env`)

Откройте файл `.env` в блокноте и заполните три строки:

```ini
BOT_TOKEN=токен_из_BotFather
BOT_USERNAME=имя_вашего_бота_без_собаки
ADMIN_IDS=ваш_telegram_id
```

Свой ID узнаете у бота @userinfobot. Токен — у @BotFather.
Остальное трогать не нужно.

---

## Шаг 4. Запустите

**macOS / Linux:**
```bash
bash scripts/run-local.sh
```

**Windows (PowerShell):**
```powershell
cd apps\api-server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cd ..\..
Add-Content .env "`nLOCAL_MODE=true`nBOT_MODE=polling"
cd apps\api-server
python -m app.bot.main
```

В консоли появится:
```
ЛОКАЛЬНЫЙ РЕЖИМ: база vpn_local.sqlite3, состояния в памяти...
Бот запущен: @ваш_бот (режим polling)
```

---

## Шаг 5. Откройте бота в Telegram и нажмите /start

Вы увидите приветствие и кнопку «🎁 Попробовать 3 дня бесплатно».
Дальше работают все экраны: кабинет, тарифы, периоды, способы оплаты,
заморозка, рефералы.

Админ-команды (если вписали свой ID в `ADMIN_IDS`):
`/stats`, `/user <id>`, `/give <id> <дней>`, `/nodes`, `/broadcast <текст>`.

Остановить бота — `Ctrl+C`.

---

## Что можно менять прямо сейчас

| Хочу изменить | Файл | Как применить |
|---|---|---|
| Любой текст бота | `config/messages.yml` | перезапустить бота |
| Цены и периоды | `config/tariffs.yml` | перезапустить бота |
| Дни триала, лимит устройств | `config/tariffs.yml` → `limits` | перезапустить бота |

Это обычные текстовые файлы — правятся в блокноте, программировать не нужно.

---

## Частые вопросы

**«Бот не отвечает».**
Проверьте, что в консоли нет ошибки и написано «Бот запущен». Самая частая
причина — опечатка в `BOT_TOKEN`.

**«Кнопка "Подключить в 1 клик" не появилась».**
Так и должно быть в локальном режиме: Telegram принимает в кнопках только
адреса `https://`, а локально у нас `http://localhost`. Бот вместо кнопки
отдаёт ссылку-подписку текстом. На боевом домене кнопка появится сама.

**«Ключ не работает / нет конфигов».**
Локально панели Marzban нет, поэтому настоящие ключи не выдаются. Подписка
в базе создаётся, интерфейс работает — VPN появится после развёртывания
по `docs/RUNBOOK.md`.

**«Как всё удалить и начать заново».**
Удалите файл `apps/api-server/vpn_local.sqlite3` — база создастся заново.

---

## Когда переходить на сервер

Как только интерфейс вас устроит: арендуете VPS, покупаете домен и идёте
по `docs/RUNBOOK.md`. Код тот же самый — меняется только `.env`
(`LOCAL_MODE=false`, адрес домена, пароли), после чего бот начинает
работать с Postgres, Redis и Marzban.

---

# 🖥 Запуск бота на VPS (первый шаг деплоя)

Если хотите, чтобы бот работал круглосуточно, но панель Marzban ещё не
настроена — поднимите на сервере только бота. Нужен VPS с Docker.

```bash
# 1. Папка проекта и код
mkdir -p ~/saltvpn && cd ~/saltvpn
git clone https://github.com/xxxmaxxxq/my-life.git repo
cd repo/vpn-service-project

# 2. Настройки (если .env уже создавали в ~/saltvpn — просто скопируйте его сюда)
cp ~/saltvpn/.env .env 2>/dev/null || cp .env.example .env
nano .env        # BOT_TOKEN, BOT_USERNAME, ADMIN_IDS

# 3. Запуск (скрипт сам соберёт образ и покажет лог)
bash scripts/deploy-bot.sh

# 4. Смотреть логи дальше
bash scripts/deploy-bot.sh logs
```

В логах должно появиться `Бот запущен: @ваш_бот (режим polling)`.
Бот переживает перезагрузку сервера (`restart: unless-stopped`), база лежит
в томе `botdata` и не теряется при пересборке образа.

| Задача | Команда (из папки `repo/vpn-service-project`) |
|---|---|
| Логи | `bash scripts/deploy-bot.sh logs` |
| Перезапуск после правки текстов/цен | `bash scripts/deploy-bot.sh restart` |
| Обновить код с GitHub | `bash scripts/deploy-bot.sh update` |
| Что запущено | `bash scripts/deploy-bot.sh status` |
| Остановить | `bash scripts/deploy-bot.sh stop` |

> Скрипт сделан без флагов в вызове намеренно: длинные docker-команды часто
> ломаются при копировании из браузера — обычный дефис `-` превращается в
> типографский минус `−`, и docker отвечает `unknown shorthand flag`.

Когда дойдёте до настоящих VPN-ключей — переходите на полный стек
(`docker-compose.yml` + домен + ноды) по [`RUNBOOK.md`](RUNBOOK.md).
Код тот же, меняется только `.env` и compose-файл.
