# Freakiness Rater 🥵

A Telegram bot that lets your group chat vote on the freakiest messages each day. At midnight it posts the results.

## How it works

- **Vote:** reply to a message with `/freaky`. Each person gets one vote per message. You can vote for as many different messages as you like, including your own.
- **Check standings:** `/standings` shows today's votes so far.
- **All-time stats:** `/alltime` shows the all-time leaderboard, how many days each person was crowned freakiest (👑), the 3 freakiest messages ever, and the "biggest enabler" (whoever has cast the most votes).
- **Daily recap:** at midnight (in `TIMEZONE`) the bot posts the freakiest person (most votes across all their messages), the freakiest message, and a leaderboard. It only posts if **at least 2 different messages from at least 2 different people** got votes that day. Ties are shown as joint winners.
- Votes count toward the day they were cast, even when the message is older.
- If the bot was offline at midnight, it posts any missed recaps when it starts up again.

## 1. Create the bot

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.
2. Optional: send `/setcommands`, pick your bot, and paste:
   ```
   freaky - Reply to a message to vote it freaky
   standings - Today's freakiness so far
   alltime - All-time leaderboard and freakiest messages ever
   help - How this works
   ```
3. Add the bot to your group. If `/freaky` gets ignored, either make the bot an admin or run `/setprivacy` → **Disable** in BotFather, then remove the bot and add it back.

## 2. Run locally

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then put your token in .env
.venv\Scripts\python bot.py
```

## 3. Deploy to a cloud host

The bot uses long polling, so it needs an always-on **worker / background process**. It doesn't serve a website. The included `Dockerfile` works on any host that runs containers:

- **Railway:** New Project → Deploy from GitHub repo. Add `BOT_TOKEN` (and `TIMEZONE` if you're not in Singapore) as variables. Attach a **Volume** mounted at `/data`.
- **Fly.io:** `fly launch --no-deploy`, then `fly volumes create data --size 1`, add a `[mounts]` section with `source = "data"` and `destination = "/data"` to `fly.toml`, run `fly secrets set BOT_TOKEN=...`, then `fly deploy`.
- **Any VPS** (e.g. Oracle Cloud Always Free): `docker build -t freaky . && docker run -d --restart unless-stopped -e BOT_TOKEN=... -v freaky-data:/data freaky`

Free tiers change often, so check current pricing. Hosts that put free *web services* to sleep, like Render's free tier, won't work for this bot.

**Persistence:** votes are stored in SQLite at `DB_PATH` (`/data/freaky.db` in Docker). Without a persistent volume, a redeploy wipes that day's votes.

## Configuration

| Variable    | Default          | Meaning                                  |
|-------------|------------------|------------------------------------------|
| `BOT_TOKEN` | required         | Token from BotFather                     |
| `TIMEZONE`  | `Asia/Singapore` | IANA timezone that defines "end of day"  |
| `DB_PATH`   | `freaky.db`      | SQLite file location                     |

To change the recap threshold, edit `MIN_MESSAGES` / `MIN_AUTHORS` in `bot.py`.

> Only run one copy of the bot at a time. Telegram allows only one polling connection per token, so stop the local copy before deploying.
