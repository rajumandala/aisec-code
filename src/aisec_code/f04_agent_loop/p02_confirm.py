import json
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()


class Order(BaseModel):
    stock: str
    buy_or_sell: Literal["buy", "sell"]
    quantity: int
    order_type: Literal["market", "limit"]
    price: float


class Orders(BaseModel):
    orders: list[Order]


INSTRUMENTS = {"RELIANCE", "INFY", "TCS", "HDFCBANK"}
MAX_ORDER_VALUE = 500000
PRICE_BAND = 0.05
MAX_STEPS = 5

QUOTES = {"RELIANCE": 2905.50, "INFY": 1480.00, "TCS": 3105.00, "HDFCBANK": 1650.00}
HOLDINGS = {
    "ravi": {
        "INFY": {"quantity": 40, "average_price": 1350.00},
        "TCS": {"quantity": 5, "average_price": 3400.00},
    },
    "mallory": {
        "RELIANCE": {"quantity": 2, "average_price": 2700.00},
    },
}
PENDING_SELLS: dict[tuple[str, str], int] = {}

MODEL = "gpt-6-luna"


def get_quote(stock: str) -> float | None:
    return QUOTES.get(stock)


def get_holding(client_name: str) -> dict:
    return HOLDINGS.get(client_name.lower(), {})


def check_order(order: Order, client_name: str) -> list[str]:
    problems = []
    if order.stock not in INSTRUMENTS:
        problems.append(f"{order.stock} is not in the instrument master")
    if order.quantity <= 0:
        problems.append("quantity must be more than 0")
    if order.order_type != "limit":
        problems.append("the assistant may only create limit orders")
    if order.price <= 0:
        problems.append("price must be more than 0")
    if order.quantity * order.price > MAX_ORDER_VALUE:
        problems.append(f"order value is above the limit of ₹{MAX_ORDER_VALUE:,}")
    live_price = get_quote(order.stock)
    if live_price is not None and abs(order.price - live_price) > live_price * PRICE_BAND:
        problems.append(
            f"price {order.price} is more than {PRICE_BAND:.0%} away from the live price {live_price}"
        )
    if order.buy_or_sell == "sell":
        owned = get_holding(client_name).get(order.stock, {}).get("quantity", 0)
        available = owned - PENDING_SELLS.get((client_name, order.stock), 0)
        if order.quantity > available:
            problems.append(f"{client_name} can sell only {available} {order.stock}")
    return problems


def confirm(order: Order) -> bool:
    question = (
        f"Place {order.buy_or_sell.upper()} {order.quantity} {order.stock} "
        f"at ₹{order.price:,.2f} {order.order_type}? (yes/no) "
    )
    return input("  " + question).strip().lower() == "yes"


def place_order(order: Order, client_name: str) -> None:
    if order.buy_or_sell == "sell":
        key = (client_name, order.stock)
        PENDING_SELLS[key] = PENDING_SELLS.get(key, 0) + order.quantity
    print("  PLACED", order.model_dump_json())


tools = [
    {
        "type": "function",
        "name": "get_quote",
        "description": "Get the last traded price of an NSE stock.",
        "parameters": {
            "type": "object",
            "properties": {
                "stock": {"type": "string", "description": "NSE trading symbol, such as RELIANCE"}
            },
            "required": ["stock"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_holding",
        "description": (
            "Get the logged-in client's share holdings. Takes no arguments. "
            "Each holding has quantity and average_price."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

instructions = (
    "You are an order assistant for an Indian broker. "
    "State each order as stock, buy or sell, quantity, order type, and price. "
    "Write the stock as its NSE symbol, such as RELIANCE, INFY, TCS, or HDFCBANK. "
    "Use only limit orders. "
    "If the investor says my shares or sell all, call get_holding. "
    "get_holding takes no arguments and returns only the logged-in client's holdings. "
    "Do not invent a holding. If the client does not hold that stock, return no orders. "
    "To book profit, sell the full quantity of every holding whose quote is above its average price."
)


def call_model(prompt: str, client_name: str) -> Orders | None:
    messages: list = [{"role": "user", "content": prompt}]
    for step in range(1, MAX_STEPS + 1):
        response = client.responses.parse(
            model=MODEL,
            instructions=instructions,
            input=messages,
            tools=tools,
            text_format=Orders,
        )
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            return response.output_parsed
        messages += response.output
        for call in calls:
            print(f"  step {step}: {call.name}({call.arguments})")
            if call.name == "get_quote":
                args = json.loads(call.arguments)
                result = get_quote(args["stock"])
            elif call.name == "get_holding":
                result = get_holding(client_name)
            else:
                result = "unknown tool"
            messages.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result),
                }
            )
    return None


def main() -> None:
    client_name = "ravi"
    prompts = (
        "book profit on all my gainers",
        "sell all my Infosys",
    )
    for prompt in prompts:
        print(prompt)
        print(f"logged in: {client_name}")
        result = call_model(prompt, client_name)
        if result is None or not result.orders:
            print("No order")
            continue
        for order in result.orders:
            print(order)
            problems = check_order(order, client_name)
            if problems:
                print("Rejected: " + "; ".join(problems))
            elif confirm(order):
                place_order(order, client_name)
            else:
                print("  Not placed")


client = OpenAI()

if __name__ == "__main__":
    main()
