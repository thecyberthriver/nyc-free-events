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

# How many events to show per category (real NYC For Free events first, then
# blog-feed items to top up a thin category).
PICKS_PER_CATEGORY = 4

# How many total feed items to keep in memory as "already sent" (rolling window).
SEEN_MEMORY = 400

# Networking / fetch.
FEED_TIMEOUT = 20
# Full browser UA — some feeds (e.g. NYC Parks) 405 a terse/bot User-Agent,
# especially from datacenter IPs like GitHub Actions runners.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

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


def _nff_date(s: str):
    try:
        return datetime.strptime(s.strip(), "%B %d, %Y").date()
    except (ValueError, AttributeError):
        return None


def _short_venue(address: str) -> str:
    """Shorten a full address to a venue name or neighborhood (<= ~34 chars)."""
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return ""
    first = parts[0]
    # If the first segment is a street number, prefer a named 2nd segment.
    if re.match(r"^\d", first) and len(parts) > 1:
        first = f"{first}, {parts[1]}" if len(first) < 6 else parts[1]
    return first[:34]


def fetch_nycforfree_events() -> list[dict]:
    """Parse the NYC For Free /events page (static Webflow HTML) into structured
    FREE events, keep those on now or upcoming, and tag each with our bucket."""
    try:
        resp = requests.get(
            CAT.NFF_EVENTS_URL,
            headers={"User-Agent": USER_AGENT,
                     "Accept": "text/html,application/xhtml+xml,*/*",
                     "Accept-Language": "en-US,en;q=0.9"},
            timeout=FEED_TIMEOUT,
        )
        resp.raise_for_status()
        h = resp.text
    except Exception as e:  # noqa: BLE001
        log(f"WARN NYC For Free events fetch failed: {e}")
        return []

    today = date.today()
    out: list[dict] = []
    seen_names: set[str] = set()
    for m in re.finditer(r'<a href="(/events/[^"]+)" class="events_list-card', h):
        a = m.start()
        pre = h[max(0, a - 700):a]          # wrapper custom attrs sit before the anchor
        post = h[a:a + 1100]                # name / description sit after it

        def _at(name: str) -> str:
            mm = re.search(name + r'="([^"]*)"', pre)
            return html.unescape(mm.group(1)).strip() if mm else ""

        nm = re.search(r'event-data="name"[^>]*>(.*?)</div>', post)
        name = clean(nm.group(1)) if nm else ""
        if not name or name.lower() in seen_names:
            continue
        end = _nff_date(_at("end-date"))
        if end and end < today:             # already over
            continue
        seen_names.add(name.lower())
        dm = re.search(r'event-data="description"[^>]*>(.*?)</div>', post)
        out.append({
            "name": name,
            "url": "https://www.nycforfree.co" + m.group(1),
            "bucket": CAT.nff_bucket(_at("category")),
            "start": _nff_date(_at("start-date")),
            "end": end,
            "start_time": _at("start-time"),
            "end_time": _at("end-time"),
            "venue": _short_venue(_at("address")),
            "desc": (clean(dm.group(1))[:110] if dm else ""),
        })
    out.sort(key=lambda e: e["start"] or today)
    return out


def _fmt_when(e: dict) -> str:
    """Compact 'when' string for an event: ongoing -> 'thru Sep 26', else date."""
    today = date.today()
    s, en = e.get("start"), e.get("end")
    if s and en and s <= today <= en and s != en:
        when = f"thru {en.strftime('%b')} {en.day}"
    elif s:
        when = f"{s.strftime('%b')} {s.day}"
    else:
        when = ""
    st, et = e.get("start_time", ""), e.get("end_time", "")
    # Skip placeholder all-day times (e.g. 8:00 AM-8:00 AM) and empty/equal times.
    if st and et and st != et:
        when = f"{when} · {st}–{et}" if when else f"{st}–{et}"
    return when


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
        resp = requests.get(
            feed["url"],
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
            },
            timeout=FEED_TIMEOUT,
        )
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


def build_digest(nff_by_cat: dict[str, list[dict]],
                 rss_by_cat: dict[str, list[dict]]) -> tuple[str, str]:
    """Event-first digest: each category leads with ACTUAL NYC For Free events
    (name · when · venue), topped up with blog-feed items when thin.
    Returns (telegram_html, plain_picks_for_the_ai_blurb)."""
    today = date.today()
    lines = [
        "🗽 <b>Free NYC — what's actually on</b>",
        f"📅 {esc(_fmt(today))}",
        "",
    ]
    plain_picks: list[str] = []

    for key in CAT.CATEGORY_DISPLAY_ORDER:
        label = CAT.CATEGORY_LABELS[key]
        block: list[str] = []

        # 1) Real events from NYC For Free — name, when, venue, inline.
        for e in nff_by_cat.get(key, [])[:PICKS_PER_CATEGORY]:
            meta = " · ".join(x for x in (_fmt_when(e), e["venue"]) if x)
            line = f"   • <a href=\"{esc(e['url'])}\">{esc(e['name'])}</a>"
            if meta:
                line += f"  <i>{esc(meta)}</i>"
            block.append(line)
            plain_picks.append(f"- {e['name']} ({_fmt_when(e)})")

        # 2) Top up a thin category with fresh blog-feed items.
        need = PICKS_PER_CATEGORY - len(block)
        if need > 0:
            for p in rss_by_cat.get(key, [])[:need]:
                block.append(
                    f"   • <a href=\"{esc(p['link'])}\">{esc(p['title'])}</a>"
                    f"  <i>[{_price_tag(p['price'])} · {esc(p['source'])}]</i>"
                )

        # 3) Never leave a category empty.
        if not block:
            for name, url in CAT.STAPLES.get(key, [])[:1]:
                block.append(f"   • <a href=\"{esc(url)}\">{esc(name)}</a>  <i>[browse]</i>")

        lines.append(f"<b>{esc(label)}</b>")
        lines.extend(block)
        lines.append("")

    lines.append(
        "🔎 <b>Full listings:</b> "
        f"<a href=\"{esc(CAT.NFF_EVENTS_URL)}\">NYC For Free</a> · "
        "<a href=\"https://theskint.com/\">The Skint</a> · "
        "<a href=\"https://www.clubfreetime.com/new-york-city-nyc/free-events-things-to-do/this-week\">Club Free Time</a>"
    )
    lines.append("<i>Events pulled live from NYC For Free — confirm date/time on each link before you go.</i>")

    return "\n".join(lines), "\n".join(plain_picks[:14])


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
    rss_by_cat, new_ids = collect(seen_ids)

    # Real, structured events straight from the NYC For Free /events page.
    nff = fetch_nycforfree_events()
    log(f"NYC For Free: {len(nff)} live events parsed")
    nff_by_cat: dict[str, list[dict]] = {k: [] for k in CAT.CATEGORY_ORDER}
    for e in nff:
        nff_by_cat.setdefault(e["bucket"], []).append(e)

    digest, plain = build_digest(nff_by_cat, rss_by_cat)
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
