"""Regression client for the two synchronized physical-microphone recordings.

Start scripts/run_demo.sh first and enroll both ``*_goc.wav`` samples. This
client then publishes ``thayDung_noi.wav`` and ``thayPhuoc_noi.wav`` as two
independent LiveKit participants so the production path (including crosstalk)
is tested instead of bypassing LiveKit with raw WebSockets.
"""

import asyncio

from tests.livekit_dual_mic_probe import main


if __name__ == "__main__":
    asyncio.run(main())
