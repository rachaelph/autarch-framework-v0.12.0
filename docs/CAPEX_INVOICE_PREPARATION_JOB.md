# CapEx Invoice Preparation Job

## Purpose

The Invoice Preparation Job deterministically converts ABBYY Vantage ZIP packages into validated `InvoicePackage v1` JSON documents. It discovers ZIP inputs, parses each through the governed ABBYY adapter, assembles and validates packages, writes successful outputs and `_run-summary.json` atomically, and then exits.

It does not invoke AI, write to Azure SQL, publish Service Bus messages, or post invoices to an ERP.

## Command and configuration

Run `capex-prepare` with `--input-dir`, `--output-dir`, and optionally `--run-id`. Equivalent environment variables are `CAPEX_INPUT_DIR` (default `/input`), `CAPEX_OUTPUT_DIR` (default `/output`), and `CAPEX_RUN_ID`.

Use the same run ID when retrying the same logical execution. Package IDs are derived from the run ID and invoice ID, so retries atomically replace the same logical output instead of creating duplicates.

## Outputs and exit codes

Successful packages are written as `/output/<package-id>.json`; the execution summary is `/output/_run-summary.json`. The summary contains processing metadata and failure descriptions, not raw invoice content, banking details, source PDFs, or images.

- `0`: all discovered packages succeeded, or no packages were present.
- `1`: at least one package failed; remaining packages were still attempted.
- `2`: job-level configuration or startup failure.

## Container contract

`Dockerfile.capex` creates a finite production job image. It runs as non-root UID `10001`, contains no sample invoices, reads `/input`, writes `/output`, starts `capex-prepare`, and exits after processing. Docker was unavailable on the development workstation, so the Dockerfile contract is covered by automated tests and the image must also be built in CI.

## Azure Container Apps Job

`infra/container-apps/capex-invoice-preparation-job.bicep` defines the scheduled job. Defaults are schedule `0 2 * * *` UTC, parallelism `1`, required completions `1`, retry limit `2`, timeout `1800` seconds, CPU `0.5`, memory `1Gi`, and a system-assigned managed identity. The image, Container Apps environment, registry, schedule, retries, timeout, CPU, and memory are deployment parameters.

The template passed Azure CLI Bicep compilation. It has not been deployed and does not modify `rg_learning`.

## Current deployment boundary

Before live scheduled processing:

1. Build and push the image to the approved Azure Container Registry.
2. Grant the job identity `AcrPull` on the registry.
3. Attach approved durable storage at `/input` and `/output`.
4. Grant only required data-plane roles to the job identity.
5. Configure monitoring and execution-failure alerts.
6. Verify a manual execution with non-production packages.
7. Enable the nightly schedule after that verification.

Durable storage, SQL persistence, `InvoiceReady v1` publication, and ERP integration remain separate work.