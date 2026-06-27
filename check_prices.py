import json
import requests
from datetime import datetime, date
from itertools import product

# ─── Настройки ────────────────────────────────────────────────────────────────

TRAVELPAYOUTS_TOKEN  = "74992fc51a9f3abeee3b491e648eb0b5"
TELEGRAM_BOT_TOKEN   = "8855541806:AAExdMGNuD593bf_BM59Nd8PWekx5inMx6o"

OWNER_CHAT_ID        = "251809170"   # ты всегда подписан по умолчанию

# Твои даты по умолчанию (используются если пользователь не указал свои)
DEFAULT_OUTBOUND_FROM = "2026-07-17"
DEFAULT_OUTBOUND_TO   = "2026-07-20"
DEFAULT_RETURN_FROM   = "2026-07-24"
DEFAULT_RETURN_TO     = "2026-07-31"

YEAR = "2026"  # фиксированный год для парсера команд

ORIGIN         = "MOW"
DESTINATION    = "EVN"
PRICES_FILE    = "prices.json"
SUBSCRIBERS_FILE = "subscribers.json"
OFFSET_FILE    = "tg_offset.json"

# ─── Вспомогательные функции ──────────────────────────────────────────────────

def date_range(date_from: str, date_to: str) -> list:
    """Генерирует список дат между date_from и date_to включительно."""
    start = datetime.strptime(date_from, "%Y-%m-%d").date()
    end   = datetime.strptime(date_to,   "%Y-%m-%d").date()
    days  = (end - start).days
    if days < 0:
        return []
    return [(start.replace(day=start.day) + __import__("datetime").timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(days + 1)]


def parse_start_command(text: str) -> tuple[str, str, str, str] | None:
    """
    Парсит команду вида: /start 17.07-20.07 24.07-31.07
    Возвращает (outbound_from, outbound_to, return_from, return_to) или None.
    Год фиксирован (YEAR = 2026), пользователь указывает только ДД.ММ.
    """
    parts = text.strip().split()
    if len(parts) != 3:
        return None

    try:
        out_range, ret_range = parts[1], parts[2]

        # Ожидаем формат "ДД.ММ-ДД.ММ"
        out_from_str, out_to_str = out_range.split("-", 1)
        ret_from_str, ret_to_str = ret_range.split("-", 1)

        def to_date(s: str) -> str:
            """Преобразует 'ДД.ММ' в 'ГГГГ-ММ-ДД'."""
            day, month = s.strip().split(".")
            d = f"{YEAR}-{int(month):02d}-{int(day):02d}"
            datetime.strptime(d, "%Y-%m-%d")  # валидация
            return d

        out_from = to_date(out_from_str)
        out_to   = to_date(out_to_str)
        ret_from = to_date(ret_from_str)
        ret_to   = to_date(ret_to_str)

        if out_from > out_to or ret_from > ret_to:
            return None

        return out_from, out_to, ret_from, ret_to

    except (ValueError, AttributeError):
        return None


# ─── Подписчики ───────────────────────────────────────────────────────────────

def load_subscribers() -> dict:
    """
    Возвращает словарь {chat_id: {out_from, out_to, ret_from, ret_to}}.
    Владелец всегда присутствует.
    """
    default_dates = {
        "out_from": DEFAULT_OUTBOUND_FROM,
        "out_to":   DEFAULT_OUTBOUND_TO,
        "ret_from": DEFAULT_RETURN_FROM,
        "ret_to":   DEFAULT_RETURN_TO,
    }
    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    # Владелец всегда в списке
    if OWNER_CHAT_ID not in data:
        data[OWNER_CHAT_ID] = default_dates

    return data


def save_subscribers(subscribers: dict):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(subscribers, f, ensure_ascii=False, indent=2)


def check_new_messages(subscribers: dict) -> dict:
    """Обрабатывает входящие сообщения от Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    try:
        with open(OFFSET_FILE, "r") as f:
            offset = json.load(f).get("offset", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        offset = 0

    try:
        resp = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
        resp.raise_for_status()
        updates = resp.json().get("result", [])
    except Exception as e:
        print(f"  [ОШИБКА] getUpdates: {e}")
        return subscribers

    max_update_id = offset

    for update in updates:
        max_update_id = max(max_update_id, update["update_id"] + 1)
        message = update.get("message", {})
        text    = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))
        name    = message.get("chat", {}).get("first_name", "Пользователь")

        if not chat_id or not text:
            continue

        # ── /start [диапазоны] ───────────────────────────────────────────────
        if text.startswith("/start"):
            parsed = parse_start_command(text)

            if parsed:
                out_from, out_to, ret_from, ret_to = parsed
                subscribers[chat_id] = {
                    "out_from": out_from,
                    "out_to":   out_to,
                    "ret_from": ret_from,
                    "ret_to":   ret_to,
                }
                print(f"  [ПОДПИСКА] {name} ({chat_id}): {out_from}–{out_to} / {ret_from}–{ret_to}")
                send_message(chat_id,
                    f"✅ Привет, {name}! Ты подписан на алерты.\n\n"
                    f"📅 Вылет из Москвы: <b>{out_from} — {out_to}</b>\n"
                    f"📅 Вылет из Еревана: <b>{ret_from} — {ret_to}</b>\n\n"
                    f"Пришлю уведомление, как только цена упадёт.\n\n"
                    f"ℹ️ Команды:\n"
                    f"/status — посмотреть текущие настройки\n"
                    f"/stop — отписаться"
                )

            elif text == "/start":
                # Написал просто /start без дат — показываем инструкцию
                default_msg = (
                    f"👋 Привет, {name}!\n\n"
                    f"Я мониторю цены на билеты <b>Москва → Ереван → Москва</b> "
                    f"и присылаю алерт при снижении цены.\n\n"
                    f"<b>Чтобы подписаться</b>, отправь команду в формате:\n\n"
                    f"<code>/start ДД.ММ-ДД.ММ ДД.ММ-ДД.ММ</code>\n\n"
                    f"Первый диапазон — даты вылета <b>из Москвы</b>, "
                    f"второй — даты вылета <b>из Еревана</b>.\n"
                    f"Внутри диапазона даты через дефис, между диапазонами — пробел.\n\n"
                    f"<b>Пример:</b>\n"
                    f"<code>/start 17.07-20.07 24.07-31.07</code>\n"
                    f"→ вылет из Москвы: 17–20 июля\n"
                    f"→ вылет обратно: 24–31 июля\n\n"
                    f"Год (2026) подставляется автоматически."
                )
                send_message(chat_id, default_msg)
                print(f"  [ИНФО] {name} ({chat_id}) написал /start без дат — отправлена инструкция")

            else:
                # Написал /start с неправильным форматом
                send_message(chat_id,
                    f"⚠️ Не удалось распознать даты.\n\n"
                    f"Нужен формат: <code>/start ДД.ММ-ДД.ММ ДД.ММ-ДД.ММ</code>\n\n"
                    f"<b>Пример:</b> <code>/start 17.07-20.07 24.07-31.07</code>\n"
                    f"→ вылет из Москвы: 17–20 июля\n"
                    f"→ вылет обратно: 24–31 июля\n\n"
                    f"Год (2026) подставляется автоматически."
                )
                print(f"  [ОШИБКА] {name} ({chat_id}) прислал неверный формат: {text}")

        # ── /stop ────────────────────────────────────────────────────────────
        elif text == "/stop":
            if chat_id in subscribers and chat_id != OWNER_CHAT_ID:
                del subscribers[chat_id]
                send_message(chat_id, "🔕 Ты отписан от алертов. Напиши /start чтобы подписаться снова.")
                print(f"  [ОТПИСКА] {name} ({chat_id})")
            elif chat_id == OWNER_CHAT_ID:
                send_message(chat_id, "ℹ️ Ты владелец бота — тебя нельзя отписать.")
            else:
                send_message(chat_id, "Ты и так не подписан. Напиши /start чтобы подписаться.")

        # ── /status ──────────────────────────────────────────────────────────
        elif text == "/status":
            if chat_id in subscribers:
                s = subscribers[chat_id]
                send_message(chat_id,
                    f"✅ Ты подписан на алерты.\n\n"
                    f"📅 Вылет из Москвы: <b>{s['out_from']} — {s['out_to']}</b>\n"
                    f"📅 Вылет из Еревана: <b>{s['ret_from']} — {s['ret_to']}</b>\n\n"
                    f"Чтобы изменить даты, напиши:\n"
                    f"<code>/start ДД.ММ-ДД.ММ ДД.ММ-ДД.ММ</code>"
                )
            else:
                send_message(chat_id, "❌ Ты не подписан. Напиши /start чтобы подписаться.")

    # Сохраняем offset
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": max_update_id}, f)

    return subscribers


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_message(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ОШИБКА] Telegram → {chat_id}: {e}")


# ─── Цены ─────────────────────────────────────────────────────────────────────

def load_previous_prices() -> dict:
    try:
        with open(PRICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_prices(prices: dict):
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)


def get_price(depart_date: str, return_date: str) -> dict | None:
    url = "https://api.travelpayouts.com/v1/prices/cheap"
    params = {
        "origin":             ORIGIN,
        "destination":        DESTINATION,
        "depart_date":        depart_date,
        "return_date":        return_date,
        "token":              TRAVELPAYOUTS_TOKEN,
        "currency":           "rub",
        "page":               1,
        "limit":              1,
        "show_to_affiliates": "false",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success") or not data.get("data"):
            return None
        dest_data = data["data"].get(DESTINATION, {})
        if not dest_data:
            return None
        cheapest = min(dest_data.values(), key=lambda x: x["price"])
        return {
            "price":       cheapest["price"],
            "airline":     cheapest.get("airline", "—"),
            "depart_date": cheapest.get("departure_at", depart_date),
            "return_date": cheapest.get("return_at", return_date),
        }
    except Exception as e:
        print(f"  [ОШИБКА] {depart_date}/{return_date}: {e}")
        return None


def format_alert(old_price: int, new_price: int, info: dict) -> str:
    drop      = old_price - new_price
    drop_pct  = round(drop / old_price * 100)
    depart    = info["depart_date"][:10]
    ret       = info["return_date"][:10] if info.get("return_date") else "—"
    link_date = depart.replace("-", "")
    return (
        f"✈️ <b>Цена на билет снизилась!</b>\n\n"
        f"🛫 <b>Москва → Ереван → Москва</b>\n"
        f"📅 Туда: <b>{depart}</b>\n"
        f"📅 Обратно: <b>{ret}</b>\n"
        f"🏷 Авиакомпания: {info['airline']}\n\n"
        f"💰 Было: <b>{old_price:,} ₽</b>\n"
        f"💰 Стало: <b>{new_price:,} ₽</b>\n"
        f"📉 Снижение: <b>−{drop:,} ₽ ({drop_pct}%)</b>\n\n"
        f"🔗 <a href='https://www.aviasales.ru/search/{ORIGIN}{link_date}{DESTINATION}1'>"
        f"Смотреть на Aviasales</a>"
    )


# ─── Главная логика ───────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Запуск...")

    # 1. Обрабатываем новые сообщения и обновляем подписчиков
    subscribers = load_subscribers()
    subscribers = check_new_messages(subscribers)
    save_subscribers(subscribers)
    print(f"Подписчиков: {len(subscribers)}")

    # 2. Собираем все уникальные пары дат по всем подписчикам
    previous = load_previous_prices()
    current  = {}

    # Словарь: ключ пары дат → список подписчиков которым это нужно
    pairs_to_subscribers: dict[str, list] = {}
    for chat_id, s in subscribers.items():
        for out_date in date_range(s["out_from"], s["out_to"]):
            for ret_date in date_range(s["ret_from"], s["ret_to"]):
                key = f"{out_date}_{ret_date}"
                pairs_to_subscribers.setdefault(key, []).append(chat_id)

    print(f"Уникальных пар дат для проверки: {len(pairs_to_subscribers)}")

    # 3. Проверяем цены и собираем алерты по каждому подписчику
    subscriber_alerts: dict[str, list] = {cid: [] for cid in subscribers}

    for key, sub_ids in pairs_to_subscribers.items():
        out_date, ret_date = key.split("_")
        info = get_price(out_date, ret_date)

        if info is None:
            print(f"  {key}: нет данных")
            continue

        price = info["price"]
        current[key] = {"price": price, "info": info}
        print(f"  {key}: {price:,} ₽  [{info['airline']}]")

        if key in previous:
            old_price = previous[key]["price"]
            if price < old_price:
                print(f"    ↓ СНИЖЕНИЕ: {old_price:,} → {price:,} ₽")
                alert_text = format_alert(old_price, price, info)
                for cid in sub_ids:
                    subscriber_alerts[cid].append(alert_text)

    # 4. Рассылаем алерты каждому подписчику
    for chat_id, alerts in subscriber_alerts.items():
        if alerts:
            header = f"🔔 Найдено снижений: {len(alerts)}\n\n"
            send_message(chat_id, header + ("\n\n" + "─" * 20 + "\n\n").join(alerts))
            print(f"  [АЛЕРТ] → {chat_id}: {len(alerts)} снижений")

    if not any(subscriber_alerts.values()):
        print("Снижений не обнаружено.")

    save_prices({**previous, **current})
    print("Готово.")


if __name__ == "__main__":
    main()
