import datetime as dt
import html
import logging
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Message, ReplyParameters, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from db import AuthorTally, Database, MessageTally

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Singapore"))
DB_PATH = os.environ.get("DB_PATH", "freaky.db")

# The daily recap is only posted if at least this many distinct messages
# from at least this many distinct people received votes.
MIN_MESSAGES = 2
MIN_AUTHORS = 2

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("freaky")

db = Database(DB_PATH)


def today() -> str:
    return dt.datetime.now(TZ).date().isoformat()


def message_preview(msg: Message, limit: int = 200) -> str:
    text = msg.text or msg.caption
    if not text:
        for kind in ("sticker", "photo", "video", "animation", "voice", "video_note", "audio", "document", "poll"):
            if getattr(msg, kind):
                return f"[{kind.replace('_', ' ')}]"
        return "[message]"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def author_of(msg: Message) -> tuple[int, str] | None:
    """(id, display name) of whoever sent the message, or None if it can't be voted on."""
    if msg.sender_chat:  # anonymous admin or channel post
        return msg.sender_chat.id, msg.sender_chat.title or "Anonymous"
    if msg.from_user and not msg.from_user.is_bot:
        return msg.from_user.id, msg.from_user.full_name
    return None


def mention(author_id: int, name: str) -> str:
    if author_id > 0:  # real user, not a chat
        return f'<a href="tg://user?id={author_id}">{html.escape(name)}</a>'
    return f"<b>{html.escape(name)}</b>"


def plural(n: int) -> str:
    return f"{n} vote" if n == 1 else f"{n} votes"


def qualifies(messages: list[MessageTally], authors: list[AuthorTally]) -> bool:
    return len(messages) >= MIN_MESSAGES and len(authors) >= MIN_AUTHORS


def build_report(messages: list[MessageTally], authors: list[AuthorTally], title: str) -> str:
    top_author_votes = authors[0].votes
    top_authors = [a for a in authors if a.votes == top_author_votes]
    top_msg_votes = messages[0].votes
    top_msgs = [m for m in messages if m.votes == top_msg_votes][:3]

    lines = [f"🥵 <b>{html.escape(title)}</b>", ""]

    names = " &amp; ".join(mention(a.author_id, a.author_name) for a in top_authors)
    label = "Freakiest person" if len(top_authors) == 1 else "Joint freakiest"
    each = " each" if len(top_authors) > 1 else ""
    lines.append(f"👑 {label}: {names} ({plural(top_author_votes)}{each})")
    lines.append("")

    label = "Freakiest message" if len(top_msgs) == 1 else "Joint freakiest messages"
    lines.append(f"💬 {label} ({plural(top_msg_votes)}{' each' if len(top_msgs) > 1 else ''}):")
    for m in top_msgs:
        lines.append(f"<blockquote>{html.escape(m.text)}</blockquote>— {html.escape(m.author_name)}")
    lines.append("")

    lines.append("🏆 <b>Leaderboard</b>")
    for i, a in enumerate(authors[:10], start=1):
        lines.append(f"{i}. {html.escape(a.author_name)} — {plural(a.votes)}")

    return "\n".join(lines)


