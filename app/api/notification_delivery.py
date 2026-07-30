import asyncio
import logging
import uuid

from app.api import notifications_store
from app.voice import tts
from app.voice.audio_reliability import AudioReliabilityResult

log = logging.getLogger("notification_delivery")


async def notify_audio_unreliable(source_id: str, transcript: str, result: AudioReliabilityResult) -> None:
    """Shared by voice_inbox_runner.py (NOTE) and entries_runner.py (GO):
    turns an unreliable AudioReliabilityResult into a spoken notification.

    Best-effort side channel, deliberately never raises - a failure here
    (TTS call, disk write) is logged and swallowed, not surfaced as a
    pipeline failure. Callers invoke this and then stop (NOTE still has
    nothing further to do; GO must not continue into the entry architect on
    a transcript that wasn't heard reliably).
    """
    try:
        audio_bytes = await asyncio.to_thread(tts.synthesize_speech, result.spoken_message)
        await asyncio.to_thread(
            notifications_store.create_notification,
            notification_id=str(uuid.uuid4()),
            source_voice_inbox_id=source_id,
            transcript=transcript,
            confidence=result.confidence,
            spoken_message=result.spoken_message,
            audio_bytes=audio_bytes,
        )
    except Exception as exc:  # noqa: BLE001 - see docstring: must never fail the calling pipeline
        log.warning("audio-clarity notification failed for source_id=%s: %r", source_id, exc)
