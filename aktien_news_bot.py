#!/usr/bin/env python3
"""
Aktien-News-Bot fuer Telegram - Version 2 (GoldNews-Stil)
==========================================================

Holt Boersen- und Geopolitik-News aus RSS-Feeds und schickt jede Meldung
als einzelne Telegram-Nachricht mit Warn-Emoji. Merkt sich Gesendetes in
seen.json.

Benoetigt nur die Python-Standardbibliothek.

Umgebungsvariablen:
  TELEGRAM_TOKEN    Pflicht. Token von @BotFather
  TELEGRAM_CHAT_ID  Pflicht. Deine Chat-ID
  KEYWORDS          Optional. Kommagetrennt. Nur passende Meldungen.
  MAX_ITEMS         Optional. Max. Meldungen pro Durchlauf (Default 15)
  EINZELN           Optional. "1" = eine Nachricht pro Meldung (Default),
                    "0" = gesammelte Liste
  MIT_QUELLE        Optional. "1" = Quelle klein darunter (Default), "0" = ohne
"""

import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# --------------------------------------------------------------------------
# Feeds
# --------------------------------------------------------------------------
FEEDS = {
    # --- Geopolitik: bewegt vor allem Gold (sicherer Hafen) ---
    "Welt": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Nahost": "https://www.aljazeera.com/xml/rss/all.xml",

    # --- US-Maerkte: Nasdaq und S&P 500 ---
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "CNBC Markets": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Investing.com": "https://www.investing.com/rss/news_25.rss",

    # --- Rohstoffe und Devisen: bewegt Gold ---
    "FXStreet": "https://www.fxstreet.com/rss",
    "ForexLive": "https://www.forexlive.com/feed/news",

    # --- Gezielte Suchfeeds ---
    "Nasdaq & S&P": "https://news.google.com/rss/search?q=Nasdaq+OR+%22S%26P+500%22&hl=en-US&gl=US&ceid=US:en",
    "Gold": "https://news.google.com/rss/search?q=%22gold+price%22+OR+%22gold+futures%22&hl=en-US&gl=US&ceid=US:en",
    "Fed": "https://news.google.com/rss/search?q=Federal+Reserve+OR+Powell+OR+%22rate+cut%22&hl=en-US&gl=US&ceid=US:en",
}

SEEN_FILE = Path(__file__).with_name("seen.json")
SEEN_LIMIT = 800
TELEGRAM_LIMIT = 3800
USER_AGENT = "Mozilla/5.0 (compatible; AktienNewsBot/2.0)"
PAUSE = 1.2  # Sekunden zwischen zwei Telegram-Nachrichten

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
}


# --------------------------------------------------------------------------
# Kategorien: erstes passendes Symbol gewinnt
# --------------------------------------------------------------------------
KATEGORIEN = [
    ("\U0001F30D", [  # Geopolitik
        "iran", "israel", "hormuz", "houthi", "ukraine", "russia", "taiwan",
        "venezuela", "war", "invasion", "ceasefire", "sanction", "missile",
        "airstrike", "attack", "military", "nato", "conflict",
    ]),
    ("\U0001F1FA\U0001F1F8", [  # Politik USA / Zoelle
        "trump", "tariff", "white house", "congress", "shutdown", "election",
    ]),
    ("\U0001F3E6", [  # Notenbank / Konjunktur
        "fed", "powell", "fomc", "rate cut", "rate hike", "inflation", "cpi",
        "ppi", "payroll", "jobs report", "unemployment", "gdp", "recession",
        "yield", "treasury", "ecb", "boj",
    ]),
    ("\U0001F7E1", [  # Gold und Rohstoffe
        "gold", "bullion", "xau", "silver", "crude", "oil price", "opec",
        "commodit",
    ]),
    ("\U0001F4C8", [  # Aktien und Indizes
        "nasdaq", "s&p", "dow jones", "wall street", "stocks", "shares",
        "earnings", "nvidia", "apple", "microsoft", "tesla", "amazon",
        "broadcom", "semiconductor", "chip",
    ]),
]


