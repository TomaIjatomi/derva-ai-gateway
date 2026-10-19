# 5. Client references before masking

Status: accepted, 2026-10-17; amended 2026-10-19 (denied references logged, existence signal recorded)

## Context

Requests name clients. Masking names to `<PERSON_1>` hides them but loses which client is meant, so tools cannot act and the audit trail cannot say whose data was involved. Sending names to models and logs is what data protection rules discourage.

## Decision

- Before masking, resolve client names against the CRM, scoped to the caller's teams: one match becomes a reference (`client:C-1042`). Several matches get a question back. A client outside the caller's teams gets a refusal that does not repeat the name.
- Masking runs after resolution and leaves references alone.
- The model receives the resolved text, so tools receive references. The audit log stores masked text, counts and hashes, never names or the mask mapping.
- A refused request keeps the out-of-scope client's reference in its `resolved` event (`denied_references`), never the name. Compliance can then see whose data a refused request was after.

## Consequences

- Names stay out of model input and logs for every resolvable client; a test scans the whole audit log for names, emails and IBANs.
- Resolution is only as good as the directory and its aliases. Names it does not know fall to Presidio, which masks them without knowing who they are.
- Older turns whose names do not resolve cleanly are forwarded unchanged. The incoming turn, which is what the checks judge, always resolved.
- The behaviour reveals who is a client. A name that is a client in another team is refused, while a name that is not a client at all goes through, masked. A caller could probe whether someone banks with the firm. This is accepted for v0.1: callers are signed-in staff, and every refusal is logged with the client's reference, so probing is visible. A stricter firm could refuse any unresolved person name instead, at the cost of blocking ordinary requests that mention non-clients.
