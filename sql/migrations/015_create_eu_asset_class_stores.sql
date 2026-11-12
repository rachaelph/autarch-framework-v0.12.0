SET NOCOUNT ON;
SET XACT_ABORT ON;

IF COL_LENGTH(N'capex.Assessment', N'TaskSuggestionID') IS NULL
    OR DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id') IS NULL
    THROW 51270, 'Apply the existing assessment migrations and create the runtime user first.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    IF SCHEMA_ID(N'ref') IS NULL EXEC(N'CREATE SCHEMA ref AUTHORIZATION dbo;');
    IF SCHEMA_ID(N'task') IS NULL EXEC(N'CREATE SCHEMA task AUTHORIZATION dbo;');

    IF OBJECT_ID(N'ref.assetClassMaster', N'U') IS NULL
        CREATE TABLE ref.assetClassMaster (
            AssetClassCode nvarchar(128) NOT NULL PRIMARY KEY,
            AssetClassName nvarchar(2000) NOT NULL,
            AccountGroup nvarchar(256) NOT NULL,
            AccountMappingsJson nvarchar(max) NOT NULL CHECK (ISJSON(AccountMappingsJson) = 1),
            SourceFile nvarchar(256) NOT NULL,
            SourceSheet nvarchar(128) NOT NULL,
            SourceSha256 char(64) NOT NULL,
            InsertedAtUtc datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME()
        );

    IF OBJECT_ID(N'task.assetClassSuggestion', N'U') IS NULL
        CREATE TABLE task.assetClassSuggestion (
            assetClassSuggestionId uniqueidentifier NOT NULL DEFAULT NEWID() PRIMARY KEY,
            correlationId nvarchar(128) NOT NULL,
            packageId nvarchar(128) NOT NULL,
            invoiceId nvarchar(128) NOT NULL,
            runId nvarchar(256) NOT NULL,
            lineId nvarchar(256) NOT NULL,
            assetClassCode nvarchar(128) NULL,
            resultStatus varchar(16) NOT NULL CHECK (resultStatus IN ('RESOLVED', 'REVIEW')),
            ItemIdentified nvarchar(2000) NULL,
            confidence decimal(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            rationale nvarchar(max) NOT NULL,
            UnresolvedReason nvarchar(max) NULL,
            evidenceRefs nvarchar(max) NOT NULL CHECK (ISJSON(evidenceRefs) = 1),
            AssetClassMasterSha256 char(64) NOT NULL,
            modelDeployment nvarchar(128) NULL,
            promptVersion nvarchar(128) NOT NULL,
            createdAtUtc datetime2(3) NOT NULL,
            status varchar(16) NOT NULL DEFAULT 'PENDING' CHECK (status = 'PENDING'),
            CONSTRAINT UQ_AssetClassSuggestion_Line UNIQUE NONCLUSTERED (correlationId, packageId, lineId),
            CONSTRAINT FK_AssetClassSuggestion_Master FOREIGN KEY (assetClassCode)
                REFERENCES ref.assetClassMaster(AssetClassCode),
            CONSTRAINT CK_AssetClassSuggestion_Resolution CHECK (
                (resultStatus = 'RESOLVED' AND assetClassCode IS NOT NULL AND UnresolvedReason IS NULL)
                OR (resultStatus = 'REVIEW' AND assetClassCode IS NULL AND UnresolvedReason IS NOT NULL)
            )
        );

    IF COL_LENGTH(N'capex.Assessment', N'AssetClassSuggestionID') IS NULL
        ALTER TABLE capex.Assessment ADD AssetClassSuggestionID uniqueidentifier NULL;
    IF COL_LENGTH(N'capex.Assessment', N'SuggestedAssetClassCode') IS NULL
        ALTER TABLE capex.Assessment ADD SuggestedAssetClassCode nvarchar(128) NULL;
    IF EXISTS (SELECT 1 FROM sys.columns
        WHERE object_id=OBJECT_ID(N'capex.BundleCandidateGroup')
            AND name=N'SuggestedTaskCode' AND is_nullable=0)
    BEGIN
        DECLARE @TaskCodeColumnId int, @TaskCodeDefinition nvarchar(max), @TaskCodePersisted bit;
        DECLARE @TaskCodeSql nvarchar(max);
        SELECT @TaskCodeColumnId=column_info.column_id,
            @TaskCodeDefinition=computed.definition, @TaskCodePersisted=computed.is_persisted
        FROM sys.columns AS column_info
        LEFT JOIN sys.computed_columns AS computed
            ON computed.object_id=column_info.object_id AND computed.column_id=column_info.column_id
        WHERE column_info.object_id=OBJECT_ID(N'capex.BundleCandidateGroup')
            AND column_info.name=N'TaskCode';
        IF @TaskCodeColumnId IS NOT NULL
        BEGIN
            IF @TaskCodeDefinition IS NULL
                THROW 51271, 'Bundle TaskCode is not the expected computed column. Review before changing it.', 1;
            IF EXISTS (SELECT 1 FROM sys.database_permissions
                WHERE class=1 AND major_id=OBJECT_ID(N'capex.BundleCandidateGroup')
                    AND minor_id=@TaskCodeColumnId)
                THROW 51272, 'Bundle TaskCode has column-specific permissions. A DBA must preserve them before changing it.', 1;
            -- SQL Server requires removing this computed dependency before altering its source.
            EXEC(N'ALTER TABLE capex.BundleCandidateGroup DROP COLUMN TaskCode;');
        END;
        EXEC(N'ALTER TABLE capex.BundleCandidateGroup ALTER COLUMN SuggestedTaskCode nvarchar(128) NULL;');
        IF @TaskCodeDefinition IS NOT NULL
        BEGIN
            SET @TaskCodeSql=N'ALTER TABLE capex.BundleCandidateGroup ADD TaskCode AS '
                + @TaskCodeDefinition + CASE WHEN @TaskCodePersisted=1 THEN N' PERSISTED' ELSE N'' END + N';';
            EXEC sys.sp_executesql @TaskCodeSql;
        END;
    END;
    IF COL_LENGTH(N'capex.BundleCandidateGroup', N'SuggestedAssetClassCode') IS NULL
        ALTER TABLE capex.BundleCandidateGroup ADD SuggestedAssetClassCode nvarchar(128) NULL;
    IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Assessment_AssetClassSuggestion')
        EXEC(N'ALTER TABLE capex.Assessment ADD CONSTRAINT FK_Assessment_AssetClassSuggestion
            FOREIGN KEY (AssetClassSuggestionID)
            REFERENCES task.assetClassSuggestion(assetClassSuggestionId);');
    IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = N'CK_Assessment_OneSuggestionType')
        EXEC(N'ALTER TABLE capex.Assessment ADD CONSTRAINT CK_Assessment_OneSuggestionType
            CHECK (TaskSuggestionID IS NULL OR AssetClassSuggestionID IS NULL);');

    GRANT SELECT ON OBJECT::ref.assetClassMaster TO [cict-ai-cus-capex-id];
    GRANT SELECT, INSERT ON OBJECT::task.assetClassSuggestion TO [cict-ai-cus-capex-id];
    EXEC(N'GRANT UPDATE (AssetClassSuggestionID, SuggestedAssetClassCode)
        ON OBJECT::capex.Assessment TO [cict-ai-cus-capex-id];');
    EXEC(N'GRANT UPDATE (SuggestedAssetClassCode)
        ON OBJECT::capex.BundleCandidateGroup TO [cict-ai-cus-capex-id];');
    COMMIT TRANSACTION;
    SELECT 'EU_ASSET_CLASS_STORES_READY' AS MigrationResult;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
