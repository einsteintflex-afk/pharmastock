"""Regenerate the endpoint tables of docs/API.md from the application's route table.

    python scripts/gen_api_docs.py > /tmp/endpoints.md

Access is read from the route dependencies (require / require_feature /
require_platform_admin / require_step_up), so the document cannot drift from
what the server enforces.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://unused@localhost/unused")

from fastapi.routing import APIRoute  # noqa: E402

from backend.main import app  # noqa: E402

PUBLIC = {"/auth/login", "/auth/forgot-password", "/auth/reset-password", "/auth/mfa/verify", "/r/{token}",
          "/billing/webhooks/paystack", "/messaging/webhooks/whatsapp", "/", "/health", "/ready", "/app"}


def access(route: APIRoute) -> str:
    if route.path in PUBLIC:
        if "webhooks" in route.path:
            return "public, signature-verified"
        return "public"
    found = []

    def walk(dependant):
        call = dependant.call
        name = getattr(call, "__qualname__", "")
        cells = {k: c.cell_contents for k, c in zip(call.__code__.co_freevars, call.__closure__ or ())} \
            if hasattr(call, "__code__") else {}
        if name.startswith("require_feature"):
            found.append(f"{cells.get('feature')} + {cells.get('permission')}")
            return
        if name.startswith("require."):
            found.append(cells.get("permission"))
            return
        if name == "require_step_up":
            found.append("platform admin + MFA + step-up")
            return
        if name == "require_platform_admin":
            found.append("platform admin + MFA session")
            return
        if name == "get_current_user":
            found.append("signed in")
            return
        for sub in dependant.dependencies:
            walk(sub)

    for dep in route.dependant.dependencies:
        walk(dep)
    return ", ".join(dict.fromkeys(x for x in found if x)) or "public"


def purpose(route: APIRoute) -> str:
    doc = " ".join((route.endpoint.__doc__ or "").split())
    first = doc.split(". ")[0].rstrip(".").split(": ")[0] if doc else ""
    return (first[:110] + "…" if len(first) > 110 else first) or route.name.replace("_", " ").capitalize()


def all_routes(routes):
    # Included routers are wrapped (FastAPI >= 0.140): unwrap to the original router's routes.
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from all_routes(original.routes)
        else:
            yield route


groups: dict[str, list] = {}
count = 0
for route in all_routes(app.routes):
    if not isinstance(route, APIRoute) or not route.include_in_schema:
        continue
    tag = (route.tags or ["System"])[0]
    for method in sorted(route.methods - {"HEAD"}):
        groups.setdefault(tag, []).append(f"| {method} | `{route.path}` | {access(route)} | {purpose(route)} |")
        count += 1
print(f"<!-- {count} endpoints -->")
for tag, rows in groups.items():
    print(f"\n## {tag}\n\n| Method | Path | Access | Purpose |\n|---|---|---|---|")
    print("\n".join(rows))
