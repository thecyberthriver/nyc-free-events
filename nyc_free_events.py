#!/usr/bin/env python3
"""
nyc_free_events.py — daily "FREE (or almost-free) things to do in NYC" scout.

Sibling to fare_watcher.py, gowild_scout.py and tech_networking_scout.py, but
this one is for YOU AS A NEW YORKER, not for content or travel: every morning it
sends a Telegram digest of free / cheap things to do around the city, grouped by
the stuff you asked for — food, wine, beauty, outdoors, museums, concerts, and
cultural events.

How it works:
  1. Pulls several REAL, verified RSS feeds of free/cheap NYC happenings
     (The Skint, NYC Parks, Secret NYC, amNewYork — see catalog.FEEDS).
  2. Keeps only items that read as free or almost-free, and sorts each into a
     category (wine / food / beauty / outdoor / museums / concerts / cultural).
  3. De-dupes against seen.json so you get fresh picks, not yesterday's list.
  4. Builds a themed digest: a few live picks per category, then evergreen FREE
     NYC standbys for any category the feeds were quiet on, then live
     "what's-on-right-now" deep-links so you can always dig deeper.
  5. Optional: a local Ollama model writes a one-line "here's your move today"
     blurb. Degrades gracefully if Ollama is off.

Honesty: this curates feeds + points at live listings. It does NOT book or RSVP,
and "free/almost-free" is a best-effort text filter — always confirm price and
date on the linked page before you head out.

Runs DAILY (Task Scheduler, 8:00 AM) via pythonw (silent).
Dry run (no Telegram):  python nyc_free_events.py --preview
Find your chat id:      python nyc_free_events.py --chatid
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import feedparser
import requests

import catalog as CAT

# Windows consoles default to cp1252 and choke on emoji. Force UTF-8 so
# --preview and logging never crash. (Telegram gets clean UTF-8 via JSON.)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------
# CONFIG — edit these
# ---------------------------------------------------------------------------

# Telegram — DEDICATED bot for this scout. Credentials resolve in this order:
#   1. Environment variables (used by GitHub Actions — repo Secrets)
#   2. secrets_local.py (untracked, for running on your own machine)
#   3. the CHANGE-ME placeholders below (nothing configured yet)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "CHANGE-ME:paste-token-from-BotFather")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "CHANGE-ME-chat-id")

# How many live feed picks to show per category (before falling back to staples).
PICKS_PER_CATEGORY = 3

# How many total feed items to keep in memory as "already sent" (rolling window).
SEEN_MEMORY = 400

# Networking / fetch.
FEED_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (nyc-free-events scout; personal use)"

# --- Claude "today's move" one-liner (optional) ----------------------------
# Uses the Anthropic API (Claude Haiku) if ANTHROPIC_API_KEY is set — in the
# cloud that's a GitHub repo Secret. If the key is absent it degrades silently:
# the digest still sends, just without the AI line. Haiku keeps this a few cents
# a month.
USE_CLAUDE = True
CLAUDE_MODEL = "claude-haiku-4-5"
CLAUDE_TIMEOUT = 30
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_SYSTEM = (
    "You are a fun, savvy NYC local who knows the free-events scene. "
    "Reply with ONE upbeat sentence (max 200 chars, no hashtags, no emoji) "
    "telling the reader the single best free move to make in NYC today."
)
CLAUDE_PROMPT = "Today's free/cheap NYC picks:\n{picks}\n\nGive me the one best free move today."

# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
SEEN_FILE = BASE_DIR / "seen.json"
LOG_FILE = BASE_DIR / "nyc_free_events.log"

# Local fallback (untracked) — only fills values the environment didn't provide,
# so GitHub Actions Secrets always win over secrets_local.py.
try:
    import secrets_local as _secrets
    if "CHANGE-ME" in TELEGRAM_BOT_TOKEN:
        TELEGRAM_BOT_TOKEN = getattr(_secrets, "TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    if "CHANGE-ME" in TELEGRAM_CHAT_ID:
        TELEGRAM_CHAT_ID = getattr(_secrets, "TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    if not ANTHROPIC_API_KEY:
        ANTHROPIC_API_KEY = getattr(_secrets, "ANTHROPIC_API_KEY", "")
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"sent_ids": []}


def save_seen(seen: dict) -> None:
    seen["sent_ids"] = seen.get("sent_ids", [])[-SEEN_MEMORY:]
    try:
        SEEN_FILE.write_text(json.dumps(seen, indent=2), encoding="utf-8")
    except OSError as e:
        log(f"WARN could not write seen cache: {e}")


def _fmt(d: date) -> str:
    return f"{d.strftime('%a %b')} {d.day}"


_TAG_RE = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    """Strip HTML tags/entities and collapse whitespace from feed text."""
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def item_id(feed_name: str, entry) -> str:
    """Stable-ish id for de-dupe across days."""
    raw = getattr(entry, "id", "") or getattr(entry, "link", "") or getattr(entry, "title", "")
    return f"{feed_name}::{raw}"[:300]


# ---------------------------------------------------------------------------
# Fetch + classify
# ---------------------------------------------------------------------------

def fetch_feed(feed: dict) -> list:
    """Return parsed entries for one feed, or [] on failure."""
    try:
        resp = requests.get(feed["url"], headers={"User-Agent": USER_AGENT}, timeout=FEED_TIMEOUT)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        return list(parsed.entries)
    except Exception as e:  # noqa: BLE001
        log(f"WARN feed fetch failed for {feed['name']}: {e}")
        return []


def collect(seen_ids: set[str]) -> tuple[dict[str, list[dict]], list[str]]:
    """Pull all feeds, filter to free/cheap, classify by category, de-dupe.

    Returns (by_category, newly_seen_ids). by_category maps category key ->
    list of {title, link, source, price} dicts (highest-weight feeds first)."""
    by_cat: dict[str, list[dict]] = {k: [] for k in CAT.CATEGORY_ORDER}
    new_ids: list[str] = []
    run_titles: set[str] = set()  # collapse same-title items (parks lists each date separately)

    # Highest-weight feeds first so their picks lead each category.
    for feed in sorted(CAT.FEEDS, key=lambda f: -f["weight"]):
        entries = fetch_feed(feed)
        log(f"feed {feed['name']}: {len(entries)} entries")
        for e in entries:
            title = clean(getattr(e, "title", ""))
            if not title:
                continue
            summary = clean(getattr(e, "summary", "") or getattr(e, "description", ""))
            link = getattr(e, "link", "") or feed["url"]
            iid = item_id(feed["name"], e)
            if iid in seen_ids:
                continue
            tkey = title.lower()
            if tkey in run_titles:
                continue  # same event repeated across dates — show it once
            run_titles.add(tkey)

            # Price uses title+summary (cues can be in either); category uses the
            # TITLE only, so long multi-topic summaries don't scatter buckets.
            price = CAT.free_verdict(f"{title} {summary}", feed["mostly_free"])
            if price is None:
                continue
            cat = CAT.classify(title)
            if cat is None:
                # Uncategorized but free — file under cultural (catch-all).
                cat = "cultural"

            by_cat[cat].append({
                "title": title,
                "link": link,
                "source": feed["name"],
                "price": price,
            })
            new_ids.append(iid)
            seen_ids.add(iid)

    return by_cat, new_ids


# ---------------------------------------------------------------------------
# Claude "today's move" one-liner (optional, degrades gracefully)
# ---------------------------------------------------------------------------

def draft_blurb(picks_text: str) -> str | None:
    if not USE_CLAUDE or not picks_text.strip() or not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=CLAUDE_TIMEOUT)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=120,
            system=CLAUDE_SYSTEM,
            messages=[{"role": "user", "content": CLAUDE_PROMPT.format(picks=picks_text)}],
        )
        out = "".join(b.text for b in resp.content if b.type == "text").strip()
        return clean(out)[:220] or None
    except Exception as e:  # noqa: BLE001
        log(f"WARN claude blurb failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def send_message(text: str) -> bool:
    if "CHANGE-ME" in TELEGRAM_BOT_TOKEN or "CHANGE-ME" in TELEGRAM_CHAT_ID:
        log("ERROR TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID still placeholder — edit secrets_local.py.")
        return False
    try:
        resp = requests.post(
            _tg_api("sendMessage"),
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log(f"WARN telegram send failed: {e}")
        return False


def _price_tag(price: str) -> str:
    return "FREE" if price == "free" else "cheap"


def build_digest(by_cat: dict[str, list[dict]]) -> tuple[str, str]:
    """Return (telegram_html, plain_picks_for_ollama)."""
    today = date.today()
    lines = [
        "🗽 <b>Free (&amp; almost-free) NYC — today's picks</b>",
        f"📅 {esc(_fmt(today))}",
        "",
    ]
    plain_picks: list[str] = []

    for key in CAT.CATEGORY_ORDER:
        label = CAT.CATEGORY_LABELS[key]
        picks = by_cat.get(key, [])[:PICKS_PER_CATEGORY]
        lines.append(f"<b>{esc(label)}</b>")
        if picks:
            for p in picks:
                tag = _price_tag(p["price"])
                lines.append(
                    f"   • <a href=\"{esc(p['link'])}\">{esc(p['title'])}</a>"
                    f"  <i>[{tag} · {esc(p['source'])}]</i>"
                )
                plain_picks.append(f"- {p['title']} ({tag})")
        else:
            # Fall back to an evergreen free staple so the category is never empty.
            for name, url in CAT.STAPLES.get(key, [])[:1]:
                lines.append(f"   • <a href=\"{esc(url)}\">{esc(name)}</a>  <i>[always free]</i>")
        lines.append("")

    lines.append("♾️ <b>Always free — NYC For Free standbys</b>")
    for label, url in CAT.EVERGREEN:
        lines.append(f"   • <a href=\"{esc(url)}\">{esc(label)}</a>")
    lines.append("")

    lines.append("🔎 <b>Dig deeper — live free listings</b>")
    for label, url in CAT.live_links():
        lines.append(f"   • <a href=\"{esc(url)}\">{esc(label)}</a>")
    lines.append("")
    lines.append("<i>Best-effort free/cheap filter — confirm price &amp; date on each link before you go.</i>")

    return "\n".join(lines), "\n".join(plain_picks[:12])


def insert_blurb(digest_html: str, blurb: str) -> str:
    """Slot the Ollama 'today's move' line just under the date header."""
    marker = "\n\n"
    idx = digest_html.find(marker)
    if idx == -1:
        return f"✨ <i>{esc(blurb)}</i>\n\n{digest_html}"
    head = digest_html[:idx]
    tail = digest_html[idx + len(marker):]
    return f"{head}\n✨ <i>{esc(blurb)}</i>\n\n{tail}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build(seen: dict) -> tuple[str, list[str]]:
    seen_ids = set(seen.get("sent_ids", []))
    by_cat, new_ids = collect(seen_ids)
    total = sum(len(v) for v in by_cat.values())
    log(f"collected {total} fresh free/cheap items across "
        f"{sum(1 for v in by_cat.values() if v)} categories")
    digest, plain = build_digest(by_cat)
    blurb = draft_blurb(plain)
    if blurb:
        digest = insert_blurb(digest, blurb)
    return digest, new_ids


