"""Deterministic pipeline configuration: the media contract and fixed encoder flags.

Single source of truth for the artifact formats (CONTEXT.md "media contract") and the
fixed ffmpeg encoder flags (assembly.md §9: fixed encoder flags, fixed -crf, fixed
order). Downstream stages read these values; `vibe make` records them in the build
manifest so re-runs cannot drift.
"""

from __future__ import annotations

FPS = 30

FULL_WIDTH = 1920
FULL_HEIGHT = 1080

SHORT_WIDTH = 1080
SHORT_HEIGHT = 1920

VIDEO_CODEC = "h264"
VIDEO_PROFILE = "high"
PIX_FMT = "yuv420p"

AUDIO_CODEC = "aac"
AUDIO_PROFILE = "lc"
AUDIO_SAMPLE_RATE = 44100
AUDIO_CHANNELS = 2

# Segment render timeline (assembly.md §2.2 / amended excursion): mute open before the
# narration begins. Narration starts at body start (~1.15 s), so a clip's container
# duration = OPEN_PADDING_S + narration duration + tail.
OPEN_PADDING_S = 1.15
DURATION_TOLERANCE_S = 0.3

# Fixed encoder flags. Same approved inputs -> byte-identical outputs.
VIDEO_ENCODE_FLAGS = [
    "-c:v",
    "libx264",
    "-profile:v",
    "high",
    "-pix_fmt",
    "yuv420p",
    "-crf",
    "18",
    "-preset",
    "medium",
]
AUDIO_ENCODE_FLAGS = ["-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]
MUX_FLAGS = ["-movflags", "+faststart"]

# Narration (docs/specs/narration.md): the fixed voice and the deterministic mp3
# encode bitrate. Downstream stages read these; `vibe narrate` encodes to them.
NARRATION_VOICE = "en-US-ChristopherNeural"
NARRATION_MP3_BITRATE = "192k"


def contract_dict() -> dict[str, object]:
    """The media contract as a JSON-serialisable dict (recorded in the manifest)."""
    return {
        "schema_version": 1,
        "full": {"width": FULL_WIDTH, "height": FULL_HEIGHT, "fps": FPS},
        "short": {"width": SHORT_WIDTH, "height": SHORT_HEIGHT, "fps": FPS},
        "video": {"codec": VIDEO_CODEC, "profile": VIDEO_PROFILE, "pix_fmt": PIX_FMT},
        "audio": {
            "codec": AUDIO_CODEC,
            "profile": AUDIO_PROFILE,
            "sample_rate": AUDIO_SAMPLE_RATE,
            "channels": AUDIO_CHANNELS,
        },
        "encode": {
            "video": list(VIDEO_ENCODE_FLAGS),
            "audio": list(AUDIO_ENCODE_FLAGS),
            "mux": list(MUX_FLAGS),
        },
    }
