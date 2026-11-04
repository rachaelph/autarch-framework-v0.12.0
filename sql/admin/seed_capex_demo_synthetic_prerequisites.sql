SET NOCOUNT ON;
SET XACT_ABORT ON;

-- UNAPPROVED SYNTHETIC DEMO ONLY. Run after the full reset, with writers paused.
-- These rows mirror the JSON demo context; they do not enable SQL policy reads.
IF DB_NAME() <> N'cict-ai-cus-sqldb'
    THROW 51300, 'Run this seed only in cict-ai-cus-sqldb.', 1;
IF @@TRANCOUNT <> 0
    THROW 51301, 'Run this seed outside an existing transaction.', 1;
IF NOT EXISTS (SELECT 1 FROM pub.v1_TaskMaster)
    THROW 51302, 'The governed active TaskMaster is empty or unavailable. Do not seed replacement tasks.', 1;

DECLARE @Context TABLE
(
    ProjectID nvarchar(128) NOT NULL PRIMARY KEY,
    SiteID nvarchar(128) NOT NULL UNIQUE,
    CountryRegion nvarchar(100) NOT NULL,
    InvoiceLabel nvarchar(100) NOT NULL
);
INSERT INTO @Context VALUES
    (N'DEMO-PROJECT-BRUEGMANN', N'DEMO-SITE-001', N'North America', N'Bruegmann'),
    (N'DEMO-PROJECT-KIMLEY-HORN', N'DEMO-SITE-002', N'North America', N'Kimley-Horn'),
    (N'DEMO-PROJECT-JC2000A07814', N'DEMO-SITE-EU-001', N'Europe', N'JC2000A07814');

DECLARE @ThresholdAmount decimal(19,4) = 1000.0000;
DECLARE @ThresholdType varchar(64) = 'SYNTHETIC_DEMO_UNIT_COST_GTE';
DECLARE @EffectiveFrom date = '20261030';
DECLARE @PolicyReference nvarchar(2000) =
    N'UNAPPROVED SYNTHETIC DEMO ONLY; SYN-CAP-003/SYN-CAP-004/SYN-SQL-002. '
    + N'USD unit cost >= 1000 for the synthetic scenario; not approved Circle K policy. '
    + N'EffectiveFrom is a fixture date, not a policy approval date. '
    + N'policy-config-v2.json and synthetic-input-register-v2.json remain the runtime inputs.';

DECLARE @Verification TABLE
(
    SchemaName sysname NOT NULL,
    TableName sysname NOT NULL,
    ExpectedRows bigint NOT NULL,
    ActualRows bigint NULL,
    PRIMARY KEY (SchemaName, TableName)
);
INSERT INTO @Verification (SchemaName, TableName, ExpectedRows) VALUES
    (N'prep', N'Project', 3),
    (N'prep', N'Site', 3),
    (N'ref', N'CapitalizationThreshold', 1),
    (N'prep', N'Invoice', 0),
    (N'prep', N'InvoicePackage', 0),
    (N'prep', N'InvoiceLine', 0),
    (N'prep', N'InvoiceDataQualityWarning', 0),
    (N'prep', N'InvoiceOutbox', 0),
    (N'prep', N'CodingDistribution', 0),
    (N'prep', N'PurchaseOrder', 0),
    (N'prep', N'PurchaseOrderLine', 0),
    (N'task', N'TaskSuggestion', 0),
    (N'capex', N'AssessmentRun', 0),
    (N'capex', N'Assessment', 0),
    (N'capex', N'BundleCandidateGroup', 0),
    (N'capex', N'BundleCandidateMember', 0),
    (N'ref', N'PartMaster', 0),
    (N'ref', N'NeverCapitalizeException', 0),
    (N'ref', N'RepairMaintenanceIndicator', 0),
    (N'review', N'ReviewCase', 0),
    (N'review', N'ReviewDecision', 0),
    (N'review', N'BundleDecision', 0),
    (N'review', N'ApprovedCoding', 0),
    (N'review', N'POPAHandoff', 0),
    (N'ops', N'Outbox', 0);

DECLARE @MasterCount bigint;
DECLARE @Schema sysname, @Table sysname, @Sql nvarchar(max), @Rows bigint;
DECLARE @Error nvarchar(2048);

