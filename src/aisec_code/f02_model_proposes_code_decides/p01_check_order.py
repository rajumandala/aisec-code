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


INSTRUMENTS = {"RELIANCE", "INFY", "TCS", "HDFCBANK"}
MAX_ORDER_VALUE = 500000


def check_order(order: Order) -> list[str]:
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
    return problems


instructions = (
    "You are an order assistant for an Indian broker that reads the investor's message "
    "and states the order: stock, buy or sell, quantity, order type and price. "
    "Write the stock as its NSE symbol, such as RELIANCE, INFY, TCS, or HDFCBANK."
)

prompts = (
    "buy 10 Reliance at 2900 limit",
    "buy Reliance worth 50000",
    "sell all my Infosys",
    "buy 1000 TCS at 3100 limit",
)

client = OpenAI()
for prompt in prompts:
    response = client.responses.parse(
        model="gpt-6-luna",
        instructions=instructions,
        input=prompt,
        text_format=Order,
    )
    order = response.output_parsed
    if order is None:
        print("Rejected: model returned no order")
        continue
    problems = check_order(order)
    if problems:
        print("Rejected: " + "; ".join(problems))
    else:
        print("Accepted")
