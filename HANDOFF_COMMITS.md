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
