SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Additive physical implementation of the v2.1 logical model.
-- Retain runtime keys and leave newly unavailable business facts NULL.
IF OBJECT_ID(N'prep.InvoicePackage', N'U') IS NULL
    OR OBJECT_ID(N'prep.InvoiceLine', N'U') IS NULL
    OR OBJECT_ID(N'task.TaskSuggestion', N'U') IS NULL
    OR OBJECT_ID(N'ref.TaskMaster', N'U') IS NULL
    OR OBJECT_ID(N'capex.Assessment', N'U') IS NULL
    OR OBJECT_ID(N'capex.BundleCandidateGroup', N'U') IS NULL
    OR OBJECT_ID(N'capex.BundleCandidateMember', N'U') IS NULL
    THROW 51110, 'Apply prerequisite migrations 001 through 007 before model completion.', 1;

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID(N'task.TaskSuggestion')
      AND name = N'taskSuggestionId' AND TYPE_NAME(system_type_id) = N'uniqueidentifier'
) OR NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID(N'capex.Assessment')
      AND name = N'AssessmentId' AND TYPE_NAME(system_type_id) = N'uniqueidentifier'
)
    THROW 51111, 'Runtime ID types differ from the supported GUID baseline. Review before migration.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    IF SCHEMA_ID(N'review') IS NULL
        EXEC(N'CREATE SCHEMA review AUTHORIZATION dbo;');
    IF SCHEMA_ID(N'ops') IS NULL
        EXEC(N'CREATE SCHEMA ops AUTHORIZATION dbo;');

    IF OBJECT_ID(N'ref.CapitalizationThreshold', N'U') IS NULL
        CREATE TABLE ref.CapitalizationThreshold
        (
            ThresholdID int IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_CapitalizationThreshold PRIMARY KEY,
            GovernedThreshold decimal(19,4) NOT NULL,
            ThresholdType varchar(64) NOT NULL,
            CountryRegion nvarchar(100) NOT NULL,
            Currency char(3) NOT NULL,
            ProjectBudgetMin decimal(19,4) NULL,
            CapitalizationPolicyReference nvarchar(2000) NOT NULL,
            IFRSReference nvarchar(2000) NULL,
            EffectiveFrom date NOT NULL,
            EffectiveTo date NULL,
            CONSTRAINT CK_DM_Threshold_Amount CHECK
                (GovernedThreshold >= 0 AND (ProjectBudgetMin IS NULL OR ProjectBudgetMin >= 0)),
            CONSTRAINT CK_DM_Threshold_Dates CHECK (EffectiveTo IS NULL OR EffectiveTo >= EffectiveFrom)
        );

    IF OBJECT_ID(N'ref.NeverCapitalizeException', N'U') IS NULL
        CREATE TABLE ref.NeverCapitalizeException
        (
            ExceptionID int IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_NeverCapitalizeException PRIMARY KEY,
            ItemCategory nvarchar(256) NOT NULL,
            Keyword nvarchar(512) NOT NULL,
            PolicyReference nvarchar(2000) NOT NULL,
            Active bit NOT NULL
        );

    IF OBJECT_ID(N'ref.RepairMaintenanceIndicator', N'U') IS NULL
        CREATE TABLE ref.RepairMaintenanceIndicator
        (
            IndicatorID int IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_RepairMaintenanceIndicator PRIMARY KEY,
            Keyword nvarchar(512) NOT NULL,
            Category varchar(128) NOT NULL,
            SignalStrength decimal(19,4) NOT NULL,
            PolicyReference nvarchar(2000) NOT NULL
        );

    IF OBJECT_ID(N'ref.PartMaster', N'U') IS NULL
        CREATE TABLE ref.PartMaster
        (
            PartMasterID bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_PartMaster PRIMARY KEY,
            VendorID nvarchar(128) NOT NULL,
            PartNumber nvarchar(256) NOT NULL,
            Version int NOT NULL,
            ItemDescription nvarchar(2000) NOT NULL,
            MappedTaskCode nvarchar(256) NULL,
            MappedAssetClass nvarchar(256) NULL,
            Source nvarchar(1024) NOT NULL,
            ConfirmedBy nvarchar(256) NULL,
            ConfirmedAtUtc datetime2(3) NULL,
            IsCurrent bit NOT NULL,
            ConflictStatus varchar(64) NULL,
            CONSTRAINT UQ_DM_PartMaster_Version UNIQUE (VendorID, PartNumber, Version),
            CONSTRAINT CK_DM_PartMaster_Version CHECK (Version >= 1)
        );

    IF OBJECT_ID(N'prep.Project', N'U') IS NULL
        CREATE TABLE prep.Project
        (
            AFEProjectID nvarchar(128) NOT NULL CONSTRAINT PK_DM_Project PRIMARY KEY,
            ProjectDescription nvarchar(2000) NOT NULL,
            ProjectTotalBudget decimal(19,4) NOT NULL,
            ProjectStatus varchar(100) NOT NULL,
            ProjectType varchar(100) NOT NULL,
            CountryRegion nvarchar(100) NOT NULL,
            ProjectStartDate date NULL,
            BundlingInScope bit NOT NULL
        );

    IF OBJECT_ID(N'prep.Site', N'U') IS NULL
        CREATE TABLE prep.Site
        (
            SiteID nvarchar(128) NOT NULL CONSTRAINT PK_DM_Site PRIMARY KEY,
            CountryRegion nvarchar(100) NOT NULL,
            IsLeased bit NULL
        );

    IF OBJECT_ID(N'prep.PurchaseOrder', N'U') IS NULL
        CREATE TABLE prep.PurchaseOrder
        (
            PONumber nvarchar(128) NOT NULL CONSTRAINT PK_DM_PurchaseOrder PRIMARY KEY,
            VendorID nvarchar(128) NULL,
            AFEProjectID nvarchar(128) NULL,
            POStatus varchar(100) NULL,
            PODescription nvarchar(2000) NULL,
            CreatedAtUtc datetime2(3) NULL,
            LastUpdatedAtUtc datetime2(3) NULL,
            CONSTRAINT FK_DM_PurchaseOrder_Project FOREIGN KEY (AFEProjectID)
                REFERENCES prep.Project (AFEProjectID)
        );

    IF OBJECT_ID(N'prep.PurchaseOrderLine', N'U') IS NULL
        CREATE TABLE prep.PurchaseOrderLine
        (
            PONumber nvarchar(128) NOT NULL,
            POLineNumber int NOT NULL,
            PODescription nvarchar(2000) NOT NULL,
            AFEProjectID nvarchar(128) NOT NULL,
            SiteID nvarchar(128) NOT NULL,
            POTaskCode nvarchar(256) NOT NULL,
            BusinessUnit nvarchar(100) NOT NULL,
            VendorID nvarchar(128) NOT NULL,
            CONSTRAINT PK_DM_PurchaseOrderLine PRIMARY KEY (PONumber, POLineNumber),
            CONSTRAINT FK_DM_PurchaseOrderLine_Order FOREIGN KEY (PONumber)
                REFERENCES prep.PurchaseOrder (PONumber),
            CONSTRAINT FK_DM_PurchaseOrderLine_Project FOREIGN KEY (AFEProjectID)
                REFERENCES prep.Project (AFEProjectID),
            CONSTRAINT FK_DM_PurchaseOrderLine_Site FOREIGN KEY (SiteID)
                REFERENCES prep.Site (SiteID),
            CONSTRAINT CK_DM_PurchaseOrderLine_Number CHECK (POLineNumber >= 1)
        );

    IF OBJECT_ID(N'prep.Invoice', N'U') IS NULL
        CREATE TABLE prep.Invoice
        (
            InvoiceID nvarchar(128) NOT NULL CONSTRAINT PK_DM_Invoice PRIMARY KEY,
            InvoiceNumber nvarchar(128) NOT NULL,
            InvoiceDate date NOT NULL,
            VendorID nvarchar(128) NOT NULL,
            VendorName nvarchar(300) NOT NULL,
            InvoiceCurrency char(3) NOT NULL,
            InvoiceTotal decimal(19,4) NOT NULL,
            PONumber nvarchar(128) NULL,
            CountryRegion nvarchar(100) NOT NULL,
            SourceSystem nvarchar(64) NOT NULL,
            NuvoloWorkOrderNo nvarchar(128) NULL,
            HasLineItems bit NOT NULL,
            RunID nvarchar(128) NOT NULL,
            LoadedAtUtc datetime2(3) NOT NULL,
            CONSTRAINT UQ_DM_Invoice_Vendor_Number UNIQUE (VendorID, InvoiceNumber),
            CONSTRAINT FK_DM_Invoice_PurchaseOrder FOREIGN KEY (PONumber)
                REFERENCES prep.PurchaseOrder (PONumber)
        );

    IF OBJECT_ID(N'prep.CodingDistribution', N'U') IS NULL
        CREATE TABLE prep.CodingDistribution
        (
            ApprovedCodingDistributionID nvarchar(128) NOT NULL CONSTRAINT PK_DM_CodingDistribution PRIMARY KEY,
            InvoiceID nvarchar(128) NOT NULL,
            CodedTaskCode nvarchar(256) NOT NULL,
            CodedTaskDescription nvarchar(2000) NOT NULL,
            CodedAssetClass nvarchar(256) NULL,
            DistributionAmount decimal(19,4) NOT NULL,
            ApproverID nvarchar(256) NOT NULL,
            ApprovalDate date NOT NULL,
            CONSTRAINT FK_DM_CodingDistribution_Invoice FOREIGN KEY (InvoiceID)
                REFERENCES prep.Invoice (InvoiceID)
        );

    IF OBJECT_ID(N'review.ReviewCase', N'U') IS NULL
        CREATE TABLE review.ReviewCase
        (
            ReviewCaseID bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_ReviewCase PRIMARY KEY,
            InvoiceID nvarchar(128) NOT NULL,
            CaseStatus varchar(64) NOT NULL,
            AssignedTo nvarchar(256) NULL,
            CreatedFromEventID nvarchar(256) NOT NULL,
            RowVersion rowversion NOT NULL,
            CreatedAtUtc datetime2(3) NOT NULL,
            UpdatedAtUtc datetime2(3) NOT NULL,
            CONSTRAINT FK_DM_ReviewCase_Invoice FOREIGN KEY (InvoiceID)
                REFERENCES prep.Invoice (InvoiceID)
        );

    IF OBJECT_ID(N'review.ReviewDecision', N'U') IS NULL
        CREATE TABLE review.ReviewDecision
        (
            DecisionID bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_ReviewDecision PRIMARY KEY,
            ReviewCaseID bigint NOT NULL,
            InvoiceLineID nvarchar(256) NOT NULL,
            AssessmentID uniqueidentifier NOT NULL,
            TaskSuggestionID uniqueidentifier NOT NULL,
            FinalTaskCode nvarchar(256) NULL,
            FinalCapExOpEx nvarchar(16) NOT NULL,
            FinalOutcomeCategory nvarchar(128) NOT NULL,
            ConfirmedItem nvarchar(2000) NULL,
            DecisionType varchar(64) NOT NULL,
            OverrideReason nvarchar(2000) NULL,
            ReviewerID nvarchar(256) NOT NULL,
            DecidedAtUtc datetime2(3) NOT NULL,
            DecisionVersion int NOT NULL,
            IsCurrent bit NOT NULL,
            CONSTRAINT FK_DM_ReviewDecision_Case FOREIGN KEY (ReviewCaseID)
                REFERENCES review.ReviewCase (ReviewCaseID),
            CONSTRAINT FK_DM_ReviewDecision_Assessment FOREIGN KEY (AssessmentID)
                REFERENCES capex.Assessment (AssessmentId),
            CONSTRAINT FK_DM_ReviewDecision_Task FOREIGN KEY (TaskSuggestionID)
                REFERENCES task.TaskSuggestion (taskSuggestionId),
            CONSTRAINT UQ_DM_ReviewDecision_Version UNIQUE (ReviewCaseID, InvoiceLineID, DecisionVersion),
            CONSTRAINT CK_DM_ReviewDecision_Version CHECK (DecisionVersion >= 1),
            CONSTRAINT CK_DM_ReviewDecision_Result CHECK (FinalCapExOpEx IN ('CAPEX', 'OPEX', 'REVIEW'))
        );

    IF OBJECT_ID(N'review.BundleDecision', N'U') IS NULL
        CREATE TABLE review.BundleDecision
        (
            BundleDecisionID bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_BundleDecision PRIMARY KEY,
            BundleGroupID nvarchar(128) NOT NULL,
            WorkflowRunID nvarchar(128) NOT NULL,
            ReviewCaseID bigint NOT NULL,
            IsOneAsset bit NOT NULL,
            Reason nvarchar(2000) NULL,
            ReviewerID nvarchar(256) NOT NULL,
            DecidedAtUtc datetime2(3) NOT NULL,
            CONSTRAINT FK_DM_BundleDecision_Case FOREIGN KEY (ReviewCaseID)
                REFERENCES review.ReviewCase (ReviewCaseID),
            CONSTRAINT FK_DM_BundleDecision_Group FOREIGN KEY (WorkflowRunID, BundleGroupID)
                REFERENCES capex.BundleCandidateGroup (WorkflowRunId, BundleGroupId)
        );

    IF OBJECT_ID(N'review.ApprovedCoding', N'U') IS NULL
        CREATE TABLE review.ApprovedCoding
        (
            ApprovedCodingID bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_ApprovedCoding PRIMARY KEY,
            ReviewCaseID bigint NOT NULL,
            InvoiceID nvarchar(128) NOT NULL,
            TaskCode nvarchar(256) NOT NULL,
            DistributionAmount decimal(19,4) NOT NULL,
            DecisionVersion int NOT NULL,
            CONSTRAINT FK_DM_ApprovedCoding_Case FOREIGN KEY (ReviewCaseID)
                REFERENCES review.ReviewCase (ReviewCaseID),
            CONSTRAINT FK_DM_ApprovedCoding_Invoice FOREIGN KEY (InvoiceID)
                REFERENCES prep.Invoice (InvoiceID),
            CONSTRAINT CK_DM_ApprovedCoding_Version CHECK (DecisionVersion >= 1)
        );

    IF OBJECT_ID(N'review.POPAHandoff', N'U') IS NULL
        CREATE TABLE review.POPAHandoff
        (
            HandoffID bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_POPAHandoff PRIMARY KEY,
            ReviewCaseID bigint NOT NULL,
            HandoffMethod varchar(64) NOT NULL,
            HandoffStatus varchar(64) NOT NULL,
            AckReference nvarchar(1024) NULL,
            UpdatedAtUtc datetime2(3) NOT NULL,
            CONSTRAINT FK_DM_POPAHandoff_Case FOREIGN KEY (ReviewCaseID)
                REFERENCES review.ReviewCase (ReviewCaseID)
        );

    IF OBJECT_ID(N'ops.Outbox', N'U') IS NULL
        CREATE TABLE ops.Outbox
        (
            OutboxID bigint IDENTITY(1,1) NOT NULL CONSTRAINT PK_DM_Outbox PRIMARY KEY,
            EventType varchar(128) NOT NULL,
            SchemaVersion varchar(16) NOT NULL,
            InvoiceID nvarchar(128) NOT NULL,
            PayloadJson nvarchar(max) NOT NULL,
            OwnerComponent varchar(128) NOT NULL,
            CreatedAtUtc datetime2(3) NOT NULL,
            PublishedAtUtc datetime2(3) NULL,
            IdempotencyKey nvarchar(256) NOT NULL,
            CONSTRAINT UQ_DM_Outbox_Idempotency UNIQUE (IdempotencyKey),
            CONSTRAINT CK_DM_Outbox_Json CHECK (ISJSON(PayloadJson) = 1)
        );

    DECLARE @Columns TABLE
    (
        SchemaName sysname NOT NULL,
        TableName sysname NOT NULL,
        ColumnName sysname NOT NULL,
        Definition nvarchar(1000) NOT NULL
    );
    INSERT INTO @Columns VALUES
        (N'prep', N'InvoicePackage', N'BundlingEligible', N'bit NULL'),
        (N'prep', N'InvoiceLine', N'InvoiceID', N'nvarchar(128) NULL'),
        (N'prep', N'InvoiceLine', N'LineDescription', N'AS ([Description])'),
        (N'task', N'TaskSuggestion', N'InvoiceLineID', N'AS ([lineId])'),
        (N'task', N'TaskSuggestion', N'ItemIdentified', N'nvarchar(2000) NULL'),
        (N'task', N'TaskSuggestion', N'SuggestedTaskCode', N'AS ([taskCode])'),
        (N'task', N'TaskSuggestion', N'TaskMasterVersion', N'int NULL'),
        (N'task', N'TaskSuggestion', N'SuggestedAssetClass', N'AS ([assetClassCode])'),
        (N'task', N'TaskSuggestion', N'MatchMethod', N'varchar(64) NULL'),
        (N'task', N'TaskSuggestion', N'PartMasterID', N'bigint NULL'),
        (N'task', N'TaskSuggestion', N'ResolutionStatus', N'AS ([resultStatus])'),
        (N'task', N'TaskSuggestion', N'UnresolvedReason', N'nvarchar(2000) NULL'),
        (N'task', N'TaskSuggestion', N'Evidence', N'AS ([evidenceRefs])'),
        (N'task', N'TaskSuggestion', N'ModelVersion', N'AS ([modelDeployment])'),
        (N'task', N'TaskSuggestion', N'ContractVersion', N'varchar(16) NULL'),
        (N'capex', N'Assessment', N'InvoiceLineID', N'AS ([LineId])'),
        (N'capex', N'Assessment', N'TaskSuggestionID', N'uniqueidentifier NULL'),
        (N'capex', N'Assessment', N'ThresholdID', N'int NULL'),
        (N'capex', N'Assessment', N'RunID', N'AS ([WorkflowRunId])'),
        (N'capex', N'Assessment', N'CapExOpExSuggestion', N'AS ([Recommendation])'),
        (N'capex', N'Assessment', N'NuvoloRiskFlag', N'bit NULL'),
        (N'capex', N'Assessment', N'MissingSiteFlag', N'bit NULL'),
        (N'capex', N'Assessment', N'LeasedSiteFlag', N'bit NULL'),
        (N'capex', N'Assessment', N'ContractVersion', N'AS ([SchemaVersion])'),
        (N'capex', N'BundleCandidateGroup', N'AFEProjectID', N'AS ([ProjectId])'),
        (N'capex', N'BundleCandidateGroup', N'TaskCode', N'AS ([SuggestedTaskCode])'),
        (N'capex', N'BundleCandidateGroup', N'GroupSeq', N'int NULL'),
        (N'capex', N'BundleCandidateGroup', N'ThresholdID', N'int NULL'),
        (N'capex', N'BundleCandidateGroup', N'CombinedValue', N'AS ([TotalAmount])'),
        (N'capex', N'BundleCandidateGroup', N'MemberCount', N'int NULL'),
        (N'capex', N'BundleCandidateGroup', N'GroupStatus', N'AS ([Disposition])'),
        (N'capex', N'BundleCandidateGroup', N'LastEvaluatedRunID', N'AS ([WorkflowRunId])'),
        (N'capex', N'BundleCandidateGroup', N'UpdatedAtUtc', N'AS ([CreatedAtUtc])'),
        (N'capex', N'BundleCandidateMember', N'InvoiceLineID', N'AS ([LineId])'),
        (N'capex', N'BundleCandidateMember', N'LineValue', N'AS ([LineAmount])'),
        (N'capex', N'BundleCandidateMember', N'AddedRunID', N'AS ([WorkflowRunId])'),
        (N'capex', N'BundleCandidateMember', N'AddedAtUtc', N'AS ([InsertedAtUtc])'),
        (N'ref', N'TaskMaster', N'TaskMasterVersion', N'int NULL'),
        (N'ref', N'TaskMaster', N'TaskDescription', N'AS ([description])'),
        (N'ref', N'TaskMaster', N'GLAccount', N'nvarchar(128) NULL'),
        (N'ref', N'TaskMaster', N'AssetType', N'nvarchar(256) NULL'),
        (N'ref', N'TaskMaster', N'IsCapitalTask', N'bit NULL'),
        (N'ref', N'TaskMaster', N'EffectiveFrom', N'date NULL'),
        (N'ref', N'TaskMaster', N'EffectiveTo', N'date NULL');

    DECLARE @Schema sysname, @Table sysname, @Column sysname, @Definition nvarchar(1000);
    DECLARE @Qualified nvarchar(517), @Sql nvarchar(max);
    DECLARE columns_to_add CURSOR LOCAL FAST_FORWARD FOR
        SELECT SchemaName, TableName, ColumnName, Definition FROM @Columns;
    OPEN columns_to_add;
    FETCH NEXT FROM columns_to_add INTO @Schema, @Table, @Column, @Definition;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @Qualified = QUOTENAME(@Schema) + N'.' + QUOTENAME(@Table);
        IF COL_LENGTH(@Qualified, @Column) IS NULL
        BEGIN
            SET @Sql = N'ALTER TABLE ' + @Qualified + N' ADD ' + QUOTENAME(@Column) + N' ' + @Definition + N';';
            EXEC sys.sp_executesql @Sql;
        END;
        FETCH NEXT FROM columns_to_add INTO @Schema, @Table, @Column, @Definition;
    END;
    CLOSE columns_to_add;
    DEALLOCATE columns_to_add;

    IF OBJECT_ID(N'task.FK_DM_TaskSuggestion_Part', N'F') IS NULL
        EXEC(N'ALTER TABLE task.TaskSuggestion WITH CHECK ADD CONSTRAINT FK_DM_TaskSuggestion_Part
            FOREIGN KEY (PartMasterID) REFERENCES ref.PartMaster (PartMasterID);');
    IF OBJECT_ID(N'capex.FK_DM_Assessment_Task', N'F') IS NULL
        EXEC(N'ALTER TABLE capex.Assessment WITH CHECK ADD CONSTRAINT FK_DM_Assessment_Task
            FOREIGN KEY (TaskSuggestionID) REFERENCES task.TaskSuggestion (taskSuggestionId);');
    IF OBJECT_ID(N'capex.FK_DM_Assessment_Threshold', N'F') IS NULL
        EXEC(N'ALTER TABLE capex.Assessment WITH CHECK ADD CONSTRAINT FK_DM_Assessment_Threshold
            FOREIGN KEY (ThresholdID) REFERENCES ref.CapitalizationThreshold (ThresholdID);');
    IF OBJECT_ID(N'capex.FK_DM_BundleGroup_Threshold', N'F') IS NULL
        EXEC(N'ALTER TABLE capex.BundleCandidateGroup WITH CHECK ADD CONSTRAINT FK_DM_BundleGroup_Threshold
            FOREIGN KEY (ThresholdID) REFERENCES ref.CapitalizationThreshold (ThresholdID);');
    IF OBJECT_ID(N'prep.FK_DM_InvoiceLine_Invoice', N'F') IS NULL
        EXEC(N'ALTER TABLE prep.InvoiceLine WITH CHECK ADD CONSTRAINT FK_DM_InvoiceLine_Invoice
            FOREIGN KEY (InvoiceID) REFERENCES prep.Invoice (InvoiceID);');

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