def main() -> int:
    seen = load_seen()
    digest, new_ids = build(seen)
    if send_message(digest):
        log(f"SENT digest with {len(new_ids)} new items")
        seen["sent_ids"] = list(seen.get("sent_ids", [])) + new_ids
        save_seen(seen)
        return 0
    log("Digest not sent (see errors above).")
    return 1


def preview() -> int:
    """Print the digest to the console instead of Telegram (dry run). Does NOT
    update seen.json, so you can re-run freely while testing."""
    seen = load_seen()
    digest, _ = build(seen)
    text = _TAG_RE.sub("", digest)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    print(text)
    return 0


def print_chat_id() -> int:
    """Run with --chatid after messaging your bot to discover the chat id."""
    if "CHANGE-ME" in TELEGRAM_BOT_TOKEN:
        print("Set TELEGRAM_BOT_TOKEN in secrets_local.py first.")
        return 1
    try:
        r = requests.get(_tg_api("getUpdates"), timeout=20)
        r.raise_for_status()
        results = r.json().get("result", [])
        if not results:
            print("No messages found. Message your bot first (press Start), then re-run --chatid.")
            return 1
        ids = {u["message"]["chat"]["id"]: u["message"]["chat"].get("username", "")
               for u in results if "message" in u}
        print("Chat id(s) that have messaged your bot:")
        for cid, uname in ids.items():
            print(f"  {cid}   (@{uname})" if uname else f"  {cid}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"Error calling getUpdates: {e}")
        return 1


if __name__ == "__main__":
    if "--chatid" in sys.argv:
        sys.exit(print_chat_id())
    if "--preview" in sys.argv or "--dry-run" in sys.argv:
        sys.exit(preview())
    sys.exit(main())
