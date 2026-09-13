"""
catalog.py — data + rules for nyc_free_events.py.

Everything the scout needs to turn raw RSS into a themed "free things to do in
NYC" digest lives here, so the main script stays about flow, not content:

  1. FEEDS      — REAL, verified RSS feeds of free / cheap NYC happenings. Each is
                  tagged `mostly_free`: True feeds (The Skint, NYC Parks) are treated
                  as free-eligible by default; False feeds must mention a free/cheap
                  cue in the title/summary before an item is kept.
  2. CATEGORIES — the buckets the user asked for (food, wine, beauty, outdoor,
                  museums, concerts, cultural) plus a few neighbors, with the
                  keywords used to classify each item.
  3. FREE_CUES / PAID_CUES — phrases that mark something as free/cheap (keep) or
                  clearly paid (drop) when a feed isn't already free-focused.
  4. STAPLES    — evergreen, always-free NYC standbys per category (free museum
                  hours, free concert series, park programs...) so every category
                  has something even on a quiet feed day. Links point at the
                  organizer's live page — you confirm the exact date/time there.
  5. live_links() — pre-filtered "what's on right now, free" deep-links
                  (Eventbrite free filters, NYC Parks calendar, The Skint...),
                  always current, no scraping, no quota.

Honesty note: this agent curates + points at live listings. It does not book,
RSVP, or promise a specific event happens on a specific day. "Free/almost-free"
is a best-effort filter on feed text — always confirm price on the linked page.
"""

from __future__ import annotations

import re
import urllib.parse

_q = urllib.parse.quote_plus


def _g(query: str) -> str:
    """Google deep-link — used when an organizer's canonical URL is uncertain, so
    the link never rots and always resolves to the current page."""
    return f"https://www.google.com/search?q={_q(query)}"


# ---------------------------------------------------------------------------
# 1. FEEDS — verified live RSS (checked 2026-09-12). `mostly_free` feeds are the
#    backbone; general feeds are kept but filtered to free/cheap items only.
# ---------------------------------------------------------------------------

FEEDS = [
    {
        "name": "The Skint",
        "url": "https://theskint.com/feed/",
        "mostly_free": True,   # entire site is free/cheap NYC events — the gold standard
        "weight": 3.0,
    },
    {
        "name": "NYC Parks Events",
        "url": "https://www.nycgovparks.org/xml/events_300_rss.xml",
        "mostly_free": True,   # city park programming — overwhelmingly free & outdoor
        "weight": 2.5,
    },
    {
        "name": "Secret NYC",
        "url": "https://secretnyc.co/feed/",
        "mostly_free": False,  # things-to-do blog; keep only items that read free/cheap
        "weight": 1.2,
    },
    {
        "name": "amNewYork",
        "url": "https://www.amny.com/feed/",
        "mostly_free": False,  # local news/events; keep only free/cheap items
        "weight": 1.0,
    },
    {
        "name": "Brokelyn",
        "url": "https://brokelyn.com/feed/",
        "mostly_free": False,  # "living cheap in Brooklyn" — keep free/cheap items
        "weight": 1.4,
    },
    {
        "name": "Village Voice",
        "url": "https://www.villagevoice.com/feed/",
        "mostly_free": False,  # arts / music / culture
        "weight": 1.1,
    },
    {
        "name": "Eater NY",
        "url": "https://ny.eater.com/rss/index.xml",
        "mostly_free": False,  # food scene; surfaces free tastings / food festivals
        "weight": 1.0,
    },
    {
        "name": "BrooklynVegan",
        "url": "https://www.brooklynvegan.com/feed/",
        "mostly_free": False,  # music; lots of free shows & residencies
        "weight": 1.0,
    },
    {
        "name": "6sqft",
        "url": "https://www.6sqft.com/feed/",
        "mostly_free": False,  # NYC culture / things-to-do
        "weight": 0.9,
    },
]


