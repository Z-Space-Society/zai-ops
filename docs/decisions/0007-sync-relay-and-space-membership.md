# ADR-0007 — An Automerge sync relay, with Spaces as the membership boundary

> Status: **Accepted** (2026-08-30). Adds a role; supersedes nothing.

## Context

The cluster is adding shared markdown notes: documents several members edit at
once, from a browser, from a laptop, and from a model acting on a member's
behalf. The pieces that answer this are settled elsewhere in the industry —
**Automerge** is the CRDT, and **automerge-repo** is the sync protocol its
clients already speak. What the cluster does not have is the server end of that
protocol. Nothing in the stack does CRDT sync: Postgres stores rows, Garage
stores objects, and HappyView indexes ATProto records.

Four forces shape the decision.

1. **The obvious membership design is unsafe.** The natural move is to put a
   member list inside the shared document — the document already describes the
   group, so why not say who is in it. This fails on the first principle of the
   data model it is written in. A CRDT is editable offline by every replica
   holder, and every edit converges. A member list stored there is a member
   list anyone in it can extend to anyone, with no server in the path to
   refuse. Membership stored in a CRDT is membership members grant themselves.

2. **The cluster already runs the primitive.** The membership registry — whose
   lexicons and Lua live in their own repo and are published to HappyView
   rather than installed by Ansible — drives an ATProto **Space** on HappyView
   in production: `atproto.spaces.get()`, `space:add_member{did, access}`,
   `space:is_member(did)`, `space:remove_member{did}`, with spaces created
   through `com.atproto.simplespace.createSpace` and listed through
   `com.atproto.space.listSpaces`. Removing a member also revokes their
   outstanding space credentials. Building a second membership mechanism beside
   this one would be a second source of truth for the same question.

3. **The reference sync server is not deployable here.** `automerge-repo-sync-server`
   is an Express app its own README calls unsecured, with no concept of users,
   documents, or per-document access — anyone who connects syncs any document
   id it holds. Using it means forking it, and it puts a Node runtime on a
   service CT for no gain.

4. **Identity is already answered.** Corliss brokers the cluster's sessions,
   bridging ATProto identity to OIDC for cluster apps
   ([ADR-0005](0005-zai-auth-over-aip.md), [ADR-0006](0006-corliss-standalone-apex.md)).
   Members are DIDs. Whatever gates the sync connection should consume a DID,
   not invent an account.

## Decision

**1. Membership lives in an ATProto Space. The Automerge document holds only
content.** Content edits are CRDT; membership edits are ATProto. The one thing
that must not be forgeable is not stored in the forgeable layer.

**2. A Space is the unit of sharing.** `at://<creator-did>/<type>/<skey>`,
holding member DIDs and access levels and nothing else. Every member has a
**Private Space** — a Space with one member — so there is no second code path
for "not shared yet".

**3. The creator is the Space authority.** Spaces are member-owned and portable;
the cluster is not the owner of a member's Spaces. Corliss creates and manages
them *as the member*, using the mechanism it already uses for registry writes: a
DPoP-authenticated XRPC procedure carrying the member's own session, where the
Lua reads `caller_did` and the runtime stamps authorship. There is deliberately
no way to perform these writes as Corliss itself.

**4. One manifest document per Space.** A plain automerge-repo folder document
listing `{name, type, url}` entries, recursively, rooted at the Space. It
carries no member field — the schema is whatever the client already writes, so
adopting a client requires no change to its data model.

**5. The Space→manifest binding is a record inside the Space.** A non-member
cannot read the Space, so a non-member cannot learn the root document address at
all. Discovery is gated before the relay is reached.

**6. Authentication is ATProto service auth.** The client mints a short-lived
JWT with `com.atproto.server.getServiceAuth`, audience-bound to the relay's
service DID; the relay verifies it by resolving the caller's DID document.
Not cluster OIDC: service auth is on-protocol, and a third-party client can mint
one from the session it already holds without being a registered OIDC client.

**7. Authorization is reachability.** A DID may open a document if and only if
that document is reachable from the manifest of some Space the DID belongs to.
The reachable set is derived from documents the relay already holds, cached, and
rebuildable — never authoritative state. Enforcement rides samod's
`AnnouncePolicy`, whose `should_announce(DocumentId, PeerId)` hook is per
document and per peer.

**8. The relay is `samod`, in Rust, over Postgres.** samod is the Rust
automerge-repo implementation, wire-compatible with the JS client, maintained by
an Automerge maintainer, and explicitly supports the sync-server shape. Storage
is a key/value table with a prefix index, because samod's `Storage` trait is a
key/value store with range queries and Postgres gives transactional compaction
for free. Deployment is a single static binary under systemd — no Node runtime,
no Docker, consistent with every other role here.

Explicitly **not** `automerge-repo-rs`: its own README states its disk layout and
WebSocket protocol are incompatible with the JS implementation, and names samod
as its successor. This is the trap when searching "rust automerge repo".

**9. The relay ships in two phases.** Phase A is transport, storage and
deployment with no enforcement, bound to the internal bridge with no Caddy
route. Phase B adds service-auth verification, the reachability index and the
public vhost. Phase A exists to prove protocol interoperability against a real
client and to prove the deployment, before the enforcement layer is written
against them.

## Consequences

- **Removal stops future sync; it does not unshare the past.** A departed member
  holds a complete replica of the full history, and revocation cannot take it
  back. This is inherent to CRDTs, not a flaw in this design, and it should be
  stated to members plainly rather than implying that removal is retroactive.

- **The relay is trusted.** It can read every document and could lie about
  membership. That is the correct trade for a single-operator community cluster
  and it is the same trade already accepted elsewhere in the stack, but it is a
  trade: this is not end-to-end encrypted.

- **Anyone in a Space can add, remove and create documents in it.** The Space is
  the boundary, so this is the intended grain rather than a gap. Finer-grained
  per-document permissions are not available and are not planned.

- **Adopting a document by URL becomes inert for non-members.** Clients that let
  a user paste a document id and adopt it need no change; there is simply now a
  server-side authority for that to fail against.

- **Corliss holds no authoritative state for this feature.** Its Space list is a
  cache, re-derivable from the registry, failing safe: a stale row means a
  missing tab, not a lost Space.

- **Documents accumulate.** Automerge retains the full change graph, which is
  what makes history and attribution work, and it means a note edited daily for
  years is far larger than its text. Compaction exists and is deferred; the
  change table's growth is the thing to watch on the Postgres volume.

- **Model writes must be targeted edits, never whole-document replacements.** A
  model asked to tidy the notes can rewrite every document in seconds, and
  convergence will faithfully preserve all of it. Rate limiting or a proposal
  step for bulk edits is a decision to make before the first incident.

- **Phase A is an open relay.** Anyone who can reach the CT can sync any
  document id it holds — the same property that disqualified the reference sync
  server. It is acceptable only as a bring-up state, and only while two
  conditions hold: no Caddy route, and no real content. Both are load-bearing.

- **Subduction may make the reachability layer redundant.** Ink & Switch's
  peer-to-peer protocol already separates connection policy from storage policy
  and ships a Keyhive-based authorization crate — the seam this ADR fills by
  hand. It is preview-grade and its README says not to use it in production, so
  samod is the choice today. The reachability layer is kept deliberately small
  because it is the part most likely to be replaced.

- **A new platform-tier CT and a new Postgres database.** The `sync-relay` CT at
  CTID 113, sized for `cargo build --release` rather than for its runtime,
  following the `happyview` precedent. Platform rather than application: a
  member's client connects to it directly, but nobody signs in to it and it has
  no surface anyone lands on — the same reading that puts the gateway at 112.
