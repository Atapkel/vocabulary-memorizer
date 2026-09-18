# Word Box (Telegram)

Private Telegram vocabulary review for English and Russian cards whose default
meaning language is Kazakh. The bot stores its primary spaced-repetition history
in SQLite and accepts manual exports from local Word Studio. It also collects
unknown words and lesson notes for LLM-assisted card creation.

Every review lets you reveal the correct answer and then grade your recall. After
early recognition practice, cards alternate between recalling the
Kazakh meaning and producing the English/Russian word. Scheduling remains
automatic: forgotten cards return sooner and remembered cards gradually move
farther away. The learner never needs to choose or see an interval.

## Memory workflows

The main menu puts reviews, unknown words, lesson notes, progress, and reminders
up front. **More options** contains prompts, imports, the library, and games.
Telegram's `/` command menu lists the main commands. The bot does not call an
LLM or send your notes to one automatically.

1. Send a word, phrase, or comma-separated list as an ordinary message. The bot
   saves it to the unknown-word inbox automatically across restarts. `/unknown`
   also works. Use **Saved unknown words** or `/unknowns` to see the newest
   entries. Remove one with `/delete word` or its 🗑 button. Removing an inbox
   entry does not delete an already imported study card.
2. Open **More options → Word prompt** or send `/unknownprompt`. Copy the prompt into an LLM.
   Check its JSON response, then send `/add` followed by the JSON in your next
   message. Successfully imported words leave the inbox.
3. For other material, send `/lesson your notes` or tap **Add lesson note** and
   send a note. Use **More options → Lesson prompt** or `/lessonprompt`, check the LLM's JSON, then
   send `/lessonimport` followed by the JSON in your next message. Review the
   resulting question and answer cards with `/lessons`.

Prompts use the first 15 unknown words or 10 lesson notes in the inbox. Repeat
the prompt and import process to work through a larger inbox. Lesson imports
accept at most 100 cards and keep duplicates from resetting review history.
The lesson JSON format is:

```json
[{"topic":"Biology","question":"What does DNA store?","answer":"Genetic information.","hint":""}]
```

Reminders check for due words and lesson cards at 09:00, 14:00, and 19:00 in
your configured time zone. They send at most one notification per review slot,
only when something is due, and stay silent during quiet hours. Defaults are
`Asia/Qyzylorda` and 22:00–08:00. Set these with `/timezone Region/City` and
`/quiet 22:00 08:00`; use `/reminders off` or `/reminders on` to toggle them.
The notification suggests a short session of up to 20 cards; you can stop at
any point. These settings are stored in the same SQLite database.

The review method uses active recall and spaced practice, both supported by a
[systematic review](https://pubmed.ncbi.nlm.nih.gov/37615780/). Quiet hours are
intended to protect sleep; a [memory meta-analysis](https://pubmed.ncbi.nlm.nih.gov/35404637/)
found a benefit from sleep. The three clock times are practical defaults, not a
research-proven optimum for every person.

## Configure and run

```bash
export BOT_TOKEN="token from BotFather"
export ALLOWED_USER_ID="your numeric Telegram user id"
uv run python bot.py
```

The bot intentionally refuses to start without `ALLOWED_USER_ID`; this prevents
an accidentally public deployment. Set `VOCAB_DB_PATH` to an absolute persistent
server path in production so the database does not depend on the working folder.

## Run on a server with Docker

Docker Compose is included. It uses Telegram long polling, so no domain name,
open port, or reverse proxy is needed. The SQLite database is stored in a named
Docker volume and survives image rebuilds and container restarts.

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
cp .env.example .env
# Edit .env: add BOT_TOKEN and your numeric ALLOWED_USER_ID
docker compose up -d --build
```

Useful commands:

```bash
docker compose logs -f
docker compose ps
docker compose pull && docker compose up -d --build
docker compose down
```

Do not run `docker compose down -v` unless you deliberately want to erase all
vocabulary and review history. Keep `.env` private; it is excluded from Git.

### Updating an existing deployment

Keep the database. The bot creates missing tables and columns at startup and
continues using existing cards and review history. Before rebuilding, make a
backup from the project directory on the server:

```bash
docker compose stop word-box
docker compose cp word-box:/data/vocab.db ./vocab-backup.db
docker compose up -d --build
docker compose logs -f word-box
```

The old `priority` column can remain in an existing database. Current code
ignores it; new databases do not create it. `docker compose down -v` removes the
named data volume and permanently erases review history.

## Import

In local Word Studio, choose **Library → Export for Telegram**. In Telegram,
choose **Import** or send `/add`, then attach the exported JSON file. Imports are
limited to 2 MB and 500 cards at a time. Duplicate cards are skipped without
resetting their existing review progress.

For words without context, use `/prompt yes, no, although`. The bot returns a
ready-to-paste ChatGPT prompt; paste the resulting JSON reply back through
`/add`. Older exports that contain a `priority` field still import; the bot
ignores that field and reviews cards by due time.

## Commands

- `/start` or `/help` — dashboard
- `/review` or `/learn` — review all currently due cards
- `/add` — import cards
- `/prompt word1, word2` — create a ChatGPT prompt for context-free words
- `/stats` — progress counts
- `/list` — first 30 cards ordered by due time
- `/game` — play quick meaning-match and context word-finding rounds
- `/leeches` — suspended cards that have been missed repeatedly
- `/note WORD_ID text` — save a personal mnemonic or memory hook
- `/reset WORD_ID` — reset a suspended card to new after improving it
- `/memory` — unknown words, lessons, and reminder menu
- `/unknown` and `/unknownprompt` — collect words and generate an LLM prompt
- `/unknowns` and `/delete word` — view and remove saved unknown words
- `/lesson`, `/lessonprompt`, `/lessonimport`, `/lessons` — collect, create, and review lesson cards
- `/settings`, `/timezone`, `/quiet`, `/reminders` — reminder controls

## Code layout

- `bot.py` — application entry point and handler registration
- `wordbox/config.py` — environment settings and limits
- `wordbox/storage.py` — vocabulary persistence and schema migration
- `wordbox/scheduling.py` — review interval calculation
- `wordbox/views.py` — vocabulary messages and buttons
- `wordbox/handlers.py` — vocabulary Telegram flows
- `wordbox/memory.py` — lesson and inbox persistence, reminder rules
- `wordbox/memory_handlers.py` — memory Telegram flows and reminder worker

Run the local checks with `.venv/bin/python -m unittest discover -s tests -v`.
