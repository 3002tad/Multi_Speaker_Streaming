# Local E2E profile

`scripts/run_e2e_streaming.sh` is the single supported local E2E entrypoint
while Meeting AI Core and the LiveKit Agent still run natively in WSL.

It reads one private runtime env file (default:
`/home/ntd/meeting_runtime/.env`), derives the Meeting Service key from
`INTERNAL_API_KEY`, then starts the native baseline and Meeting Service Docker
stack with compatible network directions:

```text
Meeting Service container -> host.docker.internal:8001 -> AI Core in WSL
Agent in WSL -> 127.0.0.1:8002 -> Meeting Service published port
```

Run from WSL:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
bash scripts/run_e2e_streaming.sh --audio audio/thayDung_noi.wav
```

The runner verifies required keys without printing them, waits for all health
endpoints, publishes the WAV through LiveKit, requires a persisted final
transcript and a `SUCCEEDED` Qwen minutes analysis, then stops only the
processes and containers it started. Add `--keep` to inspect the stack after a
successful probe. It never runs `down -v` and never changes the private env.
