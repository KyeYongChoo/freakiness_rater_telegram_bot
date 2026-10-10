# Freakiness Rater 🥵

A Telegram bot that lets your group chat vote on the freakiest messages each day. At midnight it posts the results. Currently hosted on Google Cloud.

## How it works

- **Vote:** reply to a message with `/freaky`. Each person gets one vote per message. You can vote for as many different messages as you like, including your own.
- **😈 on voted messages:** in both modes, the bot reacts 😈 to any message that has at least one vote, so freaky messages stand out in the chat. The 😈 comes off if every vote on the message is taken back. Bot commands (like someone else's `/freaky`) can't be voted for. Change `FREAKY_MESSAGE_REACTION` in `bot.py` to use a different emoji.
- **Take a vote back:** reply to the message with `/unfreaky`. This works until that day's recap has run; after that the vote is locked in.
- **Check standings:** `/standings` shows today's votes so far. Unlike the midnight recap, it doesn't tag (and notify) the leader.
- Commands only count when they're sent. Editing an old message into `/freaky` does nothing.
- **All-time stats:** `/alltime` shows the all-time leaderboard, how many days each person was crowned freakiest (👑), the 3 freakiest messages ever, and the "biggest enabler" (whoever has cast the most votes).
- **Daily recap:** at midnight (in `TIMEZONE`) the bot posts the freakiest person (most votes across all their messages), the freakiest message, and a leaderboard. It only posts if **at least 2 different messages from at least 2 different people** got votes that day. Ties are shown as joint winners.
- Votes count toward the day they were cast, even when the message is older. A vote sent just before midnight that only reaches the bot after that day's recap counts toward the new day instead.
- If the bot was offline at midnight, it posts any missed recaps when it starts up again. If Telegram is unreachable or rate-limits the bot, it retries the recap instead of skipping it.
- **Anonymous admins:** Telegram hides which admin sent a message, so all anonymous admins share one vote per message (and can take back each other's votes). Their messages are credited to the group itself.
- **Quoted messages are permanent:** the bot saves the text of voted messages when the votes come in. Recaps and `/alltime` keep quoting them even if the original is deleted, and spoilers are shown as plain text.
- **Settings** (anyone in the group can change them; `/help` shows the current state):
  - `/mutereports` / `/unmutereports`: turn the midnight report off or on. Days that pass while muted are skipped for good and aren't posted later. They still count toward `/alltime` stats and crowns.
  - `/quietvotes` / `/loudvotes`: in quiet mode the bot only reacts to `/freaky` with 🔥 (🤷 if you already voted, 💔 for `/unfreaky`) instead of replying. If the group's reaction settings block that emoji, it falls back to a reply. Bots can only use Telegram's standard reactions, so 🌶️ and 🥵 aren't possible; change `VOTE_REACTION` in `bot.py` to use ❤️‍🔥, 😈 or 💋 instead.
- **Jump to message:** in recaps and `/alltime`, tap a quoted message (marked ↗) to jump to it in the chat. This only works in **supergroups**, because basic groups don't have message links. Telegram upgrades a group to a supergroup automatically when you change certain settings (e.g. turning on *Chat history for new members*). Stats carry over when that happens, but links to messages from before the upgrade may not work.

## 1. Create the bot

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.
2. Optional: send `/setcommands`, pick your bot, and paste:
   ```
   freaky - Reply to a message to vote it freaky
   unfreaky - Reply to a message to take back your vote
   standings - Today's freakiness so far
   alltime - All-time leaderboard and freakiest messages ever
   mutereports - Stop posting the daily report
   unmutereports - Resume the daily report
   quietvotes - Confirm votes with a reaction instead of a reply
   loudvotes - Confirm votes with a reply
   help - How this works and current settings
   ```
3. Add the bot to your group. If `/freaky` gets ignored, or the bot says to reply to a message when you did, either make the bot an admin or run `/setprivacy` → **Disable** in BotFather, then remove the bot and add it back. The second problem only happens in basic groups (not supergroups): Telegram leaves out a reply when it points at a message the bot was never shown, and with privacy mode on, the bot isn't shown ordinary messages. In the end I opted to request the owner to change to supergroup instead since it doesnt have the same privacy problems

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
- **Any VPS** (e.g. Hetzner, DigitalOcean, Oracle Cloud Always Free): install Docker, copy the project over, create `.env`, then run `docker compose up -d --build`. The bot restarts by itself after crashes and reboots, and the database is kept in `./data/freaky.db`. Logs: `docker compose logs -f`. Update: copy the new code over, then run `docker compose up -d --build` again.

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
