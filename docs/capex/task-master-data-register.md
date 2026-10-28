# CapEx TaskMaster Data Register

## Source

- File: `examples/data/capex/Task_Type_Export_9_6_2026.xlsx`
- Source date/version: `2026-09-06`
- SHA-256:
  `CC41B492A5DE454EFD7405066EF1EAA6CDE1FF050C8E71ED180831197B6EBA13`
- Demo region: `US`
- Source sheet: `US Task Type`
- Approved task rows: `761`
- Active task rows: `761`
- Duplicate US task codes: `0`

The `US Books` sheet contains depreciation-book metadata and is not loaded for
the Task Determination demo.

The `CN Task Type` and `CN Book` sheets are retained in the source workbook but
are not loaded for the approved US demo invoices.

## SQL ownership

- Owning table: `ref.TaskMaster`
- Published read contract: `pub.v1_TaskMaster`
- Generated seed: `sql/data/005_seed_us_task_master.sql`
- Generator: `tools/generate_capex_task_master_seed.py`

Do not write reference data directly to the `pub` schema.

## Load behavior

The generated seed:

- verifies the expected 761-row count;
- rejects duplicate region/task-code pairs;
- uses the source SHA-256 as immutable provenance;
- keeps prior snapshots while marking them non-current;
- makes one source version current per region/task code;
- runs in a transaction with `XACT_ABORT ON`;
- rolls back if post-load validation fails;
- is safe to rerun for the same source workbook.

## Demo use

The Task Determination workflow must read active US task codes through
`pub.v1_TaskMaster`.

The model may suggest only a task code that exists in the published view.
If no supported task can be selected, the line must be recorded as unresolved
rather than mapped to an invented task code.
