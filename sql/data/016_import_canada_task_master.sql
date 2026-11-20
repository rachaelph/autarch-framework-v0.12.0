-- Canadian TaskMaster source import and scoped index change: DBA handoff only.
-- Generate the populated portal-compatible copy; no SQLCMD or workbook access in SQL.
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @ApplyChanges bit=0;
DECLARE @ApproveCatalogKeyChange bit=0;
DECLARE @Payload nvarchar(max)=N'{}';

IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0
    THROW 51400, 'Use cict-ai-cus-sqldb outside an existing transaction.', 1;
IF ISJSON(@Payload)<>1 OR JSON_VALUE(@Payload,'$.schemaVersion')<>N'1.0-canada-taskmaster-load'
    OR JSON_VALUE(@Payload,'$.schemaVersion') IS NULL
    THROW 51401, 'Generate the populated Canadian TaskMaster handoff first.', 1;
IF OBJECT_ID(N'ref.TaskMaster',N'U') IS NULL
    THROW 51402, 'Existing ref.TaskMaster is required; no replacement table is created.', 1;
IF EXISTS (
    SELECT 1 FROM (VALUES
        (N'taskMasterId'),(N'regionCode'),(N'taskCode'),(N'description'),(N'active'),
        (N'glCipMajorAccount'),(N'glCipSubAccount'),(N'glAssetMajorAccount'),(N'glAssetSubAccount'),
        (N'glAccumMajorAccount'),(N'glAccumSubAccount'),(N'glDeprMajorAccount'),(N'glDeprSubAccount'),
        (N'assetCategoryMajor'),(N'assetCategoryMinor'),(N'propertyType'),
        (N'sourceFile'),(N'sourceSheet'),(N'sourceVersion'),(N'sourceSha256'),
        (N'isCurrent'),(N'importedAtUtc'),(N'TaskDescription'),(N'TaskMasterVersion'),
        (N'GLAccount'),(N'AssetType'),(N'IsCapitalTask'),(N'EffectiveFrom'),(N'EffectiveTo')
    ) required(name)
    WHERE COL_LENGTH(N'ref.TaskMaster',required.name) IS NULL
) OR COLUMNPROPERTY(OBJECT_ID(N'ref.TaskMaster'),N'TaskDescription','IsComputed')<>1
    THROW 51403, 'TaskMaster differs from migrations 005/009; obtain complete DBA schema before loading.', 1;

DECLARE @SourceFile nvarchar(260)=JSON_VALUE(@Payload,'$.sourceFile');
DECLARE @SourceVersion nvarchar(64)=JSON_VALUE(@Payload,'$.sourceVersion');
DECLARE @SourceSha256 char(64)=JSON_VALUE(@Payload,'$.sourceSha256');
IF @SourceFile<>N'Task List CAN.xlsx' OR @SourceFile IS NULL
    OR @SourceSha256 IS NULL OR @SourceSha256 COLLATE Latin1_General_100_BIN2 LIKE '%[^0-9A-F]%'
    OR LEN(@SourceSha256)<>64 OR @SourceVersion IS NULL
    THROW 51404, 'Source provenance is incomplete or invalid.', 1;

