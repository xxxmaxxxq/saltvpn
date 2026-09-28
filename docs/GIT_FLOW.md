# 🌿 Git-flow проекта

## Ветки

| Ветка | Назначение | Куда деплоится |
|---|---|---|
| `main` | Стабильный прод. Только проверенный код. | боевой мастер-сервер |
| `test` | Тестовый стенд. Кандидат в релиз. | тестовый VPS (или тот же с другим ботом) |
| `dev` | Активная разработка, новые фишки. | локально / docker |

Поток: `feature/*` → **`dev`** → **`test`** → **`main`**.

```
feature/pause-subscription ──┐
feature/auto-healing ────────┼──► dev ──(прогон тестов)──► test ──(сутки на стенде)──► main
feature/miniapp ─────────────┘
```

## Правила

1. В `main` **никогда** не пушим напрямую — только merge из `test`.
2. Каждая фишка — отдельная ветка `feature/<короткое-имя>`.
3. Коммит описывает **что и зачем**, а не «fix». Префиксы:
   `feat:` новая функция · `fix:` исправление · `docs:` документация ·
   `chore:` инфраструктура · `refactor:` рефакторинг · `test:` тесты.
4. CI (`.github/workflows/test-api.yml`) обязан быть зелёным до merge.
5. Секреты (`.env`, ключи) в git не попадают никогда — проверяйте `git status` перед коммитом.

## Первичная настройка веток

```bash
git checkout -b dev main && git push -u origin dev
git checkout -b test main && git push -u origin test
git checkout main
```

## Типичный цикл работы

```bash
git checkout dev && git pull origin dev
git checkout -b feature/new-tariff          # начали фишку
# ... правки ...
git add . && git commit -m "feat(tariffs): добавлен годовой тариф со скидкой 35%"
git checkout dev && git merge feature/new-tariff
git push origin dev                          # CI прогонит тесты

# когда фишка проверена — на стенд:
git checkout test && git merge dev && git push origin test   # автодеплой на тест

# после суток без ошибок — в прод:
git checkout main && git merge test && git push origin main  # автодеплой на прод
git tag -a v1.0.0 -m "Релиз 1.0" && git push --tags
```

## Откат прода

```bash
git checkout main
git revert <хэш-плохого-коммита>
git push origin main        # автодеплой вернёт предыдущее поведение
```
