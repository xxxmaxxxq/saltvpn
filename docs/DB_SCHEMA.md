# 🗄 Схема базы данных

PostgreSQL 16. Все таблицы — в схеме `public`. Время хранится в UTC (`timestamptz`).

---

## users — пользователи

| Поле | Тип | Описание |
|---|---|---|
| id | bigserial PK | внутренний ID |
| telegram_id | bigint UNIQUE NOT NULL | ID в Telegram |
| username | varchar(64) | @username на момент последнего визита |
| first_name | varchar(128) | имя |
| language_code | varchar(8) | язык клиента |
| is_admin | boolean default false | админ сервиса |
| is_blocked | boolean default false | заблокирован нами |
| trial_used | boolean default false | триал уже выдавался |
| referral_code | varchar(16) UNIQUE | собственный код для приглашений |
| referred_by_id | bigint FK users.id | кто пригласил (проставляется один раз) |
| device_slots_bonus | smallint default 0 | доп. слоты за рефералов (0…3) |
| marzban_username | varchar(64) UNIQUE | имя аккаунта в панели (`u<tg_id>`) |
| subscription_token | varchar(64) UNIQUE | токен ссылки-подписки |
| created_at / updated_at | timestamptz | |

Индексы: `telegram_id`, `referral_code`, `referred_by_id`, `subscription_token`.

---

## subscriptions — подписки

| Поле | Тип | Описание |
|---|---|---|
| id | bigserial PK | |
| user_id | bigint FK users.id NOT NULL | |
| tariff_code | varchar(32) | `nl` / `ru` / `multi` |
| status | varchar(16) | `trial` `active` `paused` `expired` |
| expires_at | timestamptz | момент окончания |
| is_trial | boolean | текущая подписка — триальная |
| device_limit | smallint default 2 | лимит одновременных IP |
| streak_count | smallint default 0 | серия продлений без разрыва |
| pause_days_used | smallint default 0 | израсходовано дней паузы в этом году |
| paused_until | timestamptz NULL | до какого момента заморожено |
| traffic_used_bytes | bigint default 0 | трафик за текущий период (кэш из Marzban) |
| created_at / updated_at | timestamptz | |

Индексы: `user_id`, `(status, expires_at)` — по нему ходит воркер уведомлений.
Ограничение: у пользователя не более одной подписки со статусом ≠ `expired`.

---

## payments — платежи

| Поле | Тип | Описание |
|---|---|---|
| id | bigserial PK | |
| user_id | bigint FK users.id | |
| provider | varchar(16) | `platega` `cryptopay` `stars` |
| external_id | varchar(128) | ID платежа у провайдера |
| amount | numeric(10,2) | сумма |
| currency | varchar(8) | `RUB` `USDT` `XTR` |
| status | varchar(16) | `pending` `paid` `failed` `refunded` |
| tariff_code | varchar(32) | что купили |
| period_months | smallint | период |
| days_granted | smallint | сколько дней начислено (с бонусами) |
| promo_code | varchar(32) NULL | применённый промокод |
| payload | jsonb | сырой ответ/вебхук провайдера |
| created_at / paid_at | timestamptz | |

**UNIQUE (provider, external_id)** — защита от двойного начисления при повторном вебхуке.

---

## nodes — ноды

| Поле | Тип | Описание |
|---|---|---|
| id | bigserial PK | |
| code | varchar(16) UNIQUE | `nl-1`, `ru-1` |
| location | varchar(16) | `nl` / `ru` |
| host | varchar(128) | IP или домен |
| port | integer default 443 | боевой порт |
| marzban_node_id | integer | ID ноды в панели |
| status | varchar(16) | `healthy` `degraded` `disabled` |
| fail_count | smallint default 0 | провалов подряд |
| current_sni | varchar(128) | активная маскировка |
| sni_pool | jsonb | список резервных SNI |
| backup_ports | jsonb | список резервных портов |
| users_count | integer default 0 | для балансировки |
| last_check_at | timestamptz | |

---

## referrals — рефералы

| Поле | Тип | Описание |
|---|---|---|
| id | bigserial PK | |
| referrer_id | bigint FK users.id | пригласивший |
| referred_id | bigint FK users.id UNIQUE | приглашённый (один реферер навсегда) |
| is_qualified | boolean default false | приглашённый совершил первую оплату |
| qualified_at | timestamptz NULL | |
| reward_granted | boolean default false | слот уже начислен |
| created_at | timestamptz | |

---

## notifications — отправленные триггеры

| Поле | Тип | Описание |
|---|---|---|
| id | bigserial PK | |
| subscription_id | bigint FK subscriptions.id | |
| kind | varchar(32) | `trial_offer` `expire_3d` `expire_1d` `expire_2h` `expired` `winback` |
| sent_at | timestamptz | |

**UNIQUE (subscription_id, kind)** — каждый триггер ровно один раз на подписку.

---

## pause_periods — история заморозок

| Поле | Тип | Описание |
|---|---|---|
| id | bigserial PK | |
| subscription_id | bigint FK | |
| started_at / planned_until / ended_at | timestamptz | |
| days_requested | smallint | |
| days_actual | smallint NULL | фактически (при досрочной разморозке) |

---

## health_events — инциденты с нодами

| Поле | Тип | Описание |
|---|---|---|
| id | bigserial PK | |
| node_id | bigint FK nodes.id | |
| event | varchar(32) | `degraded` `healed` `failed` `manual_heal` |
| action | varchar(64) NULL | `sni_switch` `port_switch` `hysteria_promote` `node_failover` |
| details | jsonb | что именно поменяли |
| created_at | timestamptz | |

---

## promo_codes — промокоды

| Поле | Тип | Описание |
|---|---|---|
| id | bigserial PK | |
| code | varchar(32) UNIQUE | |
| discount_percent | smallint | 1…100 |
| bonus_days | smallint default 0 | |
| max_uses | integer NULL | NULL = безлимит |
| used_count | integer default 0 | |
| valid_until | timestamptz NULL | |
| is_personal_for_user_id | bigint NULL | персональный оффер (таймер триала) |
| is_active | boolean default true | |