# ---------------------------------------------------------------------------
# 2. CATEGORIES — the buckets, in the order the user cares about, with the
#    keywords used to classify each item (matched case-insensitively against
#    title + summary). First matching category wins in listed order, so put the
#    more specific buckets (wine, beauty) before broad ones (cultural).
# ---------------------------------------------------------------------------

CATEGORIES = [
    # Placed FIRST so restaurant/brand freebies (grand openings, sample days,
    # giveaways, pop-up shops) route here instead of into food/beauty. Keywords
    # are deliberately specific to avoid stealing ordinary food/art events.
    ("brands", "🏢 Brand & Restaurant Freebies", [
        "grand opening", "grand-opening", "now open", "opening celebration",
        "ribbon cutting", "ribbon-cutting", "free sample", "free samples",
        "free sampling", "sampling event", "giveaway", "giveaways", "freebie",
        "freebies", "free cone", "free scoop", "free coffee", "free slice",
        "free pizza", "free donut", "free doughnut", "free bagel", "free ice cream",
        "free burger", "free coffee day", "free food day", "brand activation",
        "product launch", "pop-up shop", "popup shop", "flagship", "complimentary",
        "customer appreciation", "appreciation day",
    ]),
    ("wine", "🍷 Wine, Beer & Sips", [
        "wine", "winetasting", "wine tasting", "sommelier", "vineyard", "natural wine",
        "beer", "brewery", "brewing", "ale", "lager", "cider", "cocktail", "mixology",
        "spirits", "tasting", "happy hour", "sip", "distillery", "mezcal", "tequila",
    ]),
    ("food", "🍜 Food & Eats", [
        "food", "foodie", "taste of", "eat", "dinner", "brunch", "lunch", "restaurant",
        "culinary", "chef", "dumpling", "pizza", "taco", "bbq", "barbecue", "dessert",
        "ice cream", "bake", "pastry", "night market", "food market", "smorgasburg",
        "supper", "feast", "pop-up dinner", "cook",
    ]),
    ("beauty", "💄 Beauty & Self-care", [
        "beauty", "skincare", "skin care", "makeup", "cosmetics", "spa", "self-care",
        "self care", "glow", "facial", "nails", "hair", "wellness pop", "beauty pop",
        "sample sale", "fragrance", "perfume",
    ]),
    ("museums", "🖼️ Museums & Art", [
        "museum", "gallery", "galleries", "exhibit", "exhibition", "moma", "the met",
        "guggenheim", "whitney", "brooklyn museum", "new museum", "pay-what-you-wish",
        "pay what you wish", "free admission", "free museum", "art opening", "art walk",
        "installation", "sculpture", "photography exhibit",
    ]),
    ("outdoor", "🌳 Outdoors & Parks", [
        "park", "parks", "outdoor", "outdoors", "hike", "walk", "waterfront", "pier",
        "garden", "botanic", "bike", "cycling", "kayak", "beach", "picnic", "nature",
        "birding", "bird walk", "greenway", "esplanade", "rooftop", "riverside",
        "governors island", "prospect park", "central park", "trail", "fishing",
        "yoga", "fitness", "zumba", "tai chi", "boot camp", "workout", "run",
    ]),
    ("concerts", "🎶 Concerts & Live Music", [
        "concert", "live music", "band", "dj", "jazz", "orchestra", "symphony",
        "singer", "gig", "recital", "choir", "opera", "hip-hop show", "acoustic",
        "open mic", "music series", "philharmonic", "bandshell", "summerstage",
    ]),
    ("cultural", "🎭 Culture, Film & Community", [
        "cultural", "culture", "festival", "heritage", "film", "cinema", "screening",
        "movie", "theater", "theatre", "dance", "poetry", "reading", "book", "lecture",
        "talk", "comedy", "parade", "street fair", "block party", "fair", "market",
        "workshop", "class", "tour", "history", "storytelling", "drag", "pride",
    ]),
]

