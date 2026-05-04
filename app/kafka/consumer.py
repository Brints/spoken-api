import abc
import asyncio
import contextlib
import json
import logging
from typing import Any

from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]

from app.core.config import settings
from app.core.sanitize import sanitize_log_args
from app.kafka.schemas import BaseEvent, DLQEvent
from app.kafka.topics import DLQ_PREFIX

logger = logging.getLogger(__name__)


class BaseConsumer(abc.ABC):
    """
    Abstract base class for all Kafka consumers.

    Subclasses must declare class-level attributes:
        topic: str          — the Kafka topic to subscribe to
        group_id: str       — the consumer group identifier
        event_schema: Type  — the Pydantic BaseEvent subclass for deserialization

    Features:
        - Manual offset commits (offsets only committed after successful handle())
        - Configurable linear backoff retry with KAFKA_MAX_RETRIES
        - Dead-letter queue (DLQ) forwarding via a proper DLQEvent wrapper
        - Graceful shutdown via asyncio.Task cancellation
    """

    topic: str
    group_id: str
    event_schema: type[BaseEvent[Any]]

    # Declared here so Mypy can track it on the class body
    _initialized: bool = False

    def __init__(self, producer: Any) -> None:
        """
        Args:
            producer: A KafkaProducer instance injected by KafkaManager.
                      Used to forward failed events to the DLQ.
        """
        # Import here to avoid a circular module-level import
        from app.kafka.producer import KafkaProducer

        self._producer: KafkaProducer = producer
        self._consumer: AIOKafkaConsumer | None = None
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task] = set()

    async def start(self, bootstrap_servers: str) -> None:
        """
        Start the consumer background task.
        Called by KafkaManager, which supplies the bootstrap_servers string.
        """
        if self._running:
            return

        self._running = True
        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=bootstrap_servers,
            group_id=self.group_id,
            auto_offset_reset=settings.KAFKA_CONSUMER_AUTO_OFFSET_RESET,
            # Manual commit: offsets are committed only after handle() succeeds,
            # preventing silent message loss on pod restart mid-retry.
            enable_auto_commit=False,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )
        await self._consumer.start()
        topic_safe, group_safe = sanitize_log_args(self.topic, self.group_id)
        logger.info("Consumer for '%s' (group: '%s') started", topic_safe, group_safe)

        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        """Stop the consumer background task gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        if self._consumer:
            await self._consumer.stop()
            self._consumer = None

        topic_safe = sanitize_log_args(self.topic)[0]
        logger.info("Consumer for '%s' stopped", topic_safe)

    async def _consume_loop(self) -> None:
        """Main consumption loop."""
        if not self._consumer:
            return

        try:
            async for msg in self._consumer:
                if not self._running:
                    break

                try:
                    event = self.event_schema.model_validate(msg.value)
                    await self._process_with_retry(event)
                    # Only commit after successful processing
                    await self._consumer.commit()
                except Exception:
                    topic_safe = sanitize_log_args(self.topic)[0]
                    logger.exception(
                        "Unrecoverable error on message from '%s'. "
                        "Skipping commit - offset will be re-delivered on restart.",
                        topic_safe,
                    )
        except asyncio.CancelledError:
            pass
        except Exception:
            topic_safe = sanitize_log_args(self.topic)[0]
            logger.exception("Unexpected error in consumer loop for '%s'", topic_safe)

    async def _process_with_retry(self, event: BaseEvent[Any]) -> None:
        """
        Process an event with linear backoff retries.
        After exhausting all retries, routes the event to the DLQ.
        """
        last_error: Exception | None = None

        for attempt in range(settings.KAFKA_MAX_RETRIES + 1):
            try:
                await self.handle(event)
                return  # Success
            except Exception as e:
                last_error = e
                if attempt < settings.KAFKA_MAX_RETRIES:
                    wait_secs = (settings.KAFKA_RETRY_BACKOFF_MS / 1000) * (attempt + 1)
                    event_id_safe, error_safe = sanitize_log_args(event.event_id, e)
                    logger.warning(
                        "Retry %s/%s for event %s in %.1fs. Reason: %s",
                        attempt + 1,
                        settings.KAFKA_MAX_RETRIES,
                        event_id_safe,
                        wait_secs,
                        error_safe,
                    )
                    await asyncio.sleep(wait_secs)

        event_id_safe = sanitize_log_args(event.event_id)[0]
        logger.error(
            "Event %s failed after %s retries. Routing to DLQ.",
            event_id_safe,
            settings.KAFKA_MAX_RETRIES,
        )
        await self._send_to_dlq(
            event, str(last_error), retries=settings.KAFKA_MAX_RETRIES
        )

    async def _send_to_dlq(
        self, event: BaseEvent[Any], error_message: str, retries: int
    ) -> None:
        """
        Forward a failed event to its Dead Letter Queue topic.
        Wraps it in a DLQEvent — a proper structured schema — instead of
        mutating the original event payload.
        """
        dlq_topic = f"{DLQ_PREFIX}{self.topic}"
        dlq_event = DLQEvent(
            original_event_id=event.event_id,
            original_topic=self.topic,
            original_event=event.model_dump(),
            error_message=error_message,
            retry_count=retries,
        )

        try:
            # Use the injected producer directly — no circular import needed
            dlq_payload = dlq_event.model_dump()
            await self._producer._producer.send_and_wait(  # type: ignore[union-attr]
                dlq_topic,
                value=json.dumps(dlq_payload, default=str).encode("utf-8"),
            )
            event_id_safe, dlq_topic_safe = sanitize_log_args(event.event_id, dlq_topic)
            logger.info(
                "Event %s forwarded to DLQ topic '%s'",
                event_id_safe,
                dlq_topic_safe,
            )
        except Exception:
            event_id_safe, dlq_topic_safe = sanitize_log_args(event.event_id, dlq_topic)
            logger.exception(
                "CRITICAL: Failed to forward event %s to '%s'."
                " Event is permanently lost.",
                event_id_safe,
                dlq_topic_safe,
            )

    @abc.abstractmethod
    async def handle(self, event: BaseEvent[Any]) -> None:
        """Implement message processing logic in subclasses."""
