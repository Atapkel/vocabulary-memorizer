# Word Box (Telegram)

Private Telegram vocabulary review for English and Russian cards whose default
meaning language is Kazakh. The bot stores its primary spaced-repetition history
in SQLite and accepts manual exports from local Word Studio.

Every review lets you reveal the correct answer and then grade your recall. After
early recognition practice, cards alternate between recalling the
Kazakh meaning and producing the English/Russian word. Scheduling remains
automatic: forgotten cards return sooner and remembered cards gradually move
farther away. The learner never needs to choose or see an interval.

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

## Import

In local Word Studio, choose **Library → Export for Telegram**. In Telegram,
choose **Import** or send `/add`, then attach the exported JSON file. Imports are
limited to 2 MB and 500 cards at a time. Duplicate cards are skipped without
resetting their existing review progress.

For words without context, use `/prompt yes, no, although`. The bot returns a
ready-to-paste ChatGPT prompt; paste the resulting JSON reply back through
`/add`. It includes a `priority` field, and due high-priority cards are reviewed
before normal and low-priority cards.

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
