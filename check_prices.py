import os
import json
import requests
from datetime import datetime
from itertools import product

# ─── Настройки ────────────────────────────────────────────────────────────────

TRAVELPAYOUTS_TOKEN  = os.environ["TRAVELPAYOUTS_TOKEN"]
TELEGRAM_BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]

OWNER_CHAT_ID        = os.environ["TELEGRAM_CHAT_ID"]

DEFAULT_OUTBOUND_FROM = "2026-07-17"
DEFAULT_OUTBOUND_TO   = "2026-07-20"
DEFAULT_RETURN_FROM   = "2026-07-24"
DEFAULT_RETURN_TO     = "2026-07-31"

YEAR = "2026"

ORIGIN         = "MOW"
DESTINATION    = "EVN"
PRICES_FILE    = "prices.json"
SUBSCRIBERS_FILE = "subscribers.json"
OFFSET_FILE    = "tg_offset.json"

# ─── Вспомогательные функции ──────────────────────────────────────────────────

def date_range(date_from: str, date_to: str) -> list:
    from datetime import timedelta
    start = datetime.strptime(date_from, "%Y-%m-%d").date()
    end   = datetime.strptime(date_to,   "%Y-%m-%d").date()
    days  = (end - start).days
    if days < 0:
        return []
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]


def parse_start_command(text: str):
    """
    Парсит /start ДД.ММ-ДД.ММ ДД.ММ-ДД.ММ
    Возвращает (out_from, out_to, ret_from, ret_to) или None.
    """
    parts = text.strip().split()
    if len(parts) != 3:
        return None
    try:
        out_range, ret_range = parts[1], parts[2]
        out_from_str, out_to_str = out_range.split("-", 1)
        ret_from_str, ret_to_str = ret_range.split("-", 1)

        def to_date(s: str) -> str:
            day, month = s.strip().split(".")
            d = f"{YEAR}-{int(month):02d}-{int(day):02d}"
            datetime.strptime(d, "%Y-%m-%d")
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
    if OWNER_CHAT_ID not in data:
        data[OWNER_CHAT_ID] = default_dates
    return data


def save_subscribers(subscribers: dict):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(subscribers, f, ensure_ascii=False, indent=2)


