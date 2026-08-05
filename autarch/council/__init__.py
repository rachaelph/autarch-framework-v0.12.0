"""The Council — where minds deliberate.

A proposer drafts an action; a challenger critiques it for risk and overreach.
With one provider, a single mind plays both roles; with several, this grows into
a true multi-model council. The output is a Deliberation the kernel can act on.
"""
from __future__ import annotations

from .deliberation import Council, Deliberation, Position

__all__ = ["Council", "Deliberation", "Position"]
