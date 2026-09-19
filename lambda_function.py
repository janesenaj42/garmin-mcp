"""
MCP server exposing your own Garmin Connect data as tools.
Deploy as a Lambda Function URL; add that URL to Claude as a
custom connector (Settings > Connectors > Add custom connector).
"""
import hmac
import os

from garminconnect import Garmin
from mangum import Mangum
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

API_KEY = os.environ["API_KEY"]
EXPECTED_API_KEY = API_KEY.encode()
# Bare hostname of your Function URL, e.g. abc123xyz.lambda-url.ap-southeast-2.on.aws
# (no scheme, no path) -- FastMCP rejects any request whose Host header isn't
# on this list as a DNS-rebinding defense. The X-Api-Key header check below
# already gates every request, so this is a second, narrower layer, not the
# only one.
ALLOWED_HOST = os.environ["ALLOWED_HOST"]

_client_cache = None


def _client() -> Garmin:
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    g = Garmin()
    # GARMIN_TOKENS is the JSON blob printed by setup_garmin_token.py, handed
    # in as a Lambda env var you paste once in the console -- nothing is
    # fetched from S3, SSM, or anywhere else. Passed inline (not a path), so
    # no disk access is needed; no password required since it's a live session.
    g.login(os.environ["GARMIN_TOKENS"])
    _client_cache = g
    return g


def _trim_activity(a: dict) -> dict:
    # Garmin's raw activity dict is ~4KB and mostly irrelevant here (owner
    # profile image URLs, privacy flags, etc.) -- keep only what matters for
    # judging recovery/training load day to day.
    return {
        "activityId": a.get("activityId"),
        "activityName": a.get("activityName"),
        "activityType": (a.get("activityType") or {}).get("typeKey"),
        "startTimeLocal": a.get("startTimeLocal"),
        "durationSeconds": a.get("duration"),
        "distanceMeters": a.get("distance"),
        "calories": a.get("calories"),
        "averageHR": a.get("averageHR"),
        "maxHR": a.get("maxHR"),
        "elevationGainMeters": a.get("elevationGain"),
        "aerobicTrainingEffect": a.get("aerobicTrainingEffect"),
        "anaerobicTrainingEffect": a.get("anaerobicTrainingEffect"),
        "trainingEffectLabel": a.get("trainingEffectLabel"),
        "activityTrainingLoad": a.get("activityTrainingLoad"),
        "vO2MaxValue": a.get("vO2MaxValue"),
    }


