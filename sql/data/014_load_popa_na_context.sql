/*
NA POPA context ONLY. No Invoice, InvoiceLine, coding, reference or review writes.
Generate a populated copy with tools.build_popa_context_load; do not paste entire
POPA exports into other loaders. This template alone deliberately has no source.
Preview is the default. Run in the target Azure SQL database with appropriate
SELECT/INSERT rights on only the four named tables. No schema changes are made.
Existing records must match supplied facts; this is not a snapshot overwrite.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @ApplyChanges bit = 0;
-- Evidence-backed additions only. Never substitute budget=0 or VendorID=Supplier_Name.
-- Project approvals: [{"AFEProjectID":"...","CountryRegion":"US",
--   "ProjectTotalBudget":"123.0000","BundlingInScope":true,"EvidenceReference":"..."}]
-- Budget may be omitted if supplied by POPA; scope is needed only for a new project.
DECLARE @ProjectApprovals nvarchar(max) = N'[]';
-- Vendor mappings: [{"CountryRegion":"CA","SourceSystem":"MACS",
--   "SupplierName":"...","VendorID":"...","EvidenceReference":"..."}]
DECLARE @VendorMappings nvarchar(max) = N'[]';

-- BEGIN GENERATED SOURCE PAYLOAD
DECLARE @Payload nvarchar(max) = N'{}';
-- END GENERATED SOURCE PAYLOAD

IF @ApplyChanges IS NULL
    THROW 51400, 'ApplyChanges must be explicitly 0 (preview) or 1 (approved apply).', 1;
IF ISNULL(ISJSON(@Payload), 0) <> 1 OR ISNULL(TRY_CONVERT(int, JSON_VALUE(@Payload, '$.schemaVersion')), -1) <> 1
    THROW 51400, 'Generate a source-specific POPA query before running this template.', 1;
IF ISNULL(ISJSON(@ProjectApprovals), 0) <> 1 OR ISNULL(ISJSON(@VendorMappings), 0) <> 1
    THROW 51401, 'Approval inputs must be valid JSON arrays.', 1;
IF LEFT(LTRIM(@ProjectApprovals), 1) <> N'[' OR LEFT(LTRIM(@VendorMappings), 1) <> N'['
    THROW 51402, 'Approval inputs must be JSON arrays, not objects.', 1;
IF NOT EXISTS (SELECT 1 FROM OPENJSON(@Payload, '$.sourceFiles'))
    THROW 51403, 'The POPA source manifest is empty.', 1;

DROP TABLE IF EXISTS #Issues;
DROP TABLE IF EXISTS #Projects;
DROP TABLE IF EXISTS #Sites;
DROP TABLE IF EXISTS #Orders;
DROP TABLE IF EXISTS #Lines;
DROP TABLE IF EXISTS #ProjectApproval;
DROP TABLE IF EXISTS #VendorMapping;
CREATE TABLE #Issues (TableName nvarchar(128), RecordKey nvarchar(1024), Reason nvarchar(2000));

INSERT #Issues
SELECT TableName, RecordKey, Reason
FROM OPENJSON(@Payload, '$.issues')
WITH (TableName nvarchar(128), RecordKey nvarchar(1024), Reason nvarchar(2000));

SELECT * INTO #Projects FROM OPENJSON(@Payload, '$.projects') WITH
(
    AFEProjectID nvarchar(max), ProjectDescription nvarchar(max),
    ProjectTotalBudget decimal(19,4), ProjectStatus nvarchar(max),
    ProjectType nvarchar(max), CountryRegion nvarchar(max), ProjectStartDate date
);
SELECT * INTO #Sites FROM OPENJSON(@Payload, '$.sites')
WITH (SiteID nvarchar(max), CountryRegion nvarchar(max));
SELECT * INTO #Orders FROM OPENJSON(@Payload, '$.purchaseOrders')
WITH (PONumber nvarchar(max), CountryRegion nvarchar(max));
SELECT *, CAST(NULL AS nvarchar(max)) AS VendorID INTO #Lines
FROM OPENJSON(@Payload, '$.purchaseOrderLines') WITH
(
    PONumber nvarchar(max), POLineNumber int, PODescription nvarchar(max),
    AFEProjectID nvarchar(max), SiteID nvarchar(max), POTaskCode nvarchar(max),
    BusinessUnit nvarchar(max), SupplierName nvarchar(max),
    CountryRegion nvarchar(max), SourceSystem nvarchar(max)
);
SELECT *, TRY_CONVERT(decimal(19,4), ProjectTotalBudgetText) AS ProjectTotalBudget
INTO #ProjectApproval FROM OPENJSON(@ProjectApprovals) WITH
(
    AFEProjectID nvarchar(max), CountryRegion nvarchar(max),
    ProjectTotalBudgetText nvarchar(max) '$.ProjectTotalBudget',
    BundlingInScope bit, EvidenceReference nvarchar(max)
);
SELECT * INTO #VendorMapping FROM OPENJSON(@VendorMappings) WITH
(
    CountryRegion nvarchar(max), SourceSystem nvarchar(max), SupplierName nvarchar(max),
    VendorID nvarchar(max), EvidenceReference nvarchar(max)
);

IF EXISTS (SELECT AFEProjectID FROM #ProjectApproval GROUP BY AFEProjectID HAVING COUNT(*) > 1)
    OR EXISTS (SELECT CountryRegion, SourceSystem, SupplierName FROM #VendorMapping
               GROUP BY CountryRegion, SourceSystem, SupplierName HAVING COUNT(*) > 1)
    THROW 51404, 'Duplicate approval/crosswalk keys are not allowed.', 1;
IF EXISTS
(
    SELECT 1 FROM #ProjectApproval WHERE ProjectTotalBudgetText IS NOT NULL AND
      (NULLIF(LTRIM(RTRIM(ProjectTotalBudgetText)),N'') IS NULL
       OR ProjectTotalBudgetText LIKE N'%[^0-9.-]%'
       OR ProjectTotalBudget IS NULL
       OR (CHARINDEX(N'.',ProjectTotalBudgetText) > 0 AND
           SUBSTRING(ProjectTotalBudgetText,CHARINDEX(N'.',ProjectTotalBudgetText)+5,LEN(ProjectTotalBudgetText)) LIKE N'%[1-9]%'))
)
    THROW 51404, 'An approved budget is invalid or loses decimal(19,4) precision.', 1;
IF EXISTS (SELECT 1 FROM #ProjectApproval WHERE NULLIF(LTRIM(RTRIM(EvidenceReference)), N'') IS NULL
            OR AFEProjectID IS NULL OR CountryRegion NOT IN (N'US', N'CA') OR CountryRegion IS NULL)
    OR EXISTS (SELECT 1 FROM #VendorMapping WHERE NULLIF(LTRIM(RTRIM(EvidenceReference)), N'') IS NULL
                OR CountryRegion NOT IN (N'US', N'CA') OR CountryRegion IS NULL
                OR NULLIF(LTRIM(RTRIM(SourceSystem)), N'') IS NULL
                OR NULLIF(LTRIM(RTRIM(SupplierName)), N'') IS NULL
                OR NULLIF(LTRIM(RTRIM(VendorID)), N'') IS NULL OR DATALENGTH(VendorID) > 256)
    THROW 51405, 'Every approval needs valid scoped keys, real values and an evidence reference.', 1;
IF EXISTS (SELECT 1 FROM #ProjectApproval a WHERE NOT EXISTS
            (SELECT 1 FROM #Projects p WHERE p.AFEProjectID = a.AFEProjectID AND p.CountryRegion = a.CountryRegion))
    OR EXISTS (SELECT 1 FROM #VendorMapping v WHERE NOT EXISTS
                (SELECT 1 FROM #Lines l WHERE l.CountryRegion = v.CountryRegion
                  AND l.SourceSystem = v.SourceSystem AND l.SupplierName = v.SupplierName))
    THROW 51406, 'An approval does not match this source batch.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    -- Check the known mandatory shape before depending on nullable/default behavior.
    DECLARE @Expected TABLE (TableName sysname, ColumnName sysname, TypeName sysname,
                             MaxLength smallint, Nullable bit);
    INSERT @Expected VALUES
    (N'Project',N'AFEProjectID',N'nvarchar',256,0),
    (N'Project',N'ProjectDescription',N'nvarchar',4000,0),
    (N'Project',N'ProjectTotalBudget',N'decimal',9,0),
    (N'Project',N'ProjectStatus',N'varchar',100,0),
    (N'Project',N'ProjectType',N'varchar',100,0),
    (N'Project',N'CountryRegion',N'nvarchar',200,0),
    (N'Project',N'ProjectStartDate',N'date',3,1),
    (N'Project',N'BundlingInScope',N'bit',1,0),
    (N'Site',N'SiteID',N'nvarchar',256,0),
    (N'Site',N'CountryRegion',N'nvarchar',200,0),
    (N'Site',N'IsLeased',N'bit',1,1),
    (N'PurchaseOrder',N'PONumber',N'nvarchar',256,0),
    (N'PurchaseOrderLine',N'PONumber',N'nvarchar',256,0),
    (N'PurchaseOrderLine',N'POLineNumber',N'int',4,0),
    (N'PurchaseOrderLine',N'PODescription',N'nvarchar',4000,0),
    (N'PurchaseOrderLine',N'AFEProjectID',N'nvarchar',256,0),
    (N'PurchaseOrderLine',N'SiteID',N'nvarchar',256,0),
    (N'PurchaseOrderLine',N'POTaskCode',N'nvarchar',512,0),
    (N'PurchaseOrderLine',N'BusinessUnit',N'nvarchar',200,0),
    (N'PurchaseOrderLine',N'VendorID',N'nvarchar',256,0);
    IF EXISTS
    (
        SELECT 1 FROM @Expected e
        LEFT JOIN sys.columns c ON c.object_id = OBJECT_ID(N'prep.' + e.TableName, N'U')
                               AND c.name = e.ColumnName
        WHERE c.column_id IS NULL OR TYPE_NAME(c.system_type_id) <> e.TypeName
           OR c.max_length <> e.MaxLength OR c.is_nullable <> e.Nullable
           OR c.is_computed = 1 OR c.is_identity = 1
           OR (e.ColumnName = N'ProjectTotalBudget' AND (c.precision <> 19 OR c.scale <> 4))
    )
        THROW 51407, 'Target schema differs from the reviewed four-table contract; stop and review.', 1;

    INSERT #Issues
    SELECT N'prep.Project', p.AFEProjectID, N'Approved budget conflicts with the actual POPA budget.'
    FROM #Projects p JOIN #ProjectApproval a ON a.AFEProjectID = p.AFEProjectID
    WHERE p.ProjectTotalBudget IS NOT NULL AND a.ProjectTotalBudget IS NOT NULL
      AND p.ProjectTotalBudget <> a.ProjectTotalBudget;
    UPDATE p SET ProjectTotalBudget = a.ProjectTotalBudget
    FROM #Projects p JOIN #ProjectApproval a ON a.AFEProjectID = p.AFEProjectID
    WHERE p.ProjectTotalBudget IS NULL;
    UPDATE l SET VendorID = v.VendorID
    FROM #Lines l JOIN #VendorMapping v ON v.CountryRegion = l.CountryRegion
        AND v.SourceSystem = l.SourceSystem AND v.SupplierName = l.SupplierName;

    INSERT #Issues
    SELECT N'prep.Project', COALESCE(AFEProjectID,N'?'), N'Missing/oversized required project fact or invalid country.'
    FROM #Projects WHERE NULLIF(AFEProjectID,N'') IS NULL OR DATALENGTH(AFEProjectID) > 256
      OR NULLIF(ProjectDescription,N'') IS NULL OR DATALENGTH(ProjectDescription) > 4000
      OR NULLIF(ProjectStatus,N'') IS NULL OR DATALENGTH(ProjectStatus) / 2 > 100
      OR NULLIF(ProjectType,N'') IS NULL OR DATALENGTH(ProjectType) / 2 > 100
      OR CountryRegion NOT IN (N'US',N'CA') OR CountryRegion IS NULL;
    INSERT #Issues
    SELECT N'prep.Site', COALESCE(SiteID,N'?'), N'Missing/oversized site key or invalid country.'
    FROM #Sites WHERE NULLIF(SiteID,N'') IS NULL OR DATALENGTH(SiteID) > 256
      OR CountryRegion NOT IN (N'US',N'CA') OR CountryRegion IS NULL;
    INSERT #Issues
    SELECT N'prep.PurchaseOrder', COALESCE(PONumber,N'?'), N'Missing/oversized PO key or invalid country.'
    FROM #Orders WHERE NULLIF(PONumber,N'') IS NULL OR DATALENGTH(PONumber) > 256
      OR CountryRegion NOT IN (N'US',N'CA') OR CountryRegion IS NULL;
    INSERT #Issues
    SELECT N'prep.PurchaseOrderLine', CONCAT(PONumber,N' / ',POLineNumber),
           N'Missing/oversized line context, invalid line number, or unrepresentable source distribution.'
    FROM #Lines WHERE NULLIF(PONumber,N'') IS NULL OR DATALENGTH(PONumber) > 256
      OR POLineNumber IS NULL OR POLineNumber < 1
      OR NULLIF(PODescription,N'') IS NULL OR DATALENGTH(PODescription) > 4000
      OR NULLIF(AFEProjectID,N'') IS NULL OR DATALENGTH(AFEProjectID) > 256
      OR NULLIF(SiteID,N'') IS NULL OR DATALENGTH(SiteID) > 256
      OR NULLIF(POTaskCode,N'') IS NULL OR DATALENGTH(POTaskCode) > 512
      OR NULLIF(BusinessUnit,N'') IS NULL OR DATALENGTH(BusinessUnit) > 200
      OR CountryRegion NOT IN (N'US',N'CA') OR CountryRegion IS NULL;

    -- Hold key ranges until commit/rollback so a concurrent insert cannot bypass checks.
    INSERT #Issues
    SELECT N'prep.Project', p.AFEProjectID, N'Existing project conflicts with supplied facts; no overwrite.'
    FROM #Projects p JOIN prep.Project t WITH (HOLDLOCK) ON t.AFEProjectID = p.AFEProjectID
    LEFT JOIN #ProjectApproval a ON a.AFEProjectID = p.AFEProjectID
    WHERE t.CountryRegion <> p.CountryRegion OR t.ProjectDescription <> p.ProjectDescription
       OR t.ProjectStatus <> p.ProjectStatus OR t.ProjectType <> p.ProjectType
       OR (p.ProjectTotalBudget IS NOT NULL AND t.ProjectTotalBudget <> p.ProjectTotalBudget)
       OR (p.ProjectStartDate IS NOT NULL AND (t.ProjectStartDate <> p.ProjectStartDate OR t.ProjectStartDate IS NULL))
       OR (a.BundlingInScope IS NOT NULL AND a.BundlingInScope <> t.BundlingInScope);
    INSERT #Issues
    SELECT N'prep.Project', p.AFEProjectID, N'New project needs an authoritative budget and an evidence-backed BundlingInScope approval.'
    FROM #Projects p LEFT JOIN #ProjectApproval a ON a.AFEProjectID = p.AFEProjectID
    WHERE NOT EXISTS (SELECT 1 FROM prep.Project t WITH (HOLDLOCK) WHERE t.AFEProjectID = p.AFEProjectID)
      AND (p.ProjectTotalBudget IS NULL OR a.BundlingInScope IS NULL);
    INSERT #Issues
    SELECT N'prep.Site', s.SiteID, N'Existing site belongs to a different country; no overwrite.'
    FROM #Sites s JOIN prep.Site t WITH (HOLDLOCK) ON t.SiteID = s.SiteID
    WHERE t.CountryRegion <> s.CountryRegion;
    INSERT #Issues
    SELECT N'prep.PurchaseOrder', o.PONumber, N'Existing PO header project belongs to a different country.'
    FROM #Orders o JOIN prep.PurchaseOrder t WITH (HOLDLOCK) ON t.PONumber = o.PONumber
    JOIN prep.Project p ON p.AFEProjectID = t.AFEProjectID WHERE p.CountryRegion <> o.CountryRegion;
    INSERT #Issues
    SELECT N'prep.PurchaseOrderLine', CONCAT(l.PONumber,N' / ',l.POLineNumber),
           N'Existing PO line conflicts with supplied facts or vendor mapping; no overwrite.'
    FROM #Lines l JOIN prep.PurchaseOrderLine t WITH (HOLDLOCK)
      ON t.PONumber = l.PONumber AND t.POLineNumber = l.POLineNumber
    JOIN prep.Project p ON p.AFEProjectID = t.AFEProjectID
    WHERE t.PODescription <> l.PODescription OR t.AFEProjectID <> l.AFEProjectID
       OR t.SiteID <> l.SiteID OR t.POTaskCode <> l.POTaskCode OR t.BusinessUnit <> l.BusinessUnit
       OR p.CountryRegion <> l.CountryRegion OR (l.VendorID IS NOT NULL AND t.VendorID <> l.VendorID);
    INSERT #Issues
    SELECT N'prep.PurchaseOrderLine', CONCAT(l.PONumber,N' / ',l.POLineNumber),
           N'New PO line requires an authoritative VendorID crosswalk; SupplierName is not an ID.'
    FROM #Lines l WHERE l.VendorID IS NULL AND NOT EXISTS
      (SELECT 1 FROM prep.PurchaseOrderLine t WITH (HOLDLOCK)
       WHERE t.PONumber = l.PONumber AND t.POLineNumber = l.POLineNumber);
    INSERT #Issues
    SELECT N'prep.PurchaseOrderLine', CONCAT(l.PONumber,N' / ',l.POLineNumber), N'Referenced project or site is absent from both source context and target.'
    FROM #Lines l WHERE
      (NOT EXISTS (SELECT 1 FROM #Projects p WHERE p.AFEProjectID = l.AFEProjectID)
       AND NOT EXISTS (SELECT 1 FROM prep.Project p WHERE p.AFEProjectID = l.AFEProjectID))
      OR (NOT EXISTS (SELECT 1 FROM #Sites s WHERE s.SiteID = l.SiteID)
       AND NOT EXISTS (SELECT 1 FROM prep.Site s WHERE s.SiteID = l.SiteID));

    -- The portal editor may display only the last result set.
    ;WITH CandidateCounts AS
    (
        SELECT N'prep.Project' AS TableName, COUNT(*) AS SourceCandidates FROM #Projects
        UNION ALL SELECT N'prep.Site', COUNT(*) FROM #Sites
        UNION ALL SELECT N'prep.PurchaseOrder', COUNT(*) FROM #Orders
        UNION ALL SELECT N'prep.PurchaseOrderLine', COUNT(*) FROM #Lines
    )
    SELECT N'1_SUMMARY' AS ResultType, TableName,
           CAST(NULL AS nvarchar(max)) AS RecordKey, SourceCandidates,
           CAST(CONCAT(
               CASE WHEN @ApplyChanges = 0 THEN N'PREVIEW_ONLY'
                    ELSE N'APPLY_PREFLIGHT_NOT_COMMITTED' END,
               N'; blocking issues: ', (SELECT COUNT(*) FROM #Issues),
               N'. SourceCandidates are prepared records, not inserted rows.'
           ) AS nvarchar(max)) AS Reason
    FROM CandidateCounts
    UNION ALL
    SELECT N'2_BLOCKER', TableName, RecordKey, NULL, Reason FROM #Issues
    UNION ALL
    SELECT N'3_PROJECT_APPROVAL', N'prep.Project', AFEProjectID, NULL,
           CONCAT(N'Country=',CountryRegion,N'; Budget=',ProjectTotalBudget,
                  N'; BundlingInScope=',BundlingInScope,N'; Evidence=',EvidenceReference)
    FROM #ProjectApproval
    UNION ALL
    SELECT N'4_VENDOR_MAPPING', N'prep.PurchaseOrderLine',
           CONCAT(CountryRegion,N' / ',SourceSystem,N' / ',SupplierName), NULL,
           CONCAT(N'VendorID=',VendorID,N'; Evidence=',EvidenceReference)
    FROM #VendorMapping
    ORDER BY ResultType, TableName, RecordKey;
    IF @ApplyChanges = 0
    BEGIN
        ROLLBACK TRANSACTION;
        PRINT 'POPA_CONTEXT_PREVIEW_ONLY: no permanent records were changed.';
        RETURN;
    END;
    IF EXISTS (SELECT 1 FROM #Issues)
        THROW 51408, 'POPA context has unresolved blockers. No records will be inserted.', 1;

    INSERT INTO prep.Project
        (AFEProjectID,ProjectDescription,ProjectTotalBudget,ProjectStatus,ProjectType,
         CountryRegion,ProjectStartDate,BundlingInScope)
    SELECT p.AFEProjectID,p.ProjectDescription,p.ProjectTotalBudget,p.ProjectStatus,p.ProjectType,
           p.CountryRegion,p.ProjectStartDate,a.BundlingInScope
    FROM #Projects p JOIN #ProjectApproval a ON a.AFEProjectID = p.AFEProjectID
    WHERE NOT EXISTS (SELECT 1 FROM prep.Project t WHERE t.AFEProjectID = p.AFEProjectID);
    INSERT INTO prep.Site (SiteID,CountryRegion,IsLeased)
    SELECT SiteID,CountryRegion,NULL FROM #Sites s
    WHERE NOT EXISTS (SELECT 1 FROM prep.Site t WHERE t.SiteID = s.SiteID);
    -- A distribution's project/description is not assumed to be a unique header fact.
    INSERT INTO prep.PurchaseOrder (PONumber)
    SELECT PONumber FROM #Orders o
    WHERE NOT EXISTS (SELECT 1 FROM prep.PurchaseOrder t WHERE t.PONumber = o.PONumber);
    INSERT INTO prep.PurchaseOrderLine
        (PONumber,POLineNumber,PODescription,AFEProjectID,SiteID,POTaskCode,BusinessUnit,VendorID)
    SELECT PONumber,POLineNumber,PODescription,AFEProjectID,SiteID,POTaskCode,BusinessUnit,VendorID
    FROM #Lines l WHERE NOT EXISTS
      (SELECT 1 FROM prep.PurchaseOrderLine t WHERE t.PONumber = l.PONumber AND t.POLineNumber = l.POLineNumber);

    IF EXISTS
       (SELECT 1 FROM #Projects p LEFT JOIN prep.Project t ON t.AFEProjectID = p.AFEProjectID
        LEFT JOIN #ProjectApproval a ON a.AFEProjectID = p.AFEProjectID
        WHERE t.AFEProjectID IS NULL OR t.CountryRegion <> p.CountryRegion
          OR t.ProjectDescription <> p.ProjectDescription OR t.ProjectStatus <> p.ProjectStatus
          OR t.ProjectType <> p.ProjectType
          OR (p.ProjectTotalBudget IS NOT NULL AND t.ProjectTotalBudget <> p.ProjectTotalBudget)
          OR (p.ProjectStartDate IS NOT NULL AND (t.ProjectStartDate <> p.ProjectStartDate OR t.ProjectStartDate IS NULL))
          OR (a.BundlingInScope IS NOT NULL AND a.BundlingInScope <> t.BundlingInScope))
       OR EXISTS (SELECT 1 FROM #Sites s LEFT JOIN prep.Site t ON t.SiteID = s.SiteID
                  WHERE t.SiteID IS NULL OR t.CountryRegion <> s.CountryRegion)
       OR EXISTS (SELECT PONumber FROM #Orders EXCEPT SELECT PONumber FROM prep.PurchaseOrder)
       OR EXISTS
       (SELECT 1 FROM #Lines l LEFT JOIN prep.PurchaseOrderLine t
          ON t.PONumber = l.PONumber AND t.POLineNumber = l.POLineNumber
        WHERE t.PONumber IS NULL OR t.PODescription <> l.PODescription
          OR t.AFEProjectID <> l.AFEProjectID OR t.SiteID <> l.SiteID
          OR t.POTaskCode <> l.POTaskCode OR t.BusinessUnit <> l.BusinessUnit
          OR (l.VendorID IS NOT NULL AND t.VendorID <> l.VendorID))
        THROW 51409, 'Post-load key/value reconciliation failed.', 1;
    COMMIT TRANSACTION;
    PRINT 'POPA_CONTEXT_LOAD_APPLIED: matching existing rows retained; four-table inserts committed.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
