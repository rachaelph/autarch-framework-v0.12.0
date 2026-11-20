/*
Explicit partial NA POPA load: Site and PurchaseOrder headers ONLY.
Projects and PO lines remain pending; no schema changes or fabricated facts.
Generate the populated portal query with tools.build_popa_context_load.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @ApplyChanges bit = 0;

-- BEGIN GENERATED SOURCE PAYLOAD
DECLARE @Payload nvarchar(max) = N'{}';
-- END GENERATED SOURCE PAYLOAD

IF @ApplyChanges IS NULL OR ISNULL(ISJSON(@Payload),0) <> 1
   OR ISNULL(TRY_CONVERT(int,JSON_VALUE(@Payload,'$.schemaVersion')),-1) <> 1
   OR NOT EXISTS (SELECT 1 FROM OPENJSON(@Payload,'$.sourceFiles'))
    THROW 51500, 'Generate a populated Site/PO-header query; explicitly select preview or apply.', 1;
IF EXISTS (SELECT 1 FROM OPENJSON(@Payload,'$.sourceFiles')
           WITH (CountryRegion nvarchar(100) '$.country')
           WHERE CountryRegion IS NULL OR CountryRegion NOT IN (N'US',N'CA'))
    THROW 51501, 'POPA sources must be US/Canada only.', 1;

DROP TABLE IF EXISTS #Sites;
DROP TABLE IF EXISTS #Orders;
DROP TABLE IF EXISTS #Issues;
SELECT * INTO #Sites FROM OPENJSON(@Payload,'$.sites')
WITH (SiteID nvarchar(max),CountryRegion nvarchar(max));
SELECT * INTO #Orders FROM OPENJSON(@Payload,'$.purchaseOrders')
WITH (PONumber nvarchar(max),CountryRegion nvarchar(max));
SELECT * INTO #Issues FROM OPENJSON(@Payload,'$.issues')
WITH (TableName nvarchar(128),RecordKey nvarchar(1024),Reason nvarchar(2000));
DECLARE @NewSites int, @NewOrders int, @InsertedSites int = 0, @InsertedOrders int = 0;

BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @Expected TABLE(TableName sysname,ColumnName sysname,TypeName sysname,
                            MaxLength smallint,Nullable bit);
    INSERT @Expected VALUES
    (N'Site',N'SiteID',N'nvarchar',256,0),
    (N'Site',N'CountryRegion',N'nvarchar',200,0),
    (N'Site',N'IsLeased',N'bit',1,1),
    (N'PurchaseOrder',N'PONumber',N'nvarchar',256,0),
    (N'PurchaseOrder',N'VendorID',N'nvarchar',256,1),
    (N'PurchaseOrder',N'AFEProjectID',N'nvarchar',256,1),
    (N'PurchaseOrder',N'POStatus',N'varchar',100,1),
    (N'PurchaseOrder',N'PODescription',N'nvarchar',4000,1),
    (N'PurchaseOrder',N'CreatedAtUtc',N'datetime2',7,1),
    (N'PurchaseOrder',N'LastUpdatedAtUtc',N'datetime2',7,1);
    IF EXISTS
    (
        SELECT 1 FROM @Expected e
        LEFT JOIN sys.columns c ON c.object_id=OBJECT_ID(N'prep.'+e.TableName,N'U')
                               AND c.name=e.ColumnName
        WHERE c.column_id IS NULL OR TYPE_NAME(c.system_type_id)<>e.TypeName
           OR c.max_length<>e.MaxLength OR c.is_nullable<>e.Nullable
           OR c.is_computed=1 OR c.is_identity=1
    )
        THROW 51502, 'Site/PO-header schema differs from the reviewed contract.', 1;

    INSERT #Issues
    SELECT N'prep.Site',COALESCE(SiteID,N'?'),N'Invalid site key or country.'
    FROM #Sites WHERE NULLIF(SiteID,N'') IS NULL OR DATALENGTH(SiteID)>256
       OR CountryRegion IS NULL OR CountryRegion NOT IN (N'US',N'CA');
    INSERT #Issues
    SELECT N'prep.PurchaseOrder',COALESCE(PONumber,N'?'),N'Invalid PO key or country.'
    FROM #Orders WHERE NULLIF(PONumber,N'') IS NULL OR DATALENGTH(PONumber)>256
       OR CountryRegion IS NULL OR CountryRegion NOT IN (N'US',N'CA');
    INSERT #Issues
    SELECT N'prep.Site',SiteID,N'Duplicate source site key.'
    FROM #Sites GROUP BY SiteID HAVING COUNT(*)>1;
    INSERT #Issues
    SELECT N'prep.PurchaseOrder',PONumber,N'Duplicate source PO key.'
    FROM #Orders GROUP BY PONumber HAVING COUNT(*)>1;
    INSERT #Issues
    SELECT N'prep.Site',s.SiteID,N'Existing site country conflicts; no overwrite.'
    FROM #Sites s JOIN prep.Site t WITH (HOLDLOCK) ON t.SiteID=s.SiteID
    WHERE t.CountryRegion<>s.CountryRegion;
    INSERT #Issues
    SELECT N'prep.PurchaseOrder',o.PONumber,N'Existing PO project country conflicts; no overwrite.'
    FROM #Orders o JOIN prep.PurchaseOrder t WITH (HOLDLOCK) ON t.PONumber=o.PONumber
    JOIN prep.Project p WITH (HOLDLOCK) ON p.AFEProjectID=t.AFEProjectID
    WHERE p.CountryRegion<>o.CountryRegion;
    SELECT @NewSites=COUNT(*) FROM #Sites s WHERE NOT EXISTS
        (SELECT 1 FROM prep.Site t WITH (HOLDLOCK) WHERE t.SiteID=s.SiteID);
    SELECT @NewOrders=COUNT(*) FROM #Orders o WHERE NOT EXISTS
        (SELECT 1 FROM prep.PurchaseOrder t WITH (HOLDLOCK) WHERE t.PONumber=o.PONumber);

    IF @ApplyChanges=1
    BEGIN
        IF EXISTS (SELECT 1 FROM #Issues)
            THROW 51503, 'Site/PO-header blockers remain; no inserts committed.', 1;
        INSERT INTO prep.Site(SiteID,CountryRegion,IsLeased)
        SELECT SiteID,CountryRegion,NULL FROM #Sites s WHERE NOT EXISTS
            (SELECT 1 FROM prep.Site t WHERE t.SiteID=s.SiteID);
        SET @InsertedSites=@@ROWCOUNT;
        INSERT INTO prep.PurchaseOrder(PONumber)
        SELECT PONumber FROM #Orders o WHERE NOT EXISTS
            (SELECT 1 FROM prep.PurchaseOrder t WHERE t.PONumber=o.PONumber);
        SET @InsertedOrders=@@ROWCOUNT;
        IF EXISTS (SELECT 1 FROM #Sites s LEFT JOIN prep.Site t ON t.SiteID=s.SiteID
                   WHERE t.SiteID IS NULL OR t.CountryRegion<>s.CountryRegion)
           OR EXISTS (SELECT PONumber FROM #Orders EXCEPT SELECT PONumber FROM prep.PurchaseOrder)
           OR @InsertedSites<>@NewSites OR @InsertedOrders<>@NewOrders
            THROW 51504, 'Site/PO-header post-load reconciliation failed.', 1;
        COMMIT TRANSACTION;
    END;

    SELECT N'1_SUMMARY' AS ResultType,N'prep.Site' AS TableName,
           CAST(NULL AS nvarchar(max)) AS RecordKey,
           (SELECT COUNT(*) FROM #Sites) AS SourceCandidates,@NewSites AS NewCandidates,
           @InsertedSites AS InsertedRows,
           CAST(CONCAT(CASE WHEN @ApplyChanges=0 THEN N'PREVIEW_ONLY' ELSE N'APPLIED' END,
                       N'; blockers: ',(SELECT COUNT(*) FROM #Issues)) AS nvarchar(max)) AS Reason
    UNION ALL
    SELECT N'1_SUMMARY',N'prep.PurchaseOrder',NULL,(SELECT COUNT(*) FROM #Orders),
           @NewOrders,@InsertedOrders,
           CONCAT(CASE WHEN @ApplyChanges=0 THEN N'PREVIEW_ONLY' ELSE N'APPLIED' END,
                  N'; blockers: ',(SELECT COUNT(*) FROM #Issues))
    UNION ALL SELECT N'2_BLOCKER',TableName,RecordKey,NULL,NULL,NULL,Reason FROM #Issues
    UNION ALL
    SELECT N'3_PENDING',N'prep.Project',NULL,
           TRY_CONVERT(int,JSON_VALUE(@Payload,'$.pendingProjects')),NULL,0,
           N'Not attempted: budgets and bundling-scope approvals remain unresolved.'
    UNION ALL
    SELECT N'3_PENDING',N'prep.PurchaseOrderLine',NULL,
           TRY_CONVERT(int,JSON_VALUE(@Payload,'$.pendingPurchaseOrderLines')),NULL,0,
           N'Not attempted: vendor IDs and conflicting allocations remain unresolved.'
    ORDER BY ResultType,TableName,RecordKey;
    IF @ApplyChanges=0
        ROLLBACK TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