def check_new_messages(subscribers: dict) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    try:
        resp = requests.get(url, params={"timeout": 5}, timeout=10)
        resp.raise_for_status()
        updates = resp.json().get("result", [])
    except Exception as e:
        print(f"  [ОШИБКА] getUpdates: {e}")
        return subscribers

    # Игнорируем сообщения старше 10 минут
    now = datetime.utcnow().timestamp()
    MAX_AGE_SECONDS = 600

    # Собираем все update_id чтобы пометить как прочитанные
    all_update_ids = [u["update_id"] for u in updates]
    if all_update_ids:
        max_update_id = max(all_update_ids) + 1
        try:
            requests.get(url, params={"offset": max_update_id, "timeout": 1}, timeout=5)
        except Exception:
            pass

    for update in updates:
        message = update.get("message", {})
        text    = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))
        name    = message.get("chat", {}).get("first_name", "Пользователь")
        msg_time = message.get("date", 0)

        # Пропускаем старые сообщения
        if now - msg_time > MAX_AGE_SECONDS:
            print(f"  [ПРОПУСК] Старое сообщение от {name}: «{text[:30]}»")
            continue

        if not chat_id or not text:
            continue

        if text.startswith("/start"):
            parsed = parse_start_command(text)
            if parsed:
                out_from, out_to, ret_from, ret_to = parsed
                subscribers[chat_id] = {
                    "out_from": out_from, "out_to": out_to,
                    "ret_from": ret_from, "ret_to": ret_to,
                }
                print(f"  [ПОДПИСКА] {name} ({chat_id}): {out_from}–{out_to} / {ret_from}–{ret_to}")
                send_message(chat_id,
                    f"✅ Привет, {name}! Ты подписан на алерты.\n\n"
                    f"📅 Вылет из Москвы: <b>{out_from} — {out_to}</b>\n"
                    f"📅 Вылет из Еревана: <b>{ret_from} — {ret_to}</b>\n\n"
                    f"Пришлю уведомление при снижении цены у любой авиакомпании.\n\n"
                    f"ℹ️ Команды:\n"
                    f"/status — текущие настройки\n"
                    f"/stop — отписаться"
                )
            elif text == "/start":
                send_message(chat_id,
                    f"👋 Привет, {name}!\n\n"
                    f"Я мониторю цены на билеты <b>Москва → Ереван → Москва</b> "
                    f"и присылаю алерт при снижении цены у любой авиакомпании.\n\n"
                    f"<b>Чтобы подписаться:</b>\n\n"
                    f"<code>/start ДД.ММ-ДД.ММ ДД.ММ-ДД.ММ</code>\n\n"
                    f"Первый диапазон — вылет <b>из Москвы</b>, второй — вылет <b>из Еревана</b>.\n\n"
                    f"<b>Пример:</b>\n"
                    f"<code>/start 17.07-20.07 24.07-31.07</code>\n"
                    f"→ из Москвы: 17–20 июля\n"
                    f"→ из Еревана: 24–31 июля\n\n"
                    f"Год (2026) подставляется автоматически."
                )
            else:
                send_message(chat_id,
                    f"⚠️ Не удалось распознать даты.\n\n"
                    f"Нужен формат: <code>/start ДД.ММ-ДД.ММ ДД.ММ-ДД.ММ</code>\n\n"
                    f"<b>Пример:</b> <code>/start 17.07-20.07 24.07-31.07</code>"
                )

        elif text == "/stop":
            if chat_id in subscribers and chat_id != OWNER_CHAT_ID:
                del subscribers[chat_id]
                send_message(chat_id, "🔕 Ты отписан. Напиши /start чтобы подписаться снова.")
            elif chat_id == OWNER_CHAT_ID:
                send_message(chat_id, "ℹ️ Ты владелец бота — тебя нельзя отписать.")
            else:
                send_message(chat_id, "Ты не подписан. Напиши /start чтобы подписаться.")

        elif text == "/status":
            if chat_id in subscribers:
                s = subscribers[chat_id]
                send_message(chat_id,
                    f"✅ Ты подписан на алерты.\n\n"
                    f"📅 Вылет из Москвы: <b>{s['out_from']} — {s['out_to']}</b>\n"
                    f"📅 Вылет из Еревана: <b>{s['ret_from']} — {s['ret_to']}</b>\n\n"
                    f"Чтобы изменить даты:\n"
                    f"<code>/start ДД.ММ-ДД.ММ ДД.ММ-ДД.ММ</code>"
                )
            else:
                send_message(chat_id, "❌ Ты не подписан. Напиши /start чтобы подписаться.")

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


# ─── Цены (новый эндпоинт: все АК по направлению) ─────────────────────────────