BEGIN TRY
    BEGIN TRANSACTION;

    SELECT * INTO #TaskMasterBeforeSeed FROM ref.TaskMaster WITH (TABLOCK, HOLDLOCK);
    SELECT @MasterCount = COUNT_BIG(*) FROM #TaskMasterBeforeSeed;
    INSERT INTO @Verification (SchemaName, TableName, ExpectedRows)
    VALUES (N'ref', N'TaskMaster', @MasterCount);

    IF EXISTS (
        SELECT project.AFEProjectID, project.ProjectDescription, project.ProjectTotalBudget,
            project.ProjectStatus, project.ProjectType, project.CountryRegion,
            project.ProjectStartDate, project.BundlingInScope
        FROM prep.Project AS project WITH (TABLOCKX, HOLDLOCK)
        JOIN @Context AS context ON context.ProjectID = project.AFEProjectID
        EXCEPT
        SELECT ProjectID,
            N'UNAPPROVED SYNTHETIC DEMO - ' + InvoiceLabel
                + N'; SYN-CTX-001/SYN-SQL-001; budget 0 is a placeholder, not an actual budget.',
            0.0000, 'OPEN', 'SYNTHETIC_DEMO', CountryRegion, CAST(NULL AS date), CAST(1 AS bit)
        FROM @Context
    )
        THROW 51303, 'An existing demo project differs from the fixture. No rows were overwritten.', 1;

    INSERT INTO prep.Project
        (AFEProjectID, ProjectDescription, ProjectTotalBudget, ProjectStatus,
         ProjectType, CountryRegion, ProjectStartDate, BundlingInScope)
    SELECT ProjectID,
        N'UNAPPROVED SYNTHETIC DEMO - ' + InvoiceLabel
            + N'; SYN-CTX-001/SYN-SQL-001; budget 0 is a placeholder, not an actual budget.',
        0.0000, 'OPEN', 'SYNTHETIC_DEMO', CountryRegion, NULL, 1
    FROM @Context AS context
    WHERE NOT EXISTS (SELECT 1 FROM prep.Project WHERE AFEProjectID = context.ProjectID);

    IF EXISTS (
        SELECT site.SiteID, site.CountryRegion, site.IsLeased
        FROM prep.Site AS site WITH (TABLOCKX, HOLDLOCK)
        JOIN @Context AS context ON context.SiteID = site.SiteID
        EXCEPT
        SELECT SiteID, CountryRegion, CAST(NULL AS bit) FROM @Context
    )
        THROW 51304, 'An existing demo site differs from the fixture. No rows were overwritten.', 1;

    INSERT INTO prep.Site (SiteID, CountryRegion, IsLeased)
    SELECT SiteID, CountryRegion, NULL
    FROM @Context AS context
    WHERE NOT EXISTS (SELECT 1 FROM prep.Site WHERE SiteID = context.SiteID);

    IF EXISTS (
        SELECT GovernedThreshold, CountryRegion, Currency, ProjectBudgetMin,
            CapitalizationPolicyReference, IFRSReference, EffectiveFrom, EffectiveTo
        FROM ref.CapitalizationThreshold WITH (TABLOCKX, HOLDLOCK)
        WHERE ThresholdType = @ThresholdType
        EXCEPT
        SELECT @ThresholdAmount, N'North America', 'USD', CAST(NULL AS decimal(19,4)),
            @PolicyReference, CAST(NULL AS nvarchar(2000)), @EffectiveFrom, CAST(NULL AS date)
    )
        THROW 51305, 'An existing synthetic threshold differs from the fixture. No rows were overwritten.', 1;

    IF NOT EXISTS (SELECT 1 FROM ref.CapitalizationThreshold WHERE ThresholdType = @ThresholdType)
        INSERT INTO ref.CapitalizationThreshold
            (GovernedThreshold, ThresholdType, CountryRegion, Currency, ProjectBudgetMin,
             CapitalizationPolicyReference, IFRSReference, EffectiveFrom, EffectiveTo)
        VALUES (@ThresholdAmount, @ThresholdType, N'North America', 'USD', NULL,
            @PolicyReference, NULL, @EffectiveFrom, NULL);

    WHILE EXISTS (SELECT 1 FROM @Verification WHERE ActualRows IS NULL)
    BEGIN
        SELECT TOP (1) @Schema = SchemaName, @Table = TableName
        FROM @Verification WHERE ActualRows IS NULL ORDER BY SchemaName, TableName;
        SET @Sql = N'SELECT @Rows = COUNT_BIG(*) FROM '
            + QUOTENAME(@Schema) + N'.' + QUOTENAME(@Table) + N' WITH (TABLOCK, HOLDLOCK);';
        EXEC sys.sp_executesql @Sql, N'@Rows bigint OUTPUT', @Rows = @Rows OUTPUT;
        UPDATE @Verification SET ActualRows = @Rows
        WHERE SchemaName = @Schema AND TableName = @Table;
    END;

    IF EXISTS (SELECT 1 FROM @Verification WHERE ActualRows <> ExpectedRows)
    BEGIN
        SELECT @Error = N'Seed requires the clean post-reset state. Unexpected count in '
            + QUOTENAME(SchemaName) + N'.' + QUOTENAME(TableName)
            + N': expected ' + CONVERT(nvarchar(30), ExpectedRows)
            + N', actual ' + CONVERT(nvarchar(30), ActualRows) + N'. Seed rolled back.'
        FROM @Verification WHERE ActualRows <> ExpectedRows;
        THROW 51306, @Error, 1;
    END;

    IF EXISTS (SELECT * FROM #TaskMasterBeforeSeed EXCEPT SELECT * FROM ref.TaskMaster)
        OR EXISTS (SELECT * FROM ref.TaskMaster EXCEPT SELECT * FROM #TaskMasterBeforeSeed)
        THROW 51307, 'TaskMaster preservation check failed. Seed rolled back.', 1;

    DROP TABLE #TaskMasterBeforeSeed;
    COMMIT TRANSACTION;

    -- One result grid keeps the entire prerequisite check visible to the presenter.
    SELECT
        'SYNTHETIC_PREREQUISITES_READY' AS SeedStatus,
        'UNAPPROVED_SYNTHETIC_DEMO' AS Scenario,
        SchemaName + N'.' + TableName AS ObjectName,
        ExpectedRows, ActualRows,
        CASE
            WHEN SchemaName = N'ref' AND TableName = N'TaskMaster' THEN 'PRESERVED_UNCHANGED'
            WHEN ExpectedRows > 0 THEN 'SYNTHETIC_FIXTURE'
            ELSE 'EMPTY_AWAITING_WORKFLOW_OR_SOURCE'
        END AS DataStatus
    FROM @Verification
    ORDER BY CASE WHEN ExpectedRows > 0 THEN 0 ELSE 1 END, SchemaName, TableName;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
