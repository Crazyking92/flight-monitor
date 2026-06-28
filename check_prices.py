import os
import json
import re
import requests
from datetime import datetime, timedelta

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

GRAPHQL_URL = "https://api.travelpayouts.com/graphql/v1/query"

# Расшифровка IATA-кодов АК
AIRLINE_NAMES = {
    "SU": "Аэрофлот",
    "3F": "Nordwind Airlines",
    "UT": "UTair",
    "S7": "S7 Airlines",
    "U6": "Уральские авиалинии",
    "FV": "Россия",
    "DP": "Pobeda",
    "5N": "Smartavia",
    "N4": "Nordwind Airlines",
}

def airline_name(code: str) -> str:
    return AIRLINE_NAMES.get(code, code)


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def date_range(date_from: str, date_to: str) -> list:
    start = datetime.strptime(date_from, "%Y-%m-%d").date()
    end   = datetime.strptime(date_to,   "%Y-%m-%d").date()
    days  = (end - start).days
    if days < 0:
        return []
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]


def extract_airline(ticket_link: str) -> str:
    """Извлекает IATA-код АК из ticket_link. Код — первые 2 символа после t="""
    match = re.search(r"[?&]t=([A-Z0-9]{2})", ticket_link)
    if match:
        return match.group(1)
    # Альтернативный паттерн — из пути /MOW1707EVN1?t=3F...
    match = re.search(r"t=([A-Z0-9]{2})\d", ticket_link)
    return match.group(1) if match else "??"


def parse_start_command(text: str):
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
    now = datetime.utcnow().timestamp()
    MAX_AGE = 600  # 10 минут

    try:
        resp = requests.get(url, params={"timeout": 5}, timeout=10)
        resp.raise_for_status()
        updates = resp.json().get("result", [])
    except Exception as e:
        print(f"  [ОШИБКА] getUpdates: {e}")
        return subscribers

    all_ids = [u["update_id"] for u in updates]
    if all_ids:
        try:
            requests.get(url, params={"offset": max(all_ids) + 1, "timeout": 1}, timeout=5)
        except Exception:
            pass

    for update in updates:
        message  = update.get("message", {})
        text     = message.get("text", "").strip()
        chat_id  = str(message.get("chat", {}).get("id", ""))
        name     = message.get("chat", {}).get("first_name", "Пользователь")
        msg_time = message.get("date", 0)

        if not chat_id or not text:
            continue
        if now - msg_time > MAX_AGE:
            print(f"  [ПРОПУСК] Старое сообщение от {name}: «{text[:30]}»")
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


# ─── GraphQL запрос цен ───────────────────────────────────────────────────────

def get_all_prices_graphql(out_from: str, out_to: str, ret_from: str, ret_to: str) -> dict:
    """
    Один GraphQL-запрос для всего диапазона дат подписчика.
    Возвращает dict: {"out_date_ret_date": {airline_code: price}}
    """
    # depart_months принимает первое число месяца: "2026-07-01"
    month_start = out_from[:7] + "-01"

    query = """
    {
      prices_round_trip(
        params: {
          origin: "%s"
          destination: "%s"
          depart_months: "%s"
        }
        paging: { limit: 50 offset: 0 }
        sorting: VALUE_ASC
      ) {
        departure_at
        return_at
        value
        ticket_link
      }
    }
    """ % (ORIGIN, DESTINATION, month_start)

    headers = {
        "Content-Type":   "application/json",
        "X-Access-Token": TRAVELPAYOUTS_TOKEN,
    }

    try:
        resp = requests.post(
            GRAPHQL_URL,
            json={"query": query},
            headers=headers,
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        tickets = data.get("data", {}).get("prices_round_trip", []) or []
        if not tickets:
            return {}

        result = {}
        for ticket in tickets:
            dep     = ticket.get("departure_at", "")[:10]
            ret     = ticket.get("return_at", "")[:10]
            price   = ticket.get("value", 0)
            link    = ticket.get("ticket_link", "")
            airline = extract_airline(link)

            # Фильтруем по нужным диапазонам дат
            if not (out_from <= dep <= out_to):
                continue
            if not (ret_from <= ret <= ret_to):
                continue
            if not airline or not price:
                continue

            key = f"{dep}_{ret}"
            if key not in result:
                result[key] = {}
            if airline not in result[key] or price < result[key][airline]:
                result[key][airline] = price

        return result

    except Exception as e:
        print(f"  [ОШИБКА] GraphQL: {e}")
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

def format_alert(depart_date: str, return_date: str, drops: list) -> str:
    link_date = depart_date.replace("-", "")
    header = (
        f"✈️ <b>Снижение цен!</b>\n"
        f"🛫 Москва → Ереван → Москва\n"
        f"📅 Туда: <b>{depart_date}</b> | Обратно: <b>{return_date}</b>\n\n"
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

    # 2. Запрашиваем цены — один запрос на уникальный диапазон дат
    previous = load_previous_prices()
    current  = {}

    # Собираем уникальные диапазоны дат по подписчикам
    # (у двух людей могут быть одинаковые диапазоны — запрашиваем один раз)
    unique_ranges = {}
    for chat_id, s in subscribers.items():
        range_key = f"{s['out_from']}_{s['out_to']}_{s['ret_from']}_{s['ret_to']}"
        if range_key not in unique_ranges:
            unique_ranges[range_key] = {
                "out_from": s["out_from"], "out_to": s["out_to"],
                "ret_from": s["ret_from"], "ret_to": s["ret_to"],
                "subs": []
            }
        unique_ranges[range_key]["subs"].append(chat_id)

    subscriber_alerts: dict[str, list] = {cid: [] for cid in subscribers}

    for range_key, rng in unique_ranges.items():
        print(f"Запрос: {rng['out_from']}–{rng['out_to']} / {rng['ret_from']}–{rng['ret_to']}")
        all_prices = get_all_prices_graphql(
            rng["out_from"], rng["out_to"],
            rng["ret_from"], rng["ret_to"]
        )

        if not all_prices:
            print("  Нет данных от API.")
            continue

        print(f"  Получено пар дат: {len(all_prices)}")

        for key, prices_by_airline in all_prices.items():
            out_date, ret_date = key.split("_")
            current[key] = prices_by_airline

            cheapest = min(prices_by_airline.values())
            airlines_str = ", ".join(
                f"{airline_name(k)}={v:,}₽"
                for k, v in sorted(prices_by_airline.items(), key=lambda x: x[1])
            )
            print(f"  {key}: мин={cheapest:,}₽  [{airlines_str}]")

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
                for cid in rng["subs"]:
                    subscriber_alerts[cid].append(alert_text)

    # 3. Рассылаем алерты
    for chat_id, alerts in subscriber_alerts.items():
        if alerts:
            header = f"🔔 <b>Найдено снижений цен: {len(alerts)}</b>\n\n"
            send_message(chat_id, header + ("\n\n" + "─" * 20 + "\n\n").join(alerts))
            print(f"  [АЛЕРТ] → {chat_id}: {len(alerts)} снижений")

    if not any(subscriber_alerts.values()):
        print("Снижений не обнаружено.")

    save_prices({**previous, **current})
    print("Готово.")


if __name__ == "__main__":
    main()
