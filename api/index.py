"""The Vercel entrypoint: the same FastAPI app, as a Python Function.

Vercel's Python runtime serves an ASGI callable exported as ``app``. Nothing here differs
from the local service except what has to: the deployment sets ``SESSION_STORE`` and
``DEMO_ALLOWED_HOSTS`` / ``DEMO_ALLOWED_ORIGIN_REGEX`` so the session survives between
invocations and the host check accepts the deployed domain.

One consequence of serverless worth knowing: memory extraction runs after the turn has
streamed, as a fire-and-forget task. An instance may be frozen before it finishes, so a
remembered fact can be lost on this path in a way it is not locally. Everything the shopper
can see -- the cart, the order, the transcript -- is written before the response ends.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The function's working directory is the repo root on Vercel, but be explicit so a local
# `python api/index.py` behaves the same.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.main import app  # noqa: E402

__all__ = ["app"]
