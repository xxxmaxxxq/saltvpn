"""
Публичный сайт сервиса: витрина и оферта.

Зачем он нужен, если продажи идут в боте: платёжные системы не включают
приём оплаты, пока не увидят сайт с тем, за что берут деньги. ЮKassa
проверяет четыре вещи — настоящие услуги с фиксированными ценами, как
покупатель получит заказ, оферту и контакты с ИНН самозанятого. Всё это
собрано здесь.

Цены не дублируются руками: страница считает их тем же calc_price, что
и бот, из config/tariffs.yml. Иначе витрина и бот однажды разошлись бы,
а расхождение цены на сайте с фактическим списанием — прямая претензия
от покупателя и повод отключить приём платежей.

Реквизиты берутся из .env: это персональные данные, в репозитории им
не место.
"""

from __future__ import annotations

import html
from datetime import date

from app.config import calc_price, get_settings, get_tariffs, limits

settings = get_settings()

# Периоды, которые показываем на витрине: месяц как база и год как самый
# выгодный. Полный список остаётся в боте — на странице важна не полнота,
# а честная нижняя граница цены.
SHOWCASE_MONTHS = (1, 12)


def _esc(value: str) -> str:
    """Экранирование: реквизиты приходят из .env, а не из кода."""
    return html.escape(str(value or ""), quote=True)


def _requisites_block() -> str:
    """ФИО и ИНН. Если не заполнены — честная заглушка, а не выдумка."""
    missing = settings.missing_requisites()
    if missing:
        return (
            '<p class="warn">Реквизиты не заполнены. Добавьте в <code>.env</code>: '
            + _esc(", ".join(missing))
            + "</p>"
        )

    rows = [
        ("Исполнитель", f"Самозанятый {settings.owner_full_name}"),
        ("ИНН", settings.owner_inn),
        ("Электронная почта", settings.owner_email),
    ]
    if settings.owner_phone:
        rows.append(("Телефон", settings.owner_phone))
    if settings.owner_address:
        rows.append(("Почтовый адрес", settings.owner_address))
    # Телеграм — рабочий канал поддержки, а не замена почте и телефону:
    # он идёт дополнением, чтобы у покупателя был быстрый способ связи
    if settings.support_username:
        rows.append(("Поддержка в Telegram", f"@{settings.support_username}"))

    cells = "".join(
        f"<div class='req-row'><span>{_esc(name)}</span><b>{_esc(value)}</b></div>"
        for name, value in rows
    )
    return f"<div class='req'>{cells}</div>"


def _tariff_cards() -> str:
    """Карточки тарифов с ценами из tariffs.yml."""
    cards = []
    for tariff in get_tariffs().get("tariffs", []):
        code = tariff["code"]
        prices = []
        for months in SHOWCASE_MONTHS:
            total = calc_price(code, months)
            per_month = round(total / months)
            label = "в месяц" if months == 1 else f"за год — {per_month} ₽/мес"
            prices.append(
                f"<div class='price'><b>{total}&nbsp;₽</b><span>{_esc(label)}</span></div>"
            )

        badge = tariff.get("badge")
        badge_html = f"<em class='badge'>{_esc(badge)}</em>" if badge else ""
        cards.append(
            f"""<article class="card">
      <h3>{_esc(tariff["title"])}{badge_html}</h3>
      <p>{_esc(tariff["description"])}</p>
      {"".join(prices)}
    </article>"""
        )
    return "\n".join(cards)


def _min_price() -> int:
    codes = [t["code"] for t in get_tariffs().get("tariffs", [])]
    return min(calc_price(code, 1) for code in codes) if codes else 0


def _page(title: str, body: str) -> str:
    """Общая обёртка. ИНН стоит в подвале каждой страницы — ЮKassa просит
    ссылку на страницу с реквизитами, и так подходит любая."""
    name = _esc(settings.service_name)
    inn = f"ИНН {_esc(settings.owner_inn)}" if settings.owner_inn else ""
    owner = _esc(settings.owner_full_name)
    footer_person = f"Самозанятый {owner}. {inn}" if owner and inn else inn
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
  :root {{
    --bg: #f6f7f9; --surface: #ffffff; --text: #16181d; --muted: #5d6470;
    --line: #e4e7ec; --accent: #2f6df6; --warn: #b4460f; --warn-bg: #fdf1e7;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #101216; --surface: #181b21; --text: #eceef2; --muted: #a0a7b4;
      --line: #2a2f38; --accent: #6f9bff; --warn: #f0a26a; --warn-bg: #2a1e14;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #101216; --surface: #181b21; --text: #eceef2; --muted: #a0a7b4;
    --line: #2a2f38; --accent: #6f9bff; --warn: #f0a26a; --warn-bg: #2a1e14;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 40px 16px 64px; }}
  h1 {{ font-size: 30px; line-height: 1.25; margin: 0 0 12px; }}
  h2 {{ font-size: 21px; margin: 40px 0 14px; }}
  h3 {{ font-size: 18px; margin: 0 0 8px; }}
  p {{ margin: 0 0 12px; }}
  .lead {{ color: var(--muted); font-size: 18px; margin-bottom: 28px; }}
  .cards {{ display: grid; gap: 14px; grid-template-columns: 1fr; }}
  @media (min-width: 720px) {{ .cards {{ grid-template-columns: repeat(3, 1fr); }} }}
  .card {{
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 14px; padding: 20px;
  }}
  .card p {{ color: var(--muted); font-size: 14px; min-height: 44px; }}
  .price {{ display: flex; align-items: baseline; gap: 8px; margin-top: 10px; }}
  .price b {{ font-size: 22px; }}
  .price span {{ color: var(--muted); font-size: 13px; }}
  .badge {{
    font-style: normal; font-size: 12px; margin-left: 8px; padding: 2px 8px;
    border-radius: 999px; background: var(--accent); color: #fff; vertical-align: middle;
  }}
  .btn {{
    display: inline-block; margin-top: 24px; padding: 13px 26px; border-radius: 10px;
    background: var(--accent); color: #fff; text-decoration: none; font-weight: 600;
  }}
  ol, ul {{ padding-left: 22px; }}
  li {{ margin-bottom: 8px; }}
  .req {{
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 14px; padding: 8px 20px;
  }}
  .req-row {{
    display: flex; flex-wrap: wrap; gap: 4px 16px; justify-content: space-between;
    padding: 12px 0; border-bottom: 1px solid var(--line);
  }}
  .req-row:last-child {{ border-bottom: 0; }}
  .req-row span {{ color: var(--muted); }}
  .warn {{
    background: var(--warn-bg); color: var(--warn); border-radius: 10px; padding: 14px 16px;
  }}
  footer {{
    margin-top: 48px; padding-top: 20px; border-top: 1px solid var(--line);
    color: var(--muted); font-size: 14px;
  }}
  a {{ color: var(--accent); }}
  code {{
    background: var(--bg); border: 1px solid var(--line);
    border-radius: 5px; padding: 1px 5px; font-size: 13px;
  }}
</style>
</head>
<body>
<div class="wrap">
{body}
<footer>
  <p>{name}. {footer_person}</p>
  <p><a href="/">Услуги и цены</a> · <a href="/offer">Публичная оферта</a></p>
</footer>
</div>
</body>
</html>"""


def landing_html() -> str:
    """Витрина: что продаём, почём и как покупатель получит заказ."""
    cfg = limits()
    trial_days = int(cfg.get("trial_days", 3))
    devices = int(cfg.get("device_limit_default", 2))
    bot = _esc(settings.bot_username)
    name = _esc(settings.service_name)

    body = f"""<h1>{name} — доступ к VPN по подписке</h1>
<p class="lead">
  Шифрованное подключение через серверы в Нидерландах и России.
  Без ограничений скорости и объёма трафика, до {devices} устройств
  на одной подписке. Первые {trial_days} дня — бесплатно, карта не нужна.
</p>

<h2>Услуги и цены</h2>
<div class="cards">
{_tariff_cards()}
</div>
<p class="lead" style="margin-top:16px;font-size:14px">
  Цены фиксированные, указаны за весь период и списываются один раз.
  При оплате на 3 и 6 месяцев действуют скидки 10% и 20% — итоговая
  сумма всегда показывается до списания.
</p>

<h2>Как получить доступ после оплаты</h2>
<p>
  Услуга цифровая, доставка не требуется — доступ открывается
  автоматически сразу после оплаты, обычно в течение минуты.
</p>
<ol>
  <li>Открыть телеграм-бот <a href="https://t.me/{bot}">@{bot}</a> и нажать «Старт».</li>
  <li>Выбрать тариф и срок, оплатить по СБП.</li>
  <li>Бот пришлёт ссылку-подписку и кнопку для импорта в приложение
      (Happ, Hiddify, v2RayTun — любое на выбор).</li>
  <li>Нажать кнопку — профиль добавится сам, подключение заработает сразу.</li>
</ol>
<p>
  Если доступ не открылся в течение 10 минут, напишите нам — вернём
  деньги или продлим подписку, на ваш выбор.
</p>

<h2>Контакты и реквизиты</h2>
<p>
  По любым вопросам — оплата, возврат,технические неполадки — пишите
  на электронную почту или в Telegram. Отвечаем ежедневно с 10:00 до
  22:00 по московскому времени, обычно в течение часа.
</p>
{_requisites_block()}

<a class="btn" href="https://t.me/{bot}">Открыть бота и попробовать бесплатно</a>"""
    return _page(f"{settings.service_name} — VPN по подписке", body)


def offer_html() -> str:
    """Публичная оферта на оказание услуг."""
    cfg = limits()
    trial_days = int(cfg.get("trial_days", 3))
    devices = int(cfg.get("device_limit_default", 2))
    name = _esc(settings.service_name)
    bot = _esc(settings.bot_username)
    today = date.today().strftime("%d.%m.%Y")

    body = f"""<h1>Публичная оферта</h1>
<p class="lead">Редакция от {today}</p>

<p>
  Настоящий документ является публичной офертой — предложением заключить
  договор на оказание услуг на условиях, изложенных ниже. Оплата услуги
  означает полное и безоговорочное принятие этих условий.
</p>

<h2>1. Стороны</h2>
<p>
  <b>Исполнитель</b> — лицо, указанное в разделе «Реквизиты» ниже,
  применяющее специальный налоговый режим «Налог на профессиональный
  доход».<br>
  <b>Заказчик</b> — физическое лицо, оплатившее услугу.
</p>

<h2>2. Предмет договора</h2>
<p>
  Исполнитель предоставляет Заказчику доступ к сервису {name} —
  шифрованному сетевому подключению (VPN) через серверы Исполнителя,
  на срок оплаченной подписки. Услуга оказывается дистанционно,
  в автоматическом режиме, через телеграм-бот
  <a href="https://t.me/{bot}">@{bot}</a>.
</p>

<h2>3. Порядок оказания и стоимость</h2>
<ul>
  <li>Стоимость и сроки указаны в разделе «Услуги и цены» на
      <a href="/">главной странице</a> и в боте. Цены фиксированные,
      в рублях, списываются разовым платежом за выбранный период.</li>
  <li>Доступ открывается автоматически после поступления оплаты,
      как правило в течение одной минуты.</li>
  <li>Подписка действует {devices} устройства одновременно, если в тарифе
      не указано иное.</li>
  <li>Новым пользователям предоставляется пробный период
      {trial_days} дня без оплаты.</li>
  <li>Подписка разовая и автоматически не продлевается: списаний без
      вашего участия не происходит.</li>
</ul>

<h2>4. Возврат средств</h2>
<p>
  Если услуга не была оказана — доступ не открылся или не работал по
  вине Исполнителя — Заказчик вправе потребовать возврат. Обращение
  направляется на электронную почту из раздела «Реквизиты»; срок
  рассмотрения — 3 рабочих дня, возврат производится тем же способом,
  которым была произведена оплата, в течение 10 рабочих дней.
</p>
<p>
  За неиспользованный остаток оплаченного периода возврат производится
  пропорционально, по запросу Заказчика.
</p>

<h2>5. Обязанности и ограничения</h2>
<ul>
  <li>Заказчик обязуется не использовать сервис для действий, нарушающих
      законодательство, а также для рассылки спама, сетевых атак и
      сканирования чужих ресурсов.</li>
  <li>Исполнитель вправе приостановить доступ при нарушении этого пункта
      без возврата средств за оставшийся период.</li>
  <li>Передача доступа третьим лицам сверх лимита устройств тарифа
      не допускается.</li>
</ul>

<h2>6. Данные пользователей</h2>
<p>
  Исполнитель не ведёт журналы посещаемых сайтов и передаваемого
  трафика: логирование подключений на серверах отключено. Хранятся
  только идентификатор Telegram, сведения о подписке и платежах —
  в объёме, необходимом для оказания услуги и отчётности.
</p>

<h2>7. Ответственность</h2>
<p>
  Услуга предоставляется «как есть». Исполнитель не гарантирует
  доступность сервиса при обстоятельствах вне его контроля: блокировках
  со стороны операторов связи, сбоях у хостинг-провайдера, действиях
  третьих лиц. При длительном простое по вине Исполнителя срок подписки
  продлевается на время простоя.
</p>

<h2>8. Реквизиты</h2>
{_requisites_block()}"""
    return _page(f"Публичная оферта — {settings.service_name}", body)
