"""Compatibility wrapper for the legacy Meeting AI entrypoint.

The implementation now lives in :mod:`meeting_ai.main`.  Keep this file while
scripts and operators migrate to ``python -m meeting_ai.main``.
"""

from meeting_ai.main import app, run_server


if __name__ == "__main__":
    run_server()
