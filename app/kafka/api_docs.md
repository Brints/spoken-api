# FluentMeet Kafka Architecture Documentation

> **Package Location:** `/app/kafka`
> **Purpose:** Event-driven architecture infrastructure, abstracting AIOKafka underlying intricacies.

---

## Table of Contents

- [Overview](#overview)
- [Architecture & Lifecycle](#architecture--lifecycle)
- [Topic Registry](#topic-registry)
- [Producers & Consumers](#producers--consumers)
  - [KafkaProducer (`producer.py`)](#kafkaproducer-producerpy)
  - [BaseConsumer (`consumer.py`)](#baseconsumer-consumerpy)
- [Dead Letter Queues (DLQ) & Retries](#dead-letter-queues-dlq--retries)
- [Event Schemas (`schemas.py`)](#event-schemas-schemaspy)
- [Error Handling (`exceptions.py`)](#error-handling-exceptionspy)

---

## Overview

The `app/kafka` package provides a high-level, strongly-typed asynchronous wrapper over `aiokafka`. 
It entirely hides the serialization mechanisms from feature-level developers and implements hardened stability patterns out-of-the-box including Singleton Lifecycle Management, Automatic Topic Provisioning, Manual Offset Commits, Linear Retry Backoffs, and automatic Dead Letter Queue (DLQ) routing.

---

## Architecture & Lifecycle

The package revolves around the `KafkaManager` (`manager.py`). 
This is a strictly controlled Singleton bound to the FastAPI application lifespan (typically started inside `app/main.py @asynccontextmanager`).

**Lifecycle sequence:**
1. **Instantiate:** `get_kafka_manager()` creates the `KafkaProducer` and registers instances of `BaseConsumer` (e.g., `EmailConsumerWorker`, `STTWorker`).
2. **Start (`manager.start()`):**
   - Automatically provisions missing Kafka topics defined in `topics.py` using `AIOKafkaAdminClient`.
   - Starts the global `KafkaProducer`.
   - Starts background `asyncio.Task` loops for each registered `BaseConsumer`.
3. **Run:** The application accepts requests, firing items into the Producer, and Consumers eagerly rip items from the broker.
4. **Shutdown (`manager.stop()`):**
   - Gently cancels and awaits all consumer `asyncio.Task` loops.
   - Cleans up and stops the producer.

---

## Topic Registry

Defined in `topics.py`. All standard strings are prefixed or namespaced by domain. The manager auto-creates these alongside their mirror `dlq.` prefixes.

| Topic Constant        | String                | Purpose                                                     |
|-----------------------|-----------------------|-------------------------------------------------------------|
| `NOTIFICATIONS_EMAIL` | `notifications.email` | Dispatch queue for Jinja2 rendered SMTP emails via Mailgun. |
| `AUDIO_RAW`           | `audio.raw`           | Stage 1 WebSocket base64 binary PCM streams.                |
| `TEXT_ORIGINAL`       | `text.original`       | Stage 2 original STT transcription strings.                 |
| `TEXT_TRANSLATED`     | `text.translated`     | Stage 3 Multi-casted translation blocks.                    |
| `AUDIO_SYNTHESIZED`   | `audio.synthesized`   | Stage 4 TTS returning audio binary blocks for egress.       |

*(Media upload topics like `media.upload` are registered but currently inactive awaiting feature expansions).*

---

## Producers & Consumers

### KafkaProducer (`producer.py`)

A clean abstraction over `AIOKafkaProducer`.
- **Serialization:** Forces all payloads through `json.dumps` natively. Requires developers to pass Pydantic `BaseEvent` models.
- **Methods:** `send(topic, event, key)` and a `.ping()` health-check tool to verify broker connectivity via forcing metadata refreshes.

### BaseConsumer (`consumer.py`)

An `abc.ABC` parent class that all worker daemons (like `STTWorker`) must inherit from.
Subclasses implement a single asynchronous method: `async def handle(self, event: BaseEvent) -> None`.

**Built-In Resiliency Features:**
1. **Manual Commits:** By default, disables `auto_commit`. An offset block is *only* marked as processed on the broker if the `.handle()` function exits flawlessly. A pod crash mid-process guarantees message re-delivery.
2. **Typed Context:** Automatically intercepts incoming `bytes`, unpacks the JSON, and leverages the subclass's declared `event_schema` to build and validate a Pydantic object before passing it inside `.handle()`.

---

## Dead Letter Queues (DLQ) & Retries

If `.handle()` throws an Exception, the BaseConsumer automatically traps it and triggers the **Retry Matrix**.

1. **Linear Backoff:** Uses `settings.KAFKA_MAX_RETRIES` (default 3) and `settings.KAFKA_RETRY_BACKOFF_MS`. A failed event sleeps its asynchronous task scaling linearly (e.g., attempt 1 sleeps 1s, attempt 2 sleeps 2s).
2. **DLQ Routing:** If the max retries are exhausted, the event is permanently considered unrecoverable (poison pill).
3. Instead of stalling the Kafka partition, the Consumer packages the original failed payload + integer retry counters + text exception names into a rigid **`DLQEvent`** schema.
4. It commands the *Producer* to fling this DLQ object into `dlq.{original_topic}` (e.g., `dlq.notifications.email`).
5. The offset is *then* committed, allowing the partition to move forward.

---

## Event Schemas (`schemas.py`)

All objects traversing the Kafka broker must inherit from `BaseEvent[T]`.

**`BaseEvent` Wrapper:** 
Every payload gets an automatic unique UUID `event_id` and an ISO UTC `timestamp`. This is crucial for tracking events across distributed tracing platforms.

**`DLQEvent`:**
```json
{
  "original_event_id": "uuid",
  "original_topic": "notifications.email",
  "original_event": {  },
  "error_message": "TransientEmailDeliveryError: Mailgun 500",
  "failed_at": "datetime",
  "retry_count": 3
}
```

*(Note: The high-speed pipeline payloads are located centrally in `/app/schemas/pipeline.py` rather than here, separating abstract infrastructure schemas from heavy feature logic).*

---

## Error Handling (`exceptions.py`)

Extends the core `FluentMeetException` allowing HTTP frameworks or health checks to parse standard Error Codes.
- `KafkaConnectionError`
- `KafkaPublishError`
- `KafkaConsumeError`