# ---------------------------------------------------------------------------
# NYC For Free EVENTS — the /events page renders ~100 curated FREE events into
# static HTML (name, category, start/end date + time, venue, link). We parse
# those directly so the digest shows ACTUAL events, not just a link to the site.
# This map routes NYC For Free's own category names into our buckets.
# ---------------------------------------------------------------------------

NFF_EVENTS_URL = "https://www.nycforfree.co/events"

NFF_CAT_MAP = {
    "Beauty": "beauty", "Wellness": "beauty",
    "Drink": "wine",
    "Food": "food",
    "NYFW": "brands", "Fashion": "brands", "Expo": "brands", "Cars": "brands",
    "Technology": "brands", "Travel": "brands", "Holiday": "brands", "Floral": "brands",
    "Music": "concerts",
    "Museum": "museums", "Art": "museums",
    "Sports/Fitness": "outdoor", "Nature": "outdoor", "Yoga": "outdoor",
    "Parade": "outdoor", "Pet": "outdoor",
    # everything else (TV/Movies, Community, Books, Party/Festival,
    # Performance/Dance, Culture, Kids, Other, ...) falls through to cultural
}


def nff_bucket(category: str) -> str:
    return NFF_CAT_MAP.get(category.strip(), "cultural")


CATEGORY_LABELS = {key: label for key, label, _ in CATEGORIES}

# Classification order (first keyword match wins). Keep this as-is.
CATEGORY_ORDER = [key for key, _, _ in CATEGORIES]

# DISPLAY order for the digest — independent of classification. Leads with the
# substantive buckets (food, museums, concerts) and pushes the pop-up-/fashion-
# heavy ones (beauty, brands) to the bottom. Must list every bucket exactly once.
CATEGORY_DISPLAY_ORDER = [
    "food", "museums", "concerts", "outdoor", "cultural", "wine", "beauty", "brands",
]
assert set(CATEGORY_DISPLAY_ORDER) == set(CATEGORY_ORDER), "display order must cover all buckets"


# Pre-compile one regex per category. Each keyword is matched with letter
# boundaries so short tokens don't substring-match (e.g. "ale" won't hit "sale",
# "sip" won't hit "gossip"); multi-word phrases still match as-is.
_CAT_RE = {
    key: re.compile(
        "|".join(rf"(?<![a-z]){re.escape(kw)}(?![a-z])" for kw in kws),
        re.IGNORECASE,
    )
    for key, _label, kws in CATEGORIES
}


def classify(text: str) -> str | None:
    """Return the first category (in CATEGORIES order) whose keywords appear in
    `text`, else None. Intended to run on the item TITLE — feed summaries are
    long and multi-topic, which cross-contaminates the buckets."""
    for key, _label, _kws in CATEGORIES:
        if _CAT_RE[key].search(text):
            return key
    return None


# ---------------------------------------------------------------------------
# 3. FREE / PAID cues — used only for feeds where mostly_free is False.
# ---------------------------------------------------------------------------

FREE_CUES = [
    "free", "no cover", "no charge", "complimentary", "pay what you wish",
    "pay-what-you-wish", "pay-what-you-can", "donation", "suggested donation",
    "rsvp", "$0", "gratis", "open to the public", "no cost", "admission is free",
    "free admission", "free entry",
]

# Cheap-but-not-free cues we still allow (the user said "completely free OR almost").
CHEAP_CUES = ["$5", "$8", "$10", "$12", "$15", "$16", "$18", "$20",
              "under $20", "cheap", "budget"]

# If any of these appear AND no free/cheap cue does, drop the item as clearly paid.
PAID_CUES = [
    "$25", "$30", "$35", "$40", "$45", "$50", "$60", "$75", "$85", "$95",
    "$100", "$125", "$150", "tickets from $", "starting at $", "sold out",
    "vip", "prix fixe", "bottomless", "gala",
]


