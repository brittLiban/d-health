"""DH-CONNECT-AGENT-001 §70–§79 — the on-premises DHealth Connect agent.

Deliberately **not** inside ``exchange/``. The Exchange is the central service DHealth operates;
this is a process that runs on a partner's own hardware, inside their network, next to their LIS.
Mixing them would eventually put a central-database import into a daemon that has no access to one
-- and would make it far too easy for an agent-side convenience to reach into clinical repositories
it must never touch.

It is also not inside ``connect/``: that package is the contract and the server's side of it. The
boundary between the two is the HTTP runtime API and nothing else, so the agent imports
``connect.contract`` for message shapes and imports nothing else from the server side.

**What this agent is not.** It is not a FHIR client, an HL7 parser, an MLLP listener, an SFTP poller
or a vendor adapter. §1 and §3 forbid all of those in this ticket, and the design honours that
structurally: everything vendor-specific sits behind the ``ConnectorAdapter`` protocol, and the only
implementation that ships here is synthetic. When a real partner adapter is written
(DH-CONNECT-ADAPTER-001), it implements that protocol and this runtime does not change.

**What makes it durable.** A local SQLite database -- stdlib ``sqlite3``, no server, no partner
PostgreSQL requirement (§26). Inbox rows are persisted *before* they are acknowledged and before
they reach the adapter, so a crash at any point leaves work to be redone rather than lost. The
outbox is the same story in reverse.

Synchronous on purpose. Every interesting property here is about what survives a process death
mid-sequence, and the tests that prove it need to interrupt the sequence at an exact point. Async
would have bought concurrency this workload does not need and made those tests considerably harder
to believe.
"""

#: The agent's own version, reported on every heartbeat and deliberately **separate** from both the
#: adapter distribution's ``__version__`` and the Connect contract version (§50).  Three numbers
#: that move for three different reasons: the contract changes when what a message *means* changes,
#: the runtime protocol changes when these four endpoints change shape, and this changes when the
#: agent binary does. An agent upgraded on a partner's server is an operational fact DHealth needs
#: to see -- "which build is running at Laboratoire Central" is the first question when one facility
#: behaves unlike the others -- and it is not a reason to requalify anything.
AGENT_VERSION = "0.1.0"

__all__ = ["AGENT_VERSION"]
