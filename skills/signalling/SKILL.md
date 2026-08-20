---
name: signalling
description: >-
  The agent-to-agent communication layer that turns a git repo into an active intelligence.
  A repo's git history is the ledger: every self-handoff and every inbound/outbound message is
  an atomic event file committed to git, and a small script (`agent`) enforces schema and
  hygiene. The channel is the file, not the network — no daemon, no server, no special git
  host; works on any plain git repo with any "bring your own agent". Covers the event-log
  protocol, the event schema, the message lifecycle, the BYO-agent contract, and the
  determinism gate. Use when you need one repo to talk to another (or to its own future
  self), when you want to replace a single growing HANDOFF.md with a recoverable event log,
  or when wiring an agent into any existing git repo.
metadata:
  scope: any git repo
  aliases: [handoff, inbox, messaging, event-log, agent-to-agent, signalling, protocol]
  deps: caretaker (the custodial loop this protocol powers)
---

# Signalling — the repo talks, git is the ledger

This is the layer that makes a repo more than a static asset. **A repo is an active
intelligence** when its git history is a ledger: everything it was asked, said, did, and
where it left off is an *event* in git — recoverable across reboot, session, and agent
turnover, auditable by anyone, on any plain git repo.

A single growing `HANDOFF.md` fails: it's great for context-loading but it doesn't scale —
it bloats, conflates your notes with other people's messages, and merges into chaos when two
agents write it at once. The fix is to stop maintaining one mutable state file and make the
**history itself** the state.

## The principle: git is the control plane

Every event is a tiny, atomic markdown file in `.agent/log/`, committed to git. The commit
graph is the ledger. Git gives us, for free, the properties a messaging/state layer needs:

- **Ordering** — history is the temporal spine; no trusted clock to arbitrate.
- **Integrity** — git content-hashes everything; a tampered event breaks the chain.
- **Recovery** — `git clone`/checkout anywhere restores the entire mind. No DB, no server.
- **Audit** — `git log -- .agent/` is the full story of what this repo was asked and said.
- **Concurrency** — one file per event means parallel agents never fight over a growing doc.

The channel is **the file, not the network**: you write a well-known file in the recipient
repo; it reads on its next wake and replies in place. Asynchronous, durable, inspectable,
versioned — and needs nothing but git.

## The layout

```
<repo>/
  .agent/
    log/            # the append-only event log — ONE FILE PER EVENT
      H--<ts>--<id>.md      # handoff   (a self-snapshot at session end)
      O--<ts>--<id>.md      # outbound  (to another repo/agent)
      I--<ts>--<id>.md      # inbound   (from another repo/agent)
      R--<ts>--<id>.md      # resolve   (closes an O or I event)
    STATE.md        # GENERATED read-model projection — never hand-edited
    config          # `identity: <name>` (or set AGENT_ID env)
```

## The event schema (frontmatter)

Every event is strict-frontmatter + a body. The schema is what `agent check` enforces:

```yaml
---
type: O              # H | O | I | R
id: <hex>            # event id (shared across the two sides of a mirrored message)
ts: <ISO-8601Z>      # UTC timestamp
from: <agent-id>     # the sender's identity
to: <agent-id>       # O/I — the intended recipient
thread: <hex>        # O/I — groups a conversation across replies
subject: <one-line>  # the headline
mirror: <repo-name>  # O/I — the co-located repo holding the counterpart event
re: <event-id>       # R — which event this closes; replies set it too
---
<body>
```

**Mirrored messages.** A message between two co-located repos is two files with the **same
`id`**: an `O` in the sender's log (its outbox) and an `I` in the recipient's log (its
inbox). `agent send --target <repo>` / `agent reply --target <repo>` write both. The
`thread` links all replies; the shared `id` links the two sides. Each half is
**self-describing**: its `mirror:` field names the co-located repo that holds the
counterpart, so later operations can follow the link.

## The lifecycle

```
send  →  (mirror)  →  recipient reads inbox  →  reply (re: id, same thread)
     →  claim (optional: mark owned)  →  resolve (R re: id)  →  closed, and propagates
```

Status is **derived, not stored**: an event is "open" until a **resolve** (`R`) marker
targeting its `id` exists (or, for dispatch, until it is **claimed** with a `C` marker).
That keeps the log append-only and the derivation deterministic.

- **`agent claim <id>`** marks an event **owned** — e.g. a dispatched agent claims the
  inbound it's about to work. A claimed event drops out of `inbox`/`outbox` and is excluded
  from dispatch, so it is not re-spawned while owned but not yet resolved. Claim is the
  "owned-but-not-closed" state; resolve is the terminal state.
- A **resolve propagates** along the `mirror:` link: when one side closes a mirrored event,
  an `R` marker is also written into the counterpart's repo, closing it there too — so the
  conversation closes as a whole, on both sides, even though each repo's log stays
  append-only.

