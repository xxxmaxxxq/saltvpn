/* ══════════════════════════════════════════════════════════════════════
   Mini App — логика личного кабинета.

   Авторизация: никаких логинов. Мы отправляем Telegram.WebApp.initData
   в заголовке, сервер проверяет подпись ключом бота (app/utils/security.py).
   ══════════════════════════════════════════════════════════════════════ */

const tg = window.Telegram?.WebApp;
const API = '';                 // тот же домен, что и страница
let state = null;               // ответ /api/miniapp/me
let offerTimerId = null;

// ── Инициализация ────────────────────────────────────────────────────
function init() {
  if (tg) {
    tg.ready();
    tg.expand();
    tg.setHeaderColor?.('secondary_bg_color');
  }
  bindEvents();
  loadMe();
}

// ── Запросы к API ────────────────────────────────────────────────────
async function api(path, options = {}) {
  const response = await fetch(API + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': tg?.initData || '',
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Ошибка ${response.status}`);
  }
  return response.json();
}

async function loadMe() {
  try {
    state = await api('/api/miniapp/me');
    render(state);
    document.getElementById('loader').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
  } catch (err) {
    document.getElementById('loader').innerHTML =
      `<p>Не удалось загрузить кабинет.<br><small>${err.message}</small></p>`;
  }
}

// ── Отрисовка ────────────────────────────────────────────────────────
function render(data) {
  const sub = data.subscription;

  if (!sub) {
    document.getElementById('tariff-title').textContent = 'Подписки пока нет';
    document.getElementById('status-line').textContent = 'Оформите доступ — это займёт минуту';
    setRing(0, 30, '#ef4444');
    openTariffs();
    return;
  }

  // Кольцо остатка дней: зелёное > 7 дней, жёлтое 3-7, красное < 3
  const total = sub.is_trial ? 3 : 30;
  const color = sub.days_left > 7 ? '#22c55e' : sub.days_left > 3 ? '#f59e0b' : '#ef4444';
  setRing(sub.days_left, total, color);

  document.getElementById('days-left').textContent = sub.days_left;
  document.getElementById('tariff-title').textContent = sub.tariff_title;
  document.getElementById('status-line').textContent = statusText(sub);

  document.getElementById('devices').textContent = `${sub.devices_online}/${sub.device_limit}`;
  document.getElementById('traffic').textContent = sub.traffic_used_gb.toFixed(1);
  document.getElementById('streak').textContent = sub.streak_count;

  // Серия продлений
  if (sub.streak_count > 0) {
    const card = document.getElementById('streak-card');
    card.classList.remove('hidden');
    document.getElementById('streak-text').textContent =
      `${sub.streak_count} продлений подряд. Следующее даст +${sub.next_streak_bonus_days || 1} дн. бонусом.`;
    document.getElementById('streak-bar').style.width =
      `${Math.min(100, (sub.streak_count / 7) * 100)}%`;
  }

  // Кнопка паузы превращается в «Разморозить», когда подписка на паузе
  document.getElementById('pause-label').textContent =
    sub.status === 'paused' ? 'Разморозить' : 'Заморозить';

  // Персональный оффер с обратным отсчётом
  if (data.personal_offer && data.personal_offer.valid_until) {
    showOffer(data.personal_offer);
  }

  // Рефералы
  const ref = data.referrals;
  document.getElementById('ref-text').textContent =
    `Приглашено: ${ref.total}, оплатили: ${ref.qualified}. ` +
    `Заработано слотов устройств: ${ref.slots_earned}. ` +
    (ref.next_slot_in ? `До следующего слота: ${ref.next_slot_in} друга.` : 'Максимум достигнут.');
  document.getElementById('ref-link').textContent = ref.link;
}

function statusText(sub) {
  if (sub.status === 'paused') {
    const until = new Date(sub.paused_until).toLocaleDateString('ru-RU');
    return `⏸ Заморожено до ${until}`;
  }
  if (sub.status === 'expired') return '❌ Подписка истекла';
  const date = new Date(sub.expires_at).toLocaleDateString('ru-RU');
  return sub.is_trial ? `🎁 Бесплатный период до ${date}` : `✅ Активна до ${date}`;
}

function setRing(value, total, color) {
  const circumference = 327;
  const ratio = Math.max(0, Math.min(1, value / total));
  const ring = document.getElementById('ring-progress');
  ring.style.stroke = color;
  ring.style.strokeDashoffset = circumference * (1 - ratio);
}

function showOffer(offer) {
  const banner = document.getElementById('offer-banner');
  banner.classList.remove('hidden');
  document.getElementById('offer-text').textContent = `🔥 Скидка −${offer.discount_percent}%`;

  const deadline = new Date(offer.valid_until).getTime();
  const tick = () => {
    const left = deadline - Date.now();
    if (left <= 0) {
      banner.classList.add('hidden');
      clearInterval(offerTimerId);
      return;
    }
    const hours = String(Math.floor(left / 3600000)).padStart(2, '0');
    const minutes = String(Math.floor((left % 3600000) / 60000)).padStart(2, '0');
    const seconds = String(Math.floor((left % 60000) / 1000)).padStart(2, '0');
    document.getElementById('offer-timer').textContent = `${hours}:${minutes}:${seconds}`;
  };
  tick();
  offerTimerId = setInterval(tick, 1000);
}

// ── Тарифы ───────────────────────────────────────────────────────────
async function openTariffs() {
  const section = document.getElementById('tariffs-section');
  section.classList.remove('hidden');
  section.scrollIntoView({ behavior: 'smooth' });

  const data = await api('/api/miniapp/tariffs');
  const list = document.getElementById('tariffs-list');
  list.innerHTML = '';

  data.tariffs.forEach((tariff) => {
    const block = document.createElement('div');
    block.className = 'tariff';
    block.innerHTML = `
      <div class="tariff__head">
        <span class="tariff__title">${tariff.title}</span>
        ${tariff.badge ? `<small>${tariff.badge}</small>` : ''}
      </div>
      <div class="tariff__desc">${tariff.description}</div>
      <div class="periods"></div>`;

    const periods = block.querySelector('.periods');
    tariff.periods.forEach((period) => {
      const button = document.createElement('button');
      button.className = 'period';
      button.innerHTML =
        `<b>${period.price} ₽</b>${period.title}` +
        (period.badge ? `<small>${period.badge}</small>` : '');
      button.onclick = () => choosePayment(tariff.code, period.months, period.price);
      periods.appendChild(button);
    });

    list.appendChild(block);
  });
}

function choosePayment(tariffCode, months, price) {
  if (!tg) return;
  tg.showPopup(
    {
      title: `К оплате ${price} ₽`,
      message: 'Выберите способ оплаты',
      buttons: [
        { id: 'platega', type: 'default', text: '💳 Карта / СБП' },
        { id: 'cryptopay', type: 'default', text: '₿ Криптовалюта' },
        { id: 'stars', type: 'default', text: '⭐ Stars' },
      ],
    },
    (providerId) => providerId && createOrder(tariffCode, months, providerId),
  );
}

async function createOrder(tariffCode, months, provider) {
  try {
    const order = await api('/api/miniapp/order', {
      method: 'POST',
      body: JSON.stringify({
        tariff_code: tariffCode,
        period_months: months,
        provider,
        promo_code: state?.personal_offer?.code || null,
      }),
    });

    if (order.is_telegram_invoice) {
      // Счёт в звёздах отправляет бот — закрываем кабинет и уходим в чат
      toast('Счёт отправлен в чат с ботом');
      setTimeout(() => tg.close(), 1200);
      return;
    }
    tg.openLink(order.payment_url);
  } catch (err) {
    toast(err.message);
  }
}

// ── Действия ─────────────────────────────────────────────────────────
function bindEvents() {
  document.getElementById('btn-connect').onclick = () => {
    tg?.HapticFeedback?.impactOccurred('medium');
    // Telegram.WebApp.openLink умеет только http(s): на happ://add/... она
    // молча ничего не делает — именно поэтому кнопка «не работала».
    // Ведём на нашу https-страницу, она уже открывает приложение.
    if (state?.import_url) {
      tg.openLink(state.import_url, { try_instant_view: false });
      return;
    }
    // Запасной путь: из WebView переход по своей схеме обычно срабатывает
    if (state?.deeplinks?.happ) {
      window.location.href = state.deeplinks.happ;
    }
  };

  document.getElementById('btn-copy').onclick = () => {
    navigator.clipboard.writeText(state.subscription_url);
    toast('Ссылка скопирована');
  };

  document.getElementById('btn-extend').onclick = openTariffs;

  document.getElementById('btn-pause').onclick = async () => {
    if (state?.subscription?.status === 'paused') {
      try {
        await api('/api/miniapp/resume', { method: 'POST' });
        toast('Подписка снова активна');
        loadMe();
      } catch (err) {
        toast(err.message);
      }
      return;
    }
    const section = document.getElementById('pause-section');
    section.classList.remove('hidden');
    document.getElementById('pause-text').textContent =
      `Дни не сгорают. Доступно в этом году: ${state.subscription.pause_days_left} дн.`;
    section.scrollIntoView({ behavior: 'smooth' });
  };

  document.querySelectorAll('.chip').forEach((chip) => {
    chip.onclick = async () => {
      try {
        await api('/api/miniapp/pause', {
          method: 'POST',
          body: JSON.stringify({ days: Number(chip.dataset.days) }),
        });
        toast('Подписка заморожена');
        document.getElementById('pause-section').classList.add('hidden');
        loadMe();
      } catch (err) {
        toast(err.message);
      }
    };
  });

  document.getElementById('btn-heal').onclick = async () => {
    toast('Проверяю подключение…');
    try {
      const result = await api('/api/miniapp/heal', { method: 'POST' });
      toast('Готово! Обновите подписку в приложении');
      // Та же причина: открываем https-страницу, а не схему приложения
      const page = state?.import_url || result.subscription_url?.replace('/sub/', '/i/');
      if (page) {
        setTimeout(() => tg.openLink(page, { try_instant_view: false }), 800);
      }
    } catch (err) {
      toast('Не получилось автоматически — напишите в поддержку');
    }
  };

  document.getElementById('btn-ref').onclick = () => {
    const section = document.getElementById('ref-section');
    section.classList.remove('hidden');
    section.scrollIntoView({ behavior: 'smooth' });
  };

  document.getElementById('btn-share').onclick = () => {
    const link = state.referrals.link;
    const message = 'Держи VPN, который просто работает. Первые 3 дня бесплатно:';
    tg.openTelegramLink(
      `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(message)}`,
    );
  };
}

function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.remove('hidden');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add('hidden'), 2600);
}

init();