def get_prices_all_airlines(depart_date: str, return_date: str) -> dict:
    """
    Запрашивает /v1/prices/cheap — возвращает минимальные цены по авиакомпаниям.
    Возвращает dict: {airline_code: price}
    """
    url = "https://api.travelpayouts.com/v1/prices/cheap"
    params = {
        "origin":             ORIGIN,
        "destination":        DESTINATION,
        "depart_date":        depart_date,
        "return_date":        return_date,
        "token":              TRAVELPAYOUTS_TOKEN,
        "currency":           "rub",
        "page":               1,
        "limit":              30,
        "show_to_affiliates": "false",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success") or not data.get("data"):
            return {}

        dest_data = data["data"].get(DESTINATION, {})
        if not dest_data:
            return {}

        # Группируем по авиакомпании, берём минимальную цену
        result = {}
        for ticket in dest_data.values():
            airline = ticket.get("airline", "")
            price   = ticket.get("price", 0)
            if airline and price:
                if airline not in result or price < result[airline]:
                    result[airline] = price

        return result

    except Exception as e:
        print(f"  [ОШИБКА] {depart_date}/{return_date}: {e}")
        return {}


# ─── Хранилище цен ────────────────────────────────────────────────────────────

def load_previous_prices() -> dict:
    try:
        with open(PRICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_prices(prices: dict):
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)


# ─── Форматирование алерта ────────────────────────────────────────────────────

# Расшифровка популярных IATA-кодов АК на этом направлении
AIRLINE_NAMES = {
    "SU": "Аэрофлот",
    "3F": "Nordwind Airlines",
    "UT": "UTair",
    "S7": "S7 Airlines",
    "U6": "Уральские авиалинии",
    "FV": "Россия",
    "DP": "Pobeda",
    "5N": "Smartavia",
    "GY": "Colorful Guizhou Airlines",
    "R3": "Якутия",
}

def airline_name(code: str) -> str:
    return AIRLINE_NAMES.get(code, code)


def format_alert(depart_date: str, return_date: str, drops: list) -> str:
    """
    drops — список dict: {airline, old_price, new_price}
    """
    link_date = depart_date.replace("-", "")
    header = (
        f"✈️ <b>Снижение цен: {depart_date} → {return_date}</b>\n"
        f"🛫 Москва → Ереван → Москва\n\n"
    )
    lines = []
    for d in sorted(drops, key=lambda x: x["new_price"]):
        drop     = d["old_price"] - d["new_price"]
        drop_pct = round(drop / d["old_price"] * 100)
        lines.append(
            f"🏷 <b>{airline_name(d['airline'])}</b>\n"
            f"   Было: {d['old_price']:,} ₽ → Стало: <b>{d['new_price']:,} ₽</b>\n"
            f"   Снижение: −{drop:,} ₽ ({drop_pct}%)"
        )
    footer = (
        f"\n\n🔗 <a href='https://www.aviasales.ru/search/{ORIGIN}{link_date}{DESTINATION}1'>"
        f"Смотреть на Aviasales</a>"
    )
    return header + "\n\n".join(lines) + footer


# ─── Главная логика ───────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Запуск...")

    # 1. Обрабатываем новые сообщения
    subscribers = load_subscribers()
    subscribers = check_new_messages(subscribers)
    save_subscribers(subscribers)
    print(f"Подписчиков: {len(subscribers)}")

    # 2. Собираем уникальные пары дат по всем подписчикам
    previous = load_previous_prices()
    current  = {}

    # {ключ пары дат: список chat_id которым нужна эта пара}
    pairs_to_subs: dict[str, list] = {}
    for chat_id, s in subscribers.items():
        for out_date in date_range(s["out_from"], s["out_to"]):
            for ret_date in date_range(s["ret_from"], s["ret_to"]):
                key = f"{out_date}_{ret_date}"
                pairs_to_subs.setdefault(key, []).append(chat_id)

    print(f"Уникальных пар дат: {len(pairs_to_subs)}")

    # {chat_id: [алерты]}
    subscriber_alerts: dict[str, list] = {cid: [] for cid in subscribers}

    for key, sub_ids in pairs_to_subs.items():
        out_date, ret_date = key.split("_")
        prices_by_airline = get_prices_all_airlines(out_date, ret_date)

        if not prices_by_airline:
            print(f"  {key}: нет данных")
            continue

        # Сохраняем текущие цены
        current[key] = prices_by_airline
        cheapest = min(prices_by_airline.values())
        airlines_str = ", ".join(f"{airline_name(k)}={v:,}₽" for k, v in sorted(prices_by_airline.items(), key=lambda x: x[1]))
        print(f"  {key}: мин={cheapest:,}₽  [{airlines_str}]")

        # Сравниваем с предыдущими ценами по каждой АК
        prev_prices = previous.get(key, {})
        drops = []
        for airline, new_price in prices_by_airline.items():
            if airline in prev_prices:
                old_price = prev_prices[airline]
                if new_price < old_price:
                    print(f"    ↓ {airline_name(airline)}: {old_price:,} → {new_price:,} ₽")
                    drops.append({
                        "airline":   airline,
                        "old_price": old_price,
                        "new_price": new_price,
                    })

        if drops:
            alert_text = format_alert(out_date, ret_date, drops)
            for cid in sub_ids:
                subscriber_alerts[cid].append(alert_text)

    # 3. Рассылаем алерты
    for chat_id, alerts in subscriber_alerts.items():
        if alerts:
            header = f"🔔 <b>Найдено снижений цен: {len(alerts)}</b>\n\n"
            send_message(chat_id, header + ("\n\n" + "─" * 20 + "\n\n").join(alerts))
            print(f"  [АЛЕРТ] → {chat_id}: {len(alerts)} снижений")

    if not any(subscriber_alerts.values()):
        print("Снижений не обнаружено.")

    # 4. Сохраняем цены (объединяем старые и новые)
    merged = {**previous, **current}
    save_prices(merged)
    print("Готово.")


if __name__ == "__main__":
    main()
