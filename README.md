# Quotes

A small web app for collecting quotes — add, browse, search, edit and delete.
FastAPI + htmx on top of the Postgres database you already run.

Author, source and tags are all optional; only the quote text is required.

## What it talks to

It uses the **existing** `quotes` / `tags` / `quote_tag` tables in the `ductape`
database on the `postgres` container (PostgreSQL 18.6 + pgvector — see
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
git tag v0.4.2 && git push origin v0.4.2  # publishes ghcr.io/salehihassen/quotes-store:0.4.2
docker compose -f /opt/docker-compose.yaml pull quotes
docker compose -f /opt/docker-compose.yaml up -d --no-deps quotes
```

For a later release, use the next version tag and update the `quotes` image
tag in `/opt/docker-compose.yaml` before pulling. GitHub Actions publishes
each `vX.Y.Z` tag to GHCR without the leading `v` in the image tag.

There it is published on loopback only and served on the tailnet by Caddy at
<https://quote.d.salehh.xyz>; <https://quotes.d.salehh.xyz> is an alias that
302-redirects to it, preserving path and query. `PGPASSWORD` is injected from
`/opt/postgresql/quotes.env` (mode 0600) rather than written into
`/opt/docker-compose.yaml`, which is world-readable.

Container stdout is collected automatically by the Alloy agent in
`/opt/observability` — it discovers every container over the Docker socket, so
no logging configuration is required beyond the default `json-file` driver.
Logs land in Loki labelled `container="quotes"`, `compose_project="opt"`,
`compose_service="quotes"`, `host="c3"`.

In the `/opt` stack the container joins one network only, `postgres_network`,
which `postgres` also sits on; it reaches the database by the hostname
`postgres` and has no route to the rest of the stack. The standalone
`docker-compose.yml` in this repo attaches to the external network instead. If
your Postgres lives elsewhere, change `PGHOST` in `.env` and the `name:` under
`networks:`.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PGHOST` | `postgres` | Database hostname |
| `PGPORT` | `5432` | Database port |
| `PGUSER` | `postgres` | Database user |
| `PGPASSWORD` | — | Database password |
| `PGPASSWORD_FILE` | — | Path to a file holding the password; wins over `PGPASSWORD` (docker secrets) |
| `PGDATABASE` | `postgres` | Database name |
| `DATABASE_URL` | — | Full connection URI; overrides all of the above |
| `DB_POOL_MAX` | `8` | Max pooled connections |
| `APP_VERSION` | — | Version shown in the footer; baked in at build time from `git describe` |
| `QUOTES_PORT` | `8088` | Host port the UI is published on |

### Version footer

The footer names both versions, and neither is written down in the source:

- **App** — `git describe --tags --always --dirty`, captured into `APP_VERSION`
  at build time because the image ships no `.git`. Tag a release
  (`git tag v0.4.0`) to get a clean version instead of a commit hash; with no
  tags it falls back to the short SHA, and to `dev` if the arg is not passed.
  Running straight from the working tree, `app/meta.py` asks git directly.
- **Database** — `current_setting('server_version')`, asked of the server
  itself.

Both are resolved once and cached, and the database lookup is primed during
startup, so no page render waits on either. If the database is unreachable the
number is omitted rather than blank, and the lookup is retried at most once a
minute so a missing database cannot turn every render into a connection
attempt.

```sh
docker build --build-arg APP_VERSION="$(git describe --tags --always --dirty)" \
  -t quotes:0.4.0 .
```

`GET /health` returns `ok`, or `503` with the error when the database is
unreachable. The compose healthcheck uses it.

## JSON API

Read-only endpoints intended for dashboards. Interactive docs are at `/docs`.

### `GET /api/quotes/random`

One random quote as JSON, or `404` with `{"detail": "..."}` when nothing
matches the filters.

| Parameter | Meaning |
| --- | --- |
| `max_chars` | Only consider quotes whose text is at most this long. Home Assistant caps a sensor state at 255 characters. |
| `tag` | Keep only quotes carrying at least one of these tags. |
| `exclude_tag` | Drop quotes carrying any of these tags. Takes precedence over `tag`. |

`tag` and `exclude_tag` may be repeated (`?tag=a&tag=b`) or comma-separated
(`?tag=a,b`); matching is case-insensitive. Multiple values are OR-ed, so
`?tag=funny,startups` means "funny **or** startups".

```console
$ curl -s 'https://quote.d.salehh.xyz/api/quotes/random?max_chars=120&exclude_tag=Nsfw'
{
  "id": 10,
  "quote": "Everyone has a plan until they get punched in the mouth",
  "author": "Mike Tyson",
  "source_link": null,
  "tags": [],
  "length": 55
}
```

`author`, `source_link` and `tags` reflect the optional fields: expect `null`
and `[]` rather than empty strings.

### `GET /api/tags`

Every tag in use, with how many quotes carry it.

```console
$ curl -s https://quote.d.salehh.xyz/api/tags
{"count":3,"tags":[{"name":"Funny","quotes":1},{"name":"Nsfw","quotes":1},{"name":"Startups","quotes":2}]}
```

Cross-origin `GET` is allowed, so browser-side dashboard cards can fetch these
directly. The service is unauthenticated and reachable only over the tailnet.

### Home Assistant

A sensor state is capped at 255 characters, so pass `max_chars` and keep the
attributes for the rest. `scan_interval` decides how often the quote rotates.

```yaml
# configuration.yaml
rest:
  - resource: https://quote.d.salehh.xyz/api/quotes/random
    params:
      max_chars: 200
      exclude_tag: Nsfw
    scan_interval: 900
    sensor:
      - name: Random Quote
        value_template: "{{ value_json.quote }}"
        json_attributes:
          - author
          - tags
          - source_link
```

Then on a dashboard:

```yaml
type: markdown
content: >-
  *"{{ states('sensor.random_quote') }}"*

  — {{ state_attr('sensor.random_quote', 'author') or 'Unknown' }}
```

Point Home Assistant at `quote.d.salehh.xyz` rather than the `quotes.d` alias,
to avoid a redirect hop on every poll.

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
  main.py               routes: HTML fragments for htmx, plus the JSON API
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
