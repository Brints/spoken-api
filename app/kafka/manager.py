"""Kafka Manager module.

This module provides the central `KafkaManager` singleton responsible
for orchestrating the lifecycles of all producers, consumers, and topics
during the FastAPI framework startup and shutdown events.
"""

import logging
from typing import Optional

from app.core.config import settings
from app.core.sanitize import sanitize_log_args
from app.kafka.consumer import BaseConsumer
from app.kafka.producer import KafkaProducer
from app.services.email_consumer import EmailConsumerWorker

logger = logging.getLogger(__name__)


class KafkaManager:
    """Singleton manager responsible for Kafka lifecycle.

    This manager provisions required topics, initializes the global
    Kafka producer, and starts the asynchronous tasks for all registered
    consumers.

    Example:
        manager = get_kafka_manager()
        manager.register_consumer(MyEmailConsumer())
        await manager.start()   # called from FastAPI lifespan
        await manager.stop()    # called from FastAPI lifespan
    """

    _instance: Optional["KafkaManager"] = None
    _initialized: bool = False

    def __new__(cls) -> "KafkaManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self.producer = KafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS
        )
        self.consumers: list[BaseConsumer] = []

        # Import locally to avoid circular dependencies
        from app.services.stt_worker import STTWorker
        from app.services.translation_worker import TranslationWorker
        from app.services.tts_worker import TTSWorker

        self.register_consumer(EmailConsumerWorker(producer=self.producer))
        self.register_consumer(STTWorker(producer=self.producer))
        self.register_consumer(TranslationWorker(producer=self.producer))
        self.register_consumer(TTSWorker(producer=self.producer))

        self._initialized = True

    def register_consumer(self, consumer: BaseConsumer) -> None:
        """
        Register a consumer to be started when the manager starts.
        The producer is injected into the consumer at this point so it
        can access it for DLQ forwarding without a circular import.
        """
        consumer._producer = self.producer
        self.consumers.append(consumer)
        topic_safe = sanitize_log_args(consumer.topic)[0]
        logger.info("Registered consumer for topic: '%s'", topic_safe)

    async def _init_topics(self) -> None:
        """Create required topics if they don't exist, then enforce retention."""
        from aiokafka.admin import (  # type: ignore[import-untyped]
            AIOKafkaAdminClient,
            NewTopic,
        )

        from app.kafka.topics import (
            AUDIO_RAW,
            AUDIO_SYNTHESIZED,
            TEXT_ORIGINAL,
            TEXT_TRANSLATED,
            TOPICS_TO_CREATE,
        )

        admin_client = AIOKafkaAdminClient(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS
        )
        await admin_client.start()
        try:
            # DLQ topics for each required topic + standard topics
            new_topics = []
            for topic in TOPICS_TO_CREATE:
                new_topics.append(
                    NewTopic(name=topic, num_partitions=1, replication_factor=1)
                )
                new_topics.append(
                    NewTopic(
                        name=f"dlq.{topic}", num_partitions=1, replication_factor=1
                    )
                )

            # Check existing topics
            existing_topics = await admin_client.list_topics()
            topics_to_create_metadata = [
                t for t in new_topics if t.name not in existing_topics
            ]

            if topics_to_create_metadata:
                topic_names = [t.name for t in topics_to_create_metadata]
                logger.info("Creating missing Kafka topics: %s", topic_names)
                await admin_client.create_topics(topics_to_create_metadata)

            # --- Enforce short retention on real-time pipeline topics ---
            # 5 minutes is more than enough for any active session; anything
            # older is from a dead session and should be discarded automatically.
            # This works whether the topic was just created or already existed.
            PIPELINE_TOPICS = [
                AUDIO_RAW,
                AUDIO_SYNTHESIZED,
                TEXT_ORIGINAL,
                TEXT_TRANSLATED,
            ]
            RETENTION_MS = "300000"  # 5 minutes

            try:
                from aiokafka.admin import ConfigResource

                config_resources = [
                    ConfigResource(
                        resource_type="topic",
                        name=topic,
                        configs={"retention.ms": RETENTION_MS},
                    )
                    for topic in PIPELINE_TOPICS
                ]
                await admin_client.alter_configs(config_resources)
                logger.info(
                    "Set retention.ms=%s on pipeline topics: %s",
                    RETENTION_MS,
                    PIPELINE_TOPICS,
                )
            except Exception as alter_err:
                error_safe = sanitize_log_args(alter_err)[0]
                logger.warning(
                    "Could not set topic retention (non-fatal): %s", error_safe
                )

        except Exception as e:
            error_safe = sanitize_log_args(e)[0]
            logger.warning("Failed to auto-create Kafka topics: %s", error_safe)
        finally:
            await admin_client.close()

    async def start(self) -> None:
        """Start the producer, then all registered consumers."""
        logger.info("Starting Kafka Manager...")
        await self._init_topics()
        await self.producer.start()

        for consumer in self.consumers:
            # Pass bootstrap_servers at start-time — consumers don't store it
            await consumer.start(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)

        logger.info(
            "Kafka Manager started - %s consumer(s) running", len(self.consumers)
        )

    async def stop(self) -> None:
        """Stop all consumers first, then the producer."""
        logger.info("Stopping Kafka Manager...")

        for consumer in self.consumers:
            await consumer.stop()

        await self.producer.stop()
        logger.info("Kafka Manager stopped")

    async def health_check(self) -> dict:
        """
        Verify Kafka broker connectivity via a metadata probe.
        Uses the public producer.ping() API — no private attribute access.
        """
        if not self.producer.is_started:
            return {"status": "uninitialized", "details": "Producer not started"}

        try:
            await self.producer.ping()
            return {"status": "healthy"}
        except Exception as e:
            error_safe = sanitize_log_args(e)[0]
            logger.error("Kafka health check failed: %s", error_safe)
            return {"status": "unhealthy", "error": error_safe}


def get_kafka_manager() -> KafkaManager:
    """Return the KafkaManager singleton."""
    return KafkaManager()
