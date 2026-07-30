import asyncio
import logging
import uuid
from typing import Optional

from app.api import notifications_store
from app.voice import tts
from app.voice.audio_reliability import AudioReliabilityResult

log = logging.getLogger("notification_delivery")


async def create_spoken_notification(
    source_id: str, transcript: str, spoken_message: str, confidence: Optional[float] = None
) -> None:
    """Shared by every pipeline that needs to raise a spoken notification -
    NOTE's and GO's audio-unreliable case (via notify_audio_unreliable
    below), and GO's van-build-log-uncertain and semantic-ambiguity cases
    (called directly from entries_runner.py, which has no
    AudioReliabilityResult to wrap).

    Best-effort side channel, deliberately never raises - a failure here
    (TTS call, disk write) is logged and swallowed, not surfaced as a
    pipeline failure.
    """
    try:
        audio_bytes = await asyncio.to_thread(tts.synthesize_speech, spoken_message)
        await asyncio.to_thread(
            notifications_store.create_notification,
            notification_id=str(uuid.uuid4()),
            source_voice_inbox_id=source_id,
            transcript=transcript,
            confidence=confidence,
            spoken_message=spoken_message,
            audio_bytes=audio_bytes,
        )
    except Exception as exc:  # noqa: BLE001 - see docstring: must never fail the calling pipeline
        log.warning("notification creation failed for source_id=%s: %r", source_id, exc)


async def notify_audio_unreliable(source_id: str, transcript: str, result: AudioReliabilityResult) -> None:
    """Thin wrapper over create_spoken_notification() for the
    AudioReliabilityResult shape - kept as its own function since it's the
    most common call site (voice_inbox_runner.py and entries_runner.py both
    use it as their first step)."""
    await create_spoken_notification(source_id, transcript, result.spoken_message, result.confidence)