## The tool — `scripts/agent` (the control-plane enforcement layer)

Scripts own the ceremony so a "bring your own agent" workflow stays deterministic. An agent
**never hand-formats an event file**; it calls the tool, which validates schema, stages only
its own files (never `git add -A`), and commits with a conventional message.

```bash
agent init [identity]            # scaffold .agent/ + first STATE
agent identity [name]            # get/set identity
agent handoff <subject> [-m BODY]    # snapshot current state at session end
agent send <to> <subject> [--target REPO] [--thread T] [-m BODY]
agent reply <event-id> [subject] [--target REPO] [-m BODY]
agent resolve <event-id> [reason] [--target REPO]
agent claim <event-id> [reason]    # mark owned (excluded from inbox/outbox/dispatch)
agent inbox                      # list open inbound (the mailbox, as a query)
agent outbox                     # list open outbound
agent state                      # derive + print STATE.md
agent log                        # print the event history
agent check                      # integrity/conformity gate → exit 0/2/3
agent --help
```

`agent` is a single dependency-free POSIX bash script — copy it into any repo and point an
agent at it. Identity comes from `AGENT_ID` env or `.agent/config`.

## The BYO-agent contract — what "wired in" means

Any repo is *alive* when any agent can be handed it and immediately:

1. **Read `AGENTS.md`** → the charter: who it is, why, its merits and rules.
2. **Read `skills/`** → how to act + the lived experience (gotchas, recovery).
3. **`agent state`** → where it is in time (cold start, no context preload needed).
4. **`agent inbox`** → what's waiting on it → act → `agent reply` / `agent resolve`.
5. **`agent handoff` on sleep** → continuity survives the session.

That is the whole paradigm: a repo that knows itself, remembers how to act, tracks where it
is in time, and talks to other minds — all recoverable, on any git repo, with any agent.

## The determinism gate

`agent check` is the conformity contract, mirroring a lint gate:

- every event's frontmatter is parseable and has the required fields for its type;
- the filename type prefix matches the frontmatter type;
- `O`/`I` events carry `to` + `thread`; `R` events carry `re`;
- the repo is clean (no uncommitted events) — events are written and committed atomically.

Exit: `0` conformant · `2` non-conformant · `3` git/usage problem. A repo whose log doesn't
pass `agent check` is a repo whose ledger is untrustworthy.

## Conversation discipline (what good messages look like)

- **One topic per message.** A recipient acting on it should know exactly what's being asked.
- **Clear, specific, actionable.** "Please land the fold endpoint — done = smoke green +
  gates green." beats "work on the endpoint soon."
- **Name the deadline, or say none.** Ambiguity breeds anxiety on a tight schedule.
- **Request, don't demand.** The recipient is a peer with judgment; leave room to push back.
- **Reply loudly.** A silent non-reply is a dropped baton; say why even when it's "no".
- **Resolve when done** so the sender stops tracking it.

## Gotchas

- **Never edit events.** The log is append-only; a correction is a new event (or a resolve
  + resend), never an in-place edit. Git still records the mistake if you must — but the
  protocol expects append.
- **Resolution propagates only when the mirror is co-located.** `resolve` follows the
  event's `mirror:` link (or an explicit `--target`). If that repo isn't reachable, the
  resolution is local only and the script says so — the record on each side is still what
  git guarantees, but the remote side won't auto-close until it's reachable or resolved
  there. Each repo still owns its view; propagation is a convenience on top of that.
- **Targets must be co-located or out-of-band.** `--target` mirrors into a local checkout;
  for a remote repo, the outbound is recorded in-transit and delivery is whatever the repos
  already use (push/pull, a shared checkout, copy). The *record* is what git guarantees.
- **`agent` stages only its own files.** It never `git add -A`; a dirty working tree around
  it is left untouched. Run `agent check` to confirm the log itself is clean.
- **`STATE.md` is a projection, not a source of truth.** If it looks stale or wrong, don't
  edit it — `agent state` regenerates it from the log. It's a cache for cheap context-loading,
  the log is the truth.
- **Prefer the repo-local `scripts/agent`.** Never resolve the `agent` command from PATH first
  — an unrelated `agent` binary may shadow it (a real footgun found on a machine with
  `/usr/local/bin/agent`). The control plane is copied into the repo precisely so it wins:
  `A="scripts/agent"; [ -x "$A" ] || A="$(command -v agent ...)"`. This applies to hooks and
  integrations as much as to interactive use.

## Sibling skills

- **`caretaker`** — the whole custodial loop this protocol powers: possess, orient, act,
  hand off. Signalling is the "hand off" and "talk to others" mechanism; caretaker is the job.
- **`git`** — the clean end-state repo hygiene that keeps the ledger trustworthy.
- **`agentsmd`** — authoring the `AGENTS.md` charter that every alive repo leads with.