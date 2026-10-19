from mcp.server import MCPServer

QUOTES = {"RELIANCE": 2905.50, "INFY": 1480.00, "TCS": 3105.00, "HDFCBANK": 1650.00}

mcp = MCPServer("Market Data Vendor")


@mcp.tool()
def get_quote(stock: str) -> float | None:
    """Get the last traded price of an NSE stock, such as RELIANCE."""
    return QUOTES.get(stock)


if __name__ == "__main__":
    mcp.run()
