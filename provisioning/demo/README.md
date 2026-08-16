# Two-container transport demonstration (ADR-022)

**Performed 2026-08-16.** Upgrades the pull transport from *written* to *demonstrated* — but
read §4 before treating that as complete, because two claimed properties are still untested.

## Why it exists

The unit tests exercise the poll loop against an **injected** transport and executor. That
proves the loop's logic, and proves nothing about the claim ADR-022 actually rests on: *the
agent polls a separate host over a network and refuses hostile input over the wire*. An ADR
asserting a security property on the strength of a mocked transport is a claim resting on
"should work".

```sh
docker compose -f provisioning/demo/docker-compose.demo.yml up -d
docker compose -f provisioning/demo/docker-compose.demo.yml exec -T api \
  python /app/demo/harness_hostile.py
```

Two containers on a real bridge network. `api` runs the **real** `create_app`; `agent` runs
the **real** poller, validator and HTTP transport. Only the executor is substituted, so no
clinical stack is started.

The harness includes `/demo/enqueue-raw`, which queues **arbitrary payloads** — including
ones the typed API would refuse to build. That is the point: ADR-022's residual risk is *a
compromised API enqueues hostile parameters*, so the demonstration must be able to act like
one. Without it the demo would only prove the agent accepts jobs the API already validated.

## 1. What was demonstrated

| Property | Result |
|---|---|
| Agent polls a separate container over a real socket | ✅ authenticated `200 OK` polling |
| Only a legitimate job reaches the executor | ✅ 1 of 7 executed |
| Hostile payloads refused **over the wire** | ✅ 6 of 6 |
| Agent's **own** host check fires | ✅ refused a misrouted job with its own reason |
| Agent exposes no inbound port | ✅ all probes to 22/80/443/8000/8080/9000 refused |
| "Held, not deleted" across **SIGKILL** | ✅ job stayed `claimed`, never re-handed-out |
| Agent survives process death and resumes | ✅ resumed polling after `docker kill -9` |
| Audit chain verifies after a real session | ✅ 14 entries, 7 claims + 7 reports |

## 2. Three defects the unit tests could not find

**a. The agent's own host check was never exercised.** The first run showed 5 of 6 refusals,
with the misrouted job absent — the *queue* had routed it away, so the agent never saw it.
The demo would have passed while silently never testing that refusal. Fixed by adding
`queue_as`, which decouples *which host's queue a job lands in* from *which host the job
claims to be for* — exactly what a compromised or buggy issuer would do.

**b. A refused misrouted job could not be reported.** The report endpoint authorised on the
job's **self-declared** `host_id` — a field a compromised issuer controls. So the agent
refused hostile work, tried to report it, got `403`, and the API recorded nothing. The most
security-relevant event in the system was invisible. Fixed: authorisation now uses
`delivered_to`, recorded by the queue at claim time.

**c. The audit call could fail *after* the state change.** `queue.finish()` ran, then the
audit call raised while reaching into a field, leaving a job finished with no record of it.
An audit that can fail after the thing it records has already happened is worse than none,
because the gap is invisible. Fixed: read what the audit needs **before** mutating state,
and read it defensively.

None of these were reachable from unit tests, because those inject a reporter that always
succeeds and a queue that only ever holds well-formed jobs.

## 3. Why each refusal happened — the part worth scrutinising

Checking *that* something was refused is nearly worthless; the question is **why**. The agent
records its refusal reason and class, and the results split:

| Payload | Refused by |
|---|---|
| `daher-bc evil` (space) | schema |
| `daher-bc/../../etc` | schema |
| extra top-level `volumes` key | schema |
| `base_domain: evil$(id)` | schema |
| `Daher-BC` (wrong case) | schema |
| addressed to `host-zzz` | **the agent's own host check** |

⚠️ **Five of six were caught by schema validation, not by the agent's deeper local checks.**
`revalidate_params` and the forbidden-character filter did not fire, because the typed
patterns reject those payloads first. That is correct layering — but it means the second
layer is currently proven only by unit tests that bypass the schema with `model_construct`.
It is defence in depth for the day someone loosens a pattern, and this demonstration does
not exercise it. Saying "the agent's validation refused hostile input" without that
distinction would overstate what was shown.

## 3a. TLS — demonstrated 2026-08-16

The demo now runs over HTTPS with a private CA. The agent verifies; it does not skip.

```sh
docker compose -f provisioning/demo/docker-compose.demo.yml exec -T agent \
  python /app/demo/harness_tls.py
```

| Control | Expected | Result |
|---|---|---|
| Real CA, correct hostname | succeed | ✅ `HTTP 200` |
| **Bogus CA** (unrelated issuer) | refuse | ✅ `CERTIFICATE_VERIFY_FAILED: unable to get local issuer` |
| **Real CA, wrong hostname** (IP; cert is `SAN:api`) | refuse | ✅ `CERTIFICATE_VERIFY_FAILED: IP address mismatch` |
| System trust store only | refuse | ✅ `unable to get local issuer` |

The controls are the test. Presenting only the correct certificate would pass identically
with `verify=False`, and would prove nothing.

### Two things the TLS leg caught that plain HTTP could not

**a. The CA lacked `keyUsage`.** OpenSSL 3 refused it outright — *"CA cert does not include
key usage extension"*. A CA generated without `basicConstraints` **and** `keyUsage` is not a
usable signing CA, and nothing in a plain-HTTP path would ever have said so.

**b. A control that passed for the wrong reason.** The hostname-mismatch case first used
`127.0.0.1:8443`, which has nothing listening inside the agent container. It "passed" with
`ConnectionRefused` — never reaching TLS at all. A control passing for an unrelated reason is
worse than no control, because it reads as evidence. It now connects to the real listener by
IP, where the certificate carries `SAN:api` only, and fails with a genuine
`IP address mismatch`.

**c. A harness that could not reach the system produced a convincing empty result.** After the
switch to HTTPS the hostile harness still pointed at `http://localhost:8000`, silently
enqueued nothing, and the run looked exactly like "the agent refused everything" — 2 audit
entries instead of 14. Worth remembering: an empty result is not a passing result.

## 4. What is still NOT demonstrated

- **Partial or truncated reads.** No test delivered a job payload split across TCP segments
  or truncated mid-body. The code assumes `httpx` reassembles before `.json()` returns,
  which is its documented contract but remains an assumption here.
- **The real executor.** The demo substitutes it. Nothing has ever started a clinical stack
  through this path, and the compose-invocation seam — the last place an argument could
  reach a command boundary — is unwritten.

Those two are why the transport is still **partially** demonstrated. The network boundary,
the refusal path, crash behaviour, the audit trail and now the encrypted channel with real
certificate and hostname verification are shown; the parsing edge and the execution seam are
not.
