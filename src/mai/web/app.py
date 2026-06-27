import html
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from mai.repository.users import UserRepository
from mai.web.board_api import make_board_router
from mai.web.me_api import make_me_router
from mai.web.review_api import make_review_router

_PUBLIC = {"/login", "/logout"}


def _page(title: str, body: str) -> str:
    return f"<!doctype html><title>{title}</title><body>{body}</body>"


def _login_html(error: str = "") -> str:
    err = f"<div class='auth-error'>{html.escape(error)}</div>" if error else ""
    return _page("Mai — Login", f"""
        <link rel="stylesheet" href="/static/auth.css">
        <div class="auth-wrap">
          <div class="auth-logo">
            <img src="/static/mangos-logo.png" alt="MaNGOS">
          </div>
          <div class="auth-form">
            {err}
            <form method="post" action="/login">
              <div class="auth-field">
                <input id="username" name="username" placeholder="username"
                       autocomplete="username" autofocus>
              </div>
              <div class="auth-field">
                <input id="password" name="password" type="password"
                       placeholder="password" autocomplete="current-password">
              </div>
              <button class="auth-btn" type="submit">Sign in</button>
            </form>
          </div>
        </div>""")


def _set_password_html(error: str = "") -> str:
    err = f"<div class='auth-error'>{html.escape(error)}</div>" if error else ""
    return _page("Mai — Set password", f"""
        <link rel="stylesheet" href="/static/auth.css">
        <div class="auth-wrap">
          <div class="auth-logo">
            <img src="/static/mangos-logo.png" alt="MaNGOS">
          </div>
          <div class="auth-form">
            <h1>Set your password</h1>
            {err}
            <form method="post" action="/set-password">
              <div class="auth-field">
                <input id="new_password" name="new_password" type="password"
                       placeholder="new password" autocomplete="new-password" autofocus>
              </div>
              <button class="auth-btn" type="submit">Save</button>
            </form>
            <p class="auth-hint">Choose at least 8 characters. This replaces the
               one-time password you were given.</p>
          </div>
        </div>""")


def _port_html(username: str, is_maintainer: bool) -> str:
    role = "maintainer" if is_maintainer else "member"
    return _page("Mai — Port Debt", f"""
        <link rel="stylesheet" href="/static/board.css">
        <div class="cc">
          <header class="cc-head">
            <div class="cc-titlebar">
              <span class="cc-title">Port Debt</span>
              <nav class="cc-nav"><a href="/port" class="on">Board</a><a href="/me">Control Center</a></nav>
              <span id="cc-summary" class="cc-summary"></span>
              <span class="cc-me">{html.escape(username)} · {role}
                <a href="/logout" onclick="event.preventDefault();
                   fetch('/logout',{{method:'POST'}}).then(()=>location='/login')">log out</a>
              </span>
            </div>
            <div class="cc-filters">
              <input id="f-search" placeholder="search title / subsystem">
              <select id="f-subsystem"><option value="">all subsystems</option></select>
              <select id="f-source"><option value="">all sources</option></select>
              <select id="f-tier"><option value="">all tiers</option>
                <option>surgical</option><option>small</option>
                <option>moderate</option><option>bulk</option></select>
              <button id="f-mine" class="cc-toggle" data-on="0">My ports</button>
              <button id="f-clear" class="cc-clear" hidden>clear core ✕</button>
            </div>
          </header>
          <div id="cc-radar" class="cc-radar"></div>
          <section class="cc-sec">
            <h2 class="cc-h cc-h-ready">Ready to port<span id="ready-ct" class="cc-ct"></span>
              <span class="cc-sub">git-proven · shared infra only</span></h2>
            <div id="ready-list" class="cc-list"></div>
          </section>
          <section class="cc-sec">
            <h2 class="cc-h cc-h-review">Worth reviewing<span id="review-ct" class="cc-ct"></span>
              <span class="cc-sub">closest to applying first</span></h2>
            <div id="review-list" class="cc-list"></div>
          </section>
          <div class="cc-far">
            <button id="far-toggle" class="cc-fartoggle"></button>
            <div id="far-list" class="cc-list" hidden></div>
          </div>
        </div>
        <script src="/static/portboard.js"></script>""")


