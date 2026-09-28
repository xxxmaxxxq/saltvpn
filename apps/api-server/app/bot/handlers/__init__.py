"""Все роутеры бота. Порядок подключения важен: admin проверяется первым."""

from aiogram import Router

from app.bot.handlers import (
    account,
    admin,
    buy,
    connect,
    menu,
    pause,
    referral,
    start,
    support,
)

router = Router(name="root")
router.include_router(admin.router)
router.include_router(start.router)
router.include_router(menu.router)
router.include_router(connect.router)
router.include_router(account.router)
router.include_router(buy.router)
router.include_router(pause.router)
router.include_router(referral.router)
router.include_router(support.router)

__all__ = ["router"]