def symbol(titel):
    t = titel.lower()
    for zeichen, woerter in KATEGORIEN:
        if any(w in t for w in woerter):
            return zeichen
    return "\u26a0\ufe0f"


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def text_of(element):
    if element is None:
        return ""
    return " ".join((element.text or "").split())


def parse_feed(raw):
    items = []
    root = ET.fromstring(raw)

    for item in root.findall(".//item"):
        title = text_of(item.find("title"))
        link = text_of(item.find("link"))
        guid = text_of(item.find("guid")) or link
        if title and link:
            items.append((guid, title, link))

    for entry in root.findall(".//atom:entry", NS):
        title = text_of(entry.find("atom:title", NS))
        link_el = entry.find("atom:link", NS)
        link = link_el.get("href", "") if link_el is not None else ""
        guid = text_of(entry.find("atom:id", NS)) or link
        if title and link:
            items.append((guid, title, link))

    return items


def kuerzen(titel, grenze=200):
    """Sehr lange Ueberschriften abschneiden, damit es aufgeraeumt bleibt."""
    titel = titel.strip()
    # Manche Feeds haengen die Quelle mit " - Name" an -> abtrennen
    if " - " in titel and len(titel.rsplit(" - ", 1)[1]) < 30:
        titel = titel.rsplit(" - ", 1)[0]
    if len(titel) > grenze:
        titel = titel[:grenze].rsplit(" ", 1)[0] + " ..."
    return titel


def load_seen():
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("! seen.json war defekt und wird neu angelegt")
    return []


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(seen[-SEEN_LIMIT:], ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            json.loads(resp.read())
        return True
    except urllib.error.HTTPError as err:
        print(f"! Telegram-Fehler {err.code}: {err.read().decode('utf-8', 'replace')}")
    except Exception as err:  # noqa: BLE001
        print(f"! Telegram-Fehler: {err}")
    return False


def chunk_messages(lines):
    blocks, current = [], ""
    for line in lines:
        if len(current) + len(line) > TELEGRAM_LIMIT and current:
            blocks.append(current)
            current = ""
        current += line
    if current:
        blocks.append(current)
    return blocks


def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("TELEGRAM_TOKEN und TELEGRAM_CHAT_ID muessen gesetzt sein.")

    keywords = [k.strip().lower() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]
    max_items = int(os.environ.get("MAX_ITEMS", "15"))
    einzeln = os.environ.get("EINZELN", "1") == "1"
    mit_quelle = os.environ.get("MIT_QUELLE", "1") == "1"

    seen = load_seen()
    seen_set = set(seen)
    fresh = []

    for source, url in FEEDS.items():
        try:
            items = parse_feed(fetch(url))
        except Exception as err:  # noqa: BLE001
            print(f"! {source} nicht erreichbar: {err}")
            continue

        neu = 0
        for guid, title, link in items:
            if guid in seen_set:
                continue
            seen_set.add(guid)
            seen.append(guid)
            if keywords and not any(k in title.lower() for k in keywords):
                continue
            fresh.append((source, kuerzen(title), link))
            neu += 1
        print(f"  {source}: {len(items)} Eintraege, {neu} neu")

    if not fresh:
        save_seen(seen)
        print("Keine neuen Meldungen.")
        return

    fresh = fresh[:max_items]
    ok = True

    if einzeln:
        for source, title, link in fresh:
            text = f'{symbol(title)} <a href="{html.escape(link, quote=True)}">{html.escape(title)}</a>'
            if mit_quelle:
                text += f"\n\n<i>{html.escape(source)}</i>"
            if not send_telegram(token, chat_id, text):
                ok = False
                break
            time.sleep(PAUSE)
    else:
        lines = ["<b>📈 Aktuelle Börsen-News</b>\n\n"]
        for source, title, link in fresh:
            lines.append(
                f'{symbol(title)} <a href="{html.escape(link, quote=True)}">{html.escape(title)}</a>\n'
                f"<i>{html.escape(source)}</i>\n\n"
            )
        ok = all(send_telegram(token, chat_id, b) for b in chunk_messages(lines))

    if ok:
        save_seen(seen)
        print(f"{len(fresh)} Meldungen gesendet.")
    else:
        print("Senden fehlgeschlagen - seen.json bleibt unveraendert.")


if __name__ == "__main__":
    main()
