import base64
import contextvars
import hashlib
import hmac
import json
import subprocess
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

# Stdio MCP has no Authorization header. A real HTTP serve reads the bearer
# from the library auth context. The lesson run sets this for the same check.
_incoming_bearer: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "incoming_bearer", default=None
)

RESOURCE = "https://mcp.broker.example"
SIGNING_KEY = b"lesson-broker-key"
SERVICE_CREDENTIAL = "back-office"
ORDERS_PLACE_UNTIL = date(2026, 4, 1)
NOTES_DIR = Path(__file__).with_name("notes")

TOOL_SCOPES = {
    "get_holdings": "holdings:read",
    "get_quote": "quotes:read",
    "get_contract_note": "holdings:read",
    "place_order": "orders:place",
}
SCOPE_ENDS = {"orders:place": ORDERS_PLACE_UNTIL}

QUOTES = {"RELIANCE": 2905.50, "INFY": 1480.00, "TCS": 3105.00, "HDFCBANK": 1650.00}
INSTRUMENTS = set(QUOTES)
MAX_ORDER_VALUE = 500_000
PRICE_BAND = 0.05

BOOKS = {
    "ravi": {
        "INFY": {
            "quantity": 40,
            "average_price": 1350.00,
            "pan": "ABCDE1234F",
            "demat": "IN30000111111111",
            "bank": "50100012345678",
        },
        "TCS": {
            "quantity": 5,
            "average_price": 3400.00,
            "pan": "ABCDE1234F",
            "demat": "IN30000111111111",
            "bank": "50100012345678",
        },
    },
    "mallory": {
        "RELIANCE": {
            "quantity": 2,
            "average_price": 2700.00,
            "pan": "ZZZZZ9999Z",
            "demat": "IN30000999999999",
            "bank": "50100099999999",
        },
    },
}

APPROVED_SERVERS = {
    "market-data": {
        "owner": "market data",
        "version": "1.2.0",
        "reaches": "quotes",
    },
}


class AuthError(Exception):
    pass


class Claims(BaseModel):
    model_config = ConfigDict(frozen=True)
    sub: str
    client_id: str
    audience: str
    expires_on: date
    scopes: frozenset[str]


