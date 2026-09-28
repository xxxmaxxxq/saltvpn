"""
FastAPI-приложение: вебхуки платежей, ссылка-подписка, Mini App API.

Запуск: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import base64
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import marzban as marzban_module
from app import site
from app.config import PROJECT_ROOT, get_settings, tariff_by_code
from app.db import close_connections, get_redis, get_session
from app.marzban import MarzbanClient, MarzbanError
from app.models import Payment, User
from app.payments.registry import get_provider
from app.schemas import (
    MeResponse,
    OrderRequest,
    OrderResponse,
    PauseRequest,
)
from app.services import billing, healing
from app.services import referrals as ref_service
from app.services import subscriptions as sub_service
from app.services import users as user_service
from app.utils import deeplink
from app.utils.logging_setup import setup_logging
from app.utils.security import verify_telegram_init_data

log = logging.getLogger(__name__)
settings = get_settings()

# Каталог Mini App: в контейнере задаётся переменной MINIAPP_DIR,
# при локальном запуске берётся из исходников
MINIAPP_DIR = Path(os.getenv("MINIAPP_DIR") or PROJECT_ROOT / "apps" / "client-apps" / "miniapp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("api")
    log.info("API запущен (env=%s)", settings.env)
    # Заводские секреты в боевом сервисе — открытая дверь. Не роняем
    # процесс (иначе один забытый ключ кладёт весь сервис), но говорим
    # об этом громко: та же проверка есть в `python -m app.cli doctor`.
    insecure = settings.insecure_defaults()
    if insecure:
        log.warning(
            "Не заменены секреты в .env: %s. Замените их: openssl rand -hex 32",
            ", ".join(insecure),
        )
    yield
    await close_connections()


app = FastAPI(
    title="VPN Service API",
    version="1.0.0",
    lifespan=lifespan,
    # В проде документацию не показываем — лишняя информация для посторонних
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
)

if MINIAPP_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(MINIAPP_DIR)), name="static")


# ── Служебное ──────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    """
    Проверка живости для мониторинга и Docker healthcheck.

    В локальном режиме Redis не используется (состояния бота живут в памяти),
    поэтому его отсутствие не считается проблемой — иначе сервис вечно
    рапортовал бы degraded и мониторинг звонил бы впустую.
    """
    db_ok = True
    redis_ok = False
    redis_required = not settings.local_mode

    try:
        from app.db import engine

        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
    except Exception:
        db_ok = False

    if redis_required:
        try:
            await get_redis().ping()
            redis_ok = True
        except Exception:
            redis_ok = False

    healthy = db_ok and (redis_ok or not redis_required)
    return {
        "status": "ok" if healthy else "degraded",
        "mode": "local" if settings.local_mode else "production",
        "db": db_ok,
        "redis": redis_ok if redis_required else "not_used",
    }


# ── Ссылка-подписка ────────────────────────────────────────────────────────


@app.get("/sub/{token}")
async def subscription(token: str, request: Request, session: AsyncSession = Depends(get_session)):
    """
    Отдать клиенту актуальные конфиги.

    Это сердце Dynamic Subscription: ссылка у пользователя постоянная,
    а содержимое собирается на лету из панели. Поэтому смена ноды, порта
    или SNI (в том числе автоматическая) доезжает до всех клиентов
    без перевыпуска ключей.
    """
    result = await session.execute(select(User).where(User.subscription_token == token))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Not found")

    # Имя профиля в приложении. Кириллица и эмодзи в заголовках HTTP
    # запрещены, поэтому клиенты договорились о префиксе base64:
    title = base64.b64encode(settings.service_name.encode()).decode()

    subscription_obj = user.subscription
    if subscription_obj is None or not subscription_obj.is_active:
        # Подписка кончилась: приложению отдаём пустой список конфигов,
        # чтобы оно показало понятный статус, а не ошибку сети
        return PlainTextResponse(
            content="",
            status_code=200,
            headers={
                "profile-title": f"base64:{title}",
                "profile-update-interval": "6",
                # Кириллица в заголовках HTTP запрещена (latin-1),
                # поэтому текст тоже уходит в base64 — клиенты это понимают
                "announce": "base64:"
                + base64.b64encode(
                    "Подписка закончилась. Продлите её в боте — ключ останется прежним.".encode()
                ).decode(),
            },
        )

    try:
        async with MarzbanClient() as mz:
            panel_user = await mz.get_user(user.marzban_username)

            # Панель могла не получить то, что мы уже записали у себя:
            # аккаунта нет вовсе (подписку выдали, пока панель лежала)
            # либо в нём остался старый срок (оплата прошла, а синхронизация
            # нет — деньги зачтены, доступа нет). Чиним на месте, а не
            # отдаём ошибку: клиент дёргает эту ссылку каждые 6 часов,
            # поэтому расхождение само себя лечит.
            if sub_service.panel_is_stale(panel_user, subscription_obj):
                log.warning(
                    "Аккаунт %s в панели расходится с базой — синхронизирую",
                    user.marzban_username,
                )
                await sub_service.sync_to_marzban(session, user, subscription_obj)
                panel_user = await mz.get_user(user.marzban_username)

            path = marzban_module.subscription_path_of(
                (panel_user or {}).get("subscription_url") or ""
            )
            if not path:
                raise HTTPException(status_code=404, detail="Subscription not found")

            marzban_url = f"{settings.marzban_base_url.rstrip('/')}{path}"
            resp = await mz._client.get(
                marzban_url, headers={"User-Agent": request.headers.get("user-agent", "")}
            )
    except MarzbanError as err:
        log.error("Панель недоступна при отдаче подписки: %s", err)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable") from err
    except httpx.HTTPError as err:
        log.error("Ошибка получения подписки: %s", err)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable") from err

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Not found")

    headers = {
        "Content-Type": resp.headers.get("content-type", "text/plain; charset=utf-8"),
        "profile-title": f"base64:{title}",
        # Как часто приложение перечитывает подписку. Ради этого
        # и работает Auto-Healing: смена ноды доезжает сама
        "profile-update-interval": "6",
        "profile-web-page-url": settings.public_base_url,
    }

    # Остаток трафика и дата окончания — приложение покажет их в карточке
    userinfo = resp.headers.get("subscription-userinfo")
    if userinfo:
        headers["subscription-userinfo"] = userinfo
    if settings.support_username:
        headers["support-url"] = f"https://t.me/{settings.support_username}"

    return PlainTextResponse(content=resp.text, status_code=resp.status_code, headers=headers)


# ── Страница импорта подписки в приложение ─────────────────────────────────

IMPORT_PAGE = """<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключение VPN</title>
<style>
 :root{{color-scheme:dark}}
 *{{box-sizing:border-box}}
 body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
      background:#0b1220;color:#e2e8f0;margin:0;padding:40px 20px;text-align:center}}
 .card{{background:#131c2e;border:1px solid #1e2b45;border-radius:20px;
        max-width:460px;margin:0 auto;padding:32px 24px}}
 .rocket{{font-size:44px;line-height:1;margin-bottom:12px}}
 h1{{font-size:21px;margin:0 0 8px}}
 .sub{{color:#4ade80;font-size:14px;margin:0 0 22px}}
 .a{{display:block;background:#2563eb;color:#fff;text-decoration:none;padding:15px;
     border-radius:12px;font-weight:600;margin:10px 0}}
 .g{{background:#1e293b;color:#cbd5e1;font-weight:500}}
 details{{margin-top:18px;text-align:left}}
 summary{{cursor:pointer;color:#94a3b8;font-size:14px;padding:8px 0}}
 p{{color:#94a3b8;font-size:13px;line-height:1.55}}
 code{{display:block;background:#0b1220;border:1px solid #1e2b45;padding:12px;
       border-radius:10px;word-break:break-all;font-size:11px;color:#cbd5e1;margin-top:8px}}
 .hint{{margin-top:20px;font-size:12px;color:#64748b}}
</style></head><body>
<div class="card">
  <div class="rocket">🚀</div>
  <h1>Открываем Happ…</h1>
  <p class="sub">Подтвердите открытие приложения</p>

  <a class="a" href="{first}">Перейти в Happ</a>

  <details>
    <summary>Не открылось или другое приложение</summary>
    <a class="a g" href="{happ_b64}">Happ (запасной формат)</a>
    <a class="a g" href="hiddify://import/{sub_enc}">Hiddify</a>
    <a class="a g" href="v2raytun://import/{sub}">v2RayTun</a>
    <a class="a g" href="streisand://import/{sub_enc}">Streisand (iOS)</a>
    <p>Приложения ещё нет?
      <a style="color:#60a5fa" href="{ios}">App Store</a> ·
      <a style="color:#60a5fa" href="{android}">Google Play</a>
    </p>
    <p>Ссылка-подписка для ручного добавления:<code>{sub}</code></p>
  </details>

  <p class="hint">Ссылка постоянная: серверы могут меняться — ключ остаётся прежним.</p>
</div>
<script>
 // На телефоне открываем приложение сразу: это экономит один тап.
 // Небольшая задержка нужна, чтобы страница успела отрисоваться —
 // иначе при отсутствии приложения человек увидит пустой экран.
 setTimeout(function(){{ location.href = "{first}"; }}, 600);
</script>
</body></html>"""


@app.get("/i/{token}", response_class=HTMLResponse)
async def import_page(token: str, app: str = "happ", session: AsyncSession = Depends(get_session)):
    """
    Страница импорта подписки.

    Нужна потому, что Telegram разрешает в кнопках только http(s)-адреса:
    схему happ://import/... в кнопку поставить нельзя. Кнопка ведёт сюда,
    а страница уже открывает приложение — и остаётся запасной вариант,
    если приложение не установлено или это десктоп.
    """
    result = await session.execute(select(User).where(User.subscription_token == token))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Not found")

    sub_url = f"{settings.public_base_url.rstrip('/')}/sub/{token}"
    links = deeplink.all_links(sub_url)
    return HTMLResponse(
        IMPORT_PAGE.format(
            sub=sub_url,
            sub_enc=quote(sub_url, safe=""),
            first=links.get(app, links["happ"]),
            happ_b64=links["happ_base64"],
            ios=deeplink.APP_STORES["happ_ios"],
            android=deeplink.APP_STORES["happ_android"],
        )
    )


# ── Вебхуки платежей ───────────────────────────────────────────────────────


async def _process_webhook(provider_code: str, request: Request, session: AsyncSession) -> dict:
    """
    Общая обработка вебхука: проверка подписи -> поиск платежа -> начисление.

    Ни одно начисление не происходит до успешной проверки подписи.
    """
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    provider = get_provider(provider_code)

    try:
        parsed = provider.parse_webhook(body, headers)
    except ValueError as err:
        log.warning("Вебхук %s отклонён: %s", provider_code, err)
        raise HTTPException(status_code=401, detail="Invalid signature") from err

    if not parsed.is_paid:
        return {"ok": True, "ignored": "not_paid"}

    # Уведомление без подписи (ЮKassa) само по себе ничего не доказывает:
    # провайдер переспрашивается по своему API, и только его ответ —
    # основание начислить дни. Провайдеры с honest-подписью вернут None.
    confirmed = await provider.confirm_payment(parsed.external_id)
    if confirmed is False:
        log.warning(
            "Вебхук %s: провайдер не подтвердил платёж ext=%s — начисление отменено",
            provider_code,
            parsed.external_id,
        )
        raise HTTPException(status_code=400, detail="Payment not confirmed by provider")

    payment: Payment | None = None
    if parsed.our_payment_id:
        payment = await session.get(Payment, parsed.our_payment_id)
    if payment is None and parsed.external_id:
        payment = await billing.find_payment_by_external_id(
            session, provider_code, parsed.external_id
        )

    if payment is None:
        log.error("Вебхук %s: платёж не найден (ext=%s)", provider_code, parsed.external_id)
        # Возвращаем 200, чтобы провайдер не долбил повторами вечно
        return {"ok": True, "ignored": "payment_not_found"}

    # Счёт у провайдера и наш заказ должны быть одной и той же покупкой.
    # Иначе остаётся лазейка: прислать уведомление с настоящим id дешёвого
    # оплаченного счёта, а в metadata подставить чужой дорогой заказ —
    # провайдер такой платёж честно подтвердит, ведь он и правда оплачен.
    # external_id мы записываем в момент выставления счёта, так что
    # подмену видно сразу.
    if not billing.invoice_belongs_to_payment(payment.external_id, parsed.external_id):
        log.error(
            "Вебхук %s: счёт %s не принадлежит платежу #%s (ожидался %s)",
            provider_code,
            parsed.external_id,
            payment.id,
            payment.external_id,
        )
        raise HTTPException(status_code=400, detail="Payment mismatch")

    result = await billing.apply_payment(session, payment, parsed.external_id, parsed.raw)
    if result.get("already_processed"):
        return {"ok": True, "duplicate": True}

    # Уведомляем пользователя (бот и API — разные процессы, поэтому свой Bot)
    from app.bot.notifications import create_bot, send_payment_success

    bot = create_bot()
    try:
        await send_payment_success(bot, result)
    finally:
        await bot.session.close()

    return {"ok": True}


@app.post("/webhook/platega")
async def webhook_platega(request: Request, session: AsyncSession = Depends(get_session)):
    return await _process_webhook("platega", request, session)


@app.post("/webhook/yookassa")
async def webhook_yookassa(request: Request, session: AsyncSession = Depends(get_session)):
    return await _process_webhook("yookassa", request, session)


@app.post("/webhook/cryptopay")
async def webhook_cryptopay(request: Request, session: AsyncSession = Depends(get_session)):
    return await _process_webhook("cryptopay", request, session)


@app.post("/webhook/telegram")
async def webhook_telegram(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    """Приём апдейтов Telegram в режиме webhook."""
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid secret token")

    from aiogram.types import Update

    from app.bot.main import build_dispatcher
    from app.bot.notifications import create_bot

    update = Update.model_validate(await request.json(), context={"bot": None})
    bot = create_bot()
    dispatcher = build_dispatcher()
    try:
        await dispatcher.feed_update(bot, update)
    finally:
        await bot.session.close()
    return {"ok": True}


# ── Mini App ───────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def landing():
    """
    Витрина сервиса.

    Нужна не только людям: платёжные системы не включают приём оплаты,
    пока не увидят страницу с описанием услуги, ценами, офертой и
    реквизитами. Раньше корень домена отдавал 404.
    """
    return HTMLResponse(site.landing_html())


@app.get("/offer", response_class=HTMLResponse)
async def offer():
    """Публичная оферта — на неё ссылается витрина и требует модерация."""
    return HTMLResponse(site.offer_html())


@app.get("/app", response_class=HTMLResponse)
async def miniapp_page():
    """Страница личного кабинета (Telegram Mini App)."""
    index = MINIAPP_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>Mini App не собран</h1>", status_code=404)
    return HTMLResponse(index.read_text(encoding="utf-8"))


async def current_user(
    x_telegram_init_data: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> User:
    """
    Авторизация Mini App.

    Никаких логинов и паролей: подлинность пользователя подтверждает
    подпись Telegram, которую мы проверяем ключом бота.
    """
    try:
        data = verify_telegram_init_data(x_telegram_init_data, settings.bot_token)
    except ValueError as err:
        raise HTTPException(status_code=401, detail=str(err)) from err

    tg_user = data.get("user") or {}
    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="No user in initData")

    user, _ = await user_service.get_or_create(
        session,
        telegram_id=int(telegram_id),
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
        language_code=tg_user.get("language_code"),
    )
    return user


@app.get("/api/miniapp/me", response_model=MeResponse)
async def miniapp_me(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    await sub_service.refresh_traffic(session, user)
    sub_url = sub_service.subscription_url(user)
    stats = await ref_service.stats(session, user)
    offer = await billing.active_personal_offer(session, user)

    subscription_info = None
    if user.subscription:
        s = user.subscription
        tariff = tariff_by_code(s.tariff_code)
        _, next_bonus = sub_service.calc_streak_bonus(s)
        subscription_info = {
            "tariff": s.tariff_code,
            "tariff_title": tariff["title"] if tariff else s.tariff_code,
            "status": s.status,
            "expires_at": s.expires_at,
            "days_left": s.days_left,
            "device_limit": user_service.effective_device_limit(user, s),
            "devices_online": 0,
            "traffic_used_gb": round(s.traffic_used_bytes / 1024**3, 2),
            "streak_count": s.streak_count,
            "next_streak_bonus_days": next_bonus,
            "pause_days_left": sub_service.pause_days_left(s),
            "paused_until": s.paused_until,
            "is_trial": s.is_trial,
        }

    return {
        "user": {
            "telegram_id": user.telegram_id,
            "first_name": user.first_name,
            "referral_code": user.referral_code,
        },
        "subscription": subscription_info,
        "subscription_url": sub_url,
        "import_url": deeplink.import_page(sub_url),
        "deeplinks": deeplink.all_links(sub_url),
        "referrals": stats,
        "personal_offer": (
            {
                "code": offer.code,
                "discount_percent": offer.discount_percent,
                "valid_until": offer.valid_until.isoformat() if offer.valid_until else None,
            }
            if offer
            else None
        ),
    }


@app.get("/api/miniapp/tariffs")
async def miniapp_tariffs(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    offer = await billing.active_personal_offer(session, user)
    discount = offer.discount_percent if offer else 0
    return {"discount": discount, "tariffs": billing.tariff_showcase(discount)}


@app.post("/api/miniapp/order", response_model=OrderResponse)
async def miniapp_order(
    payload: OrderRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    payment, amount = await billing.create_order(
        session,
        user,
        payload.tariff_code,
        payload.period_months,
        payload.provider,
        payload.promo_code,
    )
    provider = get_provider(payload.provider)
    try:
        invoice = await provider.create_invoice(
            payment_id=payment.id,
            amount_rub=amount,
            description=f"VPN {payload.tariff_code.upper()} на {payload.period_months} мес.",
            telegram_id=user.telegram_id,
        )
    except Exception as err:
        log.exception("Mini App: не удалось создать счёт: %s", err)
        raise HTTPException(status_code=402, detail="Payment provider unavailable") from err

    payment.external_id = invoice.external_id
    await session.flush()

    return {
        "payment_url": invoice.payment_url,
        "payment_id": payment.id,
        "amount": amount,
        "is_telegram_invoice": invoice.is_telegram_invoice,
    }


@app.post("/api/miniapp/pause")
async def miniapp_pause(
    payload: PauseRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        subscription = await sub_service.pause(session, user, payload.days)
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return {"ok": True, "paused_until": subscription.paused_until}


@app.post("/api/miniapp/resume")
async def miniapp_resume(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    try:
        subscription = await sub_service.resume(session, user, early=True)
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return {"ok": True, "expires_at": subscription.expires_at}


@app.post("/api/miniapp/heal")
async def miniapp_heal(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    """Кнопка «Не работает» из Mini App."""
    result = await healing.heal_user(session, user)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("reason", "cannot_heal"))
    return result


# ── Внутренние эндпоинты для admin-panel ───────────────────────────────────


def check_internal_token(x_internal_token: str = Header(default="")) -> None:
    if x_internal_token != settings.api_internal_token:
        raise HTTPException(status_code=401, detail="Invalid internal token")


@app.get("/internal/nodes", dependencies=[Depends(check_internal_token)])
async def internal_nodes(session: AsyncSession = Depends(get_session)):
    from app.models import Node

    nodes = (await session.execute(select(Node).order_by(Node.code))).scalars()
    return [
        {
            "code": n.code,
            "location": n.location,
            "host": n.host,
            "port": n.port,
            "status": n.status,
            "fail_count": n.fail_count,
            "users_count": n.users_count,
            "latency_ms": n.last_latency_ms,
            "current_sni": n.current_sni,
            "last_check_at": n.last_check_at,
        }
        for n in nodes
    ]


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Не показываем пользователю трейсбек, но пишем его в лог."""
    log.exception("Необработанная ошибка на %s: %s", request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Internal error"})
