# NYC Free Events Scout 🗽

A small local Windows agent that sends you a **daily Telegram digest of free (or
almost-free) things to do in NYC** — grouped by the stuff you asked for: food,
wine, beauty, outdoors, museums, concerts, and cultural events.

It's the "things to do as a New Yorker" sibling to your other agents
(`fare-watcher`, `gowild-scout`, `tech-networking-scout`).

## What it does each morning

1. Pulls several **real, verified RSS feeds** of free/cheap NYC happenings.
2. Keeps only items that read as **free or almost-free**, and sorts each into a
   category (brand/restaurant freebies · wine · food · beauty · outdoors ·
   museums · concerts · cultural).
3. **De-dupes** against `seen.json` so you get fresh picks, not yesterday's list,
   and collapses the same event repeated across dates.
4. Builds a themed digest: a few live picks per category, then a fixed
   **"Always free — NYC For Free standbys"** section (free museums, movies,
   comedy, TV tapings, in-store beauty, samplings, library freebies, birthday
   freebies), then live "what's-on-now" deep-links.
5. Optional: a local **Ollama** model adds a one-line "here's your best free move
   today." Degrades gracefully if Ollama is off.

Sent to a dedicated Telegram bot, silently, via Windows Task Scheduler at 8 AM.

## Feeds it reads (all verified live)

| Feed | Why | Free-focused? |
|------|-----|---------------|
| [The Skint](https://theskint.com/feed/) | The gold standard for free/cheap NYC daily events | ✅ yes |
| [NYC Parks Events](https://www.nycgovparks.org/xml/events_300_rss.xml) | ~1,300 upcoming outdoor park events citywide | ✅ yes |
| [Secret NYC](https://secretnyc.co/feed/) | Things-to-do blog | filtered to free/cheap only |
| [amNewYork](https://www.amny.com/feed/) | Local news + events | filtered to free/cheap only |

Feeds where the whole site is free-focused pass by default; general feeds must
mention a free/cheap cue before an item is kept. All of this is curated data +
live links — the agent never scrapes login-walled sites and never books/RSVPs.
**Confirm price & date on each link before you head out.**

### NYC-For-Free-style curated sources (no RSS — surfaced as links)

This agent is modeled on the **NYC For Free** website. The two definitive
"purely free NYC" publications don't offer a usable RSS feed, so rather than
parse them the agent surfaces their exact category pages as tap-through links —
the same curated info, always current. They lead the "Dig deeper" section, and
each category also links the matching page when the feeds are quiet:

| Source | Maps to | Example page |
|--------|---------|--------------|
| [NYC For Free](https://www.nycforfree.co/events) | beauty, food/wine, museums, film, comedy | Free In-Store Beauty Services, Free Sampling, Free Museums, Free Movies |
| [Club Free Time](https://www.clubfreetime.com/new-york-city-nyc/free-events-things-to-do/this-week) | concerts, museums/art, theater, culture | Free classical/jazz, gallery openings, theater, talks, tours |

## Runs in the cloud (GitHub Actions)

This agent ships with `.github/workflows/daily.yml`, so it runs on GitHub's
servers **whether or not your PC is on**. No machine to keep awake.

- **Schedule:** daily at 12:00 UTC (8 AM ET in summer / 7 AM ET in winter — plain
  cron has no timezone, so the local hour shifts by 1 across DST). Trigger a run
  by hand anytime from the repo's **Actions** tab → *Run workflow*.
- **Secrets** (repo → Settings → Secrets and variables → Actions):
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — required.
  - `ANTHROPIC_API_KEY` — optional; enables the ✨ "best free move today" line
    (Claude Haiku, ~cents/month). Without it the digest still sends, minus that line.
- **State:** `seen.json` persists across runs via `actions/cache`, so the daily
  de-dupe survives the ephemeral runners.

The AI line uses the **Claude API** (`claude-haiku-4-5`) instead of local Ollama,
so it works in the cloud. It's read from `ANTHROPIC_API_KEY` and degrades
gracefully if the key is missing.

## Local one-time setup (optional)

You can still run it on your own machine (Task Scheduler) with `secrets_local.py`
and `register_task.ps1`. Environment variables / GitHub Secrets always win over
`secrets_local.py`, so the same code runs in both places.


1. **Create the Telegram bot** (if not done already):
   - Open **@BotFather** → `/newbot`
   - Name it e.g. `Free NYC Scout`, username e.g. `@free_nyc_scout_9127_bot`
   - Copy the token.
2. Put your token + chat id in **`secrets_local.py`** (untracked):
   - Paste the token into `TELEGRAM_BOT_TOKEN`.
   - Open a chat with your new bot and press **Start** (send any message).
   - Run `python nyc_free_events.py --chatid` and paste the number into
     `TELEGRAM_CHAT_ID`.
3. Install deps: `pip install -r requirements.txt`
4. Register the daily task: `./register_task.ps1`

## Run it manually

```powershell
python nyc_free_events.py --preview   # dry run, prints to console, no Telegram, no state change
python nyc_free_events.py --chatid    # discover your chat id after messaging the bot
python nyc_free_events.py             # real run: sends to Telegram + updates seen.json
```

## Files

| File | Purpose |
|------|---------|
| `nyc_free_events.py` | Main agent: fetch → filter → classify → digest → send. |
| `catalog.py` | Feeds, category keywords, free/cheap rules, evergreen staples, live links. |
| `secrets_local.py` | Your bot token + chat id (git-ignored). |
| `register_task.ps1` | Registers the silent daily 8 AM Task Scheduler job. |
| `seen.json` | Rolling memory of already-sent items (git-ignored). |
| `nyc_free_events.log` | Run log (git-ignored). |

## Tuning

All in the `CONFIG` block of `nyc_free_events.py` or the tables in `catalog.py`:

- `PICKS_PER_CATEGORY` — live picks shown per category before falling back to staples.
- `FEEDS` — add/remove feeds; set `mostly_free` and `weight` per feed.
- `CATEGORIES` — add keywords or new buckets (matched with word boundaries on the title).
- `STAPLES` — the evergreen "always free" fallbacks per category.
- `USE_OLLAMA` — set `False` to skip the local-LLM one-liner.
