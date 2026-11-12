SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'task.assetClassSuggestion', N'U') IS NULL
    THROW 51295, 'Apply migration 015 before adding potential review tags.', 1;

IF COL_LENGTH(N'capex.Assessment', N'RepairMaintenanceFlag') IS NOT NULL
    AND COL_LENGTH(N'capex.Assessment', N'AroFlag') IS NOT NULL
    AND COL_LENGTH(N'capex.Assessment', N'PotentialTagsJson') IS NOT NULL
    AND COL_LENGTH(N'task.TaskSuggestion', N'PotentialAro') IS NULL
    AND COL_LENGTH(N'task.assetClassSuggestion', N'PotentialAro') IS NULL
BEGIN
    SELECT 'POTENTIAL_REVIEW_TAGS_ON_ASSESSMENT' AS MigrationResult;
    RETURN;
END;

BEGIN TRY
    BEGIN TRANSACTION;
    -- NULL means not screened (historical rows); false means screened without that signal.
    DECLARE @Table sysname, @Sql nvarchar(max);
    DECLARE tables_cursor CURSOR LOCAL FAST_FORWARD FOR
        SELECT name FROM (VALUES (N'TaskSuggestion'), (N'assetClassSuggestion')) AS t(name);
    OPEN tables_cursor;
    FETCH NEXT FROM tables_cursor INTO @Table;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        IF COL_LENGTH(N'task.' + @Table, N'PotentialTagsJson') IS NULL
        BEGIN
            SET @Sql = N'ALTER TABLE task.' + QUOTENAME(@Table) + N' ADD
                PotentialBundling bit NULL, PotentialRepairMaintenanceOpex bit NULL,
                PotentialAro bit NULL, PotentialTagsJson nvarchar(max) NULL
                CHECK (PotentialTagsJson IS NULL OR ISJSON(PotentialTagsJson) = 1);';
            EXEC sys.sp_executesql @Sql;
        END;
        SET @Sql = N'GRANT UPDATE (PotentialBundling, PotentialRepairMaintenanceOpex,
            PotentialAro, PotentialTagsJson) ON OBJECT::task.' + QUOTENAME(@Table)
            + N' TO [cict-ai-cus-capex-id];';
        EXEC sys.sp_executesql @Sql;
        FETCH NEXT FROM tables_cursor INTO @Table;
    END;
    CLOSE tables_cursor;
    DEALLOCATE tables_cursor;
    COMMIT TRANSACTION;
    SELECT 'POTENTIAL_REVIEW_TAGS_READY' AS MigrationResult;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
