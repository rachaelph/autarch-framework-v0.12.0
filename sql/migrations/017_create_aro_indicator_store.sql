SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'cict-ai-cus-sqldb' OR @@TRANCOUNT <> 0
    THROW 51340, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') IS NULL
    THROW 51341, 'Create the existing demo runtime user before granting ARO reference access.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    IF SCHEMA_ID(N'ref') IS NULL EXEC(N'CREATE SCHEMA ref AUTHORIZATION dbo;');
    IF OBJECT_ID(N'ref.AROIndicator', N'U') IS NULL
        CREATE TABLE ref.AROIndicator (
            IndicatorID int IDENTITY(1,1) NOT NULL
                CONSTRAINT PK_AROIndicator PRIMARY KEY,
            Keyword nvarchar(512) NOT NULL,
            Category varchar(128) NOT NULL,
            PolicyReference nvarchar(2000) NOT NULL,
            RequiresRemovalContext bit NOT NULL
                CONSTRAINT DF_AROIndicator_RequiresRemovalContext DEFAULT (1),
            Active bit NOT NULL CONSTRAINT DF_AROIndicator_Active DEFAULT (1),
            CONSTRAINT UQ_AROIndicator_CategoryKeyword
                UNIQUE NONCLUSTERED (Category, Keyword)
        );
    GRANT SELECT ON OBJECT::ref.AROIndicator TO [cict-ai-cus-capex-id];
    COMMIT TRANSACTION;
    SELECT 'ARO_INDICATOR_STORE_READY' AS MigrationResult;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