def free_verdict(text: str, mostly_free: bool) -> str | None:
    """Return "free", "cheap", or None (drop). Feeds that are mostly_free pass by
    default unless clearly marked expensive; other feeds must show a free/cheap
    cue. Best-effort text heuristic — always confirm price on the link."""
    low = text.lower()
    has_free = any(c in low for c in FREE_CUES)
    has_cheap = any(c in low for c in CHEAP_CUES)
    has_paid = any(c in low for c in PAID_CUES)

    if mostly_free:
        if has_paid and not (has_free or has_cheap):
            return None
        return "free" if has_free or not has_cheap else "cheap"

    # Stricter for general feeds.
    if has_free:
        return "free"
    if has_cheap and not has_paid:
        return "cheap"
    return None


# ---------------------------------------------------------------------------
# NYC-For-Free-style CURATED SOURCES — the two definitive "purely free NYC"
# publications (like the site the user modeled this on). NEITHER offers a usable
# RSS feed, so instead of parsing them we surface their exact category pages as
# tap-through links — the same curated info NYC For Free / Club Free Time show,
# always current. Slugs verified 2026-09-12.
# ---------------------------------------------------------------------------

_CFF = "https://www.nycforfree.co"          # NYC For Free (curated free NYC)
_CFT = "https://www.clubfreetime.com/new-york-city-nyc"  # Club Free Time (free culture)


# ---------------------------------------------------------------------------
# 4. STAPLES — evergreen, reliably-free NYC standbys per category. Guarantees
#    every requested category has something even when the feeds are quiet.
#    Each category LEADS with the matching NYC For Free / Club Free Time page so
#    the digest mirrors those sites. Links go live — confirm day/time there.
# ---------------------------------------------------------------------------

STAPLES = {
    "brands": [
        ("NYC For Free — brand sampling programs & giveaways",
         f"{_CFF}/resources/free-sampling-programs"),
        ("Eventbrite — free NYC pop-ups & brand activations",
         "https://www.eventbrite.com/d/ny--new-york/free--pop-up/"),
        ("Eventbrite — free NYC grand openings",
         "https://www.eventbrite.com/d/ny--new-york/free--grand-opening/"),
        ("EatDrinkDeals — national restaurant freebies & free-food days",
         "https://www.eatdrinkdeals.com/"),
        ("Eater NY — upcoming NYC restaurant openings",
         _g("Eater NY new restaurant openings this month")),
    ],
    "wine": [
        ("NYC For Free — free samplings (food & drink giveaways)",
         f"{_CFF}/resources/free-sampling-programs"),
        ("Free wine tastings at NYC wine shops (Astor, Chelsea, etc.)",
         _g("free wine tasting NYC wine shop this week")),
        ("Brooklyn Brewery — free-flow tours & tasting room",
         "https://brooklynbrewery.com/"),
    ],
    "food": [
        ("NYC For Free — free food samplings & giveaways",
         f"{_CFF}/resources/free-sampling-programs"),
        ("Smorgasburg — free entry, huge open-air food market (Apr–Oct)",
         "https://www.smorgasburg.com/"),
        ("Free food festivals & tastings around NYC",
         "https://www.eventbrite.com/d/ny--new-york/free--food-and-drink--events/"),
    ],
    "beauty": [
        ("NYC For Free — free in-store beauty services (facials, makeovers)",
         f"{_CFF}/resources/free-in-store-beauty-services"),
        ("Free beauty pop-ups, samples & sample sales in NYC",
         _g("free beauty pop-up sample sale NYC this week")),
        ("Sephora / department-store free mini-facials & makeovers",
         _g("free makeover facial NYC beauty counter this week")),
    ],
    "outdoor": [
        ("NYC Parks — free events citywide (nature walks, fitness, festivals)",
         "https://www.nycgovparks.org/events"),
        ("Club Free Time — free walking tours around NYC",
         f"{_CFT}/free-tours"),
        ("Governors Island — free ferry mornings & open-air programming",
         "https://www.govisland.com/things-to-do/events"),
        ("Bryant Park — free classes, games & performances",
         "https://bryantpark.org/programs"),
    ],
    "museums": [
        ("NYC For Free — free museums & free-admission hours",
         f"{_CFF}/resources/free-museums"),
        ("Club Free Time — gallery & exhibition openings (free)",
         f"{_CFT}/galleries-exhibition-openings"),
        ("The Met — always pay-what-you-wish for NY State residents",
         "https://www.metmuseum.org/plan-your-visit"),
    ],
    "concerts": [
        ("Club Free Time — free classical / jazz / concerts this week",
         f"{_CFT}/free-classical-music-jazz-blues-concerts"),
        ("Free NYC concert series (SummerStage, Lincoln Center, Bandshell)",
         "https://cityparksfoundation.org/summerstage/"),
        ("Free live music & open mics in NYC this week",
         "https://www.eventbrite.com/d/ny--new-york/free--music--events/"),
    ],
    "cultural": [
        ("Club Free Time — free theater performances & shows",
         f"{_CFT}/free-theater-performances-shows"),
        ("NYC For Free — free movie screenings",
         f"{_CFF}/resources/free-movies"),
        ("NYC For Free — free comedy shows",
         f"{_CFF}/resources/free-comedy-shows"),
        ("Club Free Time — free talks, readings & fairs/festivals",
         f"{_CFT}/free-talks-lectures"),
    ],
}


