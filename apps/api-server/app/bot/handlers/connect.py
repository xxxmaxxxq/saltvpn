"""Экран подключения: deep links, инструкции, ссылка-подписка."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot import keyboards as kb
from app.bot.handlers._guards import answered_callback
from app.config import text
from app.db import session_scope
from app.services import subscriptions as sub_service
from app.services import users as user_service
from app.utils.deeplink import APP_STORES

router = Router(name="connect")


async def _subscription_url(telegram_id: int) -> str | None:
    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, telegram_id)
        if user is None or user.subscription is None:
            return None
        return sub_service.subscription_url(user)


@router.callback_query(F.data == "connect:show")
@answered_callback
async def show_connect(callback: CallbackQuery) -> None:
    url = await _subscription_url(callback.from_user.id)
    if url is None:
        await callback.message.answer(text("trial_already_used", min_price=149))
        return

    await callback.message.answer(
        text("connect_instructions", subscription_url=url, **APP_STORES),
        reply_markup=kb.connect_keyboard(url),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "connect:apps")
@answered_callback
async def show_other_apps(callback: CallbackQuery) -> None:
    url = await _subscription_url(callback.from_user.id)
    if url:
        await callback.message.edit_reply_markup(reply_markup=kb.other_apps_keyboard(url))


@router.callback_query(F.data == "connect:help")
@answered_callback
async def show_help(callback: CallbackQuery) -> None:
    url = await _subscription_url(callback.from_user.id)
    if url:
        await callback.message.answer(
            text("connect_instructions", subscription_url=url, **APP_STORES),
            disable_web_page_preview=True,
        )


@router.callback_query(F.data == "connect:link")
@answered_callback
async def show_link(callback: CallbackQuery) -> None:
    """Ссылка отдельным сообщением — её удобно скопировать одним тапом."""
    url = await _subscription_url(callback.from_user.id)
    if url:
        await callback.message.answer(
            text("subscription_link", subscription_url=url),
            disable_web_page_preview=True,
        )
