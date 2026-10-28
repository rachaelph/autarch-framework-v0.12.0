"""Azure Function for InvoiceReady Task Determination."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func

from autarch.capex.agent_invocation_blob import (
    fetch_invoice_package_from_blob,
)
from autarch.capex.agent_invocation_handler import (
    invoke_task_determination_agent,
)
from autarch.capex.agent_invocation_models import (
    InvoiceReadyMessage,
)
from autarch.capex.agent_invocation_persistence import (
    load_task_catalog,
    persist_task_suggestion,
)
from autarch.capex.sql_connection import connect_azure_sql

app = func.FunctionApp()
logger = logging.getLogger("InvoiceAgentInvocation")


def _log(event: str, **fields: object) -> None:
    log_entry = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    logger.info(json.dumps(log_entry, default=str))


def _required_environment() -> dict[str, str]:
    names = (
        "CAPEX_BLOB_ACCOUNT_URL",
        "CAPEX_SQL_SERVER",
        "CAPEX_SQL_DATABASE",
        "AZURE_CLIENT_ID",
        "FOUNDRY_ENDPOINT",
        "FOUNDRY_DEPLOYMENT",
        "CAPEX_TASK_MASTER_SHA256",
    )
    values = {
        name: os.environ.get(name, "").strip()
        for name in names
    }
    missing = sorted(
        name
        for name, value in values.items()
        if not value
    )
    if missing:
        raise RuntimeError(
            "Missing required Function configuration: "
            + ", ".join(missing)
        )
    return values


def _parse_invoice_ready_message(
    message_body: bytes,
) -> InvoiceReadyMessage:
    message_dict = json.loads(message_body.decode("utf-8"))
    if not isinstance(message_dict, dict):
        raise ValueError(
            "InvoiceReady message body must be a JSON object."
        )
    return InvoiceReadyMessage.from_properties(message_dict)


@app.service_bus_topic_trigger(
    arg_name="msg",
    connection="ServiceBusConnection",
    topic_name="%CAPEX_SERVICE_BUS_TOPIC%",
    subscription_name="%CAPEX_SERVICE_BUS_SUBSCRIPTION%",
    cardinality="one",
)
def invoice_agent_invocation(
    msg: func.ServiceBusMessage,
) -> None:
    """Consume InvoiceReady and persist governed task results."""

    message_id = str(msg.message_id)

    try:
        invoice_ready = _parse_invoice_ready_message(
            msg.get_body()
        )
        correlation_id = (
            invoice_ready.correlation_id
            or msg.correlation_id
            or message_id
        )

        _log(
            "invoice_agent_invocation_started",
            messageId=message_id,
            eventId=invoice_ready.event_id,
            invoiceId=invoice_ready.invoice_id,
            packageId=invoice_ready.package_id,
            correlationId=correlation_id,
        )

        configuration = _required_environment()
        blob_container = os.environ.get(
            "CAPEX_BLOB_CONTAINER",
            "landing",
        )
        task_region = os.environ.get(
            "CAPEX_TASK_REGION",
            "US",
        )
        task_prompt_version = os.environ.get(
            "CAPEX_TASK_PROMPT_VERSION",
            "task-determination-v1",
        )

        invoice_package = fetch_invoice_package_from_blob(
            blob_account_url=configuration[
                "CAPEX_BLOB_ACCOUNT_URL"
            ],
            blob_container=blob_container,
            package_id=invoice_ready.package_id,
            run_id=invoice_ready.run_id,
            managed_identity_client_id=configuration[
                "AZURE_CLIENT_ID"
            ],
        )

        connection = connect_azure_sql(
            server_fqdn=configuration["CAPEX_SQL_SERVER"],
            database_name=configuration[
                "CAPEX_SQL_DATABASE"
            ],
            managed_identity_client_id=configuration[
                "AZURE_CLIENT_ID"
            ],
        )

        try:
            task_catalog = load_task_catalog(
                connection,
                region_code=task_region,
            )

            _log(
                "invoking_foundry_agent",
                eventId=invoice_ready.event_id,
                packageId=invoice_ready.package_id,
                taskCount=len(task_catalog),
                modelDeployment=configuration[
                    "FOUNDRY_DEPLOYMENT"
                ],
            )

            task_suggestion = (
                invoke_task_determination_agent(
                    invoice_package=invoice_package,
                    correlation_id=correlation_id,
                    package_id=invoice_ready.package_id,
                    run_id=invoice_ready.run_id,
                    invoice_id=invoice_ready.invoice_id,
                    foundry_endpoint=configuration[
                        "FOUNDRY_ENDPOINT"
                    ],
                    managed_identity_client_id=configuration[
                        "AZURE_CLIENT_ID"
                    ],
                    task_catalog=task_catalog,
                    deployment_name=configuration[
                        "FOUNDRY_DEPLOYMENT"
                    ],
                )
            )

            persisted_line_count = persist_task_suggestion(
                connection,
                task_suggestion,
                event_id=invoice_ready.event_id,
                region_code=task_region,
                model_deployment=configuration[
                    "FOUNDRY_DEPLOYMENT"
                ],
                prompt_version=task_prompt_version,
                task_master_sha256=configuration[
                    "CAPEX_TASK_MASTER_SHA256"
                ],
            )
        finally:
            if hasattr(connection, "close"):
                connection.close()

        _log(
            "invoice_agent_invocation_completed",
            eventId=invoice_ready.event_id,
            invoiceId=invoice_ready.invoice_id,
            packageId=invoice_ready.package_id,
            correlationId=correlation_id,
            persistedLineCount=persisted_line_count,
            status="success",
        )
    except Exception as exc:
        _log(
            "invoice_agent_invocation_error",
            messageId=message_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
