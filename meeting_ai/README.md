# Meeting AI Core

`meeting_ai` owns the computation-only portion of the meeting platform.
It may load local models and maintain in-memory pipeline state, but it must
not query eCabinet or Meeting Service databases, or access their object
storage.

## Layout

- `config.py`: runtime/model settings shared by the AI compatibility process.
- `core/`: deterministic audio, ASR scheduling, speaker identity, transcript
  recovery and adaptive-dictionary primitives.
- `application/`: AI-facing use cases, currently evidence-backed minutes
  composition.

## Transitional compatibility

The legacy `backend.*` paths re-export these modules while the platform is
being extracted. `ai_server.py` and `agent.py` now import this package
directly, but remain executable compatibility wrappers for the locked
baseline. Do not remove the legacy paths until Meeting Service has been
introduced and all callers have migrated.

## Service boundary

The future Meeting AI HTTP/WebSocket adapter will expose only the contracts
under `contracts/`. Meeting Service remains responsible for session lifecycle,
persisting transcript/minutes and export metadata; eCabinet Core remains
responsible for identity, roles and scheduled-meeting metadata.
