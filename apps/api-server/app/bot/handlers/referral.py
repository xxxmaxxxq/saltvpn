"""Экран реферальной программы: слоты устройств за приглашённых друзей."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.handlers._guards import answered_callback
from app.config import text
from app.db import session_scope
from app.services import referrals as ref_service
from app.services import users as user_service

router = Router(name="referral")


@router.message(Command("ref"))
async def cmd_ref(message: Message) -> None:
    if message.from_user is None:
        return
    await _send_referral(message.from_user.id, message)


@router.callback_query(F.data == "ref:show")
@answered_callback
async def show_referral(callback: CallbackQuery) -> None:
    await _send_referral(callback.from_user.id, callback.message)


async def _send_referral(telegram_id: int, message: Message) -> None:
    async with session_scope() as session:
        user, _ = await user_service.get_or_create(session, telegram_id=telegram_id)
        stats = await ref_service.stats(session, user)

    body = text(
        "referral_info",
        per_slot=stats["per_slot"],
        ref_link=stats["link"],
        total=stats["total"],
        qualified=stats["qualified"],
        slots=stats["slots_earned"],
        next_in=stats["next_slot_in"],
        invited_bonus=ref_service.invited_bonus_days(),
    )
    await message.answer(
        body, reply_markup=kb.referral_keyboard(stats["link"]), disable_web_page_preview=True
    )
