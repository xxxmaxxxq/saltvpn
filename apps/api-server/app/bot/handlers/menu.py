"""
Главное меню и команды из синего меню Telegram.

Зачем отдельный модуль: у конкурентов (Atom, Quattro) человек попадает
в нужный раздел двумя способами — кнопкой «🏠 Главное меню» в сообщении
и списком команд рядом с полем ввода. Здесь собраны оба входа, чтобы
в переписке нельзя было «потеряться».
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.config import limits, text
from app.db import session_scope
from app.services import users as user_service
from app.utils.deeplink import APP_STORES

router = Router(name="menu")


async def _menu_markup(telegram_id: int):
    """Собрать меню под конкретного человека: новичку — кнопка триала."""
    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, telegram_id)
        has_subscription = user is not None and user.subscription is not None
        trial_available = user is None or not user.trial_used
    return kb.main_menu(has_subscription=has_subscription, is_trial_available=trial_available)


async def send_main_menu(message: Message, telegram_id: int) -> None:
    markup = await _menu_markup(telegram_id)
    await message.answer(text("main_menu"), reply_markup=markup)


@router.callback_query(F.data == "menu:main")
async def show_main_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    await send_main_menu(callback.message, callback.from_user.id)


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    if message.from_user is None:
        return
    await send_main_menu(message, message.from_user.id)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Инструкции по подключению для всех платформ."""
    await message.answer(
        text("help", **APP_STORES, device_limit=limits().get("device_limit_default", 2)),
        reply_markup=kb.help_keyboard(),
        disable_web_page_preview=True,
    )


@router.message(Command("buy"))
async def cmd_buy(message: Message) -> None:
    """Команда /buy — тот же экран тарифов, что и по кнопке."""
    from app.services import billing

    await message.answer(
        text("choose_tariff", device_limit=limits().get("device_limit_default", 2)),
        reply_markup=kb.tariffs_keyboard(billing.tariff_showcase()),
    )


@router.message(Command("connect"))
async def cmd_connect(message: Message) -> None:
    """Команда /connect — ключи и ссылка-подписка."""
    from app.services import subscriptions as sub_service

    if message.from_user is None:
        return

    async with session_scope() as session:
        user = await user_service.get_by_telegram_id(session, message.from_user.id)
        if user is None or user.subscription is None:
            await send_main_menu(message, message.from_user.id)
            return
        url = sub_service.subscription_url(user)

    await message.answer(
        text("connect_instructions", subscription_url=url, **APP_STORES),
        reply_markup=kb.connect_keyboard(url),
        disable_web_page_preview=True,
    )
