SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Preview first. Commit only after pausing jobs, dispatchers, and consumers.
DECLARE @CommitReset bit = 0;
DECLARE @WritersPaused bit = 0;

IF DB_NAME() <> N'cict-ai-cus-sqldb'
    THROW 51200, 'Run this reset only in cict-ai-cus-sqldb.', 1;
IF @@TRANCOUNT <> 0
    THROW 51201, 'Run this reset outside an existing transaction.', 1;
IF @CommitReset = 1 AND @WritersPaused = 0
    THROW 51202, 'Pause all application writers and set @WritersPaused = 1 before committing.', 1;
IF OBJECT_ID(N'ref.TaskMaster', N'U') IS NULL
    THROW 51203, 'The preserved TaskMaster table is missing or not visible.', 1;

DECLARE @Scope TABLE
(
    DeleteOrder int NOT NULL PRIMARY KEY,
    SchemaName sysname NOT NULL,
    TableName sysname NOT NULL,
    ObjectId int NULL,
    BeforeRows bigint NULL,
    ArchivedRows bigint NULL,
    DeletedRows bigint NULL,
    RemainingRows bigint NULL,
    BackupTable sysname NULL,
    UNIQUE (SchemaName, TableName)
);

INSERT INTO @Scope (DeleteOrder, SchemaName, TableName) VALUES
    (1, N'review', N'POPAHandoff'),
    (2, N'review', N'ApprovedCoding'),
    (3, N'review', N'BundleDecision'),
    (4, N'review', N'ReviewDecision'),
    (5, N'review', N'ReviewCase'),
    (6, N'prep', N'CodingDistribution'),
    (7, N'capex', N'BundleCandidateMember'),
    (8, N'capex', N'Assessment'),
    (9, N'capex', N'BundleCandidateGroup'),
    (10, N'capex', N'AssessmentRun'),
    (11, N'task', N'TaskSuggestion'),
    (12, N'prep', N'InvoiceDataQualityWarning'),
    (13, N'prep', N'InvoiceOutbox'),
    (14, N'prep', N'InvoiceLine'),
    (15, N'prep', N'InvoicePackage'),
    (16, N'prep', N'Invoice'),
    (17, N'prep', N'PurchaseOrderLine'),
    (18, N'prep', N'PurchaseOrder'),
    (19, N'prep', N'Project'),
    (20, N'prep', N'Site'),
    (21, N'ops', N'Outbox'),
    (22, N'ref', N'PartMaster'),
    (23, N'ref', N'CapitalizationThreshold'),
    (24, N'ref', N'NeverCapitalizeException'),
    (25, N'ref', N'RepairMaintenanceIndicator');

UPDATE @Scope
SET ObjectId = OBJECT_ID(QUOTENAME(SchemaName) + N'.' + QUOTENAME(TableName), N'U');

IF EXISTS (SELECT 1 FROM @Scope WHERE ObjectId IS NULL)
BEGIN
    SELECT SchemaName, TableName FROM @Scope WHERE ObjectId IS NULL;
    THROW 51204, 'Required tables are missing or not visible. Nothing was cleared.', 1;
END;

-- Preserve earlier audit snapshots; never silently expand into unrelated tables.
IF EXISTS (
    SELECT 1 FROM sys.tables AS t
    WHERE t.is_ms_shipped = 0
      AND t.object_id <> OBJECT_ID(N'ref.TaskMaster')
      AND NOT EXISTS (SELECT 1 FROM @Scope AS s WHERE s.ObjectId = t.object_id)
      AND NOT (
          SCHEMA_NAME(t.schema_id) = N'ops'
          AND (t.name LIKE N'CapexDemoBackup[_]%'
               OR t.name LIKE N'TaskSuggestionDemoBackup[_]%')
      )
)
BEGIN
    SELECT SCHEMA_NAME(t.schema_id) AS SchemaName, t.name AS UnexpectedTable
    FROM sys.tables AS t
    WHERE t.is_ms_shipped = 0
      AND t.object_id <> OBJECT_ID(N'ref.TaskMaster')
      AND NOT EXISTS (SELECT 1 FROM @Scope AS s WHERE s.ObjectId = t.object_id)
      AND NOT (
          SCHEMA_NAME(t.schema_id) = N'ops'
          AND (t.name LIKE N'CapexDemoBackup[_]%'
               OR t.name LIKE N'TaskSuggestionDemoBackup[_]%')
      );
    THROW 51205, 'Unexpected tables found. Review the reset scope; nothing was cleared.', 1;
END;

DECLARE @ResetId nvarchar(32) = REPLACE(CONVERT(nvarchar(36), NEWID()), N'-', N'');
DECLARE @Order int = 0;
DECLARE @Object nvarchar(517), @Backup sysname, @Sql nvarchar(max);
DECLARE @Count bigint, @Archived bigint, @Deleted bigint, @MasterRows bigint;

