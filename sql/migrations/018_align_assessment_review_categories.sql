SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0
    THROW 51350, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF COL_LENGTH(N'capex.Assessment', N'AssetClassSuggestionID') IS NULL
    OR OBJECT_ID(N'ref.NeverCapitalizeException', N'U') IS NULL
    OR DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') IS NULL
    THROW 51351, 'Apply migrations through 017 with the existing runtime user first.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    IF OBJECT_ID(N'ref.AssessmentOutcomeCategory', N'U') IS NULL
        CREATE TABLE ref.AssessmentOutcomeCategory (
            CategoryCode nvarchar(64) NOT NULL
                CONSTRAINT PK_AssessmentOutcomeCategory PRIMARY KEY,
            CategoryName nvarchar(256) NOT NULL,
            DecisionCriteria nvarchar(2000) NOT NULL,
            ReviewConsiderations nvarchar(2000) NOT NULL,
            SuggestedAction nvarchar(2000) NOT NULL,
            EnabledForDemo bit NOT NULL
        );

    -- Recommendation and its CapExOpExSuggestion alias keep their accounting meaning.
    IF COL_LENGTH(N'capex.Assessment', N'CategorySuggestion') IS NULL
        EXEC(N'ALTER TABLE capex.Assessment ADD CategorySuggestion nvarchar(64) NULL;');
    IF COL_LENGTH(N'capex.Assessment', N'RepairMaintenanceFlag') IS NULL
        EXEC(N'ALTER TABLE capex.Assessment ADD RepairMaintenanceFlag bit NULL;');
    IF COL_LENGTH(N'capex.Assessment', N'AroFlag') IS NULL
        EXEC(N'ALTER TABLE capex.Assessment ADD AroFlag bit NULL;');
    IF COL_LENGTH(N'capex.Assessment', N'PotentialTagsJson') IS NULL
        EXEC(N'ALTER TABLE capex.Assessment ADD PotentialTagsJson nvarchar(max) NULL
            CONSTRAINT CK_Assessment_PotentialTagsJson
            CHECK (PotentialTagsJson IS NULL OR ISJSON(PotentialTagsJson)=1);');
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys
        WHERE parent_object_id=OBJECT_ID(N'capex.Assessment')
            AND name=N'FK_Assessment_OutcomeCategory')
        EXEC(N'ALTER TABLE capex.Assessment WITH CHECK
            ADD CONSTRAINT FK_Assessment_OutcomeCategory FOREIGN KEY (CategorySuggestion)
            REFERENCES ref.AssessmentOutcomeCategory(CategoryCode);');
    IF COL_LENGTH(N'ref.NeverCapitalizeException', N'ApplicabilityRule') IS NULL
        EXEC(N'ALTER TABLE ref.NeverCapitalizeException
            ADD ApplicabilityRule nvarchar(2000) NULL;');

    DECLARE @Table sysname, @Id sysname, @Link sysname;
    DECLARE @Old sysname, @New sysname, @DropLegacy bit, @Sql nvarchar(max);
    DECLARE move_cursor CURSOR LOCAL FAST_FORWARD FOR
        SELECT source.TableName, source.IdColumn, source.LinkColumn,
            flag.OldColumn, flag.NewColumn, flag.DropLegacy
        FROM (VALUES
            (N'TaskSuggestion', N'taskSuggestionId', N'TaskSuggestionID'),
            (N'assetClassSuggestion', N'assetClassSuggestionId', N'AssetClassSuggestionID')
        ) AS source(TableName, IdColumn, LinkColumn)
        CROSS JOIN (VALUES
            (N'PotentialRepairMaintenanceOpex', N'RepairMaintenanceFlag', 1),
            (N'PotentialAro', N'AroFlag', 1),
            (N'PotentialTagsJson', N'PotentialTagsJson', 0)
        ) AS flag(OldColumn, NewColumn, DropLegacy);
    OPEN move_cursor;
    FETCH NEXT FROM move_cursor INTO @Table, @Id, @Link, @Old, @New, @DropLegacy;
    WHILE @@FETCH_STATUS=0
    BEGIN
        IF COL_LENGTH(N'task.'+@Table, @Old) IS NOT NULL
        BEGIN
            SET @Sql=N'DECLARE @LockedRows bigint;
                SELECT @LockedRows=COUNT_BIG(*) FROM task.'+QUOTENAME(@Table)+N' WITH (TABLOCKX, HOLDLOCK);';
            IF @DropLegacy=1
                SET @Sql+=N'IF EXISTS (SELECT 1 FROM task.'+QUOTENAME(@Table)+N' AS source
                    WHERE source.'+QUOTENAME(@Old)+N' IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM capex.Assessment AS assessment
                        WHERE assessment.'+QUOTENAME(@Link)+N'=source.'+QUOTENAME(@Id)+N'))
                    THROW 51352, ''A screened suggestion has no linked assessment. Preserve it before moving flags.'', 1;';
            SET @Sql+=N'
                IF EXISTS (SELECT 1 FROM capex.Assessment AS assessment
                    JOIN task.'+QUOTENAME(@Table)+N' AS source
                    ON assessment.'+QUOTENAME(@Link)+N'=source.'+QUOTENAME(@Id)+N'
                    WHERE source.'+QUOTENAME(@Old)+N' IS NOT NULL
                        AND assessment.'+QUOTENAME(@New)+N' IS NOT NULL
                        AND CONVERT(varbinary(max), source.'+QUOTENAME(@Old)+N')
                            <>CONVERT(varbinary(max), assessment.'+QUOTENAME(@New)+N'))
                    THROW 51353, ''Conflicting assessment and suggestion review evidence. Nothing was moved.'', 1;
                UPDATE assessment SET '+QUOTENAME(@New)+N'=source.'+QUOTENAME(@Old)+N'
                FROM capex.Assessment AS assessment JOIN task.'+QUOTENAME(@Table)+N' AS source
                    ON assessment.'+QUOTENAME(@Link)+N'=source.'+QUOTENAME(@Id)+N'
                WHERE assessment.'+QUOTENAME(@New)+N' IS NULL
                    AND source.'+QUOTENAME(@Old)+N' IS NOT NULL;';
            IF @DropLegacy=1
                SET @Sql+=N'ALTER TABLE task.'+QUOTENAME(@Table)+N' DROP COLUMN '+QUOTENAME(@Old)+N';';
            EXEC sys.sp_executesql @Sql;
        END;
        FETCH NEXT FROM move_cursor INTO @Table, @Id, @Link, @Old, @New, @DropLegacy;
    END;
    CLOSE move_cursor;
    DEALLOCATE move_cursor;

    GRANT SELECT ON OBJECT::ref.AssessmentOutcomeCategory TO [cict-ai-cus-capex-id];
    EXEC(N'GRANT UPDATE (CategorySuggestion, RepairMaintenanceFlag, AroFlag, PotentialTagsJson)
        ON OBJECT::capex.Assessment TO [cict-ai-cus-capex-id];');
    COMMIT TRANSACTION;
    SELECT 'ASSESSMENT_REVIEW_SCHEMA_READY' AS MigrationResult;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