async def freaky(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target = msg.reply_to_message
    # In forum topics, a non-reply message "replies" to the topic's creation message.
    if target is None or target.forum_topic_created:
        await msg.reply_text("Reply to the message you think is freaky with /freaky 🥵")
        return

    author = author_of(target)
    if author is None:
        await msg.reply_text("Bots can't be freaky. Pick a human message.")
        return

    voter = update.effective_user
    day = msg.date.astimezone(TZ).date().isoformat()
    author_id, author_name = author
    added = db.add_vote(
        chat_id=msg.chat_id,
        message_id=target.message_id,
        voter_id=voter.id,
        voter_name=voter.full_name,
        author_id=author_id,
        author_name=author_name,
        text=message_preview(target),
        day=day,
    )
    count = db.message_votes(msg.chat_id, target.message_id, day)
    if added:
        await msg.reply_text(f"🔥 Vote counted. This message is at {plural(count)} today.")
    else:
        await msg.reply_text(f"You already voted for this one ({plural(count)} today).")


async def standings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    day = today()
    messages = db.message_tallies(chat_id, day)
    if not messages:
        await update.effective_message.reply_text("No freaky votes yet today. Reply to a message with /freaky.")
        return
    authors = db.author_tallies(chat_id, day)
    text = build_report(messages, authors, "Freakiness so far today")
    if not qualifies(messages, authors):
        text += (
            f"\n\n<i>Tonight's recap needs votes on at least {MIN_MESSAGES} messages "
            f"from {MIN_AUTHORS} different people.</i>"
        )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


def format_day(day: str) -> str:
    return dt.date.fromisoformat(day).strftime("%d %b %Y").lstrip("0")


def count_crowns(chat_id: int) -> dict[int, int]:
    """How many finished days each person was crowned freakiest (ties all get a crown)."""
    crowns: dict[int, int] = {}
    for day in db.voted_days(chat_id, before_day=today()):
        messages = db.message_tallies(chat_id, day)
        authors = db.author_tallies(chat_id, day)
        if not qualifies(messages, authors):
            continue
        for a in authors:
            if a.votes == authors[0].votes:
                crowns[a.author_id] = crowns.get(a.author_id, 0) + 1
    return crowns


async def alltime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    authors = db.alltime_authors(chat_id)
    if not authors:
        await update.effective_message.reply_text("No freaky votes yet. Reply to a message with /freaky.")
        return
    crowns = count_crowns(chat_id)
    voters = db.alltime_voters(chat_id)

    lines = ["🥵 <b>All-Time Freakiness</b>", ""]

    if crowns:
        most = max(crowns.values())
        names = {a.author_id: a.author_name for a in authors}
        kings = " &amp; ".join(html.escape(names[i]) for i, c in crowns.items() if c == most)
        lines.append(f"👑 Most crowns: <b>{kings}</b> ({most} day{'s' if most != 1 else ''} as freakiest)")
        lines.append("")

    lines.append("🏆 <b>All-time leaderboard</b>")
    for i, a in enumerate(authors[:10], start=1):
        c = crowns.get(a.author_id, 0)
        lines.append(f"{i}. {html.escape(a.author_name)} — {plural(a.votes)}" + (f" · 👑×{c}" if c else ""))
    lines.append("")

    lines.append("💬 <b>Freakiest messages ever</b>")
    for m, first_day in db.alltime_messages(chat_id, limit=3):
        lines.append(
            f"<blockquote>{html.escape(m.text)}</blockquote>"
            f"— {html.escape(m.author_name)}, {format_day(first_day)} ({plural(m.votes)})"
        )
    lines.append("")

    top_voter = voters[0]
    lines.append(f"🙈 Biggest enabler: {html.escape(top_voter.author_name)} ({top_voter.votes} votes cast)")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🥵 <b>Freakiness Rater</b>\n\n"
        "/freaky — reply to a message with this to vote it as freaky "
        "(one vote per person per message)\n"
        "/standings — see today's freakiness so far\n"
        "/alltime — all-time leaderboard, crowns and freakiest messages ever\n\n"
        f"At midnight I'll post who was freakiest and the freakiest message, as long as "
        f"at least {MIN_MESSAGES} messages from {MIN_AUTHORS} different people got votes.",
        parse_mode=ParseMode.HTML,
    )


async def private_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Add me to a group chat and reply to messages there with /freaky.")


async def post_pending_recaps(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post recaps for every finished day that hasn't had one yet (also catches up after downtime)."""
    for chat_id, day in db.pending_recaps(before_day=today()):
        messages = db.message_tallies(chat_id, day)
        authors = db.author_tallies(chat_id, day)
        if qualifies(messages, authors):
            text = build_report(messages, authors, f"Daily Freakiness Report — {format_day(day)}")
            try:
                await context.bot.send_message(
                    chat_id,
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_parameters=ReplyParameters(
                        message_id=messages[0].message_id, allow_sending_without_reply=True
                    ),
                )
                log.info("Posted recap for chat %s, %s", chat_id, day)
            except TelegramError as e:
                log.warning("Couldn't post recap for chat %s, %s: %s", chat_id, day, e)
        else:
            log.info("Skipped recap for chat %s, %s: not enough votes", chat_id, day)
        db.mark_recapped(chat_id, day)


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    groups = filters.ChatType.GROUPS
    app.add_handler(CommandHandler("freaky", freaky, filters=groups))
    app.add_handler(CommandHandler("standings", standings, filters=groups))
    app.add_handler(CommandHandler("alltime", alltime, filters=groups))
    app.add_handler(CommandHandler(["help", "start"], help_cmd, filters=groups))
    app.add_handler(CommandHandler(["freaky", "standings", "alltime", "help", "start"], private_chat, filters=filters.ChatType.PRIVATE))

    # A few seconds past midnight so "today" has definitely rolled over.
    app.job_queue.run_daily(post_pending_recaps, time=dt.time(0, 0, 5, tzinfo=TZ))
    app.job_queue.run_once(post_pending_recaps, when=5)

    log.info("Starting bot (timezone %s, db %s)", TZ.key, DB_PATH)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