BEGIN TRY
    BEGIN TRANSACTION;

    SELECT * INTO #TaskMasterBefore
    FROM ref.TaskMaster WITH (TABLOCK, HOLDLOCK);
    SELECT @MasterRows = COUNT_BIG(*) FROM #TaskMasterBefore;

    WHILE EXISTS (SELECT 1 FROM @Scope WHERE DeleteOrder > @Order)
    BEGIN
        SELECT TOP (1)
            @Order = DeleteOrder,
            @Object = QUOTENAME(SchemaName) + N'.' + QUOTENAME(TableName)
        FROM @Scope WHERE DeleteOrder > @Order ORDER BY DeleteOrder;
        SET @Sql = N'SELECT @Count = COUNT_BIG(*) FROM ' + @Object
            + N' WITH (TABLOCKX, HOLDLOCK);';
        EXEC sys.sp_executesql @Sql, N'@Count bigint OUTPUT', @Count = @Count OUTPUT;
        UPDATE @Scope SET BeforeRows = @Count WHERE DeleteOrder = @Order;
    END;

    IF EXISTS (
        SELECT 1 FROM sys.triggers AS tr
        JOIN @Scope AS s ON s.ObjectId = tr.parent_id
        WHERE tr.is_disabled = 0
    )
        THROW 51206, 'An enabled table trigger requires review before reset. Nothing was cleared.', 1;

    IF EXISTS (
        SELECT 1 FROM sys.foreign_keys AS fk
        JOIN @Scope AS parent ON parent.ObjectId = fk.referenced_object_id
        LEFT JOIN @Scope AS child ON child.ObjectId = fk.parent_object_id
        WHERE child.ObjectId IS NULL OR child.DeleteOrder >= parent.DeleteOrder
           OR fk.is_disabled = 1 OR fk.is_not_trusted = 1
    )
        THROW 51207, 'Foreign keys do not match the safe reset order or include a preserved dependent table. Nothing was cleared.', 1;

    IF @CommitReset = 0
    BEGIN
        SELECT 'PREVIEW_ONLY' AS ResetStatus, SchemaName, TableName,
            BeforeRows AS RowsToArchiveAndDelete, DeleteOrder
        FROM @Scope ORDER BY DeleteOrder;
        SELECT 'PRESERVED' AS TaskMasterStatus, @MasterRows AS TaskMasterRows;
        ROLLBACK TRANSACTION;
        RETURN;
    END;

    -- Complete every nonempty snapshot before deleting any application rows.
    SET @Order = 0;
    WHILE EXISTS (SELECT 1 FROM @Scope WHERE DeleteOrder > @Order AND BeforeRows > 0)
    BEGIN
        SELECT TOP (1)
            @Order = DeleteOrder,
            @Object = QUOTENAME(SchemaName) + N'.' + QUOTENAME(TableName),
            @Backup = N'CapexDemoBackup_' + @ResetId + N'_' + SchemaName + N'_' + TableName
        FROM @Scope
        WHERE DeleteOrder > @Order AND BeforeRows > 0 ORDER BY DeleteOrder;

        SET @Sql = N'SELECT * INTO [ops].' + QUOTENAME(@Backup) + N' FROM ' + @Object
            + N'; SELECT @Count = COUNT_BIG(*) FROM [ops].' + QUOTENAME(@Backup) + N';';
        EXEC sys.sp_executesql @Sql, N'@Count bigint OUTPUT', @Count = @Archived OUTPUT;
        UPDATE @Scope SET ArchivedRows = @Archived, BackupTable = @Backup
        WHERE DeleteOrder = @Order;
    END;

    IF EXISTS (SELECT 1 FROM @Scope WHERE BeforeRows > 0
        AND (ArchivedRows IS NULL OR ArchivedRows <> BeforeRows))
        THROW 51208, 'Backup reconciliation failed. Reset rolled back.', 1;

    SET @Order = 0;
    WHILE EXISTS (SELECT 1 FROM @Scope WHERE DeleteOrder > @Order)
    BEGIN
        SELECT TOP (1)
            @Order = DeleteOrder,
            @Object = QUOTENAME(SchemaName) + N'.' + QUOTENAME(TableName)
        FROM @Scope WHERE DeleteOrder > @Order ORDER BY DeleteOrder;
        SET @Sql = N'DELETE FROM ' + @Object + N'; SET @Deleted = ROWCOUNT_BIG();'
            + N' SELECT @Count = COUNT_BIG(*) FROM ' + @Object + N';';
        EXEC sys.sp_executesql @Sql, N'@Deleted bigint OUTPUT, @Count bigint OUTPUT',
            @Deleted = @Deleted OUTPUT, @Count = @Count OUTPUT;
        UPDATE @Scope SET DeletedRows = @Deleted, RemainingRows = @Count
        WHERE DeleteOrder = @Order;
    END;

    IF EXISTS (SELECT 1 FROM @Scope
        WHERE DeletedRows <> BeforeRows OR RemainingRows <> 0
           OR DeletedRows IS NULL OR RemainingRows IS NULL)
        THROW 51209, 'Deletion reconciliation failed. Reset rolled back.', 1;

    IF @MasterRows <> (SELECT COUNT_BIG(*) FROM ref.TaskMaster)
        OR EXISTS (SELECT * FROM #TaskMasterBefore EXCEPT SELECT * FROM ref.TaskMaster)
        OR EXISTS (SELECT * FROM ref.TaskMaster EXCEPT SELECT * FROM #TaskMasterBefore)
        THROW 51210, 'TaskMaster preservation check failed. Reset rolled back.', 1;

    DROP TABLE #TaskMasterBefore;
    COMMIT TRANSACTION;

    SELECT 'RESET_COMMITTED' AS ResetStatus, @ResetId AS ResetId,
        SchemaName, TableName, BeforeRows,
        COALESCE(ArchivedRows, 0) AS ArchivedRows, DeletedRows, RemainingRows,
        CASE WHEN BackupTable IS NOT NULL THEN N'[ops].' + QUOTENAME(BackupTable) END
            AS RetainedBackupTable
    FROM @Scope ORDER BY DeleteOrder;
    SELECT 'PRESERVED_UNCHANGED' AS TaskMasterStatus, @MasterRows AS TaskMasterRows;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
