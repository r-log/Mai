from datetime import datetime, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from mai.repository.users import UserRepository

_PUBLIC = {"/login", "/logout"}


def _page(title: str, body: str) -> str:
    return f"<!doctype html><title>{title}</title><body>{body}</body>"


def _login_html(error: str = "") -> str:
    err = f"<p class='error'>{error}</p>" if error else ""
    return _page("Mai — Login", f"""
        <h1>Mai</h1>{err}
        <form method='post' action='/login'>
          <input name='username' placeholder='username' autofocus>
          <input name='password' type='password' placeholder='password'>
          <button type='submit'>Log in</button>
        </form>""")


def create_app(session_factory, hasher, session_secret: str, *,
               cookie_secure: bool = True) -> FastAPI:
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
            if user is None or not hasher.verify(password, user.password_hash):
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

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> str:
        return _page("Mai", f"<p>signed in as {request.session.get('username')}</p>"
                            "<form method='post' action='/logout'>"
                            "<button>Log out</button></form>")

    return app
