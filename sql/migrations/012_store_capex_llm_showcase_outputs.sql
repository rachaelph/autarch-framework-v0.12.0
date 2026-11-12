SET NOCOUNT ON;
SET XACT_ABORT ON;

IF SCHEMA_ID(N'capex') IS NULL
    OR DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') IS NULL
    THROW 51190, 'Apply the CapEx store migration and create the approved runtime contained user first.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    IF OBJECT_ID(N'capex.LlmShowcaseRun', N'U') IS NULL
    BEGIN
        CREATE TABLE capex.LlmShowcaseRun
        (
            WorkflowRunId              nvarchar(128) NOT NULL,
            SchemaVersion              varchar(32) NOT NULL,
            Status                     varchar(16) NOT NULL,
            ModelDeployment            nvarchar(128) NOT NULL,
            InputSha256                char(64) NOT NULL,
            WorkflowResultSha256       char(64) NOT NULL,
            EvidenceSha256             char(64) NOT NULL,
            LlmCallsAttempted          int NOT NULL,
            LlmResponsesReceived       int NOT NULL,
            AccountingInvoiceCount     int NOT NULL,
            AccountingExplanationCount int NOT NULL,
            BundleExplanationCount     int NOT NULL,
            EvidenceJson               nvarchar(max) NOT NULL,
            CreatedAtUtc               datetime2(3) NOT NULL,
            InsertedAtUtc              datetime2(3) NOT NULL
                CONSTRAINT DF_LlmShowcaseRun_InsertedAtUtc DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_LlmShowcaseRun PRIMARY KEY (WorkflowRunId),
            CONSTRAINT CK_LlmShowcaseRun_Version CHECK (SchemaVersion = '1.0-sql-llm-showcase'),
            CONSTRAINT CK_LlmShowcaseRun_Status CHECK (Status IN ('SUCCEEDED', 'INCOMPLETE')),
            CONSTRAINT CK_LlmShowcaseRun_Json CHECK (ISJSON(EvidenceJson) = 1),
            CONSTRAINT CK_LlmShowcaseRun_Digests CHECK (
                LEN(InputSha256) = 64 AND InputSha256 COLLATE Latin1_General_100_BIN2 NOT LIKE '%[^0-9a-f]%'
                AND LEN(WorkflowResultSha256) = 64 AND WorkflowResultSha256 COLLATE Latin1_General_100_BIN2 NOT LIKE '%[^0-9a-f]%'
                AND LEN(EvidenceSha256) = 64 AND EvidenceSha256 COLLATE Latin1_General_100_BIN2 NOT LIKE '%[^0-9a-f]%'
            ),
            CONSTRAINT CK_LlmShowcaseRun_Counts CHECK (
                LlmCallsAttempted >= 0 AND LlmResponsesReceived >= 0
                AND LlmResponsesReceived <= LlmCallsAttempted
                AND AccountingInvoiceCount >= 0 AND AccountingExplanationCount >= 0
                AND BundleExplanationCount >= 0
                AND (Status = 'INCOMPLETE' OR (
                    LlmCallsAttempted > 0 AND LlmCallsAttempted = LlmResponsesReceived
                    AND AccountingInvoiceCount > 0 AND AccountingExplanationCount > 0
                ))
            )
        );
    END;
    IF COL_LENGTH(N'capex.LlmShowcaseRun', N'EvidenceJson') IS NULL
        OR COL_LENGTH(N'capex.LlmShowcaseRun', N'EvidenceSha256') IS NULL
        OR COL_LENGTH(N'capex.LlmShowcaseRun', N'AccountingInvoiceCount') IS NULL
        OR COL_LENGTH(N'capex.LlmShowcaseRun', N'BundleExplanationCount') IS NULL
        THROW 51191, 'Existing LlmShowcaseRun table does not match migration 012; have the SQL operator review it.', 1;

    GRANT SELECT, INSERT ON OBJECT::capex.LlmShowcaseRun TO [cict-ai-cus-capex-id];
    COMMIT TRANSACTION;
    SELECT N'CAPEX_LLM_SQL_OUTPUT_STORE_READY' AS MigrationResult;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
