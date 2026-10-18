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


instructions = (
    "You are an order assistant for an Indian broker that reads the investor's message "
    "and states the order: stock, buy or sell, quantity, order type and price."
)

prompts = (
    "buy 10 Reliance at 2900 limit",
    "buy Reliance worth 50000",
    "sell all my Infosys",
)

client = OpenAI()
for prompt in prompts:
    response = client.responses.parse(
        model="gpt-6-luna",
        instructions=instructions,
        input=prompt,
        text_format=Order,
    )
    print(prompt)
    print(response.output_parsed)
