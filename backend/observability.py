# ============================================================
# LOGGING, METRICS AND ERROR REPORTING
# ============================================================
#   LOG_FORMAT=json     one JSON object per line (for log shippers)
#   ERROR_WEBHOOK_URL   POST unhandled errors as JSON (Slack, Teams, an
#                       incident tool or a small relay); SENTRY_DSN is used
#                       instead when the sentry-sdk package is installed
#   GET /metrics        Prometheus text format (protect with METRICS_TOKEN)

import contextvars
import json
import logging
import threading
import time
import traceback
import urllib.request
from collections import defaultdict

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str, fmt: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


# ------------------------------------------------------------
# Error reporting
# ------------------------------------------------------------

_logger = logging.getLogger("pharmastock.errors")
_sentry = None
_webhook_url: str | None = None
_environment = "development"
_last_sent: dict[str, float] = {}


def configure_error_reporting(sentry_dsn: str | None, webhook_url: str | None, environment: str) -> str:
    global _sentry, _webhook_url, _environment
    _environment = environment
    if sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=sentry_dsn, environment=environment, send_default_pii=False)
            _sentry = sentry_sdk
            return "sentry"
        except ImportError:
            _logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; using the webhook if configured")
    _webhook_url = webhook_url
    return "webhook" if webhook_url else "log-only"


def report_error(error: BaseException, *, request_id: str, method: str, path: str) -> None:
    """Send an unhandled error to the configured tracker (never raises).
    Identical errors are sent at most once per 5 minutes."""
    try:
        if _sentry is not None:
            _sentry.capture_exception(error)
            return
        if not _webhook_url:
            return
        fingerprint = f"{type(error).__name__}:{path}"
        now = time.monotonic()
        if now - _last_sent.get(fingerprint, -1e9) < 300:
            return
        _last_sent[fingerprint] = now
        payload = {
            "text": f"PharmaStock {_environment}: {type(error).__name__} on {method} {path} (request {request_id})",
            "error": f"{type(error).__name__}: {error}"[:1000],
            "traceback": "".join(traceback.format_exception(error))[-4000:],
            "request_id": request_id, "method": method, "path": path, "environment": _environment,
        }
        request = urllib.request.Request(_webhook_url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        threading.Thread(target=_post, args=(request,), daemon=True).start()
    except Exception:  # noqa: BLE001 - reporting must never break a request
        _logger.exception("Error reporting failed")


def _post(request) -> None:
    try:
        urllib.request.urlopen(request, timeout=10).close()  # noqa: S310 (configured URL)
    except OSError:
        _logger.warning("Error webhook unreachable")


# ------------------------------------------------------------
# Metrics
# ------------------------------------------------------------

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.requests = defaultdict(int)          # (method, route, status) -> count
        self.latency = defaultdict(lambda: [0] * (len(BUCKETS) + 1))  # route -> bucket counts
        self.latency_sum = defaultdict(float)
        self.started = time.time()

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self.requests[(method, route, status)] += 1
            index = next((i for i, bound in enumerate(BUCKETS) if seconds <= bound), len(BUCKETS))
            self.latency[route][index] += 1
            self.latency_sum[route] += seconds

    def render(self, extra: dict[str, float]) -> str:
        lines = ["# HELP pharmastock_requests_total HTTP requests by route and status",
                 "# TYPE pharmastock_requests_total counter"]
        with self._lock:
            for (method, route, status), count in sorted(self.requests.items()):
                lines.append(f'pharmastock_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}')
            lines += ["# HELP pharmastock_request_seconds Request duration",
                      "# TYPE pharmastock_request_seconds histogram"]
            for route, counts in sorted(self.latency.items()):
                cumulative = 0
                for bound, count in zip((*BUCKETS, "+Inf"), counts):
                    cumulative += count
                    lines.append(f'pharmastock_request_seconds_bucket{{route="{route}",le="{bound}"}} {cumulative}')
                lines.append(f'pharmastock_request_seconds_sum{{route="{route}"}} {self.latency_sum[route]:.6f}')
                lines.append(f'pharmastock_request_seconds_count{{route="{route}"}} {cumulative}')
        lines.append(f"pharmastock_uptime_seconds {time.time() - self.started:.0f}")
        for name, value in extra.items():
            lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
