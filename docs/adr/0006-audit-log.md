# 6. Append-only, hash-chained, anchored audit log

Status: accepted, 2026-10-17

## Context

Regulated teams must show what the gateway decided, why, and under which policy version, and that the record has not been changed since. Database permissions alone do not stop an administrator.

## Decision

- One `audit_events` table, written through a login role that has only `SELECT` and `INSERT`.
- Triggers reject `UPDATE`, `DELETE` and `TRUNCATE`, even by the owner.
- Each event stores the previous event's hash and its own, over canonical JSON; writers take an advisory lock so concurrent instances keep one chain.
- Hourly, a job verifies the chain against the newest anchor and only then writes a new anchor (head sequence and hash) to a bucket with a retention policy.
- Events carry the questions and thresholds versions, the decider and model, fallbacks, answers and the outcome.

## Consequences

- Edits and deletions are blocked; an owner who drops the triggers can still change rows, but verification finds the break, and truncation past the last anchor is caught.
- Retention is not implemented in v0.1. The plan is monthly partitions exported with their anchor, then detached.
- The anchor bucket's retention lock is irreversible, so staging leaves it unlocked.
