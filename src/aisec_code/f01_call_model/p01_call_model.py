from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

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
    response = client.responses.create(
        model="gpt-6-luna",
        instructions=instructions,
        input=prompt,
    )
    print(prompt)
    print(response.output_text)
