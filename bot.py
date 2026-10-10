import datetime as dt
import html
import logging
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import LinkPreviewOptions, Message, MessageEntity, ReactionTypeEmoji, ReplyParameters, Update
from telegram.constants import ParseMode, ReactionEmoji
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, Defaults, MessageHandler, filters

from db import AuthorTally, Database, MessageTally

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Singapore"))
DB_PATH = os.environ.get("DB_PATH", "freaky.db")

# The daily recap is only posted if at least this many distinct messages
# from at least this many distinct people received votes.
MIN_MESSAGES = 2
MIN_AUTHORS = 2

# Reactions used in quiet-votes mode. Bots can only use Telegram's standard reaction
# emojis (see telegram.constants.ReactionEmoji); other spicy options: HEART_ON_FIRE,
# SMILING_FACE_WITH_HORNS, KISS_MARK.
VOTE_REACTION = ReactionEmoji.FIRE
ALREADY_VOTED_REACTION = ReactionEmoji.SHRUG
UNVOTE_REACTION = ReactionEmoji.BROKEN_HEART
# Put on every message that has at least one vote, in both modes. A bot can only leave
# one reaction per message, so this marks "has votes" rather than showing a count.
FREAKY_MESSAGE_REACTION = ReactionEmoji.FIRE

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


def message_link(chat_id: int, message_id: int) -> str | None:
    """Link that jumps to a message. Only supergroups (ids starting with -100) have these."""
    s = str(chat_id)
    if not s.startswith("-100"):
        return None
    return f"https://t.me/c/{s[4:]}/{message_id}"


def quote(chat_id: int, m: MessageTally) -> str:
    """The message text as a blockquote that links back to the original when possible."""
    text = html.escape(m.text)
    link = message_link(chat_id, m.message_id)
    if link:
        text = f'<a href="{link}">{text} ↗</a>'
    return f"<blockquote>{text}</blockquote>"


def plural(n: int) -> str:
    return f"{n} vote" if n == 1 else f"{n} votes"


def qualifies(messages: list[MessageTally], authors: list[AuthorTally]) -> bool:
    return len(messages) >= MIN_MESSAGES and len(authors) >= MIN_AUTHORS


def build_report(chat_id: int, messages: list[MessageTally], authors: list[AuthorTally], title: str) -> str:
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
        lines.append(f"{quote(chat_id, m)}— {html.escape(m.author_name)}")
    lines.append("")

    lines.append("🏆 <b>Leaderboard</b>")
    for i, a in enumerate(authors[:10], start=1):
        lines.append(f"{i}. {html.escape(a.author_name)} — {plural(a.votes)}")

    return "\n".join(lines)


def replied_target(msg: Message) -> Message | None:
    target = msg.reply_to_message
    # In forum topics, a non-reply message "replies" to the topic's creation message.
    if target is None or target.forum_topic_created:
        return None
    return target


async def acknowledge(msg: Message, reaction: ReactionEmoji, text: str) -> None:
    """React with an emoji in quiet-votes mode, otherwise reply with text."""
    if db.get_setting(msg.chat_id, "quiet_votes"):
        try:
            await msg.set_reaction(ReactionTypeEmoji(reaction))
            return
        except TelegramError as e:
            # e.g. the group only allows certain reactions; fall back to a reply.
            log.warning("Couldn't react in chat %s: %s", msg.chat_id, e)
    await msg.reply_text(text)


def is_command(msg: Message) -> bool:
    return any(e.type == MessageEntity.BOT_COMMAND and e.offset == 0 for e in msg.entities or ())


async def mark_freaky(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, marked: bool) -> None:
    """Add or clear the bot's reaction on a voted message. Never blocks the vote itself."""
    reaction = [ReactionTypeEmoji(FREAKY_MESSAGE_REACTION)] if marked else []
    try:
        await context.bot.set_message_reaction(chat_id, message_id, reaction)
    except TelegramError as e:
        # Reaction not allowed in this group, message deleted, etc.
        log.warning("Couldn't update reaction on message %s in chat %s: %s", message_id, chat_id, e)


