#!/usr/bin/env python3
"""
Aktien-News-Bot fuer Telegram
=============================

Holt aktuelle Boersen-News aus RSS-Feeds (USA + Deutschland) und schickt
neue Meldungen per Telegram. Merkt sich bereits gesendete Artikel in
seen.json, damit nichts doppelt kommt.

Benoetigt nur die Python-Standardbibliothek (kein pip install noetig).

Umgebungsvariablen:
  TELEGRAM_TOKEN    Pflicht. Token von @BotFather
  TELEGRAM_CHAT_ID  Pflicht. Deine Chat-ID
  KEYWORDS          Optional. Kommagetrennt, z.B. "Nvidia,Tesla,Fed"
                    Wenn gesetzt, werden nur passende Meldungen geschickt.
  MAX_ITEMS         Optional. Max. Meldungen pro Durchlauf (Default 15)

Start:
  TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... python3 aktien_news_bot.py
"""

import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# --------------------------------------------------------------------------
# Feeds - hier kannst du beliebig ergaenzen oder auskommentieren
# --------------------------------------------------------------------------
FEEDS = {
    # USA
    "US | Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "US | CNBC Markets": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "US | MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "US | Investing.com": "https://www.investing.com/rss/news_25.rss",
    # Deutschland
    "DE | Handelsblatt Finanzen": "https://www.handelsblatt.com/contentexport/feed/finanzen",
    "DE | finanzen.net": "https://www.finanzen.net/rss/news",
    "DE | manager magazin": "https://www.manager-magazin.de/finanzen/index.rss",
    "DE | tagesschau Wirtschaft": "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
}

SEEN_FILE = Path(__file__).with_name("seen.json")
SEEN_LIMIT = 800          # so viele IDs werden gemerkt
TELEGRAM_LIMIT = 3800     # Zeichen pro Nachricht (Telegram erlaubt 4096)
USER_AGENT = "Mozilla/5.0 (compatible; AktienNewsBot/1.0)"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
}


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------
def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def text_of(element):
    if element is None:
        return ""
    return " ".join((element.text or "").split())


def parse_feed(raw):
    """Liest RSS 2.0 und Atom und gibt eine Liste von (id, titel, link) zurueck."""
    items = []
    root = ET.fromstring(raw)

    # RSS 2.0
    for item in root.findall(".//item"):
        title = text_of(item.find("title"))
        link = text_of(item.find("link"))
        guid = text_of(item.find("guid")) or link
        if title and link:
            items.append((guid, title, link))

    # Atom
    for entry in root.findall(".//atom:entry", NS):
        title = text_of(entry.find("atom:title", NS))
        link_el = entry.find("atom:link", NS)
        link = link_el.get("href", "") if link_el is not None else ""
        guid = text_of(entry.find("atom:id", NS)) or link
        if title and link:
            items.append((guid, title, link))

    return items


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
    """Packt Zeilen in Nachrichten, die unter dem Telegram-Limit bleiben."""
    blocks, current = [], ""
    for line in lines:
        if len(current) + len(line) > TELEGRAM_LIMIT and current:
            blocks.append(current)
            current = ""
        current += line
    if current:
        blocks.append(current)
    return blocks


# --------------------------------------------------------------------------
# Hauptprogramm
# --------------------------------------------------------------------------
def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("TELEGRAM_TOKEN und TELEGRAM_CHAT_ID muessen gesetzt sein.")

    keywords = [k.strip().lower() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]
    max_items = int(os.environ.get("MAX_ITEMS", "15"))

    seen = load_seen()
    seen_set = set(seen)
    fresh = []

    for source, url in FEEDS.items():
        try:
            items = parse_feed(fetch(url))
        except Exception as err:  # noqa: BLE001
            print(f"! {source} nicht erreichbar: {err}")
            continue

        new_here = 0
        for guid, title, link in items:
            if guid in seen_set:
                continue
            if keywords and not any(k in title.lower() for k in keywords):
                seen_set.add(guid)
                seen.append(guid)
                continue
            seen_set.add(guid)
            seen.append(guid)
            fresh.append((source, title, link))
            new_here += 1
        print(f"  {source}: {len(items)} Eintraege, {new_here} neu")

    if not fresh:
        save_seen(seen)
        print("Keine neuen Meldungen.")
        return

    fresh = fresh[:max_items]
    lines = ["<b>📈 Aktuelle Börsen-News</b>\n\n"]
    for source, title, link in fresh:
        lines.append(
            f"<b>{html.escape(source)}</b>\n"
            f'<a href="{html.escape(link, quote=True)}">{html.escape(title)}</a>\n\n'
        )

    ok = all(send_telegram(token, chat_id, block) for block in chunk_messages(lines))
    if ok:
        save_seen(seen)
        print(f"{len(fresh)} Meldungen gesendet.")
    else:
        print("Senden fehlgeschlagen - seen.json bleibt unveraendert.")


if __name__ == "__main__":
    main()
