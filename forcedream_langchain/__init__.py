"""ForceDream tools for LangChain.

Exposes ForceDream agents as LangChain tools, and — the part that does not exist
elsewhere — captures a cryptographic proof for every tool call, so a chain run
ends holding receipts you can verify offline without trusting ForceDream or
anyone else.

    from forcedream_langchain import ForceDreamToolkit, ProofCallbackHandler

    toolkit = ForceDreamToolkit(api_key="fd_live_...")
    handler = ProofCallbackHandler()
    agent_executor.invoke({"input": "..."}, config={"callbacks": [handler]})

    for p in handler.proofs:
        print(p.task_id, p.verified)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID

from forcedream import ForceDream
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    BaseCallbackHandler,
    CallbackManagerForToolRun,
)
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

__all__ = [
    "ForceDreamAgentTool",
    "ForceDreamToolkit",
    "ProofCallbackHandler",
    "ProofRecord",
    "VerifyProofTool",
]

DEFAULT_API_BASE = "https://api.forcedream.ai"


# ── proof capture ───────────────────────────────────────────────────────────

@dataclass
class ProofRecord:
    """One execution proof captured during a chain run.

    ``verified`` is None until :meth:`ProofCallbackHandler.verify_all` runs, and
    the check happens locally against the published Ed25519 key — ForceDream is
    never asked whether its own proof is valid.
    """

    task_id: str
    agent: str
    charged_pence: Optional[int] = None
    proof_id: Optional[str] = None
    verified: Optional[bool] = None
    detail: Optional[str] = None

    @property
    def verify_url(self) -> str:
        return f"{DEFAULT_API_BASE}/v1/workforce/proof/{self.task_id}/public"


class ProofCallbackHandler(BaseCallbackHandler):
    """Collects the execution proof emitted by each ForceDream tool call.

    LangChain has no verification story: a tool returns a string and the chain
    trusts it. Attaching this handler means every ForceDream step in the run
    leaves a signed receipt that a third party can check.
    """

    def __init__(self, api_base: str = DEFAULT_API_BASE) -> None:
        super().__init__()
        self.api_base = api_base
        self.proofs: List[ProofRecord] = []

    def record(self, rec: ProofRecord) -> None:
        self.proofs.append(rec)

    async def averify_all(self) -> List[ProofRecord]:
        """Verify every captured proof locally. Returns the same records, updated."""
        client = ForceDream(api_base=self.api_base)
        for rec in self.proofs:
            if not rec.task_id:
                rec.verified, rec.detail = False, "no task_id"
                continue
            try:
                result = await client.verify(task_id=rec.task_id)
                rec.verified = bool(getattr(result, "verified", result.get("verified") if isinstance(result, dict) else False))
                rec.detail = None if rec.verified else "signature check failed"
            except Exception as exc:  # verification must never raise into a chain
                rec.verified, rec.detail = False, f"{type(exc).__name__}: {exc}"
        return self.proofs

    def verify_all(self) -> List[ProofRecord]:
        """Synchronous wrapper. Use :meth:`averify_all` inside a running loop."""
        return asyncio.run(self.averify_all())

    @property
    def all_verified(self) -> bool:
        return bool(self.proofs) and all(p.verified for p in self.proofs)


# ── tools ───────────────────────────────────────────────────────────────────

class _TaskInput(BaseModel):
    task: str = Field(description="The task for the agent, in plain language.")


class ForceDreamAgentTool(BaseTool):
    """A single ForceDream agent, callable from a LangChain agent.

    Charge-on-success: a declined or failed execution costs nothing. Invocation
    is never retried internally, because a retry would risk double-charging — a
    timeout returns ``pending`` with a task_id to poll instead.
    """

    name: str
    description: str
    agent_slug: str
    api_key: Optional[str] = None
    api_base: str = DEFAULT_API_BASE
    max_wait_seconds: float = 60.0
    price_pence: Optional[int] = None
    proof_handler: Optional[ProofCallbackHandler] = None
    args_schema: type[BaseModel] = _TaskInput

    model_config = {"arbitrary_types_allowed": True}

    async def _arun(self, task: str, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        client = ForceDream(api_key=self.api_key, api_base=self.api_base)
        result = await client.invoke(self.agent_slug, task, max_wait_seconds=self.max_wait_seconds)

        data = result if isinstance(result, dict) else getattr(result, "__dict__", {})
        status = data.get("status") or getattr(result, "status", None)
        task_id = data.get("task_id") or getattr(result, "task_id", None)

        if self.proof_handler is not None and task_id:
            self.proof_handler.record(ProofRecord(
                task_id=str(task_id),
                agent=self.agent_slug,
                charged_pence=data.get("charged_pence"),
                proof_id=data.get("proof_id"),
            ))

        if status == "pending":
            # Honest rather than convenient: the work may still complete, and the
            # caller can poll. Pretending it failed would be wrong; pretending it
            # succeeded would be worse.
            return (f"Task accepted but not yet complete (task_id={task_id}). "
                    f"Poll {self.api_base}/v1/agents/{self.agent_slug}/result/{task_id}")
        if status and status != "completed":
            return f"Agent did not complete (status={status}). No charge was made."

        output = data.get("output") or getattr(result, "output", None)
        return str(output) if output is not None else str(result)

    def _run(self, task: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        return asyncio.run(self._arun(task))


class _VerifyInput(BaseModel):
    task_id: str = Field(description="A ForceDream task_id, e.g. wtask_4cbd33a4104e6a5b05c9")


class VerifyProofTool(BaseTool):
    """Verify any ForceDream execution proof. No API key, no account.

    The signature is checked in this process against the published key. Useful
    on its own: a chain can verify work performed by someone else's agent.
    """

    name: str = "forcedream_verify_proof"
    description: str = (
        "Cryptographically verify that a ForceDream agent execution happened as claimed. "
        "Input is a task_id. Checks an Ed25519 signature locally — no key or account needed."
    )
    api_base: str = DEFAULT_API_BASE
    args_schema: type[BaseModel] = _VerifyInput

    async def _arun(self, task_id: str, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        client = ForceDream(api_base=self.api_base)
        try:
            result = await client.verify(task_id=task_id)
        except Exception as exc:
            return f"Verification could not be completed: {type(exc).__name__}: {exc}"
        verified = getattr(result, "verified", result.get("verified") if isinstance(result, dict) else False)
        msg = getattr(result, "message", "") or (result.get("message", "") if isinstance(result, dict) else "")
        return f"verified={bool(verified)}. {msg}".strip()

    def _run(self, task_id: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        return asyncio.run(self._arun(task_id))


# ── toolkit ─────────────────────────────────────────────────────────────────

@dataclass
class ForceDreamToolkit:
    """Discovers live ForceDream agents and exposes them as LangChain tools.

    Discovery needs no API key — you can list and inspect agents, and verify
    proofs, before creating an account. A key is only required to invoke.
    """

    api_key: Optional[str] = None
    api_base: str = DEFAULT_API_BASE
    capability: Optional[str] = None
    max_wait_seconds: float = 60.0
    include_verify_tool: bool = True
    proof_handler: Optional[ProofCallbackHandler] = field(default=None)

    async def aget_tools(self, limit: Optional[int] = None) -> List[BaseTool]:
        client = ForceDream(api_base=self.api_base)
        found = await client.search_agents(capability=self.capability)
        agents = found.get("agents", []) if isinstance(found, dict) else getattr(found, "agents", [])

        tools: List[BaseTool] = []
        for a in agents[: limit or len(agents)]:
            slug = a.get("slug") if isinstance(a, dict) else getattr(a, "slug", None)
            if not slug:
                continue
            desc = (a.get("description") if isinstance(a, dict) else getattr(a, "description", "")) or ""
            price = a.get("price_per_call_pence") if isinstance(a, dict) else None
            caps = (a.get("capabilities") if isinstance(a, dict) else []) or []
            detail = f"{desc} Capabilities: {', '.join(map(str, caps))}." if caps else desc
            if price:
                detail += f" Costs {price}p per successful call; failures are free."
            tools.append(ForceDreamAgentTool(
                name=f"forcedream_{slug.replace('-', '_')}",
                description=detail.strip(),
                agent_slug=slug,
                api_key=self.api_key,
                api_base=self.api_base,
                max_wait_seconds=self.max_wait_seconds,
                price_pence=price,
                proof_handler=self.proof_handler,
            ))

        if self.include_verify_tool:
            tools.append(VerifyProofTool(api_base=self.api_base))
        return tools

    def get_tools(self, limit: Optional[int] = None) -> List[BaseTool]:
        return asyncio.run(self.aget_tools(limit=limit))
