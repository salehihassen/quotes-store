"""Quotes — a small FastAPI + htmx CRUD app over an existing Postgres schema."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, meta

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Exposed as callables rather than values so the database version is resolved
# lazily -- Postgres may not be up yet when this module is imported. Both sides
# cache, so a template calling them costs nothing after the first time.
templates.env.globals["app_version"] = meta.app_version
templates.env.globals["db_version"] = db.server_version


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    yield
    if db._pool is not None:
        db._pool.close()


app = FastAPI(title="Quotes", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Read-only JSON API for dashboards (Home Assistant). The service is
# tailnet-only and unauthenticated either way, so allowing cross-origin GETs
# costs nothing and saves a confusing failure from browser-side fetches.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
    allow_credentials=False,
)


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx)


def board(request: Request, search: str, tag: str) -> HTMLResponse:
    """The results region: quote grid plus the tag bar and counts around it."""
    return render(
        request,
        "partials/board.html",
        quotes=db.list_quotes(search, tag),
        tags=db.list_tags(),
        stats=db.stats(),
        search=search,
        tag=tag,
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", tag: str = ""):
    return render(
        request,
        "index.html",
        quotes=db.list_quotes(q, tag),
        tags=db.list_tags(),
        stats=db.stats(),
        search=q,
        tag=tag,
    )


@app.get("/quotes", response_class=HTMLResponse)
def search_quotes(request: Request, q: str = "", tag: str = ""):
    return board(request, q, tag)


@app.get("/quotes/new", response_class=HTMLResponse)
def new_form(request: Request):
    return render(request, "partials/form.html", quote=None)


@app.post("/quotes", response_class=HTMLResponse)
def create(
    request: Request,
    # Empty default, not Form(...): newer Starlette reports an empty form
    # field as missing, which would turn a blank composer into a raw 422.
    # The friendly inline message below is the intended response.
    quote: str = Form(""),
    author: str = Form(""),
    source_link: str = Form(""),
    tags: str = Form(""),
    q: str = Form(""),
    tag: str = Form(""),
):
    if not quote.strip():
        # The new-quote form normally swaps the board; on error keep it in place.
        resp = render(
            request,
            "partials/form.html",
            quote=None,
            error="A quote needs some words in it.",
        )
        resp.headers["HX-Retarget"] = "#composer"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp
    db.create_quote(quote, author, source_link, db.parse_tags(tags))
    return board(request, q, tag)


@app.get("/quotes/{quote_id}/edit", response_class=HTMLResponse)
def edit_form(request: Request, quote_id: int):
    return render(request, "partials/form.html", quote=db.get_quote(quote_id))


@app.get("/quotes/{quote_id}", response_class=HTMLResponse)
def show(request: Request, quote_id: int):
    """Used by Cancel on an edit form to restore the untouched card."""
    return render(request, "partials/card.html", q=db.get_quote(quote_id))


@app.post("/quotes/{quote_id}", response_class=HTMLResponse)
def update(
    request: Request,
    quote_id: int,
    # Empty default, not Form(...): newer Starlette reports an empty form
    # field as missing, which would turn a blank composer into a raw 422.
    # The friendly inline message below is the intended response.
    quote: str = Form(""),
    author: str = Form(""),
    source_link: str = Form(""),
    tags: str = Form(""),
):
    if not quote.strip():
        return render(
            request,
            "partials/form.html",
            quote=db.get_quote(quote_id),
            error="A quote needs some words in it.",
        )
    db.update_quote(quote_id, quote, author, source_link, db.parse_tags(tags))
    return render(request, "partials/card.html", q=db.get_quote(quote_id))


@app.delete("/quotes/{quote_id}", response_class=HTMLResponse)
def destroy(request: Request, quote_id: int, q: str = "", tag: str = ""):
    db.delete_quote(quote_id)
    return board(request, q, tag)


def _tag_list(values: list[str]) -> tuple[str, ...]:
    """Accept both repeated params (?tag=a&tag=b) and commas (?tag=a,b)."""
    out: list[str] = []
    for value in values:
        out.extend(db.parse_tags(value))
    return tuple(out)


def _as_json(quote: dict) -> dict:
    return {
        "id": quote["quote_id"],
        "quote": quote["quote"],
        "author": quote["author"],
        "source_link": quote["source_link"],
        "tags": list(quote["tags"]),
        "length": len(quote["quote"]),
    }


@app.get("/api/quotes/random")
def api_random_quote(
    max_chars: int | None = Query(
        None,
        ge=1,
        description="Only consider quotes whose text is at most this many "
        "characters. Home Assistant caps a sensor state at 255.",
    ),
    tag: list[str] = Query(
        default_factory=list,
        description="Only quotes carrying at least one of these tags. "
        "Repeat the parameter or separate with commas.",
    ),
    exclude_tag: list[str] = Query(
        default_factory=list,
        description="Skip quotes carrying any of these tags. Wins over `tag`.",
    ),
):
    """A random quote matching the filters, or 404 when nothing qualifies."""
    quote = db.random_quote(
        max_chars=max_chars,
        tags=_tag_list(tag),
        exclude_tags=_tag_list(exclude_tag),
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="No quote matches those filters")
    return _as_json(quote)


@app.get("/api/tags")
def api_tags():
    """Every tag in use, with how many quotes carry it."""
    tags = db.list_tags()
    return {
        "count": len(tags),
        "tags": [{"name": t["tag_name"], "quotes": t["n"]} for t in tags],
    }


@app.get("/health", response_class=PlainTextResponse)
def health():
    try:
        with db.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as exc:  # surfaced to the container healthcheck
        return PlainTextResponse(f"db unreachable: {exc}", status_code=503)
    return "ok"