SELECT * INTO #CanadaStage FROM OPENJSON(@Payload,'$.rows') WITH (
    regionCode char(2), taskCode nvarchar(64), description nvarchar(512), active bit,
    glCipMajorAccount nvarchar(32), glCipSubAccount nvarchar(32),
    glAssetMajorAccount nvarchar(32), glAssetSubAccount nvarchar(32),
    glAccumMajorAccount nvarchar(32), glAccumSubAccount nvarchar(32),
    glDeprMajorAccount nvarchar(32), glDeprSubAccount nvarchar(32),
    assetCategoryMajor nvarchar(256), assetCategoryMinor nvarchar(256), propertyType nvarchar(64),
    sourceSheet nvarchar(128)
);
IF (SELECT COUNT(*) FROM #CanadaStage)<>4758
    OR (SELECT COUNT(*) FROM #CanadaStage WHERE sourceSheet=N'Task Type - MACS')<>1434
    OR (SELECT COUNT(*) FROM #CanadaStage WHERE sourceSheet=N'Task Type - CTI')<>3324
    OR EXISTS (SELECT 1 FROM #CanadaStage WHERE regionCode<>'CA' OR regionCode IS NULL
        OR NULLIF(LTRIM(RTRIM(taskCode)),N'') IS NULL
        OR NULLIF(LTRIM(RTRIM(description)),N'') IS NULL OR active IS NULL)
    THROW 51405, 'Canadian source row counts, scope or required values are invalid.', 1;
CREATE UNIQUE INDEX UX_CanadaStage ON #CanadaStage(sourceSheet,taskCode);

IF EXISTS (
    SELECT 1 FROM ref.TaskMaster t JOIN #CanadaStage s
        ON t.regionCode='CA' AND t.taskCode=s.taskCode AND t.sourceSheet=s.sourceSheet
    WHERE (t.isCurrent=1 AND t.sourceSha256<>@SourceSha256)
       OR (t.sourceSha256=@SourceSha256 AND t.isCurrent<>1)
) THROW 51406, 'An existing Canadian version needs approved replacement/reactivation; no automatic retirement.', 1;

IF EXISTS (
    SELECT 1 FROM ref.TaskMaster t JOIN #CanadaStage s
        ON t.regionCode='CA' AND t.taskCode=s.taskCode AND t.sourceSheet=s.sourceSheet
        AND t.sourceSha256=@SourceSha256
    WHERE EXISTS (
        SELECT s.description,s.active,s.glCipMajorAccount,s.glCipSubAccount,
            s.glAssetMajorAccount,s.glAssetSubAccount,s.glAccumMajorAccount,s.glAccumSubAccount,
            s.glDeprMajorAccount,s.glDeprSubAccount,s.assetCategoryMajor,s.assetCategoryMinor,s.propertyType
        EXCEPT
        SELECT t.description,t.active,t.glCipMajorAccount,t.glCipSubAccount,
            t.glAssetMajorAccount,t.glAssetSubAccount,t.glAccumMajorAccount,t.glAccumSubAccount,
            t.glDeprMajorAccount,t.glDeprSubAccount,t.assetCategoryMajor,t.assetCategoryMinor,t.propertyType
    ) OR t.sourceFile<>@SourceFile OR t.sourceVersion<>@SourceVersion
) THROW 51407, 'Existing rows with this source hash disagree with source facts; no overwrite allowed.', 1;

IF @ApplyChanges=0
BEGIN
    SELECT N'PREVIEW_ONLY' AS ResultType,s.sourceSheet,N'CA' AS RegionCode,
        COUNT(*) AS SourceRows,
        SUM(CASE WHEN t.taskMasterId IS NULL THEN 1 ELSE 0 END) AS NewRows,
        0 AS InsertedRows,@SourceSha256 AS SourceSha256,
        N'DBA approval required: allow CA and separate MACS/CTI keys by sourceSheet. US remains unchanged; generic mappings and business dates remain NULL.' AS Reason
    FROM #CanadaStage s LEFT JOIN ref.TaskMaster t
        ON t.regionCode='CA' AND t.sourceSheet=s.sourceSheet AND t.taskCode=s.taskCode
        AND t.sourceSha256=@SourceSha256
    GROUP BY s.sourceSheet;
    RETURN;
END;
IF @ApproveCatalogKeyChange<>1
    THROW 51408, 'DBA must explicitly approve catalog key/constraint changes before apply.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    -- Preserve the non-Canadian uniqueness rules; only CA permits per-sheet codes.
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'ref.TaskMaster')
        AND name=N'UX_TaskMaster_SourceTask' AND is_unique=1)
        OR NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'ref.TaskMaster')
        AND name=N'UX_TaskMaster_CurrentTask' AND is_unique=1)
        OR OBJECT_ID(N'ref.CK_TaskMaster_RegionCode',N'C') IS NULL
        THROW 51409, 'Expected original TaskMaster indexes/constraint are missing; obtain DBA schema.', 1;
    IF EXISTS (
        SELECT 1 FROM sys.indexes i
        CROSS APPLY (
            SELECT STRING_AGG(CONVERT(nvarchar(max),c.name),N',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS keys
            FROM sys.index_columns ic JOIN sys.columns c
                ON c.object_id=ic.object_id AND c.column_id=ic.column_id
            WHERE ic.object_id=i.object_id AND ic.index_id=i.index_id AND ic.key_ordinal>0
        ) k
        WHERE i.object_id=OBJECT_ID(N'ref.TaskMaster')
          AND ((i.name=N'UX_TaskMaster_SourceTask' AND k.keys<>N'regionCode,taskCode,sourceSha256')
            OR (i.name=N'UX_TaskMaster_CurrentTask' AND k.keys<>N'regionCode,taskCode'))
    ) THROW 51410, 'Original index keys differ from the reviewed schema; do not replace them.', 1;
    DECLARE @UsBefore varbinary(32)=HASHBYTES('SHA2_256',(
        SELECT * FROM ref.TaskMaster WITH (UPDLOCK,HOLDLOCK) WHERE regionCode='US'
        ORDER BY taskMasterId FOR JSON PATH,INCLUDE_NULL_VALUES));
    DECLARE @ExpectedFacts varbinary(32)=HASHBYTES('SHA2_256',(
        SELECT * FROM #CanadaStage ORDER BY sourceSheet,taskCode FOR JSON PATH,INCLUDE_NULL_VALUES));
    ALTER TABLE ref.TaskMaster DROP CONSTRAINT CK_TaskMaster_RegionCode;
    ALTER TABLE ref.TaskMaster ADD CONSTRAINT CK_TaskMaster_RegionCode CHECK(regionCode IN ('US','CN','CA'));
    DROP INDEX UX_TaskMaster_SourceTask ON ref.TaskMaster;
    DROP INDEX UX_TaskMaster_CurrentTask ON ref.TaskMaster;
    CREATE UNIQUE INDEX UX_TaskMaster_SourceTask ON ref.TaskMaster(regionCode,taskCode,sourceSha256)
        WHERE regionCode<>'CA';
    CREATE UNIQUE INDEX UX_TaskMaster_CurrentTask ON ref.TaskMaster(regionCode,taskCode)
        WHERE isCurrent=1 AND regionCode<>'CA';
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'ref.TaskMaster') AND name=N'UX_TaskMaster_CA_SourceTask')
        CREATE UNIQUE INDEX UX_TaskMaster_CA_SourceTask ON ref.TaskMaster(regionCode,sourceSheet,taskCode,sourceSha256)
            WHERE regionCode='CA';
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'ref.TaskMaster') AND name=N'UX_TaskMaster_CA_CurrentTask')
        CREATE UNIQUE INDEX UX_TaskMaster_CA_CurrentTask ON ref.TaskMaster(regionCode,sourceSheet,taskCode)
            WHERE regionCode='CA' AND isCurrent=1;

    DECLARE @Inserted TABLE(sourceSheet nvarchar(128));
    INSERT ref.TaskMaster (
        regionCode,taskCode,description,active,glCipMajorAccount,glCipSubAccount,
        glAssetMajorAccount,glAssetSubAccount,glAccumMajorAccount,glAccumSubAccount,
        glDeprMajorAccount,glDeprSubAccount,assetCategoryMajor,assetCategoryMinor,propertyType,
        sourceFile,sourceSheet,sourceVersion,sourceSha256,isCurrent,
        TaskMasterVersion,GLAccount,AssetType,IsCapitalTask,EffectiveFrom,EffectiveTo
    )
    OUTPUT inserted.sourceSheet INTO @Inserted
    SELECT s.regionCode,s.taskCode,s.description,s.active,s.glCipMajorAccount,s.glCipSubAccount,
        s.glAssetMajorAccount,s.glAssetSubAccount,s.glAccumMajorAccount,s.glAccumSubAccount,
        s.glDeprMajorAccount,s.glDeprSubAccount,s.assetCategoryMajor,s.assetCategoryMinor,s.propertyType,
        @SourceFile,s.sourceSheet,@SourceVersion,@SourceSha256,1,NULL,NULL,NULL,NULL,NULL,NULL
    FROM #CanadaStage s WHERE NOT EXISTS (
        SELECT 1 FROM ref.TaskMaster t WITH (UPDLOCK,HOLDLOCK)
        WHERE t.regionCode='CA' AND t.sourceSheet=s.sourceSheet AND t.taskCode=s.taskCode AND t.sourceSha256=@SourceSha256
    );
    IF (SELECT COUNT(*) FROM ref.TaskMaster WHERE regionCode='CA' AND sourceSha256=@SourceSha256 AND isCurrent=1)<>4758
        THROW 51411, 'Canadian import coverage is incomplete; rolling back.', 1;
    IF @ExpectedFacts<>HASHBYTES('SHA2_256',(
        SELECT regionCode,taskCode,description,active,glCipMajorAccount,glCipSubAccount,
            glAssetMajorAccount,glAssetSubAccount,glAccumMajorAccount,glAccumSubAccount,
            glDeprMajorAccount,glDeprSubAccount,assetCategoryMajor,assetCategoryMinor,propertyType,sourceSheet
        FROM ref.TaskMaster WHERE regionCode='CA' AND sourceSha256=@SourceSha256 AND isCurrent=1
            AND sourceFile=@SourceFile AND sourceVersion=@SourceVersion
        ORDER BY sourceSheet,taskCode FOR JSON PATH,INCLUDE_NULL_VALUES))
        THROW 51413, 'Canadian source facts did not survive exact readback; rolling back.', 1;
    IF @UsBefore<>HASHBYTES('SHA2_256',(
        SELECT * FROM ref.TaskMaster WHERE regionCode='US' ORDER BY taskMasterId FOR JSON PATH,INCLUDE_NULL_VALUES))
        THROW 51412, 'US TaskMaster changed; rolling back.', 1;
    COMMIT TRANSACTION;
    SELECT N'APPLIED' AS ResultType,s.sourceSheet,N'CA' AS RegionCode,
        COUNT(*) AS SourceRows,
        (SELECT COUNT(*) FROM @Inserted i WHERE i.sourceSheet=s.sourceSheet) AS InsertedRows,
        @SourceSha256 AS SourceSha256,
        N'MACS/CTI source scopes preserved; US unchanged. Canada workflow catalog selection is not enabled by this import.' AS Reason
    FROM #CanadaStage s GROUP BY s.sourceSheet;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
