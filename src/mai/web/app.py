import html
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from mai.repository.users import UserRepository

_PUBLIC = {"/login", "/logout"}


def _page(title: str, body: str) -> str:
    return f"<!doctype html><title>{title}</title><body>{body}</body>"


def _login_html(error: str = "") -> str:
    err = f"<p class='error'>{html.escape(error)}</p>" if error else ""
    return _page("Mai — Login", f"""
        <h1>Mai</h1>{err}
        <form method='post' action='/login'>
          <input name='username' placeholder='username' autofocus>
          <input name='password' type='password' placeholder='password'>
          <button type='submit'>Log in</button>
        </form>""")


def _set_password_html(error: str = "") -> str:
    err = f"<p class='error'>{html.escape(error)}</p>" if error else ""
    return _page("Mai — Set password", f"""
        <h1>Set your password</h1>{err}
        <form method='post' action='/set-password'>
          <input name='new_password' type='password' placeholder='new password' autofocus>
          <button type='submit'>Save</button>
        </form>""")


def _home_html(username: str) -> str:
    safe = html.escape(username)
    return _page("Mai", f"<p>signed in as {safe}</p>"
                        "<form method='post' action='/logout'>"
                        "<button>Log out</button></form>")


def create_app(session_factory, hasher, session_secret: str, *,
               cookie_secure: bool = True) -> FastAPI:
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

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> str:
        return _home_html(request.session.get("username") or "")

    return app
