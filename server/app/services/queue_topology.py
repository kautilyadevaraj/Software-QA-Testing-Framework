"""Phase 3 RabbitMQ topology — main queue + dead-letter-exchange (DLX) + DLQ.

Topology:

    phase3_test_jobs (main)
        │   x-dead-letter-exchange = phase3.dlx
        ▼   (on reject/no-requeue)
    phase3.dlx  (direct exchange)
        │
        ▼
    phase3_test_jobs.dead (DLQ — poison-pill jobs go here)

A job is dead-lettered when:
  1. Worker rejects with basic_reject(requeue=False) after attempts >= max
  2. Queue length limit exceeded (not used here)
  3. TTL expires (not used here)

Call `declare_topology(channel)` once before publishing or consuming.
It is idempotent and safe to call from every producer/consumer connection.

If an OLDER durable queue exists without DLX args, declaration will fail with
INEQUIVALENT_ARGS. In that case the caller should log a clear migration
message and the operator must delete the old queue once drained.
"""
from __future__ import annotations

import logging
from typing import Any

import pika
from pika.exceptions import ChannelClosedByBroker

from app.core.config import settings

logger = logging.getLogger(__name__)


def _main_queue_args() -> dict[str, Any]:
    return {
        "x-dead-letter-exchange": settings.rabbitmq_dlx,
        "x-dead-letter-routing-key": settings.rabbitmq_dlq,
    }


def declare_topology(channel: pika.adapters.blocking_connection.BlockingChannel) -> None:
    """Declare DLX + DLQ + main queue with DLX bindings.

    Idempotent: safe to call many times. Raises if the existing main queue
    has incompatible arguments (INEQUIVALENT_ARGS); the caller must then
    manually drop the old queue after draining it.
    """
    # 1) Dead-letter exchange — durable, direct
    channel.exchange_declare(
        exchange=settings.rabbitmq_dlx,
        exchange_type="direct",
        durable=True,
    )

    # 2) Dead-letter queue — durable, no DLX on itself
    channel.queue_declare(queue=settings.rabbitmq_dlq, durable=True)
    channel.queue_bind(
        queue=settings.rabbitmq_dlq,
        exchange=settings.rabbitmq_dlx,
        routing_key=settings.rabbitmq_dlq,
    )

    # 3) Main queue — durable, with DLX args
    try:
        channel.queue_declare(
            queue=settings.rabbitmq_queue,
            durable=True,
            arguments=_main_queue_args(),
        )
    except ChannelClosedByBroker as exc:
        # 406 PRECONDITION_FAILED — queue exists with different arguments.
        if exc.reply_code == 406:
            logger.error(
                "queue_topology: existing queue %r has INEQUIVALENT_ARGS. "
                "Drain it (or delete it in RabbitMQ mgmt) and restart. "
                "Expected args: %s",
                settings.rabbitmq_queue,
                _main_queue_args(),
            )
        raise


def republish_with_attempt(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    body: bytes,
) -> None:
    """Publish a retry message back to the main queue (persistent)."""
    channel.basic_publish(
        exchange="",
        routing_key=settings.rabbitmq_queue,
        body=body,
        properties=pika.BasicProperties(delivery_mode=2),
    )
