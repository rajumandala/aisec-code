import asyncio
import importlib.util
import subprocess
import tempfile
from pathlib import Path

from mcp import Client
from mcp.server import MCPServer

_path = Path(__file__).with_name("mcp_server_broker.py")
_spec = importlib.util.spec_from_file_location("mcp_server_broker", _path)
broker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(broker)

# The quick first version from note 06a, Step 3. The notes are text files here,
# so it uses cat instead of pdftotext; the shell is the same.
quick = MCPServer("Broker, first draft")
NOTES = Path()


@quick.tool()
def get_contract_note(date: str) -> str:
    """Text of the investor's contract note for a trading day, such as 2026-10-15."""
    sub = "ravi"                                           # the signed-in investor
    path = f"{NOTES}/{sub}/{date}.txt"
    return subprocess.run(f"cat {path}", shell=True, capture_output=True, text=True).stdout


def text_of(result) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


async def ask(server, date: str) -> tuple[bool, str]:
    async with Client(server) as client:
        result = await client.call_tool("get_contract_note", {"date" if server is quick else "day": date})
    return result.is_error, text_of(result)


async def main() -> None:
    global NOTES
    with tempfile.TemporaryDirectory() as tmp:
        NOTES = Path(tmp)
        for sub in ("ravi", "asha"):
            (NOTES / sub).mkdir()
        (NOTES / "ravi" / "2026-10-15.txt").write_text("Ravi: bought 10 INFY at 1480\n")
        (NOTES / "asha" / "2026-10-15.txt").write_text("Asha: sold 200 HDFCBANK at 1650\n")
        ran = NOTES / "ran.txt"
        # Mallory's post sends secrets out with curl. Here the second command only writes `id` to a temp file.
        shell_date = f"2026-10-15 ; id > {ran} ;"
        walk_up = "../asha/2026-10-15"

        print("== First draft: shell=True and a string date ==")
        error, text = await ask(quick, shell_date)
        print(f"shell string -> error={error}, second command ran: {ran.exists()}")
        if ran.exists():
            print(f"   it wrote: {ran.read_text().split()[0]}")
        error, text = await ask(quick, walk_up)
        print(f"../asha path -> error={error}, returned: {text.strip()!r}")

        ran.unlink(missing_ok=True)
        broker.NOTES_DIR = NOTES
        for sub in ("ravi", "asha"):
            (NOTES / sub / "2026-10-15.pdf").write_text((NOTES / sub / "2026-10-15.txt").read_text())
        ravi = broker.mint(sub="ravi", client_id="ravi-app", expires_on=broker.date(2099, 1, 1), scopes={"holdings:read"})

        print("\n== Fixed server: typed date, path built by code, no shell ==")
        with broker.signed_in(ravi):
            for label, value in (("real date", "2026-10-15"), ("shell string", shell_date), ("../asha path", walk_up)):
                error, text = await ask(broker.mcp, value)
                print(f"{label} -> error={error}, returned: {text.strip()[:90]!r}")
        print(f"second command ran: {ran.exists()}")


if __name__ == "__main__":
    asyncio.run(main())
