"""Verify a real ForceDream proof. No API key, no account.

    python examples/verify_only.py

This is the shortest path to seeing what the package does: it checks an
Ed25519 signature locally against ForceDream's published key. ForceDream is
never asked whether its own proof is valid -- the maths decides, here.
"""
import asyncio
from forcedream_langchain import VerifyProofTool

REAL_TASK = "wtask_4cbd33a4104e6a5b05c9"   # a real, settled execution


async def main() -> None:
    tool = VerifyProofTool()
    print(await tool._arun(REAL_TASK))

    # A task_id that does not exist should fail cleanly, never raise.
    print(await tool._arun("wtask_definitely_not_real"))


if __name__ == "__main__":
    asyncio.run(main())
