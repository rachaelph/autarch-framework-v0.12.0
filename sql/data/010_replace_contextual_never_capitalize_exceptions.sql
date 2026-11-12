SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Context-qualified PP&E exclusions, not unconditional keyword bans.
DECLARE @ApplyChanges bit=0;
DECLARE @ExpectedCurrentRows bigint=NULL;
IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0
    THROW 51365, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF @ApplyChanges IS NULL OR (@ApplyChanges=1 AND (@ExpectedCurrentRows IS NULL OR @ExpectedCurrentRows<0))
    THROW 51366, 'Preview first; set ApplyChanges=1 and confirm ExpectedCurrentRows before replacing exclusions.', 1;
IF COL_LENGTH(N'ref.NeverCapitalizeException', N'ApplicabilityRule') IS NULL
    THROW 51367, 'Apply migration 018 before importing contextual exclusions.', 1;

DECLARE @Proposed TABLE (
    ItemCategory nvarchar(256) NOT NULL, Keyword nvarchar(512) NOT NULL,
    PolicyReference nvarchar(2000) NOT NULL, ApplicabilityRule nvarchar(2000) NOT NULL,
    Active bit NOT NULL, PRIMARY KEY (ItemCategory, Keyword)
);
INSERT INTO @Proposed VALUES
    (N'Routine maintenance', N'routine repairs and maintenance',
     N'Reporting manual - April 2026 - Final: Property and Equipment, section 8.3; PDF page 51, printed page 49.',
     N'Apply only to routine maintenance that does not extend useful life, increase capacity or improve efficiency. Do not treat qualifying component replacement or qualifying major inspection as routine maintenance. Unclear nature or attribution requires REVIEW.', 1),
    (N'Maintenance portion of renovations', N'renovation',
     N'Reporting manual - April 2026 - Final: Property and Equipment, section 9; PDF page 51, printed page 49.',
     N'Apply only to an identifiable maintenance portion that would have been incurred anyway. Do not exclude the whole renovation. If a mixed line cannot be reliably allocated between maintenance and qualifying improvements, require REVIEW instead of a whole-line exclusion.', 1),
    (N'Transferred-asset installation', N'installation',
     N'Reporting manual - April 2026 - Final: Property and Equipment, section 3.2; PDF page 47, printed page 45.',
     N'Apply only to installation of a transferred asset when evidence establishes that original installation costs are not treated as a disposal. If original installation costs are disposed, assess capitalization normally. Missing disposal or transfer evidence requires REVIEW. Do not apply to installation of a new asset.', 1);

DECLARE @CurrentRows bigint, @ArchivedRows bigint, @FinalRows bigint;
DECLARE @Backup sysname=N'NeverCapitalizeBackup_'+REPLACE(CONVERT(nvarchar(36), NEWID()), N'-', N'');
DECLARE @Sql nvarchar(max);
BEGIN TRY
    BEGIN TRANSACTION;
    SELECT @CurrentRows=COUNT_BIG(*) FROM ref.NeverCapitalizeException WITH (TABLOCKX, HOLDLOCK);
    IF @ApplyChanges=0
    BEGIN
        SELECT 'PREVIEW_ONLY_NO_CHANGES' AS Result, @CurrentRows AS CurrentTableRows,
            (SELECT COUNT_BIG(*) FROM @Proposed) AS ProposedRows;
        SELECT 'ARCHIVE_AND_REPLACE' AS ProposedAction, * FROM ref.NeverCapitalizeException;
        SELECT 'INSERT' AS ProposedAction, * FROM @Proposed ORDER BY ItemCategory;
        ROLLBACK TRANSACTION;
        RETURN;
    END;
    IF @CurrentRows<>@ExpectedCurrentRows
        THROW 51368, 'Exclusion row count changed. Preview again before replacing.', 1;
    IF SCHEMA_ID(N'ops') IS NULL EXEC(N'CREATE SCHEMA ops AUTHORIZATION dbo;');
    SET @Sql=N'SELECT * INTO ops.'+QUOTENAME(@Backup)+N' FROM ref.NeverCapitalizeException;
        SELECT @Rows=COUNT_BIG(*) FROM ops.'+QUOTENAME(@Backup)+N';';
    EXEC sys.sp_executesql @Sql, N'@Rows bigint OUTPUT', @Rows=@ArchivedRows OUTPUT;
    IF @ArchivedRows<>@CurrentRows
        THROW 51369, 'Exclusion archive count did not reconcile.', 1;
    DELETE FROM ref.NeverCapitalizeException;
    INSERT INTO ref.NeverCapitalizeException
        (ItemCategory, Keyword, PolicyReference, ApplicabilityRule, Active)
    SELECT ItemCategory, Keyword, PolicyReference, ApplicabilityRule, Active FROM @Proposed;
    SELECT @FinalRows=COUNT_BIG(*) FROM ref.NeverCapitalizeException;
    IF @FinalRows<>(SELECT COUNT_BIG(*) FROM @Proposed) OR EXISTS (
        SELECT ItemCategory, Keyword, PolicyReference, ApplicabilityRule, Active FROM @Proposed
        EXCEPT
        SELECT ItemCategory, Keyword, PolicyReference, ApplicabilityRule, Active FROM ref.NeverCapitalizeException
    )
        THROW 51370, 'Stored contextual exclusions do not reconcile.', 1;
    COMMIT TRANSACTION;
    SELECT 'CONTEXTUAL_EXCLUSIONS_LOADED' AS Result, @FinalRows AS TotalRules,
        N'ops.'+QUOTENAME(@Backup) AS RetainedBackupTable;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
