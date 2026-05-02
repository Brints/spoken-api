# FluentMeet Schemas Documentation

> **Package Location:** `/app/schemas`
> **Purpose:** Global Pydantic definitions and Kafka Real-time Pipeline Schemas.

---

## Table of Contents

- [Overview](#overview)
- [Pipeline Architecture](#pipeline-architecture)
- [Pipeline Stages & Schemas](#pipeline-stages--schemas)
  - [Stage 1: Raw Audio Ingest](#stage-1-raw-audio-ingest)
  - [Stage 2: Transcribed Text](#stage-2-transcribed-text)
  - [Stage 3: Translated Text](#stage-3-translated-text)
  - [Stage 4: Synthesized Audio](#stage-4-synthesized-audio)
- [Data structures](#data-structures)
- [Enums](#enums)

---

## Overview

Unlike module-specific schemas (e.g., `app/modules/auth/schemas.py`), the `/app/schemas` package contains global and cross-boundary DTOs (Data Transfer Objects). Primarily, it defines the rigid contract used by the **Real-Time Audio Processing Pipeline** flowing through Kafka.

These schemas ensure that the FastAPI web consumers, STT workers, Translation workers, and TTS workers all serialize and deserialize their payloads using identical schemas and base-64 encodings format.

All pipeline events inherit from `BaseEvent[T]` (from `app.kafka.schemas`) allowing metadata headers to envelop the core payloads documented below.

---

## Pipeline Architecture

The schemas correspond directly to the 4 stages of the real-time processing loop orchestrated over Apache Kafka:

```
[ WebSocket Client (Binary) ]
           │
           ▼
[ STAGE 1: audio.raw ]           ───▶  AudioChunkEvent
           │
           ▼
[ STAGE 2: text.original ]       ───▶  TranscriptionEvent
           │
           ▼
[ STAGE 3: text.translated ]     ───▶  TranslationEvent
           │
           ▼
[ STAGE 4: audio.synthesized ]   ───▶  SynthesizedAudioEvent
           │
           ▼
[ WebSocket Egress (Binary) ]
```

---

## Pipeline Stages & Schemas

### Stage 1: Raw Audio Ingest

**Kafka Topic:** `audio.raw`
**Event wrapper:** `AudioChunkEvent` -> `{ event_type: "audio.chunk", payload: AudioChunkPayload }`

**`AudioChunkPayload`**
Represents a chunk of binary audio intercepted from an active WebSocket stream.

| Field             | Type            | Description                                                         |
|-------------------|-----------------|---------------------------------------------------------------------|
| `room_id`         | `string`        | The active room code.                                               |
| `user_id`         | `string`        | UUID or guest-tracking UUID of the active speaker.                  |
| `sequence_number` | `int`           | Monotonically increasing chunk index ensuring ordering per-speaker. |
| `audio_data`      | `string`        | Base64-encoded raw application binary bytes.                        |
| `sample_rate`     | `int`           | Default: `16000` (Hz).                                              |
| `encoding`        | `AudioEncoding` | Default: `linear16` (PCM 16-bit).                                   |
| `source_language` | `string`        | Language code (ISO 639-1) the user is speaking (e.g. `"en"`).       |

---

### Stage 2: Transcribed Text

**Kafka Topic:** `text.original`
**Event wrapper:** `TranscriptionEvent` -> `{ event_type: "text.transcription", payload: TranscriptionPayload }`

**`TranscriptionPayload`**
Produced by the Speech-to-Text Worker (Deepgram) converting the raw audio chunk into its native text.

| Field             | Type     | Description                                                            |
|-------------------|----------|------------------------------------------------------------------------|
| `room_id`         | `string` |                                                                        |
| `user_id`         | `string` |                                                                        |
| `sequence_number` | `int`    | Maintained from Stage 1.                                               |
| `text`            | `string` | The resulting recognized text.                                         |
| `source_language` | `string` | Captured or auto-detected source ISO code.                             |
| `is_final`        | `bool`   | Default: `True`. Marks interim vs finalized chunks in continuous mode. |
| `confidence`      | `float`  | `0.0` - `1.0`. Accuracy confidence from the STT provider.              |

---

### Stage 3: Translated Text

**Kafka Topic:** `text.translated`
**Event wrapper:** `TranslationEvent` -> `{ event_type: "text.translation", payload: TranslationPayload }`

**`TranslationPayload`**
Produced by the Translation Worker (DeepL) when original text diverges from the room/listener requirements.

| Field             | Type     | Description                     |
|-------------------|----------|---------------------------------|
| `room_id`         | `string` |                                 |
| `user_id`         | `string` |                                 |
| `sequence_number` | `int`    | Maintained from Stage 2.        |
| `original_text`   | `string` | Sent over from Stage 2.         |
| `translated_text` | `string` | The targeted translated output. |
| `source_language` | `string` | ISO Code (e.g., `"en"`).        |
| `target_language` | `string` | Target ISO Code (e.g., `"fr"`). |

---

### Stage 4: Synthesized Audio

**Kafka Topic:** `audio.synthesized`
**Event wrapper:** `SynthesizedAudioEvent` -> `{ event_type: "audio.synthesized", payload: SynthesizedAudioPayload }`

**`SynthesizedAudioPayload`**
Produced by the Text-to-Speech Worker (OpenAI/Voice.ai) completing the loop. The WebSocket Egress consumer looks out for this and pipes the bytes back to the target clients.

| Field             | Type            | Description                                             |
|-------------------|-----------------|---------------------------------------------------------|
| `room_id`         | `string`        |                                                         |
| `user_id`         | `string`        |                                                         |
| `sequence_number` | `int`           | Maintained for client-side assembly ordering.           |
| `audio_data`      | `string`        | Base64-encoded newly synthesized AI voice binary bytes. |
| `target_language` | `string`        | Matching the TTS synthesis configuration.               |
| `sample_rate`     | `int`           | Default: `16000` (Hz).                                  |
| `encoding`        | `AudioEncoding` | Default: `linear16`.                                    |

---

## Data structures
All audio data inside the `AudioChunkPayload` and `SynthesizedAudioPayload` are strictly shipped as stringified **Base64** text. 
This bypasses binary limitation errors inside typical JSON-Kafka serializers keeping the system extremely fault resilient across serialization borders. Handlers are manually responsible for base64 decoding the block returning to byte arrays before delivery to the websocket streams or external TTS Providers APIs.

---

## Enums

### `AudioEncoding`

| Value      | Description                                                                                                          |
|------------|----------------------------------------------------------------------------------------------------------------------|
| `linear16` | Standard PCM 16-bit signed, little-endian format. Required for maximal compatibility over native Browser WebSockets. |
| `opus`     | Compressed format used primarily by higher-bandwidth connections if toggled active.                                  |
