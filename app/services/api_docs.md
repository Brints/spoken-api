# FluentMeet Core Services Documentation

> **Package Location:** `/app/services`
> **Purpose:** Core Business Logic, Kafka Workers, WebSockets, and Communications.

---

## Table of Contents

- [Overview](#overview)
- [Real-time Audio Pipeline Workers](#real-time-audio-pipeline-workers)
  - [1. AudioIngestService (`audio_bridge.py`)](#1-audioingestservice-audio_bridgepy)
  - [2. STTWorker (`stt_worker.py`)](#2-sttworker-stt_workerpy)
  - [3. TranslationWorker (`translation_worker.py`)](#3-translationworker-translation_workerpy)
  - [4. TTSWorker (`tts_worker.py`)](#4-ttsworker-tts_workerpy)
- [WebSocket Connection Management](#websocket-connection-management)
  - [ConnectionManager (`connection_manager.py`)](#connectionmanager-connection_managerpy)
- [Email & Notification Services](#email--notification-services)
  - [EmailProducerService (`email_producer.py`)](#emailproducerservice-email_producerpy)
  - [EmailConsumerWorker (`email_consumer.py`)](#emailconsumerworker-email_consumerpy)

---

## Overview

The `app/services` package houses the heavy-lifting logic that connects FastAPI routers to external infrastructure (Kafka, Redis, Mailgun, AI Providers). 

Unlike module-specific services (e.g., `UserService` or `AuthService` which are mostly DB wrappers), the components in this package are highly asynchronous, globally utilized, and predominantly event-driven.

---

## Real-time Audio Pipeline Workers

The real-time AI audio pipeline is driven by a series of autonomous Kafka consumers (Workers) living in this package.

### 1. AudioIngestService (`audio_bridge.py`)
- **Role:** Web-to-Kafka Bridge (Producer).
- **Behavior:** Called directly by the FastAPI WebSocket routers when binary audio frames arrive from a browser. It maintains an internal monotonic `sequence_number` per user, base64 encodes the binary PCM blob, and pushes an `AudioChunkEvent` to the **`audio.raw`** Kafka topic.

### 2. STTWorker (`stt_worker.py`)
- **Role:** Speech-to-Text transcriber.
- **Topic Subscription:** **`audio.raw`** 
- **Topic Publication:** **`text.original`**
- **Behavior:** Iterates through arriving raw audio events. Calls out to the active AI Service (`Deepgram` by default) to decode the speech. Emits a `TranscriptionEvent`. Also includes logic to mock the STT layer locally if no `DEEPGRAM_API_KEY` is present.

### 3. TranslationWorker (`translation_worker.py`)
- **Role:** Target language resolution and translation.
- **Topic Subscription:** **`text.original`**
- **Topic Publication:** **`text.translated`**
- **Behavior:** 
  1. Intercepts `final` transcriptions.
  2. Reaches into Redis via `MeetingStateService` to fetch the live roster for the `room_id`.
  3. Cultivates a unique `Set` of target listener languages present in the room.
  4. Calls `DeepL` (with an automatic `OpenAI` fallback if DeepL fails or a language is unsupported).
  5. Multi-casts loop: Publishes exactly one `TranslationEvent` per target language needed.

### 4. TTSWorker (`tts_worker.py`)
- **Role:** Text-to-Speech synthesis.
- **Topic Subscription:** **`text.translated`**
- **Topic Publication:** **`audio.synthesized`**
- **Behavior:** Takes translated text snippets and calls an asynchronous synthesis provider (governed by the `ACTIVE_TTS_PROVIDER` setting, allowing toggling between `OpenAI` and `Voice.ai`). Emits base64 application audio frames ready for clients to ingest back over their open WebSockets.

---

## WebSocket Connection Management

### ConnectionManager (`connection_manager.py`)
- **Role:** Multi-pod scaling for WebSocket connections.
- **Behavior:** Standard FastAPI Websocket lists fail the moment you scale to 2+ pods or workers, because users in the same room might be connected to different pods. 
- **Architecture (Redis Pub/Sub):**
  - Maintains a local memory `dict` of active websocket clients.
  - Automatically spins up an `asyncio.Task` to subscribe to a Redis channel named `ws:room:{room_code}` when the first user joins a room.
  - Exposes `broadcast_to_room()` and `send_to_user()`. When called, these serialize the message and publish it to the Redis channel securely multi-casting across all active backend pods instantly.
  - The internal subscriber `_listen_to_redis()` task pulls payloads off the Redis backplane and commands the local `WebSocket` items to transmit JSON back to clients.

---

## Email & Notification Services

### EmailProducerService (`email_producer.py`)
- **Role:** Non-blocking async queue offloader.
- **Topic:** **`notifications.email`**
- **Behavior:** Injected into HTTP endpoints (e.g., `POST /auth/forgot-password`). It prevents endpoints from hanging on HTTP mailer calls. It accepts subject blocks, a template name, and its dictionary context payload, emitting it into Kafka.

### EmailConsumerWorker (`email_consumer.py`)
- **Role:** Dedicated template rendering and mailer HTTP agent.
- **Topic Subscription:** **`notifications.email`**
- **Behavior:** 
  1. Pulls email requests out of the Kafka broker.
  2. Utilizes **Jinja2** to compile the injected context variables against atomic HTML files stored in the `app/templates/email/` directory.
  3. Opens an async `httpx` HTTP session against the integrated **Mailgun V3** REST API, handling authorization natively. Includes transient error trapping capable of failing out in a way that respects Kafka's natural message retry architecture.
