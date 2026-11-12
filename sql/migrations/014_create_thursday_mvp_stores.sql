-- DBA-reviewed opt-in stores only. Does not alter Friday prep/task/capex/review tables.
SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;
    IF SCHEMA_ID(N'mvp_batch') IS NULL EXEC(N'CREATE SCHEMA mvp_batch AUTHORIZATION dbo;');
    IF SCHEMA_ID(N'mvp_review') IS NULL EXEC(N'CREATE SCHEMA mvp_review AUTHORIZATION dbo;');

    IF OBJECT_ID(N'mvp_batch.runs', N'U') IS NULL
        CREATE TABLE mvp_batch.runs (
            run_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
            input_sha char(64) NOT NULL,
            status varchar(32) NOT NULL,
            started_at varchar(40) NOT NULL,
            error nvarchar(max) NULL
        );
    IF OBJECT_ID(N'mvp_batch.checkpoints', N'U') IS NULL
        CREATE TABLE mvp_batch.checkpoints (
            run_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            unit nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
            payload nvarchar(max) NOT NULL CHECK (ISJSON(payload) = 1),
            PRIMARY KEY (run_id, unit),
            FOREIGN KEY (run_id) REFERENCES mvp_batch.runs(run_id)
        );
    IF OBJECT_ID(N'mvp_batch.attempts', N'U') IS NULL
        CREATE TABLE mvp_batch.attempts (
            id bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
            run_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            unit nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL,
            status varchar(64) NOT NULL,
            payload nvarchar(max) NOT NULL CHECK (ISJSON(payload) = 1),
            created_at varchar(40) NOT NULL,
            FOREIGN KEY (run_id) REFERENCES mvp_batch.runs(run_id)
        );
    IF OBJECT_ID(N'mvp_review.imports', N'U') IS NULL
        CREATE TABLE mvp_review.imports (
            run_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
            sha char(64) NOT NULL,
            payload nvarchar(max) NOT NULL CHECK (ISJSON(payload) = 1),
            imported_at varchar(40) NOT NULL
        );
    IF OBJECT_ID(N'mvp_review.documents', N'U') IS NULL
        CREATE TABLE mvp_review.documents (
            run_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            document_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            sha char(64) NOT NULL,
            content varbinary(max) NOT NULL CHECK (DATALENGTH(content) <= 20971520),
            PRIMARY KEY (run_id, document_id),
            FOREIGN KEY (run_id) REFERENCES mvp_review.imports(run_id)
        );
    IF OBJECT_ID(N'mvp_review.decisions', N'U') IS NULL
        CREATE TABLE mvp_review.decisions (
            run_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            document_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            version int NOT NULL CHECK (version > 0),
            payload nvarchar(max) NOT NULL CHECK (ISJSON(payload) = 1),
            previous_sha char(64) NULL,
            sha char(64) NOT NULL,
            created_at varchar(40) NOT NULL,
            PRIMARY KEY (run_id, document_id, version),
            FOREIGN KEY (run_id, document_id) REFERENCES mvp_review.documents(run_id, document_id)
        );

    IF DATABASE_PRINCIPAL_ID(N'capex_mvp_batch_runtime') IS NULL
        CREATE ROLE capex_mvp_batch_runtime AUTHORIZATION dbo;
    IF DATABASE_PRINCIPAL_ID(N'capex_mvp_review_runtime') IS NULL
        CREATE ROLE capex_mvp_review_runtime AUTHORIZATION dbo;
    GRANT SELECT, INSERT ON SCHEMA::mvp_batch TO capex_mvp_batch_runtime;
    GRANT UPDATE ON OBJECT::mvp_batch.runs TO capex_mvp_batch_runtime;
    GRANT SELECT, INSERT ON SCHEMA::mvp_review TO capex_mvp_review_runtime;
    COMMIT TRANSACTION;
    SELECT N'THURSDAY_MVP_STORES_READY' AS MigrationResult;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;

-- After separate identity/access approval, the DBA creates distinct Entra users
-- and adds each ONLY to its matching role. Agent/reference receive no SQL role.
-- CREATE USER [<batch-managed-identity-name>] FROM EXTERNAL PROVIDER;
-- ALTER ROLE capex_mvp_batch_runtime ADD MEMBER [<batch-managed-identity-name>];
-- CREATE USER [<review-managed-identity-name>] FROM EXTERNAL PROVIDER;
-- ALTER ROLE capex_mvp_review_runtime ADD MEMBER [<review-managed-identity-name>];
