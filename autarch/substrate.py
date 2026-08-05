"""The Substrate Bus — the host Autarch runs on.

Autarch's core (capability kernel, council, memory) is portable: it runs as an
ordinary process on Linux, macOS, Windows, or Android-via-Termux today, and could
sit on a thin microkernel later. This module formalizes that boundary with a
small, dependency-free description of the host and where Autarch keeps its data.

It deliberately does *not* touch hardware — it is the seam that a future
bare-metal/confidential/real-time substrate would implement, not the substrate
itself.
"""
from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Substrate:
    os_name: str
    machine: str
    python: str
    mobile: bool

    @classmethod
    def detect(cls) -> "Substrate":
        system = platform.system() or "unknown"
        # Android (e.g. Termux) reports as Linux but sets these markers.
        mobile = "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ
        return cls(
            os_name="Android" if mobile else system,
            machine=platform.machine() or "unknown",
            python=platform.python_version(),
            mobile=mobile,
        )

    @property
    def tags(self) -> list:
        """Coarse capability tags a platform-specific adapter could match on."""
        tags = [self.os_name.lower()]
        tags.append("mobile" if self.mobile else "desktop")
        if "arm" in self.machine.lower() or "aarch" in self.machine.lower():
            tags.append("arm")
        return tags

    def data_dir(self, app: str = "autarch") -> Path:
        """Per-user data directory, resolved per platform."""
        if self.os_name == "Windows":
            base = os.environ.get("APPDATA") or str(Path.home())
        elif self.os_name == "Darwin":
            base = str(Path.home() / "Library" / "Application Support")
        else:  # Linux / Android / other POSIX
            base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(base) / app
        return path

    def describe(self) -> str:
        kind = "mobile" if self.mobile else "desktop"
        return f"{self.os_name} ({self.machine}) · Python {self.python} · {kind} · interpreter {sys.executable}"