def _me_html(username: str, is_maintainer: bool) -> str:
    role = "maintainer" if is_maintainer else "member"
    return _page("Mai — Control Center", f"""
        <link rel="stylesheet" href="/static/board.css">
        <div class="cc">
          <header class="cc-head">
            <div class="cc-titlebar">
              <span class="cc-title">Control Center</span>
              <nav class="cc-nav"><a href="/port">Board</a><a href="/me" class="on">Control Center</a></nav>
              <span class="cc-me">{html.escape(username)} · {role}
                <a href="/logout" onclick="event.preventDefault();
                   fetch('/logout',{{method:'POST'}}).then(()=>location='/login')">log out</a>
              </span>
            </div>
          </header>
          <div id="me-cards" class="me-cards"></div>
          <div class="me-grid">
            <section class="cc-sec me-main">
              <h2 class="cc-h cc-h-ready">My todo<span id="todo-ct" class="cc-ct"></span>
                <span class="cc-sub">auto-resolves when the port lands — no closing</span></h2>
              <div id="todo-list" class="cc-list"></div>
              <h2 class="cc-h me-shiph">Shipped<span id="ship-ct" class="cc-ct"></span>
                <span class="cc-sub">detected by the engine, not marked by hand</span></h2>
              <div id="ship-list" class="cc-list"></div>
            </section>
            <aside class="me-side">
              <div class="me-panel">
                <div class="me-panel-h">This week</div>
                <div id="me-spark" class="me-spark"></div>
              </div>
              <div class="me-panel">
                <div class="me-panel-h">Recent activity</div>
                <div id="me-activity" class="me-activity"></div>
              </div>
              <div class="me-panel">
                <div class="me-panel-h">Team</div>
                <div id="me-team" class="me-team"></div>
              </div>
              <div class="me-panel">
                <div class="me-panel-h">Project — confident ports</div>
                <div id="me-project" class="me-project"></div>
              </div>
            </aside>
          </div>
        </div>
        <script src="/static/me.js"></script>""")


def create_app(session_factory, hasher, session_secret: str, *,
               cookie_secure: bool = True, review_git_client=None,
               review_judge=None) -> FastAPI:
    dummy_hash = hasher.hash(secrets.token_urlsafe(16))
    app = FastAPI()

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC or path.startswith("/static"):
            return await call_next(request)
        if not request.session.get("username"):
            return RedirectResponse("/login", status_code=303)
        if request.session.get("must_change") and path != "/set-password":
            return RedirectResponse("/set-password", status_code=303)
        return await call_next(request)

    # SessionMiddleware added last so it is outermost and runs before gate.
    app.add_middleware(SessionMiddleware, secret_key=session_secret,
                       https_only=cookie_secure, same_site="lax")

    @app.get("/login", response_class=HTMLResponse)
    async def login_form() -> str:
        return _login_html()

    @app.post("/login")
    async def login(request: Request, username: str = Form(...),
                    password: str = Form(...)):
        async with session_factory() as session:
            user = await UserRepository(session).get(username)
            target = user.password_hash if user is not None else dummy_hash
            password_ok = hasher.verify(password, target)
            if user is None or not password_ok:
                return HTMLResponse(
                    _login_html("Invalid username or password"), status_code=401)
            user.last_login = datetime.now(timezone.utc)
            await session.commit()
            must_change = user.must_change_password
        request.session["username"] = username
        request.session["must_change"] = must_change
        return RedirectResponse("/set-password" if must_change else "/",
                                status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/set-password", response_class=HTMLResponse)
    async def set_password_form(request: Request) -> str:
        return _set_password_html()

    @app.post("/set-password")
    async def set_password(request: Request, new_password: str = Form(...)):
        if len(new_password) < 8:
            return HTMLResponse(
                _set_password_html("Password must be at least 8 characters"),
                status_code=400)
        username = request.session["username"]
        async with session_factory() as db:
            repo = UserRepository(db)
            user = await repo.get(username)
            await repo.set_password(user, hasher.hash(new_password))
            await db.commit()
        request.session["must_change"] = False
        return RedirectResponse("/", status_code=303)

    @app.get("/")
    async def home():
        return RedirectResponse("/port", status_code=303)

    @app.get("/port", response_class=HTMLResponse)
    async def port(request: Request):
        username = request.session["username"]
        async with session_factory() as session:
            user = await UserRepository(session).get(username)
        return _port_html(username, bool(user and user.is_maintainer))

    @app.get("/me", response_class=HTMLResponse)
    async def me_page(request: Request):
        username = request.session["username"]
        async with session_factory() as session:
            user = await UserRepository(session).get(username)
        return _me_html(username, bool(user and user.is_maintainer))

    app.include_router(make_board_router(session_factory))
    app.include_router(make_me_router(session_factory))
    app.include_router(make_review_router(session_factory, review_git_client, review_judge))
    app.mount("/static",
              StaticFiles(directory=Path(__file__).parent / "static"),
              name="static")
    return app
