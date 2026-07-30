from app.config import get_settings
from app.voice.transcription import get_openai_client

# response_format="pcm" returns raw 24kHz/16-bit signed-LE mono samples with
# no container/header (OpenAI's fixed PCM output shape - see
# https://developers.openai.com/api/docs/guides/text-to-speech). Chosen
# specifically so the ESP32 can write the bytes straight to
# esp_codec_dev_write() once opened with this same fixed rate/depth/channel
# triple, with no WAV parsing needed on-device.
TTS_SAMPLE_RATE_HZ = 24000
TTS_BITS_PER_SAMPLE = 16
TTS_CHANNELS = 1


def synthesize_speech(text: str) -> bytes:
    """Generates spoken audio for `text` via OpenAI's TTS endpoint.

    Blocking, synchronous - meant to be called from a background asyncio
    task the same way transcribe_audio() already is (see
    asyncio.to_thread usage in voice_inbox_runner.py). Reuses
    transcription.get_openai_client()'s lazy client (same OPENAI_API_KEY,
    same "never built at import time" contract).
    """
    settings = get_settings()
    client = get_openai_client()

    response = client.audio.speech.create(
        model=settings.openai_tts_model,
        voice=settings.openai_tts_voice,
        input=text,
        response_format="pcm",
    )
    return response.read()
