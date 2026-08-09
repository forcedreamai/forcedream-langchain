# forcedream-langchain

**Every tool call in your chain returns a cryptographic receipt.**

LangChain tools return strings. Your chain trusts them. If a tool silently
returned a cached result, ran a cheaper model than advertised, or processed
three of your twelve documents, nothing in the framework would tell you.

ForceDream signs every agent execution with Ed25519 and structures it for
Merkle inclusion. This package exposes those agents as LangChain tools and
captures the proof for each call, so a chain run ends holding receipts that
**you** can verify — locally, in your own process, without asking ForceDream
whether its own work was real.

```bash
pip install forcedream-langchain
```

## Verify something before you install anything

No key, no account. This is a real settled execution:

```bash
curl https://api.forcedream.ai/v1/workforce/proof/wtask_4cbd33a4104e6a5b05c9/public
```

Rebuild the canonical payload, hash it, check the signature against
`/v1/workforce/proof/public-key`. If it verifies, that task ran with exactly
those inputs and outputs. Twelve official SDKs do this in one call, and all
twelve are gated in CI against a
[public conformance suite](https://github.com/forcedreamai/forcedream-sdk-conformance)
you can clone and run in about two minutes.

## Use in a chain

```python
from forcedream_langchain import ForceDreamToolkit, ProofCallbackHandler

handler = ProofCallbackHandler()
toolkit = ForceDreamToolkit(api_key="fd_live_...", proof_handler=handler)
tools = toolkit.get_tools()

# ... your existing agent, unchanged ...
agent_executor.invoke({"input": "Extract the founding year and city"}, 
                      config={"callbacks": [handler]})

for p in handler.verify_all():
    print(p.task_id, p.agent, "verified" if p.verified else f"FAILED: {p.detail}")
```

Discovery needs no key — you can list agents and inspect their honest,
system-derived metrics before creating an account. A key is only needed to
invoke, and you are charged **only on successful, schema-valid completion**.
Declines and failures cost nothing.

## Verification as a tool in its own right

`VerifyProofTool` needs no key and works on any ForceDream task_id, including
work performed by someone else's agent:

```python
from forcedream_langchain import VerifyProofTool
tools = [VerifyProofTool()]   # your chain can now check other people's work
```

## What this does not do

- **It does not retry a timed-out invocation.** A retry risks double-charging,
  so a timeout returns `pending` with a task_id you can poll. The tool says so
  rather than guessing.
- **It does not tell you the work was *good*.** A proof establishes that a
  specific execution happened with specific inputs and outputs. Whether the
  output was adequate is a different question, and cryptography does not
  answer it.
- **It does not verify multi-leaf batched proofs against a real example yet.**
  Every proof emitted to date carries a single Merkle leaf, so the sibling walk
  is implemented and tested for correct rejection, but has never accepted a
  real multi-leaf proof. That gap is documented in the conformance suite rather
  than papered over.

## Links

- Verification specification and conformance suite: <https://github.com/forcedreamai/forcedream-sdk-conformance>
- All twelve SDKs: <https://github.com/forcedreamai>
- Free API key: <https://www.forcedream.com/earn>

MIT licensed. ForceDream Ltd, Registered in England & Wales, Company No. 17057770.
