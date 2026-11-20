-- PAUSED DRAFT: do not apply pending PO/bundling business decisions.
-- Explicit remaining-table load: Projects and PO-line CORE only.
-- Apply migration 021 first. No distribution table, allocation flattening or invented facts.
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @ApplyChanges bit = 0;
-- BEGIN GENERATED SOURCE PAYLOAD
DECLARE @Payload nvarchar(max) = N'{}';
-- END GENERATED SOURCE PAYLOAD
IF DB_NAME()<>N'cict-ai-cus-sqldb' OR @@TRANCOUNT<>0 OR @ApplyChanges IS NULL
    OR ISNULL(ISJSON(@Payload),0)<>1
    OR ISNULL(TRY_CONVERT(int,JSON_VALUE(@Payload,'$.schemaVersion')),-1)<>1
    OR ISNULL(JSON_VALUE(@Payload,'$.allocationContextImported'),N'?')<>N'false'
    OR NOT EXISTS (SELECT 1 FROM OPENJSON(@Payload,'$.sourceFiles'))
    THROW 51610, 'Generate the populated remaining-table query and use the target database outside a transaction.', 1;
IF EXISTS (SELECT 1 FROM OPENJSON(@Payload,'$.sourceFiles') WITH (country nvarchar(100))
           WHERE country IS NULL OR country NOT IN (N'US',N'CA'))
    THROW 51611, 'POPA context is US/Canada only.', 1;
DROP TABLE IF EXISTS #Projects;
DROP TABLE IF EXISTS #Lines;
DROP TABLE IF EXISTS #Issues;
SELECT * INTO #Projects FROM OPENJSON(@Payload,'$.projects') WITH (
    AFEProjectID nvarchar(max),ProjectDescription nvarchar(max),ProjectTotalBudget decimal(19,4),
    ProjectStatus nvarchar(max),ProjectType nvarchar(max),CountryRegion nvarchar(max),ProjectStartDate date);
SELECT * INTO #Lines FROM OPENJSON(@Payload,'$.purchaseOrderLines') WITH (
    PONumber nvarchar(max),POLineNumber int,PODescription nvarchar(max),
    BusinessUnit nvarchar(max),CountryRegion nvarchar(max),SourceSystem nvarchar(max));
SELECT * INTO #Issues FROM OPENJSON(@Payload,'$.issues') WITH (
    TableName nvarchar(128),RecordKey nvarchar(1024),Reason nvarchar(2000));
