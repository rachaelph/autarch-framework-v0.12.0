"""Finite Container Apps Job entry point for InvoiceReady dispatch."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional
from uuid import uuid4

from .outbox_dispatcher import (
    InvoiceReadyPublisher,
    OutboxDispatchError,
    dispatch_next_invoice_ready,
)
from .persistence import DatabaseConnection
from .service_bus import (
    AzureServiceBusInvoiceReadyPublisher,
    ServiceBusPublisherConfigurationError,
)
from .sql_connection import (
    AzureSqlConfigurationError,
    AzureSqlConnectionError,
    connect_azure_sql,
)


def _log(event: str, **fields: Any) -> None:
    print(
        json.dumps(
            {
                "event": event,
                **fields,
            },
            sort_keys=True,
        ),
        flush=True,
    )


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_dispatch_job(
    connection: DatabaseConnection,
    publisher: InvoiceReadyPublisher,
    *,
    max_messages: int = 100,
) -> int:
    """Publish up to max_messages eligible outbox records."""
    if max_messages < 1:
        raise ValueError(
            "max_messages must be at least 1"
        )

    published = 0
    while published < max_messages:
        dispatched = dispatch_next_invoice_ready(
            connection,
            publisher,
        )
        if not dispatched:
            break
        published += 1

    return published


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Publish a bounded batch of InvoiceReady v1 "
            "events from the transactional outbox."
        )
    )
    parser.add_argument(
        "--sql-server",
        default=os.environ.get("CAPEX_SQL_SERVER"),
    )
    parser.add_argument(
        "--sql-database",
        default=os.environ.get("CAPEX_SQL_DATABASE"),
    )
    parser.add_argument(
        "--managed-identity-client-id",
        default=os.environ.get("AZURE_CLIENT_ID"),
    )
    parser.add_argument(
        "--service-bus-namespace",
        default=os.environ.get(
            "CAPEX_SERVICE_BUS_NAMESPACE"
        ),
    )
    parser.add_argument(
        "--topic-name",
        default=os.environ.get(
            "CAPEX_SERVICE_BUS_TOPIC",
            "capex-invoice-ready-v1",
        ),
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=int(
            os.environ.get(
                "CAPEX_DISPATCH_MAX_MESSAGES",
                "100",
            )
        ),
    )
    return parser


def _close(resource: object) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def main(
    argv: Optional[List[str]] = None,
    *,
    connection_factory: Callable[..., DatabaseConnection] = (
        connect_azure_sql
    ),
    publisher_factory: Callable[..., InvoiceReadyPublisher] = (
        AzureServiceBusInvoiceReadyPublisher
    ),
) -> int:
    args = _parser().parse_args(argv)
    dispatch_run_id = str(uuid4())
    connection: Optional[DatabaseConnection] = None
    publisher: Optional[InvoiceReadyPublisher] = None

    _log(
        "invoice_ready_dispatch_started",
        dispatchRunId=dispatch_run_id,
        startedAtUtc=_utc_now_text(),
        topicName=args.topic_name,
        maxMessages=args.max_messages,
    )

    try:
        connection = connection_factory(
            server_fqdn=args.sql_server,
            database_name=args.sql_database,
            managed_identity_client_id=(
                args.managed_identity_client_id
            ),
        )
        publisher = publisher_factory(
            fully_qualified_namespace=(
                args.service_bus_namespace
            ),
            topic_name=args.topic_name,
        )
        published = run_dispatch_job(
            connection,
            publisher,
            max_messages=args.max_messages,
        )
        _log(
            "invoice_ready_dispatch_completed",
            dispatchRunId=dispatch_run_id,
            completedAtUtc=_utc_now_text(),
            publishedCount=published,
        )
        return 0
    except (
        AzureSqlConfigurationError,
        ServiceBusPublisherConfigurationError,
        ValueError,
    ) as exc:
        _log(
            "invoice_ready_dispatch_configuration_error",
            dispatchRunId=dispatch_run_id,
            failedAtUtc=_utc_now_text(),
            error=f"{type(exc).__name__}: {exc}",
        )
        return 2
    except (
        AzureSqlConnectionError,
        OutboxDispatchError,
    ) as exc:
        _log(
            "invoice_ready_dispatch_failed",
            dispatchRunId=dispatch_run_id,
            failedAtUtc=_utc_now_text(),
            error=f"{type(exc).__name__}: {exc}",
        )
        return 1
    finally:
        if publisher is not None:
            _close(publisher)
        if connection is not None:
            _close(connection)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "main",
    "run_dispatch_job",
]
