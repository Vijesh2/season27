import hmac
import secrets
from datetime import datetime

import uvicorn
from fasthtml.common import (
    H1,
    H2,
    A,
    Body,
    Button,
    Div,
    FastHTML,
    Footer,
    Form,
    Head,
    Header,
    Html,
    Img,
    Input,
    Label,
    Li,
    Link,
    Main,
    Meta,
    P,
    Small,
    Span,
    Title,
    Ul,
    to_xml,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from app.auth.service import (
    GENERIC_LOGIN_ERROR,
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    authenticate,
    logout,
    resolve_session,
    seed_development_players,
)
from app.clock import Clock, clock_from_iso
from app.config import Settings
from app.db.models import AppSession
from app.db.session import create_database_engine, create_schema, session_factory
from app.seasons import calculate_phase, get_current_season, london, seed_development_season
from app.teams.service import OFFICIAL_2026_27_TEAMS, approve_roster, get_roster, import_roster


def format_time(value: datetime) -> str:
    return london(value).strftime("%d %B %Y, %H:%M %Z")


def page(*content: object, title: str = "Season 27", status_code: int = 200) -> HTMLResponse:
    document = Html(
        Head(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Title(title),
            Link(rel="stylesheet", href="/static/app.css"),
            Link(
                rel="icon",
                type="image/png",
                sizes="32x32",
                href="/static/brand/favicon-32.png",
            ),
            Link(rel="apple-touch-icon", href="/static/brand/apple-touch-icon.png"),
        ),
        Body(*content),
    )
    return HTMLResponse(to_xml(document), status_code=status_code)


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastHTML:
    settings = settings or Settings()
    clock = clock or clock_from_iso(settings.dev_now)
    engine = create_database_engine(settings.database_url)
    sessions = session_factory(engine)
    create_schema(engine)
    with sessions() as session:
        season = seed_development_season(session)
        seed_development_players(session, clock())
        if not get_roster(session, season.id):
            import_roster(session, season, OFFICIAL_2026_27_TEAMS, clock())

    app = FastHTML()
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    def current_session(request: Request) -> AppSession | None:
        with sessions() as session:
            return resolve_session(session, request.cookies.get(SESSION_COOKIE), clock())

    def redirect_to_login() -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    @app.get("/login")
    def login_page(request: Request) -> Response:
        if current_session(request):
            return RedirectResponse("/", status_code=303)
        csrf_token = secrets.token_urlsafe(24)
        response = page(
            Main(
                Div(
                    Img(src="/static/brand/season27-logo.png", alt="", cls="brand-logo"),
                    Span("Season27", cls="brand-name"),
                    cls="brand-lockup",
                ),
                H1("Sign in to Season27"),
                P("Enter your four-character player code."),
                Form(
                    Input(type="hidden", name="csrf_token", value=csrf_token),
                    Label("Player code", fr="code"),
                    Input(
                        id="code",
                        name="code",
                        minlength="4",
                        maxlength="4",
                        pattern="[A-Za-z0-9]{4}",
                        autocomplete="one-time-code",
                        required=True,
                        autofocus=True,
                    ),
                    Button("Sign in", type="submit"),
                    method="post",
                    action="/login",
                    cls="login-form",
                ),
                cls="container login-container",
            ),
            title="Sign in · Season27",
        )
        response.set_cookie(
            LOGIN_CSRF_COOKIE,
            csrf_token,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            max_age=900,
        )
        return response

    @app.post("/login")
    async def login_submit(request: Request) -> Response:
        form = await request.form()
        submitted_csrf = str(form.get("csrf_token", ""))
        cookie_csrf = request.cookies.get(LOGIN_CSRF_COOKIE, "")
        if not cookie_csrf or not hmac.compare_digest(submitted_csrf, cookie_csrf):
            return page(
                Main(
                    H1("Request expired"),
                    P("Your sign-in form is no longer valid."),
                    A("Back to sign in", href="/login"),
                    cls="container",
                ),
                title="Request expired · Season27",
                status_code=403,
            )
        code = str(form.get("code", ""))
        ip = request.client.host if request.client else "unknown"
        with sessions() as session:
            result = authenticate(session, code, ip, clock(), settings)
        if result.player is None or result.token is None:
            csrf_token = secrets.token_urlsafe(24)
            response = page(
                Main(
                    Div(
                        Img(src="/static/brand/season27-logo.png", alt="", cls="brand-logo"),
                        Span("Season27", cls="brand-name"),
                        cls="brand-lockup",
                    ),
                    H1("Sign in to Season27"),
                    P(GENERIC_LOGIN_ERROR, cls="error", role="alert"),
                    Form(
                        Input(type="hidden", name="csrf_token", value=csrf_token),
                        Label("Player code", fr="code"),
                        Input(id="code", name="code", maxlength="4", required=True, autofocus=True),
                        Button("Sign in", type="submit"),
                        method="post",
                        action="/login",
                        cls="login-form",
                    ),
                    cls="container login-container",
                ),
                title="Sign in · Season27",
            )
            response.set_cookie(
                LOGIN_CSRF_COOKIE,
                csrf_token,
                httponly=True,
                secure=settings.secure_cookies,
                samesite="lax",
                max_age=900,
            )
            return response
        redirect = RedirectResponse("/", status_code=303)
        redirect.set_cookie(
            SESSION_COOKIE,
            result.token,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            max_age=settings.session_days * 24 * 60 * 60,
        )
        redirect.delete_cookie(LOGIN_CSRF_COOKIE)
        return redirect

    @app.get("/")
    def dashboard(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return page(Main(H1("Season 27"), P("No season has been configured.")))
            now = clock()
            phase = calculate_phase(season, now)
            windows = tuple(
                Li(
                    Span(f"Swap {window.sequence_number}", cls="window-name"),
                    Small(f"{format_time(window.opens_at)} – {format_time(window.closes_at)}"),
                    cls=(
                        "window active" if phase.active_swap == window.sequence_number else "window"
                    ),
                )
                for window in season.swap_windows
            )
            admin_link = A("Admin", href="/admin") if app_session.player.is_admin else None
            roster = get_roster(session, season.id)
            roster_status = "Approved" if season.roster_approved_at else "Awaiting approval"
            return page(
                Main(
                    Header(
                        A(
                            Img(src="/static/brand/season27-logo.png", alt=""),
                            Span("Season27"),
                            href="/",
                            cls="header-brand",
                            aria_label="Season27 home",
                        ),
                        P(f"Signed in as {app_session.player.display_name}"),
                        admin_link,
                        Form(
                            Input(type="hidden", name="csrf_token", value=app_session.csrf_token),
                            Button("Log out", type="submit", cls="link-button"),
                            method="post",
                            action="/logout",
                        ),
                        cls="account-bar",
                    ),
                    P("Development time override active", cls="dev-banner")
                    if settings.dev_now
                    else None,
                    H1(f"Premier League {season.name}"),
                    Div(
                        P("Current phase", cls="label"),
                        P(phase.label, cls="phase"),
                        P(f"Server time: {format_time(now)}", cls="server-time"),
                        cls="status-card",
                    ),
                    Div(
                        H2("Prediction period"),
                        P(f"Opens {format_time(season.game_opens_at)}"),
                        P(f"Locks {format_time(season.prediction_locks_at)}"),
                        cls="section-card",
                    ),
                    Div(H2("Swap windows"), Ul(*windows), cls="section-card"),
                    Div(
                        H2("Season teams"),
                        P(roster_status, cls="roster-status"),
                        Ul(
                            *(
                                Li(
                                    Span(str(item.display_order), cls="team-number"),
                                    item.team.name,
                                    cls="team-row",
                                )
                                for item in roster
                            ),
                            cls="team-list",
                        ),
                        cls="section-card",
                    ),
                    Footer(A("Service health", href="/health")),
                    cls="container",
                )
            )

    @app.post("/logout")
    async def logout_submit(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf_token", "")), app_session.csrf_token):
            return HTMLResponse("Request rejected", status_code=403)
        with sessions() as session:
            stored = session.get(AppSession, app_session.id)
            if stored is not None:
                logout(session, stored, clock())
        response = redirect_to_login()
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/admin")
    def admin(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        if not app_session.player.is_admin:
            return page(
                Main(H1("Forbidden"), P("Administrator access is required."), cls="container"),
                title="Forbidden · Season 27",
                status_code=403,
            )
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return page(Main(H1("Administration"), P("No season configured.")))
            roster = get_roster(session, season.id)
            status = "Approved" if season.roster_approved_at else "Awaiting approval"
            locked = clock() >= london(season.game_opens_at)
            approval_form = None
            if not season.roster_approved_at and not locked:
                approval_form = Form(
                    Input(type="hidden", name="csrf_token", value=app_session.csrf_token),
                    P("Confirm that these are the 20 clubs for the 2026/27 season."),
                    Button("Approve roster", type="submit"),
                    method="post",
                    action="/admin/roster/approve",
                )
            return page(
                Main(
                    A("← Back to dashboard", href="/"),
                    H1("Season27 administration"),
                    Div(
                        H2("2026/27 team roster"),
                        P(f"Status: {status}", cls="roster-status"),
                        P(f"Source: {season.roster_source}"),
                        P("Validation: 20 unique clubs", cls="success"),
                        P("The roster is locked because the game has opened.", cls="error")
                        if locked and not season.roster_approved_at
                        else None,
                        Ul(*(Li(item.team.name) for item in roster), cls="admin-team-list"),
                        approval_form,
                        cls="section-card",
                    ),
                    cls="container",
                ),
                title="Administration · Season27",
            )

    @app.post("/admin/roster/approve")
    async def approve_roster_submit(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        if not app_session.player.is_admin:
            return HTMLResponse("Forbidden", status_code=403)
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf_token", "")), app_session.csrf_token):
            return HTMLResponse("Request rejected", status_code=403)
        with sessions() as session:
            season = get_current_season(session)
            if season is None or not approve_roster(
                session, season, app_session.player_id, clock()
            ):
                return HTMLResponse("Roster cannot be approved", status_code=409)
        return RedirectResponse("/admin", status_code=303)

    @app.get("/health")
    def health() -> JSONResponse:
        with sessions() as session:
            session.connection()
        return JSONResponse({"status": "ok"})

    app.state.engine = engine
    app.state.session_factory = sessions
    return app


app = create_app()


def run() -> None:
    uvicorn.run("app.main:app", host="127.0.0.1", port=5001, reload=True)