def _build_asgi_app():
    # FastMCP's StreamableHTTPSessionManager can only be .run() once per
    # instance -- it's designed for a long-lived server process's lifespan,
    # not a per-request cycle. Mangum, though, drives a fresh ASGI lifespan
    # startup/shutdown pair on every single Lambda invocation, and this
    # module is reused across warm invocations. So a brand-new FastMCP
    # instance (and its session manager) is built fresh per invocation
    # instead of once at import time, to avoid "run() can only be called
    # once per instance" on the second request a warm container handles.
    mcp = FastMCP(
        "garmin",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=[ALLOWED_HOST]),
    )

    @mcp.tool()
    def get_steps(date: str) -> dict:
        """Total steps, step goal, and distance (meters) for a date (YYYY-MM-DD)."""
        days = _client().get_daily_steps(date, date)
        return days[0] if days else {}

    @mcp.tool()
    def get_sleep(date: str) -> dict:
        """Sleep stages, sleep score, and recovery signals (HRV, sleep need) for a date (YYYY-MM-DD)."""
        sleep = _client().get_sleep_data(date)
        daily = sleep.get("dailySleepDTO") or {}
        sleep_need = daily.get("sleepNeed") or {}
        return {
            "calendarDate": daily.get("calendarDate"),
            "sleepTimeSeconds": daily.get("sleepTimeSeconds"),
            "deepSleepSeconds": daily.get("deepSleepSeconds"),
            "lightSleepSeconds": daily.get("lightSleepSeconds"),
            "remSleepSeconds": daily.get("remSleepSeconds"),
            "awakeSleepSeconds": daily.get("awakeSleepSeconds"),
            "awakeCount": daily.get("awakeCount"),
            "avgSleepStress": daily.get("avgSleepStress"),
            "avgHeartRate": daily.get("avgHeartRate"),
            "sleepScore": (daily.get("sleepScores") or {}).get("overall", {}).get("value"),
            "sleepScoreQualifier": (daily.get("sleepScores") or {}).get("overall", {}).get("qualifierKey"),
            "sleepScoreFeedback": daily.get("sleepScoreFeedback"),
            "sleepNeedBaselineMinutes": sleep_need.get("baseline"),
            "sleepNeedActualMinutes": sleep_need.get("actual"),
            "sleepNeedFeedback": sleep_need.get("feedback"),
            "avgOvernightHrv": sleep.get("avgOvernightHrv"),
            "hrvStatus": sleep.get("hrvStatus"),
            "bodyBatteryChange": sleep.get("bodyBatteryChange"),
            "restingHeartRate": sleep.get("restingHeartRate"),
            "restlessMomentsCount": sleep.get("restlessMomentsCount"),
        }

    @mcp.tool()
    def get_heart_rate(date: str) -> dict:
        """Resting, 7-day-avg resting, min, and max heart rate for a date (YYYY-MM-DD)."""
        hr = _client().get_heart_rates(date)
        return {
            "calendarDate": hr.get("calendarDate"),
            "restingHeartRate": hr.get("restingHeartRate"),
            "lastSevenDaysAvgRestingHeartRate": hr.get("lastSevenDaysAvgRestingHeartRate"),
            "minHeartRate": hr.get("minHeartRate"),
            "maxHeartRate": hr.get("maxHeartRate"),
        }

    @mcp.tool()
    def get_body_battery(date: str) -> dict:
        """Body Battery charged/drained totals and feedback for a date (YYYY-MM-DD)."""
        days = _client().get_body_battery(date, date)
        if not days:
            return {}
        d = days[0]
        dynamic = d.get("bodyBatteryDynamicFeedbackEvent") or {}
        end_of_day = d.get("endOfDayBodyBatteryDynamicFeedbackEvent") or {}
        return {
            "date": d.get("date"),
            "charged": d.get("charged"),
            "drained": d.get("drained"),
            "dynamicFeedback": dynamic.get("feedbackLongType"),
            "endOfDayFeedback": end_of_day.get("feedbackLongType"),
            "activityEvents": [
                {
                    "eventType": e.get("eventType"),
                    "durationSeconds": (e.get("durationInMilliseconds") or 0) // 1000,
                    "bodyBatteryImpact": e.get("bodyBatteryImpact"),
                }
                for e in d.get("bodyBatteryActivityEvent") or []
            ],
        }

    @mcp.tool()
    def get_activities(limit: int = 10) -> list:
        """Most recent activities with training load/effect, newest first."""
        return [_trim_activity(a) for a in _client().get_activities(0, limit)]

    @mcp.tool()
    def get_stats(date: str) -> dict:
        """Daily summary stats: calories, distance, floors, stress, for a date."""
        return _client().get_stats(date)

    @mcp.tool()
    def get_cycle_tracking(date: str) -> dict:
        """Menstrual cycle phase and fertile window status for a date (YYYY-MM-DD), if cycle tracking is enabled in Garmin Connect."""
        try:
            data = _client().get_menstrual_data_for_date(date)
        except Exception:
            return {"trackingEnabled": False}
        summary = (data or {}).get("daySummary") or {}
        day_in_cycle = summary.get("dayInCycle")
        if day_in_cycle is None:
            return {"trackingEnabled": False}
        period_length = summary.get("periodLength") or 0
        fertile_start = summary.get("fertileWindowStart")
        fertile_length = summary.get("lengthOfFertileWindow") or 0
        # Garmin's own "currentPhase" field is an undocumented numeric enum
        # -- rather than guess what each value means, the phase name here is
        # derived from the day-in-cycle/period-length/fertile-window fields,
        # whose meaning is given directly by their names.
        if day_in_cycle <= period_length:
            phase = "menstrual"
        elif fertile_start is not None and fertile_start <= day_in_cycle < fertile_start + fertile_length:
            phase = "fertile_window"
        elif fertile_start is not None and day_in_cycle < fertile_start:
            phase = "follicular"
        else:
            phase = "luteal"
        return {
            "trackingEnabled": True,
            "dayInCycle": day_in_cycle,
            "phase": phase,
            "periodLength": summary.get("periodLength"),
            "predictedCycleLength": summary.get("predictedCycleLength"),
            "daysUntilNextPhase": summary.get("daysUntilNextPhase"),
            "cycleType": summary.get("cycleType"),
        }

    return mcp.streamable_http_app()


_asgi_app = None


async def app(scope, receive, send):
    global _asgi_app
    # Mangum sends one lifespan startup/shutdown pair per invocation, ahead
    # of the actual http scope call below -- rebuild the ASGI app here each
    # time so its session manager is always a fresh, never-yet-run instance.
    if scope["type"] == "lifespan":
        _asgi_app = _build_asgi_app()
        await _asgi_app(scope, receive, send)
        return

    # MCP clients (Claude included) probe OAuth-discovery paths like
    # /.well-known/oauth-authorization-server and /register when adding a
    # connector, to decide whether OAuth is available -- independent of any
    # header-based auth configured for the actual tool-call endpoint. Let
    # these fall through to FastMCP's app (which doesn't define them, so
    # they 404 cleanly) instead of the API-key gate below: a blanket 401
    # here reads as "OAuth is required" and sends the client down a doomed
    # dynamic-client-registration attempt instead of just using X-Api-Key.
    path = scope.get("path", "")
    if path.startswith("/.well-known/") or path == "/register":
        await _asgi_app(scope, receive, send)
        return

    # Shared-secret gate: a Lambda Function URL with auth-type NONE is
    # otherwise reachable by anyone who has the URL. Claude's "Add custom
    # connector" dialog has a Request headers field for exactly this case
    # (an API key instead of OAuth), so the secret travels as a custom
    # header rather than sitting in the URL where it could end up in
    # browser history or incidental logging. Not "Authorization" -- Claude
    # blocks that one as a reserved/OAuth-managed header name.
    got_key = b""
    for name, value in scope.get("headers") or []:
        if name == b"x-api-key":
            got_key = value
            break
    if not hmac.compare_digest(got_key, EXPECTED_API_KEY):
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b"unauthorized"})
        return
    await _asgi_app(scope, receive, send)


handler = Mangum(app)
