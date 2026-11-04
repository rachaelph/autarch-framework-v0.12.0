SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Pause task-producing jobs before running. Preview first, then set to 1.
DECLARE @CommitReset bit = 0;
DECLARE @ExpectedTaskCount bigint = 15;

IF DB_NAME() <> N'cict-ai-cus-sqldb'
    THROW 51100, 'Run this reset only in cict-ai-cus-sqldb.', 1;
IF @@TRANCOUNT <> 0
    THROW 51101, 'Run this reset outside an existing transaction.', 1;

DECLARE @OriginalCount bigint;
DECLARE @ArchivedCount bigint;
DECLARE @DeletedCount bigint;
DECLARE @RemainingCount bigint;
DECLARE @BackupTable sysname =
    N'TaskSuggestionDemoBackup_' + REPLACE(CONVERT(nvarchar(36), NEWID()), N'-', N'');
DECLARE @BackupObject nvarchar(300) = N'[ops].' + QUOTENAME(@BackupTable);
DECLARE @Sql nvarchar(max);

BEGIN TRY
    BEGIN TRANSACTION;

    SELECT @OriginalCount = COUNT_BIG(*)
    FROM task.TaskSuggestion WITH (TABLOCKX, HOLDLOCK);

    IF @OriginalCount <> @ExpectedTaskCount OR @OriginalCount = 0
        THROW 51102, 'Task count differs from the expected nonzero count. Review the baseline before changing @ExpectedTaskCount.', 1;

    IF EXISTS (
        SELECT 1
        FROM capex.Assessment WITH (HOLDLOCK)
        WHERE TaskSuggestionID IS NOT NULL
    )
        THROW 51103, 'Assessments reference task suggestions. Nothing was cleared; retain the dependent evidence.', 1;

    IF EXISTS (
        SELECT 1
        FROM review.ReviewDecision WITH (HOLDLOCK)
        WHERE TaskSuggestionID IS NOT NULL
    )
        THROW 51104, 'Human decisions reference task suggestions. Nothing was cleared; retain the dependent evidence.', 1;

    -- Snapshot every column before deletion; a fresh name preserves earlier backups.
    SET @Sql =
        N'SELECT * INTO ' + @BackupObject + N' FROM task.TaskSuggestion;'
        + N' SELECT @Count = COUNT_BIG(*) FROM ' + @BackupObject + N';';
    EXEC sys.sp_executesql @Sql, N'@Count bigint OUTPUT', @Count = @ArchivedCount OUTPUT;

    IF @ArchivedCount <> @OriginalCount
        THROW 51105, 'Backup count does not match the source. Reset rolled back.', 1;

    DELETE FROM task.TaskSuggestion;
    SET @DeletedCount = ROWCOUNT_BIG();

    SELECT @RemainingCount = COUNT_BIG(*) FROM task.TaskSuggestion;
    IF @DeletedCount <> @OriginalCount OR @RemainingCount <> 0
        THROW 51106, 'Reset reconciliation failed. Reset rolled back.', 1;

    IF @CommitReset = 1
    BEGIN
        COMMIT TRANSACTION;
        SELECT
            'RESET_COMMITTED' AS ResetStatus,
            @BackupObject AS RetainedBackupTable,
            @ArchivedCount AS ArchivedRows,
            @DeletedCount AS DeletedRows,
            @RemainingCount AS RemainingTaskSuggestions;
    END
    ELSE
    BEGIN
        ROLLBACK TRANSACTION;
        SELECT
            'PREVIEW_ROLLED_BACK' AS ResetStatus,
            @OriginalCount AS RowsThatWouldBeArchivedAndDeleted,
            'No changes retained. Set @CommitReset = 1 to archive and clear.' AS NextAction;
    END;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