# ---------------------------------------------------------------------------
# 4b. EVERGREEN — NYC For Free's "resource" pages: perennial freebies that are
#     always available (not date-specific events), so they get their own fixed
#     section in every digest. Slugs verified 2026-09-12.
# ---------------------------------------------------------------------------

EVERGREEN = [
    ("🖼️ Free museums & free-admission hours", f"{_CFF}/resources/free-museums"),
    ("🎬 Free movie screenings", f"{_CFF}/resources/free-movies"),
    ("😂 Free comedy shows", f"{_CFF}/resources/free-comedy-shows"),
    ("📺 Free TV talk-show tapings", f"{_CFF}/resources/free-talk-shows"),
    ("💄 Free in-store beauty services", f"{_CFF}/resources/free-in-store-beauty-services"),
    ("🧴 Free sampling programs (food/drink/beauty)", f"{_CFF}/resources/free-sampling-programs"),
    ("📚 NY library freebies (passes, events, tech)", f"{_CFF}/resources/ny-library-freebies"),
    ("🎂 Birthday freebies around NYC", f"{_CFF}/resources/birthday-freebies"),
]


# ---------------------------------------------------------------------------
# 5. LIVE LINKS — pre-filtered "free, on right now" deep-links. Always current.
# ---------------------------------------------------------------------------

def live_links() -> list[tuple[str, str]]:
    return [
        ("⭐ NYC For Free — this week's free NYC events", f"{_CFF}/events"),
        ("⭐ Club Free Time — 300+ free NYC events this week",
         f"{_CFT}/free-events-things-to-do/this-week"),
        ("The Skint — today's free & cheap NYC picks", "https://theskint.com/"),
        ("NYC Parks — free events calendar (outdoor)", "https://www.nycgovparks.org/events"),
        ("Eventbrite — ALL free NYC events today",
         "https://www.eventbrite.com/d/ny--new-york/free--events--today/"),
        ("Eventbrite — free NYC food & drink",
         "https://www.eventbrite.com/d/ny--new-york/free--food-and-drink--events/"),
        ("Eventbrite — free NYC music",
         "https://www.eventbrite.com/d/ny--new-york/free--music--events/"),
        ("Eventbrite — free NYC arts",
         "https://www.eventbrite.com/d/ny--new-york/free--arts--events/"),
        ("Eventbrite — free NYC pop-ups & brand events",
         "https://www.eventbrite.com/d/ny--new-york/free--pop-up/"),
        ("EatDrinkDeals — national restaurant freebies & free-food days",
         "https://www.eatdrinkdeals.com/"),
        ("NYC Tourism — free things to do", "https://www.nyctourism.com/free-things-to-do/"),
        ("Time Out — best free things in NYC", "https://www.timeout.com/newyork/things-to-do/free-things-to-do-in-new-york"),
    ]
