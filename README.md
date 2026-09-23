# Quotes

A small web app for collecting quotes — add, browse, search, edit and delete.
FastAPI + htmx on top of the Postgres database you already run.

Author, source and tags are all optional; only the quote text is required.

## What it talks to

It uses the **existing** `quotes` / `tags` / `quote_tag` tables in the `ductape`
database on the `postgres18` container (PostgreSQL 18.6 + pgvector — see
`../pg18-vector`) — the 17 quotes already in there show up on first load. Nothing is dropped or recreated; startup only issues
`CREATE TABLE IF NOT EXISTS` plus two indexes, so it is equally happy against an
empty database.

| Table | Columns |
| --- | --- |
| `quotes` | `quote_id`, `quote`, `author`, `source_link` |
| `tags` | `tag_id`, `tag_name` |
| `quote_tag` | `tag_id`, `quote_id` |

Empty author/source/tag inputs are stored as `NULL` rather than `''`. Tags are
entered comma-separated, matched case-insensitively so `funny` and `Funny` are
the same tag, and tags left with no quotes are cleaned up automatically.

## Run it

```sh
cp .env.example .env    # then set PGPASSWORD
docker compose up -d --build
```

Open <http://localhost:8088>.

### Deployed on c3

On the `c3` host this runs from the `/opt/docker-compose.yaml` stack rather
than this compose file, so both are not started at once:

```sh
docker build -t quotes:0.1.1 ~/repos/quotes          # after code changes
docker compose -f /opt/docker-compose.yaml up -d quotes
```

There it is published on loopback only and served on the tailnet by Caddy at
<https://quote.d.salehh.xyz>. `PGPASSWORD` is injected from
`/opt/postgresql/quotes.env` (mode 0600) rather than written into
`/opt/docker-compose.yaml`, which is world-readable.

Container stdout is collected automatically by the Alloy agent in
`/opt/observability` — it discovers every container over the Docker socket, so
no logging configuration is required beyond the default `json-file` driver.
Logs land in Loki labelled `container="quotes"`, `compose_project="opt"`,
`compose_service="quotes"`, `host="c3"`.

The container joins the external docker network `opt_default` — the one the
database already sits on — and reaches it by the hostname `postgres18`. If your Postgres lives elsewhere, change `PGHOST` in `.env` and the
`name:` under `networks:` in `docker-compose.yml`.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PGHOST` | `postgres` | Database hostname (set to `postgres18`) |
| `PGPORT` | `5432` | Database port |
| `PGUSER` | `postgres` | Database user |
| `PGPASSWORD` | — | Database password |
| `PGPASSWORD_FILE` | — | Path to a file holding the password; wins over `PGPASSWORD` (docker secrets) |
| `PGDATABASE` | `postgres` | Database name |
| `DATABASE_URL` | — | Full connection URI; overrides all of the above |
| `DB_POOL_MAX` | `8` | Max pooled connections |
| `QUOTES_PORT` | `8088` | Host port the UI is published on |

`GET /health` returns `ok`, or `503` with the error when the database is
unreachable. The compose healthcheck uses it.

## Developing without Docker

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PGHOST=localhost PGUSER=saleh PGPASSWORD=... PGDATABASE=ductape \
  uvicorn app.main:app --reload
```

## Layout

```
app/
  main.py               routes (HTML fragments for htmx, no JSON API)
  db.py                 connection pool, queries, schema bootstrap
  templates/
    base.html           shell, theme toggle
    index.html          header, search bar, page frame
    partials/
      board.html        tag filter bar, counters, quote grid
      card.html         one quote
      form.html         new + edit form (same template both ways)
  static/
    style.css           light/dark theme
    htmx.min.js         vendored, so there is no CDN dependency
```

Swaps are server-rendered HTML fragments: searching, filtering, adding, editing
and deleting all replace part of the page rather than reloading it. There is no
build step and no client-side framework.
