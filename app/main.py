"""Quotes — a small FastAPI + htmx CRUD app over an existing Postgres schema."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    yield
    if db._pool is not None:
        db._pool.close()


app = FastAPI(title="Quotes", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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
    quote: str = Form(...),
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
    quote: str = Form(...),
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


@app.get("/health", response_class=PlainTextResponse)
def health():
    try:
        with db.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as exc:  # surfaced to the container healthcheck
        return PlainTextResponse(f"db unreachable: {exc}", status_code=503)
    return "ok"
