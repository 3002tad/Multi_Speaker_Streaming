# Handoff source checkpoint

This workspace is intentionally maintained as two local Git repositories.
The eCabinet repository must not be pushed to an external remote; final
delivery is a ZIP archive containing both source trees.

## Source checkpoints

| Component | Local branch | Commit | Description |
|---|---|---|---|
| Root / Meeting AI + Meeting Service | `feature/meeting-platform-microservices` | `e3faffe364a5e0b22dfc8c3dec5813c46c0ecc27` | Runtime AI lifecycle, signed runtime token verification and transcript/minutes REST contract |
| eCabinet | `feature/meeting-platform-integration` | `133aa58a8806f904e3a3c8eb0d829a1f1a7841a4` | Meeting runtime façade client |

The root repository is clean at this checkpoint. The eCabinet repository remains local-only and was not committed or pushed; it currently has pending integration changes in the session runtime adapter/token facade and frontend meeting feature files. Review and commit those separately after validation.

Latest local checkpoints after the Day 4 REST/UI slice:

- Root: `e3faffe364a5e0b22dfc8c3dec5813c46c0ecc27`
- eCabinet: `b92ce1df3485b2ffe9a64b337109c6de823b631d`

Day 4 remains partial: PostgreSQL transcript/minutes persistence, full Socket.IO rehydrate, state/permission guards, purge lifecycle and complete regression/boundary tests are still pending. eCabinet was committed locally only and not pushed.

## Packaging rules

The handoff ZIP should include:

- root source code;
- `ecabinet/` source code;
- `contracts/`, `meeting_ai/`, `meeting_service/` and tests;
- this checkpoint file.

Do not include `.git/` directories, `.env` files, model files, Hugging Face or
Ollama caches, Qdrant data, Docker volumes, `__pycache__/`, `output/` or other
runtime-generated data. The recipient should configure secrets separately from
the provided `.env.example` files.

## Validation before delivery

Run the root unit/contract tests and verify both commit hashes again before
creating the final ZIP. The eCabinet commit is intentionally local-only and
has no required remote.

## Latest handoff checkpoint (2026-08-07)

The Day 4 hardening and realtime workspace slice are now committed locally:

| Component | Local branch | Commit | Description |
|---|---|---|---|
| Root / Meeting Service | `feature/meeting-platform-microservices` | `c990709` | JSON-safe AI event encoding for Socket.IO broadcasts; UUID fields no longer cause realtime event HTTP 500 |
| eCabinet | `feature/meeting-platform-integration` | `75478a5` | Vite development proxy for eCabinet REST and Meeting Service Socket.IO paths |

The preceding realtime checkpoints remain in history:

- Root `abd0b6a`: Socket.IO room authorization bound to meeting/runtime claims,
  leave-room cleanup, and permission regression tests.
- eCabinet `0515115`: Meeting Workspace token acquisition, Socket.IO join/leave
  lifecycle, partial/final transcript rendering, and REST rehydrate.

Previous required checkpoints remain in history:

- Root `de05b44`: durable transcript/minutes PostgreSQL persistence and migration `0003_transcript_minutes`.
- Root `71564fb`: purge, runtime/minutes state guards, and transactional `transcript.final` callback persistence.
- eCabinet `d60d7a7`: purge tombstone, migration `b7d2a1c4e9f0`, and admin retry endpoint.

Validation for this checkpoint:

- Root full unittest suite: 108 tests passed.
- Meeting Service compile and contract checks passed.
- eCabinet backend compile and Alembic migration passed.
- Frontend Docker build and Vite production build passed.
- No repository was pushed to a remote.

UI E2E checkpoint completed with Playwright Edge headless against the local
Docker stack: login, DRAFT start rejection (409), APPROVED runtime start,
token issuance, Socket.IO join, partial/final transcript delivery, REST
rehydrate after reload, and minutes revision save (200). Screenshot:
`output/playwright/meeting-workspace-smoke.png`.

The automated run left the demo meeting `hop test` in an active runtime with
synthetic transcript/minutes data for manual inspection. Services remain
running. The full pytest suite could not be rerun in the WSL runtime because
`pytest` is not installed; Python compile and direct JSON-encoding checks
passed. No repository was pushed to a remote.

Remaining next step: manual UI verification followed by the audio/LiveKit
wiring. The current commits do not include audio/LiveKit wiring.
