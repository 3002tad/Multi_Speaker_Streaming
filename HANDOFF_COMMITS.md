# Handoff source checkpoint

This workspace is intentionally maintained as two local Git repositories.
The eCabinet repository must not be pushed to an external remote; final
delivery is a ZIP archive containing both source trees.

## Source checkpoints

| Component | Local branch | Commit | Description |
|---|---|---|---|
| Root / Meeting AI + Meeting Service | `feature/meeting-platform-microservices` | `a59b133522a140a5d6a31b73e8af04d005ba5739` | Runtime AI lifecycle, AI callback and Socket.IO event boundary |
| eCabinet | `feature/meeting-platform-integration` | `133aa58a8806f904e3a3c8eb0d829a1f1a7841a4` | Meeting runtime façade client |

Both working trees were clean when this checkpoint was recorded.

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
