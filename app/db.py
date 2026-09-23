"""Database access for the quotes app.

Wraps the existing `quotes` / `tags` / `quote_tag` schema. Every helper returns
plain dicts so the templates stay free of driver types.
"""

import os
from contextlib import contextmanager

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def _secret(name: str, default: str = "") -> str:
    """Read a setting that may be supplied inline or via a mounted file.

    `<NAME>_FILE` takes precedence over `<NAME>`, matching the convention used
    by the postgres image and docker secrets, so the password never has to
    appear in a compose file or environment listing.
    """
    path = os.getenv(f"{name}_FILE")
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.getenv(name, default)


def conninfo() -> str:
    """Build a libpq conninfo string from the environment.

    DATABASE_URL wins when set; otherwise the discrete PG* vars are assembled
    with proper quoting so passwords containing punctuation survive intact.
    """
    url = _secret("DATABASE_URL")
    if url:
        return url
    return make_conninfo(
        host=os.getenv("PGHOST", "postgres"),
        port=os.getenv("PGPORT", "5432"),
        user=os.getenv("PGUSER", "postgres"),
        password=_secret("PGPASSWORD"),
        dbname=os.getenv("PGDATABASE", "postgres"),
        connect_timeout=10,
        application_name="quotes",
    )


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo(),
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX", "8")),
            kwargs={"row_factory": dict_row},
            open=False,
        )
    return _pool


@contextmanager
def cursor(commit: bool = False):
    with pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur
        if commit:
            conn.commit()


SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    quote_id    serial PRIMARY KEY,
    quote       text,
    author      text,
    source_link text
);

CREATE TABLE IF NOT EXISTS tags (
    tag_id   serial PRIMARY KEY,
    tag_name text
);

CREATE TABLE IF NOT EXISTS quote_tag (
    tag_id   integer NOT NULL REFERENCES tags(tag_id),
    quote_id integer NOT NULL REFERENCES quotes(quote_id),
    PRIMARY KEY (tag_id, quote_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS tags_name_lower_idx ON tags (lower(tag_name));
CREATE INDEX IF NOT EXISTS quote_tag_quote_id_idx ON quote_tag (quote_id);
"""


def init_schema() -> None:
    """Create anything missing. Safe against the already-populated database."""
    pool().open(wait=True, timeout=30)
    with cursor(commit=True) as cur:
        cur.execute(SCHEMA)


# --- reads -----------------------------------------------------------------

_SELECT = """
SELECT q.quote_id,
       q.quote,
       q.author,
       q.source_link,
       COALESCE(
           ARRAY_REMOVE(ARRAY_AGG(t.tag_name ORDER BY lower(t.tag_name)), NULL),
           '{}'
       ) AS tags
  FROM quotes q
  LEFT JOIN quote_tag qt ON qt.quote_id = q.quote_id
  LEFT JOIN tags t       ON t.tag_id = qt.tag_id
"""


def list_quotes(search: str = "", tag: str = "") -> list[dict]:
    """Quotes newest-first, optionally narrowed by free text and/or one tag."""
    where, params = [], []
    if search:
        where.append(
            "(q.quote ILIKE %s OR q.author ILIKE %s OR q.source_link ILIKE %s)"
        )
        params += [f"%{search}%"] * 3
    if tag:
        where.append(
            "q.quote_id IN (SELECT qt2.quote_id FROM quote_tag qt2 "
            "JOIN tags t2 ON t2.tag_id = qt2.tag_id WHERE lower(t2.tag_name) = lower(%s))"
        )
        params.append(tag)

    sql = _SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY q.quote_id ORDER BY q.quote_id DESC"

    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_quote(quote_id: int) -> dict | None:
    with cursor() as cur:
        cur.execute(_SELECT + " WHERE q.quote_id = %s GROUP BY q.quote_id", (quote_id,))
        return cur.fetchone()


def list_tags() -> list[dict]:
    """Every tag that is actually in use, with how many quotes carry it."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT t.tag_name, COUNT(qt.quote_id) AS n
              FROM tags t
              JOIN quote_tag qt ON qt.tag_id = t.tag_id
             GROUP BY t.tag_name
             ORDER BY lower(t.tag_name)
            """
        )
        return cur.fetchall()


def stats() -> dict:
    with cursor() as cur:
        cur.execute(
            """
            SELECT (SELECT COUNT(*) FROM quotes) AS quotes,
                   (SELECT COUNT(DISTINCT author) FROM quotes
                     WHERE author IS NOT NULL AND btrim(author) <> '') AS authors,
                   (SELECT COUNT(DISTINCT tag_id) FROM quote_tag) AS tags
            """
        )
        return cur.fetchone()


# --- writes ----------------------------------------------------------------


def parse_tags(raw: str) -> list[str]:
    """Split a comma/newline separated tag string, trimmed and de-duplicated.

    Comparison is case-insensitive but the first spelling seen is preserved.
    """
    seen, out = set(), []
    for chunk in raw.replace("\n", ",").split(","):
        name = " ".join(chunk.split())
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _tag_id(cur, name: str) -> int:
    cur.execute("SELECT tag_id FROM tags WHERE lower(tag_name) = lower(%s)", (name,))
    row = cur.fetchone()
    if row:
        return row["tag_id"]
    cur.execute("INSERT INTO tags (tag_name) VALUES (%s) RETURNING tag_id", (name,))
    return cur.fetchone()["tag_id"]


def _set_tags(cur, quote_id: int, tags: list[str]) -> None:
    cur.execute("DELETE FROM quote_tag WHERE quote_id = %s", (quote_id,))
    for name in tags:
        cur.execute(
            "INSERT INTO quote_tag (tag_id, quote_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (_tag_id(cur, name), quote_id),
        )


def _prune_tags(cur) -> None:
    """Drop tags no quote references any more, so the filter bar stays honest."""
    cur.execute(
        "DELETE FROM tags WHERE tag_id NOT IN (SELECT tag_id FROM quote_tag)"
    )


def _clean(value: str | None) -> str | None:
    """Optional fields collapse to NULL rather than empty string."""
    value = (value or "").strip()
    return value or None


def create_quote(quote: str, author: str, source_link: str, tags: list[str]) -> int:
    with cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quotes (quote, author, source_link) VALUES (%s, %s, %s) "
            "RETURNING quote_id",
            (quote.strip(), _clean(author), _clean(source_link)),
        )
        quote_id = cur.fetchone()["quote_id"]
        _set_tags(cur, quote_id, tags)
        return quote_id


def update_quote(
    quote_id: int, quote: str, author: str, source_link: str, tags: list[str]
) -> None:
    with cursor(commit=True) as cur:
        cur.execute(
            "UPDATE quotes SET quote = %s, author = %s, source_link = %s "
            "WHERE quote_id = %s",
            (quote.strip(), _clean(author), _clean(source_link), quote_id),
        )
        _set_tags(cur, quote_id, tags)
        _prune_tags(cur)


def delete_quote(quote_id: int) -> None:
    with cursor(commit=True) as cur:
        cur.execute("DELETE FROM quote_tag WHERE quote_id = %s", (quote_id,))
        cur.execute("DELETE FROM quotes WHERE quote_id = %s", (quote_id,))
        _prune_tags(cur)
