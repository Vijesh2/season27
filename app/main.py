import csv
import hmac
import io
import secrets
from collections.abc import Sequence
from datetime import datetime, timedelta

import uvicorn
from fasthtml.common import (
    H1,
    H2,
    A,
    Body,
    Button,
    Caption,
    Details,
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
    Option,
    P,
    Script,
    Select,
    Small,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Title,
    Tr,
    Ul,
    to_xml,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from app.admin.service import (
    InvalidAdminAction,
    correct_prediction,
    correct_standings,
    create_player,
    reinstate_player,
    reset_player_lock,
    reverse_swap,
    revoke_player_sessions,
    revoke_session,
    rotate_login_code,
    update_player,
    update_season_dates,
)
from app.auth.service import (
    GENERIC_LOGIN_ERROR,
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    authenticate,
    bootstrap_admin,
    logout,
    resolve_session,
    seed_development_players,
    throttle_key,
)
from app.bulletins import BULLETIN_TITLE
from app.bulletins.automation import ResultsSource, fact_pack_digest, prepare_bulletin
from app.bulletins.fact_pack import FactPackError, build_fact_pack
from app.bulletins.service import (
    InvalidBulletin,
    get_published_bulletins,
    publish_bulletin,
    save_and_publish_automated,
    suppress_bulletin,
    update_bulletin,
)
from app.bulletins.service import (
    save_draft as save_bulletin_draft,
)
from app.clock import Clock, MutableClock, clock_from_iso
from app.config import Settings
from app.db.models import (
    AppSession,
    AuditEvent,
    Bulletin,
    Player,
    Prediction,
    PredictionStatus,
    Season,
    SeasonTeam,
    Swap,
)
from app.db.session import create_database_engine, create_schema, session_factory
from app.leaderboard.service import build_leaderboard, find_entry
from app.media_predictions import MEDIA_PREDICTIONS
from app.predictions.service import (
    InvalidPrediction,
    editing_is_open,
    ensure_draft,
    get_draft,
    get_participant_predictions,
    get_status,
    has_unsubmitted_changes,
    move_team,
    process_deadline,
    save_draft,
    submit_prediction,
)
from app.results.source import BBCResultsSource, ResultsSourceError
from app.seasons import calculate_phase, get_current_season, london, seed_development_season
from app.standings.refresh import (
    RefreshOutcome,
    get_refresh_state,
    refresh_standings,
    snapshot_is_stale,
)
from app.standings.service import get_latest_snapshot, seed_development_snapshot
from app.standings.source import (
    BBCStandingsSource,
    DevelopmentStandingsSource,
    ExternalStanding,
    StandingsSource,
)
from app.swaps.service import (
    InvalidSwap,
    active_swap_window,
    apply_swap,
    get_shared_swaps,
    get_swaps,
    validate_swap,
)
from app.teams.service import get_season_teams, seed_fixed_teams


def format_time(value: datetime) -> str:
    return london(value).strftime("%d %B %Y, %H:%M %Z")


def bulletin_card(bulletin: Bulletin) -> Div:
    return Div(
        P(BULLETIN_TITLE, cls="bulletin-kicker"),
        H2(A(bulletin.title, href=f"/bulletins/{bulletin.slug}")),
        P(bulletin.body, cls="bulletin-body"),
        P(
            f"Published {format_time(bulletin.published_at)}"
            if bulletin.published_at
            else "Published",
            cls="bulletin-date",
        ),
        A("Previous editions", href="/bulletins", cls="bulletin-archive-link"),
        cls="bulletin-card",
    )


def parse_local_datetime(value: object) -> datetime:
    try:
        return london(datetime.fromisoformat(str(value)))
    except ValueError as error:
        raise InvalidBulletin("Enter a valid reporting date and time.") from error


def how_to_play() -> Details:
    return Details(
        Summary("How to play"),
        Div(
            Ul(
                Li(
                    Span("1", cls="team-number"),
                    Div(
                        P("Predict the table", cls="how-to-title"),
                        P(
                            "Put all 20 teams in the order you think they will finish, "
                            "then submit before the prediction lock at 00:00 BST on "
                            "21 August 2026. You may revise and resubmit your prediction "
                            "until that deadline."
                        ),
                    ),
                    cls="how-to-step",
                ),
                Li(
                    Span("2", cls="team-number"),
                    Div(
                        P("Use your swaps", cls="how-to-title"),
                        P(
                            "You may exchange the positions of two teams once in each window: "
                            "21 August–31 October 2026, 1 November–31 December 2026, "
                            "1 January–28 February 2027, and 1 March–30 April 2027."
                        ),
                        P(
                            "A confirmed swap cannot be undone. Each window is use it or lose it: "
                            "an unused swap does not carry over to the next window.",
                            cls="how-to-warning",
                        ),
                    ),
                    cls="how-to-step",
                ),
                Li(
                    Span("3", cls="team-number"),
                    Div(
                        P("Lowest score wins", cls="how-to-title"),
                        P(
                            "You score one point for every place each team finishes "
                            "away from your prediction."
                        ),
                    ),
                    cls="how-to-step",
                ),
                cls="how-to-list",
            ),
            A("Make or review your prediction", href="/prediction"),
            cls="how-to-content",
        ),
        cls="how-to-card",
    )


def _page(
    *content: object,
    title: str = "Season 27",
    status_code: int = 200,
    environment: str | None = None,
) -> HTMLResponse:
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
        Body(
            Div(
                "DEVELOPMENT — LOCAL ONLY"
                if environment == "development"
                else f"{environment.upper()} ENVIRONMENT — NOT LIVE",
                cls="environment-banner",
                role="status",
            )
            if environment in {"development", "staging", "test"}
            else None,
            how_to_play(),
            Div(
                A(
                    Div(
                        Span("Third-party predictions", cls="media-link-title"),
                        Span(
                            "Compare predictions from independent publishers and platforms",
                            cls="public-link-note",
                        ),
                    ),
                    Span("→", cls="media-link-arrow", aria_hidden="true"),
                    href="/third-party-predictions",
                    cls="media-navigation-link",
                    aria_label="View third-party predictions; no sign-in required",
                ),
                cls="public-navigation",
            ),
            *content,
        ),
        lang="en",
    )
    return HTMLResponse(to_xml(document), status_code=status_code)


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    standings_source: StandingsSource | None = None,
    results_source: ResultsSource | None = None,
) -> FastHTML:
    settings = settings or Settings()
    clock = clock or clock_from_iso(settings.dev_now)
    engine = create_database_engine(settings.database_url)
    sessions = session_factory(engine)
    create_schema(engine)
    with sessions() as session:
        season = seed_development_season(session)
        if settings.environment in {"development", "test", "staging"}:
            seed_development_players(session, clock())
        elif settings.bootstrap_admin_name and settings.bootstrap_admin_code:
            bootstrap_admin(
                session,
                settings.bootstrap_admin_name,
                settings.bootstrap_admin_code.get_secret_value(),
                clock(),
            )
        elif session.scalar(select(Player.id).limit(1)) is None:
            raise RuntimeError(
                "An empty production database requires bootstrap administrator secrets"
            )
        seed_fixed_teams(session, season)
        if settings.environment == "development" and standings_source is None:
            snapshot = seed_development_snapshot(session, season.id, clock())
            standings_source = DevelopmentStandingsSource(
                tuple(
                    ExternalStanding(
                        identity=row.team.source_identity,
                        name=row.team.name,
                        position=row.position,
                        played=row.played or 0,
                        points=row.points or 0,
                        goal_difference=row.goal_difference or 0,
                        goals_scored=row.goals_scored or 0,
                    )
                    for row in snapshot.rows
                )
            )
    if standings_source is None:
        standings_source = BBCStandingsSource(
            settings.standings_url,
            settings.standings_connect_timeout_seconds,
            settings.standings_read_timeout_seconds,
        )

    app = FastHTML()
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    if settings.environment == "test" and isinstance(clock, MutableClock):

        @app.post("/__test__/clock")
        async def set_test_clock(request: Request) -> JSONResponse:
            expected = settings.test_control_token
            supplied = request.headers.get("x-season27-test-token", "")
            if expected is None or not hmac.compare_digest(expected.get_secret_value(), supplied):
                return JSONResponse({"status": "forbidden"}, status_code=403)
            payload = await request.json()
            try:
                clock.set(datetime.fromisoformat(str(payload["now"])))
            except (KeyError, TypeError, ValueError):
                return JSONResponse({"status": "invalid"}, status_code=400)
            return JSONResponse({"status": "ok", "now": clock().isoformat()})

    def automation_authorized(request: Request) -> bool:
        secret = settings.bulletin_automation_token
        authorization = request.headers.get("authorization", "")
        if secret is None or not authorization.startswith("Bearer "):
            return False
        return hmac.compare_digest(
            secret.get_secret_value(), authorization.removeprefix("Bearer ")
        )

    def automation_actor(session: Session) -> Player | None:
        return session.scalar(
            select(Player).where(
                Player.display_name == settings.bulletin_automation_actor_name,
                Player.is_admin.is_(True),
                Player.is_active.is_(True),
            )
        )

    def configured_result_source() -> ResultsSource:
        return results_source or BBCResultsSource(
            settings.results_url,
            settings.results_connect_timeout_seconds,
            settings.results_read_timeout_seconds,
            settings.results_retry_attempts,
        )

    def parse_automation_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Automation timestamps must include a timezone.")
        return parsed

    @app.post("/internal/bulletins/prepare")
    async def automation_prepare(request: Request) -> JSONResponse:
        if not automation_authorized(request):
            return JSONResponse({"status": "forbidden"}, status_code=403)
        try:
            payload = await request.json()
            start = parse_automation_datetime(payload.get("period_start"))
            end = parse_automation_datetime(payload.get("period_end"))
            with sessions() as session:
                season = get_current_season(session)
                if season is None:
                    return JSONResponse({"status": "no_season"}, status_code=409)
                prepared = prepare_bulletin(
                    session,
                    season,
                    configured_result_source(),
                    standings_source,
                    settings,
                    clock(),
                    period_start=start,
                    period_end=end,
                )
            return JSONResponse(
                {
                    "status": "already_published"
                    if prepared.existing_status == "published"
                    else "prepared",
                    "fact_pack_digest": prepared.fact_pack_digest,
                    "fact_pack": prepared.fact_pack.to_dict(),
                    "results_import": {
                        "inserted": prepared.results_inserted,
                        "updated": prepared.results_updated,
                    },
                    "standings_outcome": prepared.standings_outcome,
                    "existing_status": prepared.existing_status,
                }
            )
        except (ValueError, FactPackError) as error:
            return JSONResponse({"status": "invalid", "detail": str(error)}, status_code=422)
        except (ResultsSourceError, RuntimeError):
            return JSONResponse({"status": "source_unavailable"}, status_code=503)

    @app.post("/internal/bulletins/publish")
    async def automation_publish(request: Request) -> JSONResponse:
        if not automation_authorized(request):
            return JSONResponse({"status": "forbidden"}, status_code=403)
        try:
            payload = await request.json()
            start = parse_automation_datetime(payload.get("period_start"))
            end = parse_automation_datetime(payload.get("period_end"))
            expected_digest = str(payload["fact_pack_digest"])
            body = str(payload["body"])
            with sessions() as session:
                season = get_current_season(session)
                actor = automation_actor(session)
                if season is None or actor is None:
                    return JSONResponse({"status": "not_configured"}, status_code=409)
                prepared = prepare_bulletin(
                    session,
                    season,
                    configured_result_source(),
                    standings_source,
                    settings,
                    clock(),
                    period_start=start,
                    period_end=end,
                )
                if not hmac.compare_digest(prepared.fact_pack_digest, expected_digest):
                    return JSONResponse({"status": "facts_changed"}, status_code=409)
                bulletin, changed = save_and_publish_automated(
                    session, prepared.fact_pack, body, actor.id, clock()
                )
            return JSONResponse(
                {
                    "status": "published" if changed else "already_published",
                    "slug": bulletin.slug,
                    "public_path": f"/bulletins/{bulletin.slug}",
                    "fact_pack_digest": fact_pack_digest(prepared.fact_pack),
                }
            )
        except KeyError:
            return JSONResponse({"status": "invalid"}, status_code=422)
        except (ValueError, FactPackError, InvalidBulletin) as error:
            return JSONResponse({"status": "invalid", "detail": str(error)}, status_code=422)
        except (ResultsSourceError, RuntimeError):
            return JSONResponse({"status": "source_unavailable"}, status_code=503)

    @app.get("/internal/bulletins/{slug}")
    def automation_verify(slug: str, request: Request) -> JSONResponse:
        if not automation_authorized(request):
            return JSONResponse({"status": "forbidden"}, status_code=403)
        with sessions() as session:
            bulletin = session.scalar(select(Bulletin).where(Bulletin.slug == slug))
            if bulletin is None:
                return JSONResponse({"status": "not_found"}, status_code=404)
            return JSONResponse(
                {
                    "status": bulletin.status,
                    "slug": bulletin.slug,
                    "title": bulletin.title,
                    "body": bulletin.body,
                    "published_at": bulletin.published_at.isoformat()
                    if bulletin.published_at
                    else None,
                }
            )

    def page(
        *content: object, title: str = "Season 27", status_code: int = 200
    ) -> HTMLResponse:
        return _page(
            *content,
            title=title,
            status_code=status_code,
            environment=settings.environment,
        )

    def current_session(request: Request) -> AppSession | None:
        with sessions() as session:
            return resolve_session(session, request.cookies.get(SESSION_COOKIE), clock())

    def redirect_to_login() -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    @app.get("/media-predictions")
    def legacy_media_predictions() -> Response:
        return RedirectResponse("/third-party-predictions", status_code=308)

    @app.get("/third-party-predictions")
    def third_party_predictions(request: Request) -> Response:
        destination = "/" if current_session(request) else "/login"
        return page(
            Main(
                A("← Back", href=destination),
                H1("Third-party predictions"),
                P(
                    "Published predictions from prominent football and media platforms. "
                    "These entries are shown for comparison and do not participate in the game."
                ),
                Div(
                    Div(
                        Table(
                            Caption(
                                "Predicted Premier League finishing positions by source",
                                cls="visually-hidden",
                            ),
                            Thead(
                                Tr(
                                    Th("Pos", scope="col", cls="comparison-position"),
                                    *(
                                        Th(
                                            A(
                                                prediction.publisher,
                                                href=prediction.source_url,
                                                target="_blank",
                                                rel="noopener noreferrer",
                                            ),
                                            Small(prediction.date_note),
                                            scope="col",
                                        )
                                        for prediction in MEDIA_PREDICTIONS
                                    ),
                                )
                            ),
                            Tbody(
                                *(
                                    Tr(
                                        Th(
                                            str(position),
                                            scope="row",
                                            cls="comparison-position",
                                        ),
                                        *(
                                            Td(prediction.teams[position - 1])
                                            for prediction in MEDIA_PREDICTIONS
                                        ),
                                    )
                                    for position in range(1, 21)
                                )
                            ),
                            cls="results-table prediction-comparison-table",
                        ),
                        cls="prediction-comparison-scroll",
                        role="region",
                        aria_label="Third-party prediction comparison table",
                        tabindex="0",
                    ),
                    P(
                        "Select a publisher name to view its original prediction.",
                        cls="comparison-note",
                    ),
                    cls="section-card comparison-card",
                ),
                cls="container wide-container",
            ),
            title="Third-party predictions · Season27",
        )

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
            published_bulletins = get_published_bulletins(session, season.id)
            latest_bulletin = published_bulletins[0] if published_bulletins else None
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
                        A("Leaderboard", href="/leaderboard")
                        if prediction_status and prediction_status.locked_at
                        else None,
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
                        H2("Season results"),
                        A("View leaderboard", href="/leaderboard"),
                        A("Swap activity", href="/activity", cls="card-link"),
                        cls="section-card",
                    )
                    if prediction_status and prediction_status.locked_at
                    else None,
                    bulletin_card(latest_bulletin) if latest_bulletin else None,
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
                    Span(str(item.predicted_position), cls="prediction-position"),
                    Span(
                        item.team.name,
                        cls="prediction-team drag-handle" if editable else "prediction-team",
                        title="Press and hold to reorder" if editable else None,
                    ),
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
                        data_editable="true" if editable else "false",
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

    def game_access(
        session: Session, player_id: int, season: Season, now: datetime
    ) -> Response | None:
        process_deadline(session, season, now)
        if now < london(season.prediction_locks_at):
            return HTMLResponse("Predictions remain private until the deadline.", status_code=403)
        status = get_status(session, player_id, season.id)
        if status is None or status.locked_at is None or status.excluded_at is not None:
            return HTMLResponse("This season is unavailable for this player.", status_code=403)
        return None

    @app.get("/participant-predictions")
    def participant_predictions_page(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            denied = game_access(session, app_session.player_id, season, clock())
            if denied:
                return denied
            predictions = get_participant_predictions(session, season.id)
            return page(
                Main(
                    A("← Back to leaderboard", href="/leaderboard"),
                    H1("Participant predictions"),
                    P(
                        "Compare every participating player's predicted finishing table."
                    ),
                    Div(
                        Div(
                            Table(
                                Caption(
                                    "Predicted Premier League finishing positions by participant",
                                    cls="visually-hidden",
                                ),
                                Thead(
                                    Tr(
                                        Th("Pos", scope="col", cls="comparison-position"),
                                        *(
                                            Th(
                                                A(
                                                    prediction.player.display_name,
                                                    href=(
                                                        f"/leaderboard/"
                                                        f"{prediction.player.id}"
                                                    ),
                                                ),
                                                scope="col",
                                            )
                                            for prediction in predictions
                                        ),
                                    )
                                ),
                                Tbody(
                                    *(
                                        Tr(
                                            Th(
                                                str(position),
                                                scope="row",
                                                cls="comparison-position",
                                            ),
                                            *(
                                                Td(prediction.teams[position - 1])
                                                for prediction in predictions
                                            ),
                                        )
                                        for position in range(1, 21)
                                    )
                                ),
                                cls=(
                                    "results-table prediction-comparison-table "
                                    "participant-comparison-table"
                                ),
                            ),
                            cls=(
                                "prediction-comparison-scroll "
                                "participant-comparison-scroll"
                            ),
                            role="region",
                            aria_label="Participant prediction comparison table",
                            tabindex="0",
                        ),
                        P(
                            "Select a participant name to view their score breakdown.",
                            cls="comparison-note",
                        ),
                        cls="section-card comparison-card",
                    ),
                    cls="container wide-container participant-comparison-container",
                ),
                title="Participant predictions · Season27",
            )

    @app.get("/leaderboard")
    def leaderboard_page(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            denied = game_access(session, app_session.player_id, season, clock())
            if denied:
                return denied
            now = clock()
            refresh_result = refresh_standings(
                session, season, standings_source, now, settings
            )
            snapshot = refresh_result.snapshot
            refresh_state = get_refresh_state(session, season.id)
            stale = snapshot_is_stale(snapshot, refresh_state, now, settings)
            feedback_key = request.query_params.get("refresh")
            feedback_messages = {
                RefreshOutcome.UPDATED.value: "Standings refreshed and scores updated.",
                RefreshOutcome.UNCHANGED.value: "Standings checked; the table has not changed.",
                RefreshOutcome.THROTTLED.value: "Please wait before refreshing standings again.",
                RefreshOutcome.FAILED.value: (
                    "Standings could not be refreshed; showing the last valid table."
                ),
                RefreshOutcome.CACHED.value: "Standings are already up to date.",
            }
            feedback = feedback_messages.get(feedback_key) if feedback_key else None
            if snapshot is None:
                return page(
                    Main(
                        A("← Back to dashboard", href="/"),
                        H1("Leaderboard"),
                        P(
                            "No valid standings are available yet. Please try refreshing shortly.",
                            cls="notice",
                            role="status",
                        ),
                        Form(
                            Input(
                                type="hidden",
                                name="csrf_token",
                                value=app_session.csrf_token,
                            ),
                            Button("Refresh standings", type="submit", cls="save-button"),
                            method="post",
                            action="/standings/refresh",
                        ),
                        cls="container",
                    ),
                    title="Leaderboard · Season27",
                )
            entries = build_leaderboard(session, season.id, snapshot)
            published_bulletins = get_published_bulletins(session, season.id)
            latest_bulletin = published_bulletins[0] if published_bulletins else None
            state_label = "Final" if snapshot.is_final else "As it stands"
            return page(
                Main(
                    A("← Back to dashboard", href="/"),
                    H1("Leaderboard"),
                    P(feedback, cls="success", role="status") if feedback else None,
                    P(
                        "Standings may be out of date. Scores use the last valid table.",
                        cls="error",
                        role="alert",
                    )
                    if stale
                    else None,
                    P(state_label, cls="result-state"),
                    P(f"Standings recorded: {format_time(snapshot.recorded_at)}"),
                    P(f"Last checked: {format_time(snapshot.refreshed_at)}"),
                    bulletin_card(latest_bulletin) if latest_bulletin else None,
                    Form(
                        Input(type="hidden", name="csrf_token", value=app_session.csrf_token),
                        Button("Refresh standings", type="submit", cls="save-button"),
                        method="post",
                        action="/standings/refresh",
                        cls="standings-refresh",
                    ),
                    Table(
                        Thead(
                            Tr(
                                Th("Rank", scope="col"),
                                Th("Player", scope="col"),
                                Th("Score", scope="col"),
                                Th("Exact", scope="col"),
                                Th("Worst error", scope="col"),
                            )
                        ),
                        Tbody(
                            *(
                                Tr(
                                    Td(str(entry.score.rank)),
                                    Td(
                                        A(
                                            entry.player.display_name,
                                            href=f"/leaderboard/{entry.player.id}",
                                        )
                                    ),
                                    Td(str(entry.score.total)),
                                    Td(str(entry.score.exact_count)),
                                    Td(str(entry.score.largest_error)),
                                )
                                for entry in entries
                            )
                        ),
                        cls="results-table",
                    ),
                    Div(
                        A(
                            "Compare all participant predictions",
                            href="/participant-predictions",
                        ),
                        A("View shared swap activity", href="/activity"),
                        cls="leaderboard-actions",
                    ),
                    cls="container",
                ),
                title="Leaderboard · Season27",
            )

    @app.get("/leaderboard/{player_id}")
    def player_score_page(request: Request, player_id: int) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            denied = game_access(session, app_session.player_id, season, clock())
            if denied:
                return denied
            refresh_result = refresh_standings(
                session, season, standings_source, clock(), settings
            )
            snapshot = refresh_result.snapshot
            if snapshot is None:
                return HTMLResponse("Standings are not available yet.", status_code=404)
            entry = find_entry(build_leaderboard(session, season.id, snapshot), player_id)
            if entry is None:
                return HTMLResponse("Player not found", status_code=404)
            teams = {row.team_id: row.team for row in snapshot.rows}
            return page(
                Main(
                    A("← Back to leaderboard", href="/leaderboard"),
                    H1(f"{entry.player.display_name}'s prediction"),
                    P("Final" if snapshot.is_final else "As it stands", cls="result-state"),
                    P(
                        f"Score {entry.score.total} · {entry.score.exact_count} exact · "
                        f"rank {entry.score.rank}"
                    ),
                    Table(
                        Thead(
                            Tr(
                                Th("Team", scope="col"),
                                Th("Predicted", scope="col"),
                                Th("Actual", scope="col"),
                                Th("Penalty", scope="col"),
                            )
                        ),
                        Tbody(
                            *(
                                Tr(
                                    Td(teams[item.team_id].name),
                                    Td(str(item.predicted_position)),
                                    Td(str(item.actual_position)),
                                    Td("Exact" if item.exact else str(item.penalty)),
                                )
                                for item in entry.score.breakdown
                            )
                        ),
                        cls="results-table",
                    ),
                    cls="container",
                ),
                title=f"{entry.player.display_name} · Season27",
            )

    @app.post("/standings/refresh")
    async def standings_refresh(request: Request) -> Response:
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
            denied = game_access(session, app_session.player_id, season, now)
            if denied:
                return denied
            ip = request.client.host if request.client else "unknown"
            keys = (
                throttle_key(settings, "standings-session", app_session.token_hash),
                throttle_key(settings, "standings-ip", ip),
            )
            result = refresh_standings(
                session,
                season,
                standings_source,
                now,
                settings,
                force=True,
                throttle_keys=keys,
            )
        return RedirectResponse(f"/leaderboard?refresh={result.outcome}", status_code=303)

    @app.get("/activity")
    def activity_page(request: Request) -> Response:
        app_session = current_session(request)
        if app_session is None:
            return redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            denied = game_access(session, app_session.player_id, season, clock())
            if denied:
                return denied
            swaps = get_shared_swaps(session, season.id)
            return page(
                Main(
                    A("← Back to leaderboard", href="/leaderboard"),
                    H1("Swap activity"),
                    Ul(
                        *(
                            Li(
                                Span(item.player.display_name, cls="window-name"),
                                Span(
                                    f"swapped {item.first_team.name} and "
                                    f"{item.second_team.name} in window "
                                    f"{item.swap_window.sequence_number}"
                                ),
                                Small(format_time(item.created_at)),
                                cls="swap-history-row",
                            )
                            for item in swaps
                        ),
                        cls="swap-history",
                    )
                    if swaps
                    else P("No swaps have been made yet."),
                    cls="container",
                ),
                title="Swap activity · Season27",
            )

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

    @app.get("/bulletins")
    def bulletin_archive(request: Request) -> Response:
        if current_session(request) is None:
            return redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            bulletins = get_published_bulletins(session, season.id)
        return page(
            Main(
                A("← Back to dashboard", href="/"),
                H1(BULLETIN_TITLE),
                P("Every published edition, newest first."),
                *(
                    Div(
                        H2(A(item.title, href=f"/bulletins/{item.slug}")),
                        P(item.body),
                        P(
                            format_time(item.published_at)
                            if item.published_at
                            else "Published",
                            cls="bulletin-date",
                        ),
                        cls="section-card bulletin-archive-item",
                    )
                    for item in bulletins
                ),
                P("No bulletins have been published yet.", cls="notice")
                if not bulletins
                else None,
                cls="container",
            ),
            title=f"{BULLETIN_TITLE} · Season27",
        )

    @app.get("/bulletins/{slug}")
    def bulletin_detail(request: Request, slug: str) -> Response:
        if current_session(request) is None:
            return redirect_to_login()
        with sessions() as session:
            bulletin = session.scalar(
                select(Bulletin).where(
                    Bulletin.slug == slug,
                    Bulletin.status == "published",
                )
            )
        if bulletin is None:
            return HTMLResponse("Bulletin not found", status_code=404)
        return page(
            Main(
                A("← All bulletins", href="/bulletins"),
                P(BULLETIN_TITLE, cls="bulletin-kicker"),
                H1(bulletin.title),
                P(bulletin.body, cls="bulletin-body bulletin-detail-body"),
                P(
                    f"Published {format_time(bulletin.published_at)}"
                    if bulletin.published_at
                    else "Published",
                    cls="bulletin-date",
                ),
                cls="container bulletin-detail",
            ),
            title=f"{bulletin.title} · Season27",
        )

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
            refresh_state = get_refresh_state(session, season.id) if season else None
            participants = list(
                session.scalars(
                    select(Player).where(Player.is_active.is_(True)).order_by(Player.display_name)
                )
            )
            prediction_statuses = (
                {
                    status.player_id: status
                    for status in session.scalars(
                        select(PredictionStatus).where(
                            PredictionStatus.season_id == season.id
                        )
                    )
                }
                if season
                else {}
            )
            open_swap_window = active_swap_window(season, clock()) if season else None
            swapped_player_ids = (
                set(
                    session.scalars(
                        select(Swap.player_id).where(
                            Swap.season_id == season.id,
                            Swap.swap_window_id == open_swap_window.id,
                        )
                    )
                )
                if season and open_swap_window
                else set()
            )
        return page(
            Main(
                A("← Back to dashboard", href="/"),
                H1("Season27 administration"),
                P(
                    "Standings source requires attention. Players are seeing the last valid table.",
                    cls="error",
                    role="alert",
                )
                if refresh_state and refresh_state.incident_open
                else None,
                Div(
                    H2("Participant status"),
                    P(f"Current season: {season.name}") if season else P("No season configured."),
                    Div(
                        Table(
                            Thead(
                                Tr(
                                    Th("Participant", scope="col"),
                                    Th("Prediction", scope="col"),
                                    Th(
                                        f"Swap {open_swap_window.sequence_number}",
                                        scope="col",
                                    )
                                    if open_swap_window
                                    else None,
                                )
                            ),
                            Tbody(
                                *(
                                    Tr(
                                        Td(player.display_name),
                                        Td(
                                            "Submitted"
                                            if prediction_statuses.get(player.id)
                                            and prediction_statuses[player.id].submitted_at
                                            else "Not yet"
                                        ),
                                        Td(
                                            "Executed"
                                            if player.id in swapped_player_ids
                                            else "Not yet"
                                        )
                                        if open_swap_window
                                        else None,
                                    )
                                    for player in participants
                                )
                            ),
                            cls="results-table participant-status-table",
                        ),
                        cls="participant-status-scroll",
                    )
                    if season and participants
                    else None,
                    cls="section-card",
                ),
                Div(
                    H2("Security and players"),
                    A("Manage players and login codes", href="/admin/players"),
                    A("Manage sessions", href="/admin/sessions", cls="card-link"),
                    cls="section-card",
                ),
                Div(
                    H2("Game operations"),
                    A("Season dates", href="/admin/season"),
                    A("Corrections and reinstatement", href="/admin/game", cls="card-link"),
                    cls="section-card",
                ),
                Div(
                    H2("Records and operations"),
                    A("Manage bulletins", href="/admin/bulletins"),
                    A("Audit history", href="/admin/audit"),
                    A("Exports", href="/admin/exports", cls="card-link"),
                    A("Operational health", href="/admin/health", cls="card-link"),
                    cls="section-card",
                ),
                cls="container",
            ),
            title="Administration · Season27",
        )

    def admin_access(request: Request) -> tuple[AppSession | None, Response | None]:
        app_session = current_session(request)
        if app_session is None:
            return None, redirect_to_login()
        if not app_session.player.is_admin:
            return None, HTMLResponse("Administrator access is required.", status_code=403)
        return app_session, None

    def admin_csrf(form: object, app_session: AppSession) -> bool:
        value = form.get("csrf_token", "")  # type: ignore[attr-defined]
        return hmac.compare_digest(str(value), app_session.csrf_token)

    def bulletin_fact_list(fact_pack: dict[str, object]) -> tuple[Ul, Ul]:
        raw_matches = fact_pack.get("matches", [])
        raw_impacts = fact_pack.get("period_player_impacts", [])
        matches = raw_matches if isinstance(raw_matches, (list, tuple)) else []
        impacts = raw_impacts if isinstance(raw_impacts, (list, tuple)) else []
        return (
            Ul(
                *(
                    Li(
                        f"{item.get('home_team', 'Unknown')} "
                        f"{item.get('home_score', '?')}–{item.get('away_score', '?')} "
                        f"{item.get('away_team', 'Unknown')} "
                        f"({item.get('evidence', 'period_context_only')})"
                    )
                    for item in matches
                    if isinstance(item, dict)
                )
            ),
            Ul(
                *(
                    Li(
                        f"{item.get('display_name', 'Unknown')}: "
                        f"rank {item.get('previous_rank', '?')} → "
                        f"{item.get('current_rank', '?')}; "
                        f"score {item.get('previous_score', '?')} → "
                        f"{item.get('current_score', '?')}"
                    )
                    for item in impacts
                    if isinstance(item, dict)
                )
            ),
        )

    def admin_bulletin_preview(bulletin: Bulletin, csrf_token: str) -> Response:
        match_facts, impact_facts = bulletin_fact_list(bulletin.fact_pack)
        return page(
            Main(
                A("← Back to bulletins", href="/admin/bulletins"),
                H1("Bulletin preview"),
                P(f"Status: {bulletin.status.title()}", cls="result-state"),
                Div(
                    P(BULLETIN_TITLE, cls="bulletin-kicker"),
                    H2(bulletin.title),
                    P(bulletin.body, cls="bulletin-body"),
                    P(
                        f"Reporting period: {format_time(bulletin.period_start)} – "
                        f"{format_time(bulletin.period_end)}",
                        cls="bulletin-date",
                    ),
                    cls="bulletin-card bulletin-preview",
                ),
                Div(
                    H2("Edit copy"),
                    Form(
                        Input(type="hidden", name="csrf_token", value=csrf_token),
                        Label("Bulletin text", fr="bulletin-body"),
                        Textarea(
                            bulletin.body,
                            id="bulletin-body",
                            name="body",
                            rows="7",
                            minlength="15",
                            required=True,
                        ),
                        Button("Save changes", type="submit", cls="save-button"),
                        method="post",
                        action=f"/admin/bulletins/{bulletin.id}/update",
                        cls="admin-form bulletin-editor",
                    ),
                    cls="section-card",
                ),
                Div(
                    H2("Publication"),
                    Form(
                        Input(type="hidden", name="csrf_token", value=csrf_token),
                        Button(
                            "Republish" if bulletin.status == "suppressed" else "Publish",
                            type="submit",
                            cls="save-button",
                        ),
                        method="post",
                        action=f"/admin/bulletins/{bulletin.id}/publish",
                        cls="inline-admin-form",
                    )
                    if bulletin.status in {"draft", "suppressed"}
                    else None,
                    Form(
                        Input(type="hidden", name="csrf_token", value=csrf_token),
                        Button("Suppress bulletin", type="submit"),
                        method="post",
                        action=f"/admin/bulletins/{bulletin.id}/suppress",
                        cls="inline-admin-form",
                    )
                    if bulletin.status == "published"
                    else None,
                    A(
                        "View published edition",
                        href=f"/bulletins/{bulletin.slug}",
                        cls="card-link",
                    )
                    if bulletin.status == "published"
                    else None,
                    cls="section-card",
                ),
                Div(
                    H2("Verified source matches"),
                    match_facts,
                    H2("Leaderboard changes"),
                    impact_facts,
                    cls="section-card bulletin-facts",
                ),
                cls="container wide-container",
            ),
            title="Bulletin preview · Season27",
        )

    @app.get("/admin/bulletins")
    def admin_bulletins(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        now = clock()
        with sessions() as session:
            bulletins = list(
                session.scalars(select(Bulletin).order_by(Bulletin.period_end.desc()))
            )
        period_end = now.strftime("%Y-%m-%dT%H:%M")
        period_start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
        return page(
            Main(
                A("← Back to administration", href="/admin"),
                H1(BULLETIN_TITLE),
                Div(
                    H2("Prepare an edition"),
                    P(
                        "The preview is built from stored match results and standings snapshots. "
                        "Nothing is public until Publish is selected."
                    ),
                    Form(
                        Input(
                            type="hidden",
                            name="csrf_token",
                            value=app_session.csrf_token,
                        ),
                        Label("Period start", fr="period-start"),
                        Input(
                            type="datetime-local",
                            id="period-start",
                            name="period_start",
                            value=period_start,
                            required=True,
                        ),
                        Label("Period end", fr="period-end"),
                        Input(
                            type="datetime-local",
                            id="period-end",
                            name="period_end",
                            value=period_end,
                            required=True,
                        ),
                        Label("Bulletin text", fr="new-bulletin-body"),
                        Textarea(
                            id="new-bulletin-body",
                            name="body",
                            rows="7",
                            minlength="15",
                            maxlength="1200",
                            required=True,
                            placeholder=(
                                "A short, sharp and factual dose of Monday morning banter."
                            ),
                        ),
                        Button("Build preview", type="submit", cls="save-button"),
                        method="post",
                        action="/admin/bulletins/preview",
                        cls="admin-form bulletin-editor",
                    ),
                    cls="section-card",
                ),
                Div(
                    H2("Editions"),
                    *(
                        Div(
                            A(item.title, href=f"/admin/bulletins/{item.id}"),
                            Span(item.status.title(), cls=f"bulletin-status {item.status}"),
                            Small(format_time(item.period_end)),
                            cls="admin-summary-row",
                        )
                        for item in bulletins
                    ),
                    P("No bulletin drafts yet.") if not bulletins else None,
                    cls="section-card",
                ),
                cls="container wide-container",
            ),
            title=f"{BULLETIN_TITLE} · Administration",
        )

    @app.post("/admin/bulletins/preview")
    async def admin_bulletin_create_preview(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            period_start = parse_local_datetime(form.get("period_start"))
            period_end = parse_local_datetime(form.get("period_end"))
            with sessions() as session:
                season = get_current_season(session)
                if season is None:
                    return HTMLResponse("No season configured", status_code=409)
                pack = build_fact_pack(session, season.id, period_start, period_end)
                bulletin = save_bulletin_draft(
                    session,
                    pack,
                    str(form.get("body", "")),
                    app_session.player_id,
                    clock(),
                )
                bulletin_id = bulletin.id
        except (FactPackError, InvalidBulletin) as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse(f"/admin/bulletins/{bulletin_id}", status_code=303)

    @app.get("/admin/bulletins/{bulletin_id}")
    def admin_bulletin_detail(request: Request, bulletin_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        with sessions() as session:
            bulletin = session.get(Bulletin, bulletin_id)
            if bulletin is None:
                return HTMLResponse("Bulletin not found", status_code=404)
            return admin_bulletin_preview(bulletin, app_session.csrf_token)

    @app.post("/admin/bulletins/{bulletin_id}/update")
    async def admin_bulletin_update(request: Request, bulletin_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                bulletin = session.get(Bulletin, bulletin_id)
                if bulletin is None:
                    return HTMLResponse("Bulletin not found", status_code=404)
                update_bulletin(
                    session,
                    bulletin,
                    str(form.get("body", "")),
                    app_session.player_id,
                    clock(),
                )
        except InvalidBulletin as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse(f"/admin/bulletins/{bulletin_id}", status_code=303)

    @app.post("/admin/bulletins/{bulletin_id}/publish")
    async def admin_bulletin_publish(request: Request, bulletin_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                bulletin = session.get(Bulletin, bulletin_id)
                if bulletin is None:
                    return HTMLResponse("Bulletin not found", status_code=404)
                publish_bulletin(session, bulletin, app_session.player_id, clock())
        except InvalidBulletin as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse(f"/admin/bulletins/{bulletin_id}", status_code=303)

    @app.post("/admin/bulletins/{bulletin_id}/suppress")
    async def admin_bulletin_suppress(request: Request, bulletin_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                bulletin = session.get(Bulletin, bulletin_id)
                if bulletin is None:
                    return HTMLResponse("Bulletin not found", status_code=404)
                suppress_bulletin(session, bulletin, app_session.player_id, clock())
        except InvalidBulletin as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse(f"/admin/bulletins/{bulletin_id}", status_code=303)

    def parse_order(form: object) -> list[int]:
        try:
            values = form.getlist("team_id")  # type: ignore[attr-defined]
            return [int(str(value)) for value in values]
        except (TypeError, ValueError, AttributeError) as error:
            raise InvalidAdminAction("Invalid team order.") from error

    def team_order_form(
        action: str,
        csrf_token: str,
        teams: Sequence[SeasonTeam],
        current_ids: list[int],
        submit_label: str,
        *,
        include_final: bool = False,
    ) -> Form:
        by_id = {item.team_id: item.team for item in teams}
        return Form(
            Input(type="hidden", name="csrf_token", value=csrf_token),
            Ul(
                *(
                    Li(
                        Label(f"Position {position}", fr=f"position-{position}"),
                        Select(
                            *(
                                Option(
                                    team.name,
                                    value=team_id,
                                    selected=team_id == selected_id,
                                )
                                for team_id, team in by_id.items()
                            ),
                            id=f"position-{position}",
                            name="team_id",
                        ),
                        cls="admin-order-row",
                    )
                    for position, selected_id in enumerate(current_ids, start=1)
                ),
                cls="admin-order",
            ),
            Label(
                Input(type="checkbox", name="is_final", value="yes"),
                " Mark these standings as final",
            )
            if include_final
            else None,
            Label("Reason", fr="reason"),
            Input(id="reason", name="reason", minlength="8", maxlength="500", required=True),
            Button(submit_label, type="submit", cls="save-button"),
            method="post",
            action=action,
            cls="admin-form",
        )

    @app.get("/admin/players")
    def admin_players(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        with sessions() as session:
            players = list(session.scalars(select(Player).order_by(Player.id)))
        return page(
            Main(
                A("← Back to administration", href="/admin"),
                H1("Players and login codes"),
                P(
                    "Generated codes are displayed once. Only their secure hashes are retained.",
                    cls="notice",
                ),
                Div(
                    H2("Add participant"),
                    Form(
                        Input(
                            type="hidden",
                            name="csrf_token",
                            value=app_session.csrf_token,
                        ),
                        Label("Display name", fr="new-player-name"),
                        Input(
                            id="new-player-name",
                            name="display_name",
                            maxlength="80",
                            required=True,
                        ),
                        Button("Add participant", type="submit", cls="save-button"),
                        method="post",
                        action="/admin/players/create",
                        cls="admin-form",
                    ),
                    cls="section-card",
                ),
                *(
                    Div(
                        H2(player.display_name),
                        Form(
                            Input(
                                type="hidden",
                                name="csrf_token",
                                value=app_session.csrf_token,
                            ),
                            Label("Display name", fr=f"name-{player.id}"),
                            Input(
                                id=f"name-{player.id}",
                                name="display_name",
                                value=player.display_name,
                                maxlength="80",
                                required=True,
                            ),
                            Label(
                                Input(
                                    type="checkbox",
                                    name="is_active",
                                    value="yes",
                                    checked=player.is_active,
                                ),
                                " Active player",
                            ),
                            Button("Save player", type="submit"),
                            method="post",
                            action=f"/admin/players/{player.id}/update",
                            cls="admin-form",
                        ),
                        Form(
                            Input(
                                type="hidden",
                                name="csrf_token",
                                value=app_session.csrf_token,
                            ),
                            Button("Generate new login code", type="submit"),
                            method="post",
                            action=f"/admin/players/{player.id}/rotate-code",
                            cls="inline-admin-form",
                        ),
                        Form(
                            Input(
                                type="hidden",
                                name="csrf_token",
                                value=app_session.csrf_token,
                            ),
                            Button("Clear login lock", type="submit"),
                            method="post",
                            action=f"/admin/players/{player.id}/unlock",
                            cls="inline-admin-form",
                        ),
                        cls="section-card",
                    )
                    for player in players
                ),
                cls="container",
            ),
            title="Players · Season27",
        )

    @app.post("/admin/players/create")
    async def admin_player_create(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                player, code = create_player(
                    session,
                    app_session.player_id,
                    str(form.get("display_name", "")),
                    clock(),
                )
        except InvalidAdminAction as error:
            return HTMLResponse(str(error), status_code=422)
        response = page(
            Main(
                H1("Participant added"),
                P(f"Initial login code for {player.display_name}"),
                P(code, cls="one-time-code"),
                P("Copy and distribute this code now. It will not be shown again.", cls="notice"),
                A("Return to players", href="/admin/players"),
                cls="container login-container",
            ),
            title="Participant added · Season27",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/admin/players/{player_id}/update")
    async def admin_player_update(request: Request, player_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                update_player(
                    session,
                    app_session.player_id,
                    player_id,
                    str(form.get("display_name", "")),
                    form.get("is_active") == "yes",
                    clock(),
                )
        except InvalidAdminAction as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/admin/players", status_code=303)

    @app.post("/admin/players/{player_id}/rotate-code")
    async def admin_rotate_code(request: Request, player_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                player = session.get(Player, player_id)
                code = rotate_login_code(
                    session, app_session.player_id, player_id, clock()
                )
                player_name = player.display_name if player else "player"
        except InvalidAdminAction as error:
            return HTMLResponse(str(error), status_code=422)
        response = page(
            Main(
                H1("New login code"),
                P(f"New code for {player_name}"),
                P(code, cls="one-time-code"),
                P("Copy and distribute this code now. It will not be shown again.", cls="notice"),
                A("Return to players", href="/admin/players"),
                cls="container login-container",
            ),
            title="New login code · Season27",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/admin/players/{player_id}/unlock")
    async def admin_unlock_player(request: Request, player_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                reset_player_lock(session, app_session.player_id, player_id, clock())
        except InvalidAdminAction as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/admin/players", status_code=303)

    @app.get("/admin/sessions")
    def admin_sessions(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        with sessions() as session:
            stored_sessions = list(
                session.scalars(
                    select(AppSession)
                    .options(selectinload(AppSession.player))
                    .order_by(AppSession.last_seen_at.desc())
                )
            )
        return page(
            Main(
                A("← Back to administration", href="/admin"),
                H1("Device sessions"),
                *(
                    Div(
                        H2(item.player.display_name),
                        P(f"Last seen {format_time(item.last_seen_at)}"),
                        P("Revoked" if item.revoked_at else "Active"),
                        Form(
                            Input(
                                type="hidden",
                                name="csrf_token",
                                value=app_session.csrf_token,
                            ),
                            Button(
                                "Revoke this session",
                                type="submit",
                                disabled=bool(item.revoked_at),
                            ),
                            method="post",
                            action=f"/admin/sessions/{item.id}/revoke",
                        ),
                        cls="section-card",
                    )
                    for item in stored_sessions
                ),
                *(
                    Form(
                        Input(
                            type="hidden",
                            name="csrf_token",
                            value=app_session.csrf_token,
                        ),
                        Button(f"Revoke all sessions for {player.display_name}", type="submit"),
                        method="post",
                        action=f"/admin/players/{player.id}/revoke-sessions",
                        cls="admin-form section-card",
                    )
                    for player in {item.player.id: item.player for item in stored_sessions}.values()
                ),
                cls="container",
            ),
            title="Sessions · Season27",
        )

    @app.post("/admin/sessions/{session_id}/revoke")
    async def admin_revoke_session(request: Request, session_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                revoke_session(session, app_session.player_id, session_id, clock())
        except InvalidAdminAction as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/admin/sessions", status_code=303)

    @app.post("/admin/players/{player_id}/revoke-sessions")
    async def admin_revoke_all_sessions(request: Request, player_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        with sessions() as session:
            revoke_player_sessions(session, app_session.player_id, player_id, clock())
        return RedirectResponse("/admin/sessions", status_code=303)

    @app.get("/admin/season")
    def admin_season(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            return page(
                Main(
                    A("← Back to administration", href="/admin"),
                    H1("Season dates"),
                    P("Dates become immutable when predictions lock.", cls="notice"),
                    Form(
                        Input(
                            type="hidden",
                            name="csrf_token",
                            value=app_session.csrf_token,
                        ),
                        Label("Game opens", fr="game-opens"),
                        Input(
                            id="game-opens",
                            name="game_opens_at",
                            type="datetime-local",
                            value=london(season.game_opens_at).strftime("%Y-%m-%dT%H:%M"),
                            required=True,
                        ),
                        Label("Predictions lock", fr="prediction-locks"),
                        Input(
                            id="prediction-locks",
                            name="prediction_locks_at",
                            type="datetime-local",
                            value=london(season.prediction_locks_at).strftime("%Y-%m-%dT%H:%M"),
                            required=True,
                        ),
                        Button("Save dates", type="submit", cls="save-button"),
                        method="post",
                        action="/admin/season",
                        cls="admin-form",
                    ),
                    cls="container",
                ),
                title="Season dates · Season27",
            )

    @app.post("/admin/season")
    async def admin_season_update(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            opens_at = datetime.fromisoformat(str(form.get("game_opens_at", "")))
            locks_at = datetime.fromisoformat(str(form.get("prediction_locks_at", "")))
            opens_at = london(opens_at)
            locks_at = london(locks_at)
            with sessions() as session:
                season = get_current_season(session)
                if season is None:
                    return HTMLResponse("No season configured", status_code=409)
                update_season_dates(
                    session,
                    app_session.player_id,
                    season,
                    opens_at,
                    locks_at,
                    clock(),
                )
        except (ValueError, InvalidAdminAction) as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/admin/season", status_code=303)

    @app.get("/admin/game")
    def admin_game(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            now = clock()
            process_deadline(session, season, now)
            players = list(session.scalars(select(Player).order_by(Player.id)))
            statuses = {
                status.player_id: status
                for status in session.scalars(
                    select(PredictionStatus).where(PredictionStatus.season_id == season.id)
                )
            }
            swaps = get_shared_swaps(session, season.id)
            snapshot = get_latest_snapshot(session, season.id)
            reveal_open = now >= london(season.prediction_locks_at)
            return page(
                Main(
                    A("← Back to administration", href="/admin"),
                    H1("Game corrections"),
                    P(
                        "Prediction contents remain hidden until the deadline, including here.",
                        cls="notice",
                    )
                    if not reveal_open
                    else None,
                    Div(
                        H2("Players"),
                        Ul(
                            *(
                                Li(
                                    Span(player.display_name),
                                    Span(
                                        "Excluded"
                                        if statuses.get(player.id)
                                        and statuses[player.id].excluded_at
                                        else "Locked"
                                        if statuses.get(player.id)
                                        and statuses[player.id].locked_at
                                        else "Not locked"
                                    ),
                                    A("Open", href=f"/admin/game/player/{player.id}")
                                    if reveal_open
                                    else None,
                                    cls="admin-summary-row",
                                )
                                for player in players
                            )
                        ),
                        cls="section-card",
                    ),
                    Div(
                        H2("Recorded swaps"),
                        *(
                            Div(
                                P(
                                    f"{swap.player.display_name}: {swap.first_team.name} / "
                                    f"{swap.second_team.name}"
                                ),
                                P("Corrected" if swap.corrected_at else "Final"),
                                Form(
                                    Input(
                                        type="hidden",
                                        name="csrf_token",
                                        value=app_session.csrf_token,
                                    ),
                                    Label("Reason", fr=f"swap-reason-{swap.id}"),
                                    Input(
                                        id=f"swap-reason-{swap.id}",
                                        name="reason",
                                        minlength="8",
                                        maxlength="500",
                                        required=True,
                                    ),
                                    Button(
                                        "Reverse as correction",
                                        type="submit",
                                        disabled=bool(swap.corrected_at),
                                    ),
                                    method="post",
                                    action=f"/admin/game/swap/{swap.id}/reverse",
                                    cls="admin-form",
                                ),
                            )
                            for swap in swaps
                        )
                        if reveal_open and swaps
                        else P("No visible swaps."),
                        cls="section-card",
                    ),
                    Div(
                        H2("Standings"),
                        A("Create a corrected standings version", href="/admin/game/standings")
                        if snapshot
                        else P("No standings snapshot is available."),
                        cls="section-card",
                    ),
                    cls="container",
                ),
                title="Game corrections · Season27",
            )

    @app.get("/admin/game/player/{player_id}")
    def admin_game_player(request: Request, player_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            player = session.get(Player, player_id)
            if season is None or player is None:
                return HTMLResponse("Player or season not found", status_code=404)
            now = clock()
            if now < london(season.prediction_locks_at):
                return HTMLResponse(
                    "Predictions remain private until the deadline.", status_code=403
                )
            process_deadline(session, season, now)
            status = get_status(session, player_id, season.id)
            teams = get_season_teams(session, season.id)
            draft = get_draft(session, player_id, season.id)
            current_ids = [item.team_id for item in draft] or [item.team_id for item in teams]
            action = (
                f"/admin/game/player/{player_id}/reinstate"
                if status and status.excluded_at
                else f"/admin/game/player/{player_id}/correct"
            )
            label = (
                "Reinstate and lock prediction"
                if status and status.excluded_at
                else "Correct prediction"
            )
            return page(
                Main(
                    A("← Back to game corrections", href="/admin/game"),
                    H1(player.display_name),
                    P(
                        "Reinstatement is exceptional and immediately locks the entered prediction."
                        if status and status.excluded_at
                        else "This creates immutable before-and-after correction snapshots."
                    ),
                    team_order_form(
                        action,
                        app_session.csrf_token,
                        teams,
                        current_ids,
                        label,
                    ),
                    cls="container",
                ),
                title=f"Correct {player.display_name} · Season27",
            )

    async def player_order_action(
        request: Request, player_id: int, *, reinstate: bool
    ) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            team_ids = parse_order(form)
            with sessions() as session:
                season = get_current_season(session)
                if season is None:
                    return HTMLResponse("No season configured", status_code=409)
                operation = reinstate_player if reinstate else correct_prediction
                operation(
                    session,
                    app_session.player_id,
                    player_id,
                    season,
                    team_ids,
                    str(form.get("reason", "")),
                    clock(),
                )
        except (InvalidAdminAction, InvalidPrediction) as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/admin/game", status_code=303)

    @app.post("/admin/game/player/{player_id}/reinstate")
    async def admin_reinstate(request: Request, player_id: int) -> Response:
        return await player_order_action(request, player_id, reinstate=True)

    @app.post("/admin/game/player/{player_id}/correct")
    async def admin_correct_prediction(request: Request, player_id: int) -> Response:
        return await player_order_action(request, player_id, reinstate=False)

    @app.post("/admin/game/swap/{swap_id}/reverse")
    async def admin_reverse_swap(request: Request, swap_id: int) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                reverse_swap(
                    session,
                    app_session.player_id,
                    swap_id,
                    str(form.get("reason", "")),
                    clock(),
                )
        except InvalidAdminAction as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/admin/game", status_code=303)

    @app.get("/admin/game/standings")
    def admin_standings_correction(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            if season is None:
                return HTMLResponse("No season configured", status_code=409)
            snapshot = get_latest_snapshot(session, season.id)
            if snapshot is None:
                return HTMLResponse("No standings available", status_code=404)
            teams = get_season_teams(session, season.id)
            return page(
                Main(
                    A("← Back to game corrections", href="/admin/game"),
                    H1("Correct standings"),
                    P("A correction creates a new immutable version."),
                    team_order_form(
                        "/admin/game/standings",
                        app_session.csrf_token,
                        teams,
                        [row.team_id for row in snapshot.rows],
                        "Create corrected standings",
                        include_final=True,
                    ),
                    cls="container",
                ),
                title="Correct standings · Season27",
            )

    @app.post("/admin/game/standings")
    async def admin_standings_update(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        form = await request.form()
        if not admin_csrf(form, app_session):
            return HTMLResponse("Request rejected", status_code=403)
        try:
            with sessions() as session:
                season = get_current_season(session)
                if season is None:
                    return HTMLResponse("No season configured", status_code=409)
                snapshot = get_latest_snapshot(session, season.id)
                if snapshot is None:
                    return HTMLResponse("No standings available", status_code=404)
                correct_standings(
                    session,
                    app_session.player_id,
                    snapshot,
                    parse_order(form),
                    str(form.get("reason", "")),
                    clock(),
                    is_final=form.get("is_final") == "yes",
                )
        except (InvalidAdminAction, InvalidPrediction, KeyError) as error:
            return HTMLResponse(str(error), status_code=422)
        return RedirectResponse("/admin/game", status_code=303)

    @app.get("/admin/audit")
    def admin_audit(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        with sessions() as session:
            events = list(
                session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(250))
            )
            players = {
                player.id: player.display_name for player in session.scalars(select(Player))
            }
        return page(
            Main(
                A("← Back to administration", href="/admin"),
                H1("Audit history"),
                P("The most recent 250 security and game events are shown."),
                Table(
                    Thead(
                        Tr(
                            Th("Time", scope="col"),
                            Th("Actor", scope="col"),
                            Th("Event", scope="col"),
                            Th("Details", scope="col"),
                        )
                    ),
                    Tbody(
                        *(
                            Tr(
                                Td(format_time(event.created_at)),
                                Td(
                                    players.get(event.actor_player_id, "System")
                                    if event.actor_player_id is not None
                                    else "System"
                                ),
                                Td(event.event_type.replace("_", " ")),
                                Td(str(event.event_metadata)),
                            )
                            for event in events
                        )
                    ),
                    cls="results-table audit-table",
                ),
                cls="container wide-container",
            ),
            title="Audit history · Season27",
        )

    @app.get("/admin/exports")
    def admin_exports(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        return page(
            Main(
                A("← Back to administration", href="/admin"),
                H1("Data exports"),
                P(
                    "Prediction and score exports remain unavailable until predictions are public.",
                    cls="notice",
                ),
                *(
                    Div(
                        H2(kind.title()),
                        A("CSV", href=f"/admin/export/{kind}/csv"),
                        A("JSON", href=f"/admin/export/{kind}/json", cls="card-link"),
                        cls="section-card",
                    )
                    for kind in ("predictions", "swaps", "standings", "scores")
                ),
                cls="container",
            ),
            title="Exports · Season27",
        )

    def export_rows(
        session: Session, kind: str, season: Season, now: datetime
    ) -> list[dict[str, object]]:
        sensitive = {"predictions", "swaps", "scores"}
        if kind in sensitive and now < london(season.prediction_locks_at):
            raise InvalidAdminAction("This export is unavailable before predictions are public.")
        if kind == "predictions":
            players = {
                player.id: player.display_name for player in session.scalars(select(Player))
            }
            predictions = session.scalars(
                select(Prediction)
                .join(
                    PredictionStatus,
                    (PredictionStatus.player_id == Prediction.player_id)
                    & (PredictionStatus.season_id == Prediction.season_id),
                )
                .options(selectinload(Prediction.team))
                .where(
                    Prediction.season_id == season.id,
                    PredictionStatus.locked_at.is_not(None),
                    PredictionStatus.excluded_at.is_(None),
                )
                .order_by(Prediction.player_id, Prediction.predicted_position)
            )
            return [
                {
                    "player": players[item.player_id],
                    "position": item.predicted_position,
                    "team": item.team.name,
                }
                for item in predictions
            ]
        if kind == "swaps":
            return [
                {
                    "player": item.player.display_name,
                    "window": item.swap_window.sequence_number,
                    "first_team": item.first_team.name,
                    "second_team": item.second_team.name,
                    "created_at": london(item.created_at).isoformat(),
                    "corrected": item.corrected_at is not None,
                }
                for item in get_shared_swaps(session, season.id)
            ]
        snapshot = get_latest_snapshot(session, season.id)
        if kind == "standings":
            if snapshot is None:
                return []
            return [
                {
                    "version": snapshot.version,
                    "position": row.position,
                    "team": row.team.name,
                    "played": row.played,
                    "points": row.points,
                    "goal_difference": row.goal_difference,
                    "goals_scored": row.goals_scored,
                    "is_final": snapshot.is_final,
                }
                for row in snapshot.rows
            ]
        if kind == "scores":
            if snapshot is None:
                return []
            return [
                {
                    "rank": entry.score.rank,
                    "player": entry.player.display_name,
                    "score": entry.score.total,
                    "exact": entry.score.exact_count,
                    "worst_error": entry.score.largest_error,
                }
                for entry in build_leaderboard(session, season.id, snapshot)
            ]
        raise InvalidAdminAction("Unknown export type.")

    @app.get("/admin/export/{kind}/{format_name}")
    def admin_export(request: Request, kind: str, format_name: str) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        try:
            with sessions() as session:
                season = get_current_season(session)
                if season is None:
                    return HTMLResponse("No season configured", status_code=409)
                rows = export_rows(session, kind, season, clock())
        except InvalidAdminAction as error:
            return HTMLResponse(str(error), status_code=422)
        if format_name == "json":
            json_response = JSONResponse(rows)
            json_response.headers["Content-Disposition"] = (
                f'attachment; filename="season27-{kind}.json"'
            )
            return json_response
        if format_name == "csv":
            output = io.StringIO()
            if rows:
                writer = csv.DictWriter(output, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            csv_response = Response(output.getvalue(), media_type="text/csv")
            csv_response.headers["Content-Disposition"] = (
                f'attachment; filename="season27-{kind}.csv"'
            )
            return csv_response
        return HTMLResponse("Unknown export format", status_code=404)

    @app.get("/admin/health")
    def admin_health(request: Request) -> Response:
        app_session, denied = admin_access(request)
        if denied or app_session is None:
            return denied or redirect_to_login()
        with sessions() as session:
            season = get_current_season(session)
            snapshot = get_latest_snapshot(session, season.id) if season else None
            refresh_state = get_refresh_state(session, season.id) if season else None
        return page(
            Main(
                A("← Back to administration", href="/admin"),
                H1("Operational health"),
                Div(
                    P("Database", cls="label"),
                    P("Connected", cls="success"),
                    cls="section-card",
                ),
                Div(
                    P("Standings", cls="label"),
                    P(
                        f"Version {snapshot.version}; checked {format_time(snapshot.refreshed_at)}"
                        if snapshot
                        else "No valid snapshot"
                    ),
                    P("Source incident open", cls="error")
                    if refresh_state and refresh_state.incident_open
                    else P("No unresolved source incident", cls="success"),
                    cls="section-card",
                ),
                P(f"Environment: {settings.environment}"),
                cls="container",
            ),
            title="Operational health · Season27",
        )

    @app.get("/live")
    def live() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    def database_health() -> JSONResponse:
        try:
            with sessions() as session:
                session.execute(text("SELECT 1"))
            return JSONResponse({"status": "ok"})
        except Exception:
            return JSONResponse({"status": "degraded"}, status_code=503)

    @app.get("/ready")
    def ready() -> JSONResponse:
        return database_health()

    @app.get("/health")
    def health() -> JSONResponse:
        return database_health()

    app.state.engine = engine
    app.state.session_factory = sessions
    return app


app = create_app()


def run() -> None:
    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
    )
