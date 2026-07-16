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
    Script,
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
from app.db.models import AppSession, Prediction, Season, Swap
from app.db.session import create_database_engine, create_schema, session_factory
from app.predictions.service import (
    InvalidPrediction,
    editing_is_open,
    ensure_draft,
    get_draft,
    get_status,
    has_unsubmitted_changes,
    move_team,
    process_deadline,
    save_draft,
    submit_prediction,
)
from app.seasons import calculate_phase, get_current_season, london, seed_development_season
from app.swaps.service import (
    InvalidSwap,
    active_swap_window,
    apply_swap,
    get_swaps,
    validate_swap,
)
from app.teams.service import get_season_teams, seed_fixed_teams


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
            Script(src="/static/app.js", defer=True),
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
        seed_fixed_teams(session, season)

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
            process_deadline(session, season, now)
            phase = calculate_phase(season, now)
            player_swaps = get_swaps(session, app_session.player_id, season.id)
            used_window_ids = {swap.swap_window_id for swap in player_swaps}
            windows = tuple(
                Li(
                    Span(f"Swap {window.sequence_number}", cls="window-name"),
                    Small(f"{format_time(window.opens_at)} – {format_time(window.closes_at)}"),
                    Span(
                        "Used"
                        if window.id in used_window_ids
                        else "Open"
                        if phase.active_swap == window.sequence_number
                        else "Upcoming"
                        if now < london(window.opens_at)
                        else "Missed",
                        cls="window-state",
                    ),
                    cls="window active"
                    if phase.active_swap == window.sequence_number
                    else "window",
                )
                for window in season.swap_windows
            )
            admin_link = A("Admin", href="/admin") if app_session.player.is_admin else None
            season_teams = get_season_teams(session, season.id)
            prediction_status = get_status(session, app_session.player_id, season.id)
            if prediction_status and prediction_status.locked_at:
                submission_label = "Prediction locked"
            elif prediction_status and prediction_status.excluded_at:
                submission_label = "No submitted prediction"
            elif prediction_status and prediction_status.submitted_at:
                submission_label = "Prediction submitted"
            else:
                submission_label = "Prediction not submitted"
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
                        A("My prediction", href="/prediction"),
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
                        P(submission_label, cls="submission-status"),
                        A("Manage swaps", href="/swaps")
                        if prediction_status and prediction_status.locked_at
                        else None,
                        cls="section-card",
                    ),
                    Div(H2("Swap windows"), Ul(*windows), cls="section-card"),
                    Div(
                        H2("Season teams"),
                        Ul(
                            *(
                                Li(
                                    Span(str(item.display_order), cls="team-number"),
                                    item.team.name,
                                    cls="team-row",
                                )
                                for item in season_teams
                            ),
                            cls="team-list",
                        ),
                        cls="section-card",
                    ),
                    Footer(A("Service health", href="/health")),
                    cls="container",
                )
            )

    @app.get("/prediction")
    def prediction_page(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return page(Main(H1("My prediction"), P("No season configured.")))
            now = clock()
            process_deadline(session, season, now)
            status = get_status(session, app_session.player_id, season.id)
            if status and status.excluded_at:
                return page(
                    Main(
                        A("← Back to dashboard", href="/"),
                        H1("Prediction unavailable"),
                        P("No prediction was submitted before the deadline."),
                        cls="container",
                    ),
                    status_code=403,
                )
            draft = ensure_draft(session, app_session.player_id, season, now)
            editable = editing_is_open(season, now) and not (status and status.locked_at)
            saved = request.query_params.get("saved") == "1"
            changed_since_submission = has_unsubmitted_changes(draft, status)
            rows = tuple(
                Li(
                    Input(type="hidden", name="team_id", value=item.team_id),
                    Span("↕", cls="drag-handle", aria_hidden="true"),
                    Span(str(item.predicted_position), cls="prediction-position"),
                    Span(item.team.name, cls="prediction-team"),
                    Button(
                        "↑",
                        type="submit",
                        formaction=f"/prediction/move/{item.team_id}/up",
                        aria_label=f"Move {item.team.name} up",
                        disabled=not editable or item.predicted_position == 1,
                        cls="move-button move-up",
                    ),
                    Button(
                        "↓",
                        type="submit",
                        formaction=f"/prediction/move/{item.team_id}/down",
                        aria_label=f"Move {item.team.name} down",
                        disabled=not editable or item.predicted_position == 20,
                        cls="move-button move-down",
                    ),
                    cls="prediction-row",
                    data_team_id=str(item.team_id),
                )
                for item in draft
            )
            last_saved = max(item.updated_at for item in draft)
            availability = (
                P("Your draft is private. Reorder the teams, then save your changes.")
                if editable
                else P(
                    "Predictions are read-only until the entry period is open."
                    if now < london(season.game_opens_at)
                    else "The prediction deadline has passed; this draft is read-only.",
                    cls="notice",
                )
            )
            if status and status.locked_at:
                availability = P("This is your final locked prediction.", cls="notice")
            return page(
                Main(
                    A("← Back to dashboard", href="/"),
                    H1("My prediction"),
                    availability,
                    P("Draft saved", cls="success", role="status") if saved else None,
                    P(
                        "You have changes that have not been submitted.",
                        cls="notice",
                        role="status",
                    )
                    if changed_since_submission
                    else None,
                    P(
                        f"Submitted: {format_time(status.submitted_at)}",
                        cls="success",
                    )
                    if status and status.submitted_at and not changed_since_submission
                    else None,
                    P("Unsaved changes", cls="unsaved", hidden=True, role="status"),
                    P("", id="prediction-announcement", cls="visually-hidden", aria_live="polite"),
                    Form(
                        Input(type="hidden", name="csrf_token", value=app_session.csrf_token),
                        Ul(*rows, cls="prediction-list"),
                        Button(
                            "Save draft",
                            type="submit",
                            disabled=not editable,
                            cls="save-button",
                        ),
                        A("Review and submit", href="/prediction/review", cls="review-link")
                        if editable
                        else None,
                        method="post",
                        action="/prediction",
                        id="prediction-form",
                    ),
                    P(f"Last saved: {format_time(last_saved)}", cls="last-saved"),
                    P(f"Deadline: {format_time(season.prediction_locks_at)}"),
                    cls="container",
                ),
                title="My prediction · Season27",
            )

    def swap_page_content(
        app_session: AppSession,
        season: Season,
        now: datetime,
        draft: list[Prediction],
        swaps: list[Swap],
        error: str | None = None,
        preview: tuple[int, int, list[int]] | None = None,
        success: bool = False,
    ) -> HTMLResponse:
        active = active_swap_window(season, now)
        used_window_ids = {item.swap_window_id for item in swaps}
        team_by_id = {item.team_id: item.team for item in draft}
        windows = tuple(
            Li(
                Span(f"Swap {window.sequence_number}", cls="window-name"),
                Small(f"{format_time(window.opens_at)} – {format_time(window.closes_at)}"),
                Span(
                    "Used"
                    if window.id in used_window_ids
                    else "Open"
                    if active and active.id == window.id
                    else "Upcoming"
                    if now < london(window.opens_at)
                    else "Missed",
                    cls="window-state",
                ),
                cls="window active" if active and active.id == window.id else "window",
            )
            for window in season.swap_windows
        )
        selection = None
        confirmation = None
        if active and active.id not in used_window_ids and preview is None:
            selection = Form(
                Input(type="hidden", name="csrf_token", value=app_session.csrf_token),
                P("Select exactly two teams to exchange positions."),
                Ul(
                    *(
                        Li(
                            Label(
                                Input(type="checkbox", name="team_id", value=item.team_id),
                                Span(str(item.predicted_position), cls="team-number"),
                                item.team.name,
                            ),
                            cls="swap-team-row",
                        )
                        for item in draft
                    ),
                    cls="swap-team-list",
                ),
                Button("Preview swap", type="submit", cls="save-button"),
                method="post",
                action="/swaps/preview",
            )
        if preview is not None:
            first_id, second_id, preview_order = preview
            position_by_id = {item.team_id: item.predicted_position for item in draft}
            confirmation = Div(
                H2("Preview"),
                P(
                    f"{team_by_id[first_id].name} moves from position "
                    f"{position_by_id[first_id]} "
                    f"to {preview_order.index(first_id) + 1}."
                ),
                P(
                    f"{team_by_id[second_id].name} moves from position "
                    f"{position_by_id[second_id]} "
                    f"to {preview_order.index(second_id) + 1}."
                ),
                Form(
                    Input(type="hidden", name="csrf_token", value=app_session.csrf_token),
                    Input(type="hidden", name="first_team_id", value=first_id),
                    Input(type="hidden", name="second_team_id", value=second_id),
                    Label(
                        Input(type="checkbox", name="confirmed", value="yes", required=True),
                        " I confirm this swap. It cannot be changed in this window.",
                    ),
                    Button("Confirm swap", type="submit", cls="save-button"),
                    method="post",
                    action="/swaps/confirm",
                    cls="submit-form",
                ),
                A("Choose different teams", href="/swaps"),
                cls="section-card swap-preview",
            )
        history = tuple(
            Li(
                Span(f"Swap {item.swap_window.sequence_number}", cls="window-name"),
                Span(
                    f"{item.first_team.name} ({item.first_position} → {item.second_position}) "
                    f"and {item.second_team.name} ({item.second_position} → {item.first_position})"
                ),
                Small(format_time(item.created_at)),
                cls="swap-history-row",
            )
            for item in swaps
        )
        return page(
            Main(
                A("← Back to dashboard", href="/"),
                H1("My swaps"),
                P("Swap applied successfully.", cls="success", role="status") if success else None,
                P(error, cls="error", role="alert") if error else None,
                Div(H2("Swap windows"), Ul(*windows), cls="section-card"),
                confirmation,
                Div(H2("Make this window's swap"), selection, cls="section-card")
                if selection
                else None,
                Div(
                    H2("Current prediction"),
                    Ul(
                        *(
                            Li(
                                Span(str(item.predicted_position), cls="team-number"),
                                item.team.name,
                                cls="team-row",
                            )
                            for item in draft
                        ),
                        cls="team-list",
                    ),
                    cls="section-card",
                ),
                Div(
                    H2("Swap history"),
                    Ul(*history, cls="swap-history") if history else P("No swaps used yet."),
                    cls="section-card",
                ),
                cls="container",
            ),
            title="My swaps · Season27",
        )

    @app.get("/swaps")
    def swaps_page(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            now = clock()
            process_deadline(session, season, now)
            status = get_status(session, app_session.player_id, season.id)
            if status is None or status.locked_at is None or status.excluded_at is not None:
                return HTMLResponse("Swaps are unavailable for this player.", status_code=403)
            return swap_page_content(
                app_session,
                season,
                now,
                get_draft(session, app_session.player_id, season.id),
                get_swaps(session, app_session.player_id, season.id),
                success=request.query_params.get("applied") == "1",
            )

    def selected_team_ids(form: object) -> tuple[int, int]:
        try:
            values = form.getlist("team_id")  # type: ignore[attr-defined]
            if len(values) != 2:
                raise ValueError
            return int(values[0]), int(values[1])
        except (TypeError, ValueError, AttributeError) as error:
            raise InvalidSwap("Select exactly two teams.") from error

    @app.post("/swaps/preview")
    async def swaps_preview(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf_token", "")), app_session.csrf_token):
            return HTMLResponse("Request rejected", status_code=403)
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            now = clock()
            process_deadline(session, season, now)
            try:
                first_id, second_id = selected_team_ids(form)
                _, _, preview_order = validate_swap(
                    session, app_session.player_id, season, first_id, second_id, now
                )
            except InvalidSwap as error:
                return swap_page_content(
                    app_session,
                    season,
                    now,
                    get_draft(session, app_session.player_id, season.id),
                    get_swaps(session, app_session.player_id, season.id),
                    error=str(error),
                )
            return swap_page_content(
                app_session,
                season,
                now,
                get_draft(session, app_session.player_id, season.id),
                get_swaps(session, app_session.player_id, season.id),
                preview=(first_id, second_id, preview_order),
            )

    @app.post("/swaps/confirm")
    async def swaps_confirm(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf_token", "")), app_session.csrf_token):
            return HTMLResponse("Request rejected", status_code=403)
        if form.get("confirmed") != "yes":
            return HTMLResponse("Confirmation is required", status_code=422)
        try:
            first_id = int(str(form.get("first_team_id", "")))
            second_id = int(str(form.get("second_team_id", "")))
        except ValueError:
            return HTMLResponse("Invalid team selection", status_code=422)
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            now = clock()
            process_deadline(session, season, now)
            try:
                apply_swap(session, app_session.player_id, season, first_id, second_id, now)
            except InvalidSwap as error:
                return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/swaps?applied=1", status_code=303)

    @app.get("/prediction/review")
    def prediction_review(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            now = clock()
            if not editing_is_open(season, now):
                return HTMLResponse(
                    "Predictions cannot be submitted at this time.", status_code=422
                )
            draft = ensure_draft(session, app_session.player_id, season, now)
            return page(
                Main(
                    A("← Back to prediction", href="/prediction"),
                    H1("Review prediction"),
                    P("Check all 20 positions before submitting."),
                    Ul(
                        *(
                            Li(
                                Span(str(item.predicted_position), cls="team-number"),
                                item.team.name,
                                cls="team-row",
                            )
                            for item in draft
                        ),
                        cls="team-list",
                    ),
                    Form(
                        Input(type="hidden", name="csrf_token", value=app_session.csrf_token),
                        Label(
                            Input(type="checkbox", name="confirmed", value="yes", required=True),
                            " I confirm this is the prediction I want to submit.",
                        ),
                        Button("Submit prediction", type="submit", cls="save-button"),
                        method="post",
                        action="/prediction/submit",
                        cls="submit-form",
                    ),
                    P("You may revise and resubmit until the deadline."),
                    cls="container",
                ),
                title="Review prediction · Season27",
            )

    @app.post("/prediction/submit")
    async def prediction_submit(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf_token", "")), app_session.csrf_token):
            return HTMLResponse("Request rejected", status_code=403)
        if form.get("confirmed") != "yes":
            return HTMLResponse("Confirmation is required", status_code=422)
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            try:
                submit_prediction(session, app_session.player_id, season, clock())
            except InvalidPrediction as error:
                return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/prediction?submitted=1", status_code=303)

    @app.post("/prediction")
    async def prediction_save(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf_token", "")), app_session.csrf_token):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            raw_team_ids = form.getlist("team_id")
            team_ids = []
            for value in raw_team_ids:
                if not isinstance(value, str):
                    raise ValueError
                team_ids.append(int(value))
        except (TypeError, ValueError):
            return HTMLResponse("Invalid prediction", status_code=422)
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            try:
                save_draft(session, app_session.player_id, season, team_ids, clock())
            except InvalidPrediction as error:
                return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/prediction?saved=1", status_code=303)

    @app.post("/prediction/move/{team_id}/{direction}")
    async def prediction_move(request: Request, team_id: int, direction: str) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf_token", "")), app_session.csrf_token):
            return HTMLResponse("Request rejected", status_code=403)
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            try:
                move_team(
                    session,
                    app_session.player_id,
                    season,
                    team_id,
                    direction,
                    clock(),
                )
            except InvalidPrediction as error:
                return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/prediction?saved=1", status_code=303)

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
        return page(
            Main(
                A("← Back to dashboard", href="/"),
                H1("Season27 administration"),
                P("Admin tools arrive in a later checkpoint."),
                cls="container",
            ),
            title="Administration · Season27",
        )

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
