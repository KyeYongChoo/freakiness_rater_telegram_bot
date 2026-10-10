import sqlite3
from dataclasses import dataclass


@dataclass
class MessageTally:
    message_id: int
    author_id: int
    author_name: str
    text: str
    votes: int


@dataclass
class AuthorTally:
    author_id: int
    author_name: str
    votes: int


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS votes (
                chat_id     INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                voter_id    INTEGER NOT NULL,
                voter_name  TEXT    NOT NULL,
                author_id   INTEGER NOT NULL,
                author_name TEXT    NOT NULL,
                text        TEXT    NOT NULL,
                day         TEXT    NOT NULL,  -- YYYY-MM-DD in the bot's timezone
                voted_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE (chat_id, message_id, voter_id)
            );
            CREATE INDEX IF NOT EXISTS votes_chat_day ON votes (chat_id, day);

            CREATE TABLE IF NOT EXISTS recaps (
                chat_id INTEGER NOT NULL,
                day     TEXT    NOT NULL,
                PRIMARY KEY (chat_id, day)
            );

            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id       INTEGER PRIMARY KEY,
                reports_muted INTEGER NOT NULL DEFAULT 0,
                quiet_votes   INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self.conn.commit()

    def add_vote(self, chat_id, message_id, voter_id, voter_name, author_id, author_name, text, day) -> bool:
        """Record a vote. Returns False if this voter already voted for this message."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO votes "
            "(chat_id, message_id, voter_id, voter_name, author_id, author_name, text, day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, message_id, voter_id, voter_name, author_id, author_name, text, day),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def vote_day(self, chat_id, message_id, voter_id) -> str | None:
        """The day this voter voted for this message, or None if they haven't."""
        row = self.conn.execute(
            "SELECT day FROM votes WHERE chat_id = ? AND message_id = ? AND voter_id = ?",
            (chat_id, message_id, voter_id),
        ).fetchone()
        return row[0] if row else None

    def remove_vote(self, chat_id, message_id, voter_id):
        self.conn.execute(
            "DELETE FROM votes WHERE chat_id = ? AND message_id = ? AND voter_id = ?",
            (chat_id, message_id, voter_id),
        )
        self.conn.commit()

    def get_setting(self, chat_id, name: str) -> bool:
        row = self.conn.execute(f"SELECT {name} FROM chat_settings WHERE chat_id = ?", (chat_id,)).fetchone()
        return bool(row[0]) if row else False

    def set_setting(self, chat_id, name: str, value: bool):
        # `name` is always one of our own column names, never user input.
        self.conn.execute(
            f"INSERT INTO chat_settings (chat_id, {name}) VALUES (?, ?) "
            f"ON CONFLICT (chat_id) DO UPDATE SET {name} = excluded.{name}",
            (chat_id, int(value)),
        )
        self.conn.commit()

    def message_votes(self, chat_id, message_id, day=None) -> int:
        """Votes on a message on `day`, or across all days if `day` is None."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM votes WHERE chat_id = ? AND message_id = ? AND (? IS NULL OR day = ?)",
            (chat_id, message_id, day, day),
        ).fetchone()
        return row[0]

    def message_tallies(self, chat_id, day) -> list[MessageTally]:
        rows = self.conn.execute(
            "SELECT message_id, author_id, MAX(author_name), MAX(text), COUNT(*) AS v "
            "FROM votes WHERE chat_id = ? AND day = ? "
            "GROUP BY message_id ORDER BY v DESC, MIN(voted_at)",
            (chat_id, day),
        ).fetchall()
        return [MessageTally(*r) for r in rows]

    def author_tallies(self, chat_id, day) -> list[AuthorTally]:
        rows = self.conn.execute(
            "SELECT author_id, MAX(author_name), COUNT(*) AS v "
            "FROM votes WHERE chat_id = ? AND day = ? "
            "GROUP BY author_id ORDER BY v DESC, MIN(voted_at)",
            (chat_id, day),
        ).fetchall()
        return [AuthorTally(*r) for r in rows]

    # All-time queries. In SQLite, a bare column next to MAX(rowid) comes from the row with
    # that max rowid, so names below are each person's most recent display name.

    def alltime_authors(self, chat_id) -> list[AuthorTally]:
        rows = self.conn.execute(
            "SELECT author_id, author_name, COUNT(*) AS v, MAX(rowid) "
            "FROM votes WHERE chat_id = ? GROUP BY author_id ORDER BY v DESC, MIN(voted_at)",
            (chat_id,),
        ).fetchall()
        return [AuthorTally(*r[:3]) for r in rows]

    def alltime_messages(self, chat_id, limit) -> list[tuple[MessageTally, str]]:
        """Most-voted messages ever, with the first day each got a vote."""
        rows = self.conn.execute(
            "SELECT message_id, author_id, author_name, text, COUNT(*) AS v, MIN(day), MAX(rowid) "
            "FROM votes WHERE chat_id = ? GROUP BY message_id ORDER BY v DESC, MIN(voted_at) LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [(MessageTally(*r[:5]), r[5]) for r in rows]

    def alltime_voters(self, chat_id) -> list[AuthorTally]:
        """Who has handed out the most votes (reuses AuthorTally: id, name, count)."""
        rows = self.conn.execute(
            "SELECT voter_id, voter_name, COUNT(*) AS v, MAX(rowid) "
            "FROM votes WHERE chat_id = ? GROUP BY voter_id ORDER BY v DESC, MIN(voted_at)",
            (chat_id,),
        ).fetchall()
        return [AuthorTally(*r[:3]) for r in rows]

    def voted_days(self, chat_id, before_day) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT day FROM votes WHERE chat_id = ? AND day < ? ORDER BY day",
            (chat_id, before_day),
        ).fetchall()
        return [r[0] for r in rows]

    def pending_recaps(self, before_day) -> list[tuple[int, str]]:
        """(chat_id, day) pairs with votes from before `before_day` that haven't been recapped."""
        return self.conn.execute(
            "SELECT DISTINCT v.chat_id, v.day FROM votes v "
            "LEFT JOIN recaps r ON r.chat_id = v.chat_id AND r.day = v.day "
            "WHERE v.day < ? AND r.chat_id IS NULL ORDER BY v.day",
            (before_day,),
        ).fetchall()

    def migrate_chat(self, old_chat_id, new_chat_id):
        self.conn.execute("UPDATE OR IGNORE votes SET chat_id = ? WHERE chat_id = ?", (new_chat_id, old_chat_id))
        self.conn.execute("UPDATE OR IGNORE recaps SET chat_id = ? WHERE chat_id = ?", (new_chat_id, old_chat_id))
        self.conn.execute("UPDATE OR IGNORE chat_settings SET chat_id = ? WHERE chat_id = ?", (new_chat_id, old_chat_id))
        # Rows that clashed with the new chat can't be reached any more, and leaving them would
        # make the recap job keep trying the dead chat id.
        self.conn.execute("DELETE FROM votes WHERE chat_id = ?", (old_chat_id,))
        self.conn.commit()

    def is_recapped(self, chat_id, day) -> bool:
        row = self.conn.execute("SELECT 1 FROM recaps WHERE chat_id = ? AND day = ?", (chat_id, day)).fetchone()
        return row is not None

    def mark_recapped(self, chat_id, day):
        self.conn.execute("INSERT OR IGNORE INTO recaps (chat_id, day) VALUES (?, ?)", (chat_id, day))
        self.conn.commit()
