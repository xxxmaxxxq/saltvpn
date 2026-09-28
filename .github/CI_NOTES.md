# CI/CD

Сами файлы workflow лежат в корне репозитория: `.github/workflows/`.

Так устроен GitHub Actions: он читает workflow только из корневой папки
`.github/workflows/`, даже если проект находится в подкаталоге.

| Файл | Что делает |
|---|---|
| `.github/workflows/test-api.yml` | ruff + pytest + проверка, что секреты не закоммичены |
| `.github/workflows/deploy-server.yml` | push в `test`/`main` → деплой по SSH |

Оба workflow срабатывают только на изменения внутри `vpn-service-project/**`,
поэтому правки учебных файлов в корне репозитория их не запускают.