class Order(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    stock: str
    buy_or_sell: Literal["buy", "sell"]
    quantity: int
    order_type: Literal["market", "limit"]
    price: float


class Audit(BaseModel):
    model_config = ConfigDict(frozen=True)
    user: str
    client_id: str
    tool: str
    args_summary: str
    result_size: int


AUDIT: list[Audit] = []
PENDING: dict[str, Order] = {}
PLACED: list[tuple[str, Order]] = []
SERVICE_CALLS: list[str] = []

mcp = MCPServer("Broker")
_day = TypeAdapter(date)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(
    *,
    sub: str,
    client_id: str,
    audience: str = RESOURCE,
    expires_on: date,
    scopes: set[str],
) -> str:
    payload = {
        "aud": audience,
        "client_id": client_id,
        "exp": expires_on.isoformat(),
        "scopes": sorted(scopes),
        "sub": sub,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(SIGNING_KEY, body, hashlib.sha256).digest()
    return _b64(body) + "." + _b64(sig)


def verify_bearer(token: str, today: date) -> Claims:
    parts = token.split(".")
    if len(parts) != 2:
        raise AuthError("token is not valid")
    try:
        body = _b64d(parts[0])
        sig = _b64d(parts[1])
    except Exception as exc:
        raise AuthError("token is not valid") from exc
    expected = hmac.new(SIGNING_KEY, body, hashlib.sha256).digest()
    if len(sig) != len(expected) or not hmac.compare_digest(sig, expected):
        raise AuthError("token is not valid")
    try:
        payload = json.loads(body)
        claims = Claims(
            sub=payload["sub"],
            client_id=payload["client_id"],
            audience=payload["aud"],
            expires_on=date.fromisoformat(payload["exp"]),
            scopes=frozenset(payload["scopes"]),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise AuthError("token is not valid") from exc
    if claims.audience != RESOURCE:
        raise AuthError("token is for a different server")
    if today > claims.expires_on:
        raise AuthError("token is expired")
    return claims


def verified_claims(today: date) -> Claims:
    raw = _incoming_bearer.get()
    access = get_access_token()
    if access is not None:
        raw = access.token
    if not raw:
        raise AuthError("missing token")
    return verify_bearer(raw, today)


def caller_from(bearer: str | None, session_id: str, today: date) -> Claims:
    if not bearer:
        raise AuthError("a session id is not a caller")
    return verify_bearer(bearer, today)


@contextmanager
def signed_in(token: str):
    marker = _incoming_bearer.set(token)
    try:
        yield
    finally:
        _incoming_bearer.reset(marker)


def scope_block(claims: Claims, scope: str, today: date) -> str | None:
    ended = SCOPE_ENDS.get(scope)
    if ended is not None and today >= ended:
        return f"scope {scope} ended on {ended.isoformat()}"
    if scope not in claims.scopes:
        return f"missing scope {scope}"
    return None


def _summarize(arguments: dict[str, Any]) -> str:
    shown = {key: value for key, value in arguments.items() if key != "token"}
    return json.dumps(shown, default=str, sort_keys=True)


def _record(user: str, client_id: str, tool: str, arguments: dict[str, Any], result: object) -> None:
    encoded = json.dumps(result, default=str)
    AUDIT.append(
        Audit(
            user=user,
            client_id=client_id,
            tool=tool,
            args_summary=_summarize(arguments),
            result_size=len(encoded),
        )
    )


def holdings_for(sub: str) -> dict[str, dict[str, object]]:
    SERVICE_CALLS.append(SERVICE_CREDENTIAL)
    return BOOKS.get(sub, {})


def public_holdings(book: dict[str, dict[str, object]]) -> dict[str, int]:
    return {symbol: int(row["quantity"]) for symbol, row in book.items()}


def check_order(order: Order, sub: str) -> list[str]:
    problems: list[str] = []
    if order.stock not in INSTRUMENTS:
        problems.append(f"unknown symbol {order.stock}")
    if order.quantity <= 0:
        problems.append("quantity must be above zero")
    if order.order_type != "limit":
        problems.append("only limit orders are accepted")
    if order.price <= 0:
        problems.append("price must be above zero")
    if order.quantity * order.price > MAX_ORDER_VALUE:
        problems.append(f"order value is above the limit of ₹{MAX_ORDER_VALUE:,}")
    live = QUOTES.get(order.stock)
    if live is not None and abs(order.price - live) > live * PRICE_BAND:
        problems.append("price is outside the 5% band")
    if order.buy_or_sell == "sell":
        book = holdings_for(sub)
        owned = int(book.get(order.stock, {}).get("quantity", 0))
        if order.quantity > owned:
            problems.append(f"{sub} holds {owned} {order.stock}")
    return problems


def contract_note_path(sub: str, day: date) -> Path:
    if sub in {"", ".", ".."} or "/" in sub or "\\" in sub:
        raise AuthError("bad investor")
    return NOTES_DIR / sub / f"{day.isoformat()}.pdf"


def assert_inside(path: Path, sub: str) -> Path:
    root = (NOTES_DIR / sub).resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise AuthError("contract note path left the investor's notes")
    return resolved


def read_contract_note(sub: str, day: date) -> str:
    kept = assert_inside(contract_note_path(sub, day), sub)
    if not kept.is_file():
        raise AuthError("no contract note for that day")
    completed = subprocess.run(
        [sys.executable, "-c", "import sys; sys.stdout.write(open(sys.argv[1]).read())", str(kept)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AuthError("no contract note for that day")
    return completed.stdout


def require_approved(name: str) -> dict[str, str]:
    entry = APPROVED_SERVERS.get(name)
    if entry is None:
        raise AuthError(f"server {name} is not approved")
    return entry


def _get_holdings(claims: Claims, arguments: dict[str, Any]) -> dict[str, int]:
    return public_holdings(holdings_for(claims.sub))


def _get_quote(claims: Claims, arguments: dict[str, Any]) -> float | None:
    return QUOTES.get(str(arguments["stock"]))


def _get_contract_note(claims: Claims, arguments: dict[str, Any]) -> str:
    day = _day.validate_python(arguments["day"])
    return read_contract_note(claims.sub, day)


def _place_order(claims: Claims, arguments: dict[str, Any]) -> str:
    order = Order.model_validate(arguments)
    problems = check_order(order, claims.sub)
    if problems:
        return "Rejected: " + "; ".join(problems)
    PENDING[claims.sub] = order
    return "not placed: confirm this order in the broker app"


HANDLERS = {
    "get_holdings": _get_holdings,
    "get_quote": _get_quote,
    "get_contract_note": _get_contract_note,
    "place_order": _place_order,
}


def invoke(name: str, arguments: dict[str, Any], today: date | None = None):
    day = date.today() if today is None else today
    scope = TOOL_SCOPES[name]
    try:
        claims = verified_claims(day)
    except AuthError as exc:
        _record("-", "-", name, arguments, {"error": str(exc)})
        raise
    block = scope_block(claims, scope, day)
    if block is not None:
        _record(claims.sub, claims.client_id, name, arguments, {"error": block})
        raise AuthError(block)
    try:
        result = HANDLERS[name](claims, arguments)
    except ValidationError:
        _record(claims.sub, claims.client_id, name, arguments, {"error": "invalid arguments"})
        raise
    _record(claims.sub, claims.client_id, name, arguments, result)
    return result


def broker_app_confirm(sub: str, answer: str) -> str:
    order = PENDING.get(sub)
    if order is None:
        return "not placed: no order is waiting"
    if answer.strip().lower() != "yes":
        return "not placed"
    problems = check_order(order, sub)
    if problems:
        return "Rejected: " + "; ".join(problems)
    PLACED.append((sub, order))
    del PENDING[sub]
    return "PLACED"


@mcp.tool()
def get_holdings() -> dict[str, int]:
    """Holdings of the signed-in investor. Stock and quantity only."""
    return invoke("get_holdings", {})


@mcp.tool()
def get_quote(stock: str) -> float | None:
    """Last traded price of an NSE stock, such as RELIANCE."""
    return invoke("get_quote", {"stock": stock})


@mcp.tool()
def get_contract_note(day: date) -> str:
    """Text of the investor's contract note for one trading day, such as 2026-10-15."""
    return invoke("get_contract_note", {"day": day})


@mcp.tool()
def place_order(
    stock: str,
    buy_or_sell: Literal["buy", "sell"],
    quantity: int,
    order_type: Literal["market", "limit"],
    price: float,
) -> str:
    """Ask to place one limit order. The broker app must confirm it."""
    return invoke(
        "place_order",
        {
            "stock": stock,
            "buy_or_sell": buy_or_sell,
            "quantity": quantity,
            "order_type": order_type,
            "price": price,
        },
    )


def require_bindings() -> None:
    registered = {tool.name for tool in mcp._tool_manager.list_tools()}
    scoped = set(TOOL_SCOPES)
    handled = set(HANDLERS)
    if scoped != handled or scoped != registered:
        raise RuntimeError(
            "tool bindings differ: "
            f"scopes {sorted(scoped)}, handlers {sorted(handled)}, registered {sorted(registered)}"
        )


require_bindings()


if __name__ == "__main__":
    mcp.run()
