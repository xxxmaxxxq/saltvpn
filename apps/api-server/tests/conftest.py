"""Общие фикстуры тестов."""

import os
import sys
import tempfile
from pathlib import Path

# Чтобы тесты запускались и локально, и в CI без установки пакета
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Значения по умолчанию, чтобы Settings не требовал реального .env
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("JWT_SECRET", "test-secret")

# Тесты работают с настоящей базой SQLite во временном файле: часть ошибок
# (вроде обращения к незагруженной связи) проявляется только на живой сессии
_TMP_DB = Path(tempfile.gettempdir()) / "vpn_test.sqlite3"
_TMP_DB.unlink(missing_ok=True)
os.environ.setdefault("LOCAL_MODE", "true")
os.environ.setdefault("SQLITE_PATH", str(_TMP_DB))
