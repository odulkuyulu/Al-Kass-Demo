"""
Speaker registry for diarised transcription.

Azure's ConversationTranscriber emits volatile speaker tags such as
"Guest_1", "Guest_2", "Unknown". The SpeakerRegistry maps those into
stable, human-friendly personas ("Speaker 1", "Speaker 2", ...) plus
a fixed colour from the Alkass palette.

The registry is session-scoped: every pipeline restart should create
a fresh registry so numbering resets to 1.
"""

import threading
from dataclasses import dataclass
from typing import Optional


# Alkass-themed high-contrast palette (works on dark background)
DEFAULT_PALETTE = [
    "#FFD400",  # Alkass gold     — Speaker 1
    "#00C2FF",  # Cyan            — Speaker 2
    "#FF6B6B",  # Coral           — Speaker 3
    "#7CFFB2",  # Mint            — Speaker 4
    "#C792EA",  # Lilac           — Speaker 5
    "#FFA552",  # Amber           — Speaker 6
]

UNKNOWN_COLOUR = "#9AA0A6"  # Neutral grey for unattributed speech


@dataclass(frozen=True)
class Persona:
    id: str          # "S1"
    label: str       # "Speaker 1"
    colour: str      # "#FFD400"


class SpeakerRegistry:
    """Thread-safe map: raw Azure speaker_id → stable Persona."""

    def __init__(self, palette: Optional[list] = None):
        self._palette = palette or DEFAULT_PALETTE
        self._map: dict = {}
        self._lock = threading.Lock()

    def resolve(self, raw_id: Optional[str]) -> Persona:
        """
        Map a raw speaker_id ("Guest_1", "Unknown", None) to a stable Persona.

        Unknown / None always returns the neutral persona without consuming
        a palette slot. Known speakers are assigned in first-seen order.
        """
        key = (raw_id or "").strip()
        if not key or key.lower() == "unknown":
            return Persona(id="S0", label="Speaker", colour=UNKNOWN_COLOUR)

        with self._lock:
            if key not in self._map:
                n = len(self._map) + 1
                self._map[key] = Persona(
                    id=f"S{n}",
                    label=f"Speaker {n}",
                    colour=self._palette[(n - 1) % len(self._palette)],
                )
            return self._map[key]

    def reset(self) -> None:
        with self._lock:
            self._map.clear()
