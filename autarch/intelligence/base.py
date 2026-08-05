"""The model provider interface — the single seam between Autarch and any AI.

A future fine-tuned 'OurModel' drops in here without touching the kernel.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:  # keep the seam import-light; vision types are only needed for annotations
    from .vision import ImageRef


class ModelProvider(ABC):
    """A voice in the council. The only thing Autarch asks of intelligence."""

    name: str = "provider"

    @abstractmethod
    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        """Return a text completion for `prompt`."""
        raise NotImplementedError

    def supports_vision(self) -> bool:
        """True if this provider can accept images in :meth:`complete_vision`."""
        return False

    def complete_vision(self, prompt: str, images: Sequence[ImageRef], system: Optional[str] = None) -> str:
        """Complete a prompt that also references one or more images.

        Text-only providers ignore the images and fall back to a plain text completion;
        vision-capable providers override this to actually send the images to the model.
        """
        return self.complete(prompt, system=system)
