# Diagrams

Three views of the same cluster, drawn in [Mermaid](https://mermaid.js.org/) so
they render on GitHub and stay diffable in review:

- [Cluster topology](#cluster-topology) — what exists and how it's wired.
- [Build and provision flow](#build-and-provision-flow) — how a bare host becomes that.
- [Member login path](#member-login-path) — what a member's request actually walks through.

Everything here is derived from the committed blueprint
([`inventory/hosts.yml`](../ansible/inventory/hosts.yml), the
[`proxy`](roles/proxy.md) role's `caddy_proxy_hosts`, and the role docs). The
**CTIDs shown are the example layout** from the [README](../README.md) — real
numbers are per-cluster runtime data bound with `zai-assign`, so read them as
tier markers, not identity. See [Service CTID
assignment](README.md#service-ctid-assignment).

---

## Cluster topology

The proxy is the only LAN-facing container; everything else lives on the
uplink-less `vmbr1` and routes out through the host's NAT. The inference nodes
are bare metal, outside the Proxmox host entirely.

```mermaid
flowchart TB
    members["Members<br/>browsers and API clients"]
    cf["Cloudflare<br/>DNS + origin cert"]

    subgraph host["Proxmox host — NAT gateway 10.1.1.1"]
        direction TB

        subgraph app["Applications 120–129 — surfaces members land on"]
            corliss["corliss · 120<br/>ATProto sign-in, membership, OIDC provider<br/>:8000"]
            owui["open-webui · 121<br/>chat UI<br/>:8080"]
        end

        subgraph plat["Platform 110–119 — consumed by other services"]
            proxy["proxy · 110 · Caddy<br/>LAN-facing edge, dual-homed<br/>:80 / :443"]
            happyview["happyview · 111<br/>ATProto AppView, membership registry<br/>:3000"]
            litellm["litellm · 112<br/>OpenAI-compatible gateway<br/>+ co-located CPU embedder<br/>:4000"]
            relay["sync-relay · 113<br/>Automerge sync server<br/>:7030 — no proxy route by design"]
        end

        subgraph core["Core infra 100–109 — data foundations"]
            control["control · 100<br/>Ansible, vault, SSH key · 10.1.1.100<br/>builds every other CT — see the provision flow"]
            objstore["object-store · 101 · Garage<br/>S3 restic backend"]
            postgres["postgres · 102<br/>PG 17"]
            redis["redis · 103<br/>revocation markers"]
        end
    end

    subgraph inf["Inference nodes — bare metal, outside the host"]
        salmon["salmon · llama-server<br/>NVIDIA + CUDA"]
        orca["orca · llama-server<br/>NVIDIA + CUDA"]
    end

    members -->|"browsers and per-member API keys"| cf
    cf -->|"vmbr0 / LAN"| proxy

    proxy -->|"apex → :8000"| corliss
    proxy -->|"chat. → :8080"| owui
    proxy -->|"api. → :4000"| litellm
    proxy -->|"view. → :3000"| happyview

    corliss -->|"OIDC id_token"| owui
    corliss -->|"membership reconcile"| happyview
    corliss -->|"mints member keys"| litellm
    owui -->|"chat + embeddings"| litellm
    owui -->|"revoked_at markers"| redis
    litellm -->|"GPU inference"| salmon
    litellm -->|"GPU inference"| orca

    corliss --> postgres
    owui --> postgres
    happyview --> postgres
    litellm --> postgres
    relay --> postgres

    control -->|"daily restic backup"| objstore

    classDef tierCore fill:#eef4fb,stroke:#4a6f9c,color:#123
    classDef tierPlat fill:#eef8f0,stroke:#4d8a5e,color:#123
    classDef tierApp fill:#fbf2e8,stroke:#a3763c,color:#123
    classDef outside fill:#f4f0f7,stroke:#6f5a8a,color:#123
    class control,objstore,postgres,redis tierCore
    class proxy,happyview,litellm,relay tierPlat
    class corliss,owui tierApp
    class salmon,orca,members,cf outside
```

**Reading the tiers.** The line between platform and applications is *who talks
to it* — platform CTs are consumed by other services, application CTs are
consumed by members. That rule does **not** separate core from platform: what
makes core infra core is that it is the data foundations, storage with no logic
of its own. `corliss` is an application despite doing the authentication;
`litellm` and `sync-relay` stay platform even though a member's own client
connects to each directly, because neither has a page anyone lands on. See
[CTID tiers](README.md#networking).

**Two edges worth noticing:**

- `sync-relay` has **no** `caddy_proxy_hosts` entry on purpose — the Phase A
  relay enforces no membership, so `vmbr1` is the entire access boundary
  ([ADR-0007](decisions/0007-sync-relay-and-space-membership.md)). Do not add a
  route before the Phase B verifier lands.
- The dotted `corliss → happyview` and `corliss → open-webui` service calls go
  **CT-to-CT over `vmbr1`**, not out through Cloudflare and back — they are
  recovery and revocation paths that must not depend on public DNS, the CDN and
  the proxy CT all being up.

---

## Build and provision flow

One host-level script, then everything from inside CT 100. The two-pass shape —
assign every CTID first, provision second — is what lets each role render
cross-service references regardless of provisioning order.

```mermaid
flowchart LR
    start(["Freshly flashed<br/>Proxmox host"])

    subgraph phase1["1 · bootstrap.sh — the only host-level step, run as root"]
        direction TB
        b1["apt repos, full-upgrade, suppress subscription nag"]
        b2["create vmbr1 10.1.1.0/24, no uplink<br/>host at 10.1.1.1 + NAT masquerade"]
        b3["fetch debian-13-standard template"]
        b4["create CT 100 ansible-control<br/>attach vmbr1 at 10.1.1.100"]
        b5["locale, ansible + git, clone repo to /opt/zai-ops<br/>pin community.proxmox 1.6.0 or newer, bin/ on PATH"]
        b6["mint ansible@pve API token + ZaiProvision role<br/>write encrypted vault, print vault password"]
        b7["record proxmox_node_name from hostname"]
        b1 --> b2 --> b3 --> b4 --> b5 --> b6 --> b7
    end

    subgraph phase2["2 · inside CT 100 — identity"]
        direction TB
        c1["ansible-playbook site.yml<br/>configure the control node"]
        c2["ansible-playbook verify-proxmox.yml<br/>confirm the token authenticates"]
        c3["zai-set-domain example.com<br/>required before the proxy"]
        c4["zai-set-registry client_key / service_did<br/>optional — blank is a working state"]
        c1 --> c2 --> c3 --> c4
    end

    subgraph phase3["3 · assign — records numbers only, creates nothing"]
        direction TB
        a1["zai-assign object-store · postgres · redis"]
        a2["zai-assign proxy · happyview · litellm · sync-relay"]
        a3["zai-assign corliss · open-webui"]
    end

    subgraph phase4["4 · provision.yml — create over the API, configure over SSH"]
        direction TB
        p1["object-store<br/>restic backend, so it comes up first"]
        p2["postgres<br/>every app below creates its own role + DB here"]
        p3["redis<br/>before open-webui builds its REDIS_URL"]
        p4["proxy"]
        p5["happyview"]
        p6["litellm"]
        p7["sync-relay<br/>~15–30 min, cargo build on a cold CT"]
        p8["corliss"]
        p9["open-webui"]
        p1 --> p2 --> p3 --> p4 --> p5 --> p6 --> p7 --> p8 --> p9
    end

    subgraph phase5["5 · steady state"]
        direction TB
        s1["ansible-playbook backup.yml<br/>daily restic timer to the object store"]
        s2["enroll-inference-node.yml -e name=salmon<br/>then inference.yml --limit salmon"]
        s3["add-github-user.yml<br/>human admin accounts from GitHub keys"]
    end

    start --> phase1 --> phase2 --> phase3 --> phase4 --> phase5

    a1 -.->|"assigning all up front is what<br/>makes provision order-independent"| phase4
    a2 -.-> phase4
    a3 -.-> phase4
```

**Why assign is its own pass.** The proxy's Caddy route points at litellm's
derived address (`10.1.1.<litellm-ctid>`); if litellm has no CTID yet, the
Caddyfile fails to render. Binding every number first sidesteps the whole class
of ordering problem — see [Service CTID
assignment](README.md#service-ctid-assignment).

**The ordering that is still real** (data dependencies, not convenience):

| Must come first | Because |
| --------------- | ------- |
| `object-store` | it is the restic backend `backup.yml` writes to |
| `postgres` | `happyview`, `litellm`, `sync-relay`, `corliss` and `open-webui` each create their own role + database on it |
| `redis` | `open-webui` builds its `REDIS_URL` from that CT's address |

`corliss` additionally needs `sync-relay`, `redis`, `object-store` and `proxy`
to have been **assigned** — its `/systems/` probe URLs derive from `hostvars`
unguarded. That is an inventory dependency, not an ordering one: `zai-assign` is
the fix, no provisioning required.

---

## Member login path

What a member actually walks through, from typing the apex to a token reaching a
GPU. Everything crossing the LAN goes through Caddy; everything between two CTs
stays on `vmbr1`.

```mermaid
sequenceDiagram
    autonumber
    actor M as Member browser
    participant C as Caddy · proxy CT
    participant CO as corliss · apex
    participant PDS as Member's ATProto PDS
    participant HV as happyview · registry
    participant OW as open-webui
    participant LL as litellm
    participant GPU as llama-server · salmon/orca

    M->>C: GET https://apex/
    C->>CO: reverse_proxy 10.1.1.120:8000
    CO-->>M: landing page, signed out

    rect rgb(238, 244, 251)
    Note over M,PDS: ATProto sign-in — anyone with a handle may sign in
    M->>CO: POST /auth/login with handle
    CO->>PDS: OAuth authorize, DPoP-bound<br/>client_id = /auth/client-metadata.json
    PDS-->>M: consent
    M->>CO: GET /auth/oauth/callback
    CO->>PDS: com.atproto.repo.getRecord<br/>app.bsky.actor.profile for displayName
    CO->>CO: session keyed on DID
    end

    rect rgb(238, 248, 240)
    Note over CO,HV: Membership is a separate question from identity
    CO->>HV: read membership over vmbr1, Host view.apex
    HV-->>CO: grants and revocations, latest-event-wins
    CO->>CO: cache standing and tier
    end

    M->>C: GET https://chat.apex/
    C->>C: /auth* → /oauth/oidc/login<br/>skipped when the token cookie is present
    C->>OW: reverse_proxy 10.1.1.121:8080

    rect rgb(251, 242, 232)
    Note over OW,CO: OIDC — the membership gate
    OW->>CO: GET /.well-known/openid-configuration
    OW->>CO: GET /oidc/authorize
    CO->>CO: membership gate, re-checked every exchange
    alt not a member
        CO-->>M: refused, sent to /membership/apply
    else member
        CO-->>OW: authorization code
        OW->>CO: POST /oidc/token
        CO-->>OW: id_token RS256, name + handle claims
        OW->>OW: mint own session JWT, keyed on handle
    end
    end

    M->>OW: chat
    OW->>LL: /v1/chat/completions over vmbr1
    LL->>GPU: route to an available node
    GPU-->>M: streamed response through open-webui and Caddy

    Note over M,LL: A member's own API key skips the UI entirely —<br/>issued at corliss /api/, used against https://api.apex
```

### Revocation — why redis exists

Open WebUI authenticates from its **own** session JWT after the exchange, so
corliss is not consulted again until that token expires. The gate at
`/oidc/authorize` therefore cannot end a session already in flight; the
back-channel can.

```mermaid
sequenceDiagram
    autonumber
    actor A as Admin
    participant CO as corliss /manage/
    participant HV as happyview registry
    participant OW as open-webui
    participant R as redis
    actor M as Revoked member

    A->>CO: revoke, authored by the admin's own registry session
    CO->>HV: write revocation event
    CO->>OW: POST /oauth/backchannel-logout<br/>signed logout_token, CT-to-CT on vmbr1
    OW->>R: SET open-webui:auth:user:id:revoked_at
    Note over OW,R: without redis this handler can only drop stored OAuth<br/>sessions — it cannot kill the JWT the browser is using
    M->>OW: next request with the old JWT
    OW->>R: check revoked_at
    OW-->>M: signed out, in seconds rather than at JWT_EXPIRES_IN
```

**The two addresses for the same service are not interchangeable.** `corliss`
reaches `happyview`, `litellm` and `open-webui` at their **internal**
`10.1.1.x` addresses, while the links it renders for a member's browser use the
public origins. Conflating them breaks quietly: the internal happyview call must
still present `Host: view.<domain>` or the AppView answers HTTP 421, and
`corliss_api_url` (what a member points a client at) is not
`corliss_litellm_url` (what corliss calls to mint that member's key).
