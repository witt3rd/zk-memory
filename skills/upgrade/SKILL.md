---
name: upgrade
description: >-
  Upgrade an already-inhabited repo to the latest dotagent. Covers what changes between
  versions, the upgrade procedure (copy latest CLI + dispatch hook, clean deprecated artifacts,
  merge new skills), verification, and self-upgrade. Use when a dotagent-inhabited repo has an
  outdated scripts/agent or dispatch hook, when new guardrails are added, when STATE.md needs
  to be removed (S-event migration), or when asked to "upgrade dotagent" on a repo. Pairs with
  inhabit (which establishes); upgrade maintains.
metadata:
  aliases: [self-upgrade, update-dotagent]
  deps: [signalling, caretaker]
---

# Upgrade — maintain an inhabited repo at the latest dotagent

`inhabit` establishes. `upgrade` maintains. When dotagent evolves — new guardrails, new event
types, new prompt — existing inhabited repos need the latest tooling. This skill is the
procedure for bringing them current.

## When to upgrade

A repo needs upgrading when any of these hold:

- **`scripts/agent` is outdated** — missing new event types (S events), missing commands
  (claim), or missing the STATE.md removal.
- **Dispatch hook is outdated** — missing guardrails (self-dispatch filter, chaining cap,
  rate limiting, bounded epoch), or still has the old `has_work` filter / pre-claim step.
- **STATE.md is still tracked** — the S-event migration removed the shared mutable file;
  any repo still tracking STATE.md should be upgraded.
- **`.gitignore` is incomplete** — missing `.agent/.dispatch.log` or `.agent/.busy/`.
- **Skills are missing** — new core skills were added to dotagent since the repo was
  inhabited.

## The upgrade procedure

### 1. Copy the latest CLI

```bash
cp <dotagent>/scripts/agent <repo>/scripts/agent
chmod +x <repo>/scripts/agent
```

This brings the S-event state model, the claim command, and all bug fixes.

### 2. Copy the latest dispatch hook

```bash
cp <dotagent>/integrations/dispatch/dispatch.sh <repo>/.agent/hooks/post-commit
chmod +x <repo>/.agent/hooks/post-commit
```

This brings the guardrails: self-dispatch filter, chaining cap, rate limiting, bounded
epoch, the generalized prompt, and the removal of pre-claim.

### 3. Clean deprecated artifacts

```bash
# Remove STATE.md if still tracked (S-event migration)
git -C <repo> rm .agent/STATE.md 2>/dev/null || true
# Remove from disk if present but untracked
rm -f <repo>/.agent/STATE.md
```

### 4. Update .gitignore

Ensure `.gitignore` includes:
```
.agent/.busy/
.agent/.dispatch.log
```

### 5. Merge new skills (idempotent)

```bash
cp -rn <dotagent>/skills/. <repo>/skills/
```

`cp -rn` never overwrites existing skill dirs — safe to re-run.

### 6. Commit and verify

```bash
git -C <repo> add scripts/agent .agent/hooks/post-commit .gitignore .agent/STATE.md
git -C <repo> commit -m "upgrade dotagent: <what changed>"
<dotagent>/scripts/agent -C <repo> check   # must exit 0
```

## Self-upgrade

dotagent itself is an inhabited repo. To upgrade dotagent with its own latest:

```bash
./scripts/agent check   # verify current state is clean
# Copy latest dispatch hook to .git/hooks (if using the symlink, it updates automatically)
# STATE.md: already removed (S-event migration done)
# Skills: already current (source of truth)
# Commit if anything changed
```

Since dotagent's dispatch is a **symlink** to `integrations/dispatch/dispatch.sh`, editing
the source updates the hook automatically — no copy needed. Other repos use copies and need
the explicit copy step.

## Verification checklist

After upgrading:

- [ ] `scripts/agent check` exits 0
- [ ] `scripts/agent state` works (no STATE.md reference errors)
- [ ] Dispatch hook has guardrails (`grep -c 'MAX_CHAIN_DEPTH' <repo>/.agent/hooks/post-commit`)
- [ ] No STATE.md tracked or on disk
- [ ] `.gitignore` includes `.agent/.dispatch.log`
- [ ] Log is clean: `scripts/agent log` shows only valid event types (H/O/I/R/C/S)

## Rollback

If an upgrade breaks something:

```bash
git -C <repo> revert HEAD   # undo the upgrade commit
```

The event log is append-only and unaffected by tooling changes — reverting the tooling
never loses the ledger.