SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Preview first. Pause jobs/review writers. Keep reference data and original blobs.
DECLARE @CommitReset bit = 0;
DECLARE @WritersPaused bit = 0;
DECLARE @ExpectedCountsJson nvarchar(max) = NULL; -- Replace NULL with N'<exact preview JSON>'.

IF DB_NAME() <> N'cict-ai-cus-sqldb' OR @@TRANCOUNT <> 0
    THROW 51300, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF @CommitReset IS NULL OR @WritersPaused IS NULL
    THROW 51301, 'Reset and writer-pause flags must explicitly be 0 or 1.', 1;
IF @CommitReset = 1 AND (@WritersPaused <> 1 OR @ExpectedCountsJson IS NULL
    OR ISJSON(@ExpectedCountsJson) <> 1)
    THROW 51301, 'Pause writers and supply the preview counts before committing.', 1;

DECLARE @Scope TABLE (
    DeleteOrder int NOT NULL PRIMARY KEY, SchemaName sysname NOT NULL, TableName sysname NOT NULL,
    BeforeRows bigint NULL, ArchivedRows bigint NULL, DeletedRows bigint NULL, BackupTable sysname NULL
);
INSERT INTO @Scope(DeleteOrder, SchemaName, TableName) VALUES
    (1, N'capex', N'LlmShowcaseRun'),
    (2, N'capex', N'BundleDecision'),
    (3, N'capex', N'BundleCandidateMember'),
    (4, N'capex', N'Assessment'),
    (5, N'capex', N'BundleCandidateGroup'),
    (6, N'capex', N'AssessmentRun'),
    (7, N'task', N'TaskSuggestion'),
    (8, N'task', N'assetClassSuggestion'),
    (9, N'mvp_review', N'documents'),
    (10, N'mvp_review', N'imports'),
    (11, N'prep', N'InvoiceDataQualityWarning'),
    (12, N'prep', N'InvoiceOutbox'),
    (13, N'prep', N'InvoiceLine'),
    (14, N'prep', N'InvoicePackage'),
    (15, N'prep', N'PurchaseOrderLine'),
    (16, N'prep', N'PurchaseOrder'),
    (17, N'prep', N'Project'),
    (18, N'prep', N'Site'),
    (19, N'ref', N'NeverCapitalizeException'),
    (20, N'ref', N'PartMaster');
-- The EU store may not have been created yet; all other reset tables are required.
DELETE FROM @Scope WHERE TableName = N'assetClassSuggestion'
    AND OBJECT_ID(N'task.assetClassSuggestion', N'U') IS NULL;
IF EXISTS (SELECT 1 FROM @Scope
    WHERE OBJECT_ID(QUOTENAME(SchemaName) + N'.' + QUOTENAME(TableName), N'U') IS NULL)
    THROW 51302, 'Required reset tables are missing or not visible. Nothing was cleared.', 1;

DECLARE @Order int = 0, @Object nvarchar(517), @Sql nvarchar(max);
DECLARE @Rows bigint, @Archived bigint, @Deleted bigint, @Remaining bigint;
DECLARE @ResetId nvarchar(32) = REPLACE(CONVERT(nvarchar(36), NEWID()), N'-', N'');
DECLARE @Backup sysname, @Counts nvarchar(max);
DECLARE @LinkedInvoiceRows bigint;
BEGIN TRY
    BEGIN TRANSACTION;
    IF EXISTS (SELECT 1 FROM review.ReviewDecision WITH (HOLDLOCK))
        OR EXISTS (SELECT 1 FROM review.BundleDecision WITH (HOLDLOCK))
        THROW 51303, 'Human-review decisions exist. Preserve them; this reset will not delete them.', 1;
    IF OBJECT_ID(N'mvp_review.decisions', N'U') IS NOT NULL
        EXEC(N'IF EXISTS (SELECT 1 FROM mvp_review.decisions WITH (HOLDLOCK))
            THROW 51303, ''Human-review actions exist. Nothing will be cleared.'', 1;');
    WHILE EXISTS (SELECT 1 FROM @Scope WHERE DeleteOrder > @Order)
    BEGIN
        SELECT TOP (1) @Order = DeleteOrder,
            @Object = QUOTENAME(SchemaName) + N'.' + QUOTENAME(TableName)
        FROM @Scope WHERE DeleteOrder > @Order ORDER BY DeleteOrder;
        SET @Sql = N'SELECT @Rows=COUNT_BIG(*) FROM ' + @Object + N' WITH (TABLOCKX, HOLDLOCK);';
        EXEC sys.sp_executesql @Sql, N'@Rows bigint OUTPUT', @Rows=@Rows OUTPUT;
        UPDATE @Scope SET BeforeRows=@Rows WHERE DeleteOrder=@Order;
    END;
    SELECT @LinkedInvoiceRows=COUNT_BIG(*)
    FROM prep.Invoice AS invoice WITH (HOLDLOCK)
    JOIN prep.PurchaseOrder AS purchase_order WITH (HOLDLOCK)
        ON purchase_order.PONumber=invoice.PONumber;
    SET @Counts = (SELECT SchemaName, TableName, BeforeRows FROM @Scope ORDER BY DeleteOrder FOR JSON PATH);
    IF @CommitReset = 0
    BEGIN
        ROLLBACK TRANSACTION;
        SELECT 'PREVIEW_ONLY_NO_CHANGES' AS Result, @Counts AS ExpectedCountsJson;
        SELECT SchemaName, TableName, BeforeRows FROM @Scope ORDER BY DeleteOrder;
        SELECT @LinkedInvoiceRows AS PreservedInvoicesReferencingPurchaseOrders,
            CASE WHEN @LinkedInvoiceRows>0
                THEN 'BLOCKED: prep.Invoice is preserved and still references purchase orders.'
                ELSE 'No preserved invoice-to-purchase-order links block this reset.' END AS DependencyStatus;
        RETURN;
    END;
    IF @LinkedInvoiceRows>0
        THROW 51307, 'prep.Invoice still references purchase orders. This reset preserves invoices; review those links before clearing purchase orders.', 1;
    IF EXISTS (
        SELECT 1 FROM @Scope s FULL JOIN OPENJSON(@ExpectedCountsJson) WITH (
            SchemaName sysname '$.SchemaName', TableName sysname '$.TableName', BeforeRows bigint '$.BeforeRows'
        ) e ON e.SchemaName=s.SchemaName AND e.TableName=s.TableName
        WHERE s.TableName IS NULL OR e.TableName IS NULL OR e.BeforeRows IS NULL OR e.BeforeRows<>s.BeforeRows
    ) OR (SELECT COUNT(*) FROM OPENJSON(@ExpectedCountsJson)) <> (SELECT COUNT(*) FROM @Scope)
        THROW 51304, 'Result counts changed since preview. Preview again before resetting.', 1;

    IF SCHEMA_ID(N'ops') IS NULL EXEC(N'CREATE SCHEMA ops AUTHORIZATION dbo;');
    SET @Order = 0;
    WHILE EXISTS (SELECT 1 FROM @Scope WHERE DeleteOrder > @Order)
    BEGIN
        SELECT TOP (1) @Order = DeleteOrder,
            @Object = QUOTENAME(SchemaName) + N'.' + QUOTENAME(TableName),
            @Backup = N'CapexResultBackup_' + CONVERT(nvarchar(3), DeleteOrder) + N'_' + @ResetId
        FROM @Scope WHERE DeleteOrder > @Order ORDER BY DeleteOrder;
        SET @Sql = N'SELECT * INTO ops.' + QUOTENAME(@Backup) + N' FROM ' + @Object
            + N'; SELECT @Rows=COUNT_BIG(*) FROM ops.' + QUOTENAME(@Backup) + N';';
        EXEC sys.sp_executesql @Sql, N'@Rows bigint OUTPUT', @Rows=@Archived OUTPUT;
        IF @Archived <> (SELECT BeforeRows FROM @Scope WHERE DeleteOrder=@Order)
            THROW 51305, 'Backup count did not reconcile. No reset was retained.', 1;
        UPDATE @Scope SET ArchivedRows=@Archived, BackupTable=@Backup WHERE DeleteOrder=@Order;
    END;
    SET @Order = 0;
    WHILE EXISTS (SELECT 1 FROM @Scope WHERE DeleteOrder > @Order)
    BEGIN
        SELECT TOP (1) @Order = DeleteOrder,
            @Object = QUOTENAME(SchemaName) + N'.' + QUOTENAME(TableName)
        FROM @Scope WHERE DeleteOrder > @Order ORDER BY DeleteOrder;
        SET @Sql = N'DELETE FROM ' + @Object + N'; SET @Deleted=ROWCOUNT_BIG();'
            + N' SELECT @Remaining=COUNT_BIG(*) FROM ' + @Object + N';';
        EXEC sys.sp_executesql @Sql, N'@Deleted bigint OUTPUT, @Remaining bigint OUTPUT',
            @Deleted=@Deleted OUTPUT, @Remaining=@Remaining OUTPUT;
        IF @Remaining <> 0 OR @Deleted <> (SELECT BeforeRows FROM @Scope WHERE DeleteOrder=@Order)
            THROW 51306, 'Reset counts did not reconcile. Everything was rolled back.', 1;
        UPDATE @Scope SET DeletedRows=@Deleted WHERE DeleteOrder=@Order;
    END;
    COMMIT TRANSACTION;
    SELECT 'RESULT_RESET_COMMITTED' AS Result, @ResetId AS ResetId;
    SELECT SchemaName, TableName, ArchivedRows, DeletedRows,
        N'ops.' + QUOTENAME(BackupTable) AS RetainedBackupTable FROM @Scope ORDER BY DeleteOrder;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