async def freaky(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target = replied_target(msg)
    if target is None:
        await msg.reply_text("Reply to the message you think is freaky with /freaky 🥵")
        return

    author = author_of(target)
    if author is None:
        await msg.reply_text("Bots can't be freaky. Pick a human message.")
        return
    # Votes on commands (e.g. someone else's /freaky) would just be noise, and in quiet
    # mode the bot's reaction on them already means "vote counted".
    if is_command(target):
        await msg.reply_text("That's a command, not a freaky message. Reply to the original message instead.")
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
    # Also on duplicates, so a reaction someone removed gets restored.
    await mark_freaky(context, msg.chat_id, target.message_id, True)
    if added:
        await acknowledge(msg, VOTE_REACTION, f"🔥 Vote counted. This message is at {plural(count)} today.")
    else:
        await acknowledge(msg, ALREADY_VOTED_REACTION, f"You already voted for this one ({plural(count)} today).")


async def unfreaky(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target = replied_target(msg)
    if target is None:
        await msg.reply_text("Reply to the message you voted for with /unfreaky to take your vote back.")
        return

    voter_id = update.effective_user.id
    day = db.vote_day(msg.chat_id, target.message_id, voter_id)
    if day is None:
        await acknowledge(msg, ALREADY_VOTED_REACTION, "You haven't voted for that message.")
        return
    if day != today():
        # Past days are already recapped and feed into all-time stats.
        await msg.reply_text("Too late, that vote's locked in. Only today's votes can be taken back.")
        return

    db.remove_vote(msg.chat_id, target.message_id, voter_id)
    count = db.message_votes(msg.chat_id, target.message_id, day)
    if db.message_votes(msg.chat_id, target.message_id) == 0:  # no votes left on any day
        await mark_freaky(context, msg.chat_id, target.message_id, False)
    await acknowledge(msg, UNVOTE_REACTION, f"💔 Vote removed. This message is at {plural(count)} today.")


def setting_command(name: str, value: bool, confirmation: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        db.set_setting(update.effective_chat.id, name, value)
        await update.effective_message.reply_text(confirmation)
    return handler


async def standings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    day = today()
    messages = db.message_tallies(chat_id, day)
    if not messages:
        await update.effective_message.reply_text("No freaky votes yet today. Reply to a message with /freaky.")
        return
    authors = db.author_tallies(chat_id, day)
    text = build_report(chat_id, messages, authors, "Freakiness so far today")
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

    lines = ["🥵 <b>All-Time Freakiness</b>", "🪞Mirror mirror on the wall, who's the FREAKIEST of them all?🥵",""]

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
            f"{quote(chat_id, m)}"
            f"— {html.escape(m.author_name)}, {format_day(first_day)} ({plural(m.votes)})"
        )
    lines.append("")

    top_voter = voters[0]
    lines.append(f"🙈 Biggest enabler: {html.escape(top_voter.author_name)} ({top_voter.votes} votes cast)")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    reports = "🔕 muted" if db.get_setting(chat_id, "reports_muted") else "🔔 on"
    votes = f"{VOTE_REACTION.value} react only" if db.get_setting(chat_id, "quiet_votes") else "💬 reply"
    await update.effective_message.reply_text(
        "🥵 <b>Freakiness Rater</b>\n\n"
        "/freaky — reply to a message with this to vote it as freaky "
        "(one vote per person per message)\n"
        "/unfreaky — reply to a message to take back today's vote on it\n"
        "/standings — see today's freakiness so far\n"
        "/alltime — all-time leaderboard, crowns and freakiest messages ever\n\n"
        "/mutereports · /unmutereports — turn the midnight report off or on\n"
        "/quietvotes · /loudvotes — confirm votes with just a reaction, or with a reply\n\n"
        f"At midnight I'll post who was freakiest and the freakiest message, as long as "
        f"at least {MIN_MESSAGES} messages from {MIN_AUTHORS} different people got votes.\n\n"
        f"<b>Settings here:</b> daily report {reports} · vote confirmations {votes}",
        parse_mode=ParseMode.HTML,
    )


async def private_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Add me to a group chat and reply to messages there with /freaky.")


async def chat_migrated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A basic group was upgraded to a supergroup and got a new id; carry its stats over."""
    msg = update.effective_message
    db.migrate_chat(msg.chat_id, msg.migrate_to_chat_id)
    log.info("Chat %s migrated to %s", msg.chat_id, msg.migrate_to_chat_id)


async def post_pending_recaps(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post recaps for every finished day that hasn't had one yet (also catches up after downtime)."""
    for chat_id, day in db.pending_recaps(before_day=today()):
        messages = db.message_tallies(chat_id, day)
        authors = db.author_tallies(chat_id, day)
        if db.get_setting(chat_id, "reports_muted"):
            log.info("Skipped recap for chat %s, %s: reports muted", chat_id, day)
        elif qualifies(messages, authors):
            text = build_report(chat_id, messages, authors, f"Daily Freakiness Report — {format_day(day)}")
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
    # Link previews off so the message links in reports don't expand into big cards.
    defaults = Defaults(link_preview_options=LinkPreviewOptions(is_disabled=True))
    app = Application.builder().token(BOT_TOKEN).defaults(defaults).build()

    groups = filters.ChatType.GROUPS
    app.add_handler(CommandHandler("freaky", freaky, filters=groups))
    app.add_handler(CommandHandler("unfreaky", unfreaky, filters=groups))
    app.add_handler(CommandHandler("standings", standings, filters=groups))
    app.add_handler(CommandHandler("mutereports", setting_command(
        "reports_muted", True, "🔕 Daily reports muted. /unmutereports to turn them back on."), filters=groups))
    app.add_handler(CommandHandler("unmutereports", setting_command(
        "reports_muted", False, "🔔 Daily reports are back on."), filters=groups))
    app.add_handler(CommandHandler("quietvotes", setting_command(
        "quiet_votes", True, f"🤫 Quiet votes on: I'll just react {VOTE_REACTION.value} to /freaky. /loudvotes to undo."),
        filters=groups))
    app.add_handler(CommandHandler("loudvotes", setting_command(
        "quiet_votes", False, "📢 Loud votes on: I'll reply to every /freaky again."), filters=groups))
    app.add_handler(CommandHandler("alltime", alltime, filters=groups))
    app.add_handler(CommandHandler(["help", "start"], help_cmd, filters=groups))
    app.add_handler(CommandHandler(["freaky", "unfreaky", "standings", "alltime", "mutereports", "unmutereports", "quietvotes", "loudvotes", "help", "start"], private_chat, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.StatusUpdate.MIGRATE & filters.ChatType.GROUP, chat_migrated))

    # A few seconds past midnight so "today" has definitely rolled over.
    app.job_queue.run_daily(post_pending_recaps, time=dt.time(0, 0, 5, tzinfo=TZ))
    app.job_queue.run_once(post_pending_recaps, when=5)

    log.info("Starting bot (timezone %s, db %s)", TZ.key, DB_PATH)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