DECLARE @NewProjects int,@NewLines int,@InsertedProjects int=0,@InsertedLines int=0;
DECLARE @InsertedProjectKeys TABLE(AFEProjectID nvarchar(128));
DECLARE @InsertedLineKeys TABLE(PONumber nvarchar(128),POLineNumber int);
BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @Expected TABLE(TableName sysname,ColumnName sysname,TypeName sysname,MaxLength smallint,Nullable bit);
    INSERT @Expected VALUES
        (N'Project',N'AFEProjectID',N'nvarchar',256,0),
        (N'Project',N'ProjectDescription',N'nvarchar',4000,0),
        (N'Project',N'ProjectTotalBudget',N'decimal',9,1),
        (N'Project',N'ProjectStatus',N'varchar',100,0),
        (N'Project',N'ProjectType',N'varchar',100,0),
        (N'Project',N'CountryRegion',N'nvarchar',200,0),
        (N'Project',N'ProjectStartDate',N'date',3,1),
        (N'Project',N'BundlingInScope',N'bit',1,1),
        (N'PurchaseOrderLine',N'PONumber',N'nvarchar',256,0),
        (N'PurchaseOrderLine',N'POLineNumber',N'int',4,0),
        (N'PurchaseOrderLine',N'PODescription',N'nvarchar',4000,0),
        (N'PurchaseOrderLine',N'BusinessUnit',N'nvarchar',200,0),
        (N'PurchaseOrderLine',N'AFEProjectID',N'nvarchar',256,1),
        (N'PurchaseOrderLine',N'SiteID',N'nvarchar',256,1),
        (N'PurchaseOrderLine',N'POTaskCode',N'nvarchar',512,1),
        (N'PurchaseOrderLine',N'VendorID',N'nvarchar',256,1);
    IF EXISTS (
        SELECT 1 FROM @Expected e LEFT JOIN sys.columns c
            ON c.object_id=OBJECT_ID(N'prep.'+e.TableName,N'U') AND c.name=e.ColumnName
        WHERE c.column_id IS NULL OR TYPE_NAME(c.system_type_id)<>e.TypeName
            OR c.max_length<>e.MaxLength OR c.is_nullable<>e.Nullable OR c.is_computed=1 OR c.is_identity=1
            OR (e.TypeName=N'decimal' AND (c.precision<>19 OR c.scale<>4))
    ) THROW 51612, 'Project/PO-line schema differs; apply the reviewed nullable migration 021 first.', 1;
    INSERT #Issues
    SELECT N'prep.Project',COALESCE(AFEProjectID,N'?'),N'Invalid project key, description, country or required source facts.'
    FROM #Projects WHERE NULLIF(AFEProjectID,N'') IS NULL OR DATALENGTH(AFEProjectID)>256
        OR NULLIF(ProjectDescription,N'') IS NULL OR DATALENGTH(ProjectDescription)>4000
        OR NULLIF(ProjectStatus,N'') IS NULL OR DATALENGTH(ProjectStatus)>200
        OR NULLIF(ProjectType,N'') IS NULL OR DATALENGTH(ProjectType)>200
        OR CountryRegion IS NULL OR CountryRegion NOT IN (N'US',N'CA')
        OR ProjectTotalBudget<0
        OR CONVERT(nvarchar(max),CONVERT(varchar(100),ProjectStatus)) COLLATE Latin1_General_100_BIN2
            <>ProjectStatus COLLATE Latin1_General_100_BIN2
        OR CONVERT(nvarchar(max),CONVERT(varchar(100),ProjectType)) COLLATE Latin1_General_100_BIN2
            <>ProjectType COLLATE Latin1_General_100_BIN2;
    INSERT #Issues
    SELECT N'prep.PurchaseOrderLine',CONCAT(PONumber,N' / ',POLineNumber),N'Invalid PO-line key, description, business unit or source identity.'
    FROM #Lines WHERE NULLIF(PONumber,N'') IS NULL OR DATALENGTH(PONumber)>256
        OR POLineNumber IS NULL OR POLineNumber<1 OR NULLIF(PODescription,N'') IS NULL
        OR DATALENGTH(PODescription)>4000 OR NULLIF(BusinessUnit,N'') IS NULL OR DATALENGTH(BusinessUnit)>200
        OR CountryRegion IS NULL OR CountryRegion NOT IN (N'US',N'CA') OR NULLIF(SourceSystem,N'') IS NULL;
    INSERT #Issues SELECT N'prep.Project',AFEProjectID,N'Duplicate source project key.'
        FROM #Projects GROUP BY AFEProjectID HAVING COUNT(*)>1;
    INSERT #Issues SELECT N'prep.PurchaseOrderLine',CONCAT(PONumber,N' / ',POLineNumber),N'Duplicate source PO-line key.'
        FROM #Lines GROUP BY PONumber,POLineNumber HAVING COUNT(*)>1;
    INSERT #Issues
    SELECT N'prep.PurchaseOrderLine',CONCAT(s.PONumber,N' / ',s.POLineNumber),N'PO header missing; load headers first.'
    FROM #Lines s LEFT JOIN prep.PurchaseOrder t WITH (HOLDLOCK) ON t.PONumber=s.PONumber WHERE t.PONumber IS NULL;
    INSERT #Issues
    SELECT N'prep.Project',s.AFEProjectID,N'Existing project disagrees with known source facts; no overwrite.'
    FROM #Projects s JOIN prep.Project t WITH (HOLDLOCK) ON t.AFEProjectID=s.AFEProjectID
    WHERE t.ProjectDescription COLLATE Latin1_General_100_BIN2<>s.ProjectDescription COLLATE Latin1_General_100_BIN2
        OR t.ProjectStatus<>s.ProjectStatus OR t.ProjectType<>s.ProjectType OR t.CountryRegion<>s.CountryRegion
        OR (s.ProjectTotalBudget IS NOT NULL AND (t.ProjectTotalBudget IS NULL OR t.ProjectTotalBudget<>s.ProjectTotalBudget))
        OR (s.ProjectStartDate IS NOT NULL AND (t.ProjectStartDate IS NULL OR t.ProjectStartDate<>s.ProjectStartDate));
    INSERT #Issues
    SELECT N'prep.PurchaseOrderLine',CONCAT(s.PONumber,N' / ',s.POLineNumber),N'Existing line differs or has unverified allocation context; no overwrite/clearing.'
    FROM #Lines s JOIN prep.PurchaseOrderLine t WITH (HOLDLOCK)
        ON t.PONumber=s.PONumber AND t.POLineNumber=s.POLineNumber
    WHERE t.PODescription COLLATE Latin1_General_100_BIN2<>s.PODescription COLLATE Latin1_General_100_BIN2
        OR t.BusinessUnit<>s.BusinessUnit OR t.AFEProjectID IS NOT NULL OR t.SiteID IS NOT NULL OR t.POTaskCode IS NOT NULL;
    SELECT @NewProjects=COUNT(*) FROM #Projects s WHERE NOT EXISTS
        (SELECT 1 FROM prep.Project t WITH (HOLDLOCK) WHERE t.AFEProjectID=s.AFEProjectID);
    SELECT @NewLines=COUNT(*) FROM #Lines s WHERE NOT EXISTS
        (SELECT 1 FROM prep.PurchaseOrderLine t WITH (HOLDLOCK) WHERE t.PONumber=s.PONumber AND t.POLineNumber=s.POLineNumber);
    IF @ApplyChanges=1
    BEGIN
        IF EXISTS (SELECT 1 FROM #Issues) THROW 51613, 'Remaining-table blockers exist; no rows committed.', 1;
        INSERT INTO prep.Project (
            AFEProjectID,ProjectDescription,ProjectTotalBudget,ProjectStatus,ProjectType,CountryRegion,ProjectStartDate,BundlingInScope)
        OUTPUT inserted.AFEProjectID INTO @InsertedProjectKeys
        SELECT AFEProjectID,ProjectDescription,ProjectTotalBudget,ProjectStatus,ProjectType,CountryRegion,ProjectStartDate,NULL
        FROM #Projects s WHERE NOT EXISTS (SELECT 1 FROM prep.Project t WHERE t.AFEProjectID=s.AFEProjectID);
        SET @InsertedProjects=@@ROWCOUNT;
        INSERT INTO prep.PurchaseOrderLine (
            PONumber,POLineNumber,PODescription,BusinessUnit,AFEProjectID,SiteID,POTaskCode,VendorID)
        OUTPUT inserted.PONumber,inserted.POLineNumber INTO @InsertedLineKeys
        SELECT PONumber,POLineNumber,PODescription,BusinessUnit,NULL,NULL,NULL,NULL FROM #Lines s
        WHERE NOT EXISTS (SELECT 1 FROM prep.PurchaseOrderLine t WHERE t.PONumber=s.PONumber AND t.POLineNumber=s.POLineNumber);
        SET @InsertedLines=@@ROWCOUNT;
        IF @InsertedProjects<>@NewProjects OR @InsertedLines<>@NewLines
            OR EXISTS (
                SELECT 1 FROM #Projects s LEFT JOIN prep.Project t ON t.AFEProjectID=s.AFEProjectID
                WHERE t.AFEProjectID IS NULL OR t.ProjectDescription COLLATE Latin1_General_100_BIN2<>s.ProjectDescription COLLATE Latin1_General_100_BIN2
                    OR t.ProjectStatus<>s.ProjectStatus OR t.ProjectType<>s.ProjectType OR t.CountryRegion<>s.CountryRegion
                    OR (s.ProjectTotalBudget IS NOT NULL AND (t.ProjectTotalBudget IS NULL OR t.ProjectTotalBudget<>s.ProjectTotalBudget))
                    OR (s.ProjectStartDate IS NOT NULL AND (t.ProjectStartDate IS NULL OR t.ProjectStartDate<>s.ProjectStartDate)))
            OR EXISTS (
                SELECT 1 FROM #Lines s LEFT JOIN prep.PurchaseOrderLine t
                    ON t.PONumber=s.PONumber AND t.POLineNumber=s.POLineNumber
                WHERE t.PONumber IS NULL OR t.PODescription COLLATE Latin1_General_100_BIN2<>s.PODescription COLLATE Latin1_General_100_BIN2
                    OR t.BusinessUnit<>s.BusinessUnit OR t.AFEProjectID IS NOT NULL OR t.SiteID IS NOT NULL OR t.POTaskCode IS NOT NULL)
            THROW 51614, 'Remaining-table source-fact/count reconciliation failed; rolling back.', 1;
        IF EXISTS (
            SELECT 1 FROM @InsertedProjectKeys k JOIN prep.Project t ON t.AFEProjectID=k.AFEProjectID
            JOIN #Projects s ON s.AFEProjectID=k.AFEProjectID
            WHERE t.BundlingInScope IS NOT NULL
                OR (s.ProjectTotalBudget IS NULL AND t.ProjectTotalBudget IS NOT NULL)
                OR (s.ProjectStartDate IS NULL AND t.ProjectStartDate IS NOT NULL))
            OR EXISTS (
                SELECT 1 FROM @InsertedLineKeys k JOIN prep.PurchaseOrderLine t
                    ON t.PONumber=k.PONumber AND t.POLineNumber=k.POLineNumber
                WHERE t.VendorID IS NOT NULL)
            THROW 51615, 'Unknown source facts were replaced during insert; rolling back.', 1;
        COMMIT TRANSACTION;
    END;
    SELECT N'1_SUMMARY' AS ResultType,N'prep.Project' AS TableName,CAST(NULL AS nvarchar(max)) AS RecordKey,
        (SELECT COUNT(*) FROM #Projects) AS SourceCandidates,@NewProjects AS NewCandidates,@InsertedProjects AS InsertedRows,
        CAST(CONCAT(CASE WHEN @ApplyChanges=0 THEN N'PREVIEW_ONLY' ELSE N'APPLIED' END,
                    N'; blockers: ',(SELECT COUNT(*) FROM #Issues)) AS nvarchar(max)) AS Reason
    UNION ALL SELECT N'1_SUMMARY',N'prep.PurchaseOrderLine',NULL,(SELECT COUNT(*) FROM #Lines),@NewLines,@InsertedLines,
        CONCAT(CASE WHEN @ApplyChanges=0 THEN N'PREVIEW_ONLY' ELSE N'APPLIED' END,N'; blockers: ',(SELECT COUNT(*) FROM #Issues))
    UNION ALL SELECT N'2_BLOCKER',TableName,RecordKey,NULL,NULL,NULL,Reason FROM #Issues
    UNION ALL SELECT N'3_DEFERRED',N'PO distribution context',NULL,
        TRY_CONVERT(int,JSON_VALUE(@Payload,'$.sourceAllocationCount')),NULL,0,
        N'AFE/site/task allocations preserved in source JSON/CSVs, not flattened into PO lines or imported to a new table.'
    ORDER BY ResultType,TableName,RecordKey;
    IF @ApplyChanges=0 ROLLBACK TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
