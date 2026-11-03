SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF SCHEMA_ID(N'capex') IS NULL
        EXEC(N'CREATE SCHEMA capex AUTHORIZATION dbo;');

    IF SCHEMA_ID(N'pub') IS NULL
        EXEC(N'CREATE SCHEMA pub AUTHORIZATION dbo;');

    IF OBJECT_ID(N'capex.AssessmentRun', N'U') IS NULL
    BEGIN
        CREATE TABLE capex.AssessmentRun
        (
            WorkflowRunId       nvarchar(128) NOT NULL,
            SchemaVersion       varchar(16)   NOT NULL,
            ResultSha256        char(64)      NOT NULL,
            AssessmentCount     int           NOT NULL,
            BundleGroupCount    int           NOT NULL,
            BundleMemberCount   int           NOT NULL,
            CreatedAtUtc        datetime2(3)  NOT NULL,
            InsertedAtUtc       datetime2(3)  NOT NULL
                CONSTRAINT DF_AssessmentRun_InsertedAtUtc
                DEFAULT (SYSUTCDATETIME()),

            CONSTRAINT PK_AssessmentRun
                PRIMARY KEY (WorkflowRunId),

            CONSTRAINT CK_AssessmentRun_SchemaVersion
                CHECK (SchemaVersion = '2.0'),

            CONSTRAINT CK_AssessmentRun_ResultSha256
                CHECK
                (
                    LEN(ResultSha256) = 64
                    AND ResultSha256 COLLATE Latin1_General_100_BIN2
                        NOT LIKE '%[^0-9a-f]%'
                ),

            CONSTRAINT CK_AssessmentRun_Counts
                CHECK
                (
                    AssessmentCount >= 1
                    AND BundleGroupCount >= 0
                    AND BundleMemberCount >= 0
                )
        );
    END;

    IF OBJECT_ID(N'capex.Assessment', N'U') IS NULL
    BEGIN
        CREATE TABLE capex.Assessment
        (
            AssessmentId              uniqueidentifier NOT NULL
                CONSTRAINT DF_Assessment_AssessmentId
                DEFAULT (NEWSEQUENTIALID()),
            WorkflowRunId             nvarchar(128)  NOT NULL,
            SchemaVersion             varchar(16)    NOT NULL,
            PackageId                 nvarchar(128)  NOT NULL,
            InvoiceId                 nvarchar(128)  NOT NULL,
            LineId                    nvarchar(256)  NOT NULL,
            SuggestedTaskCode         nvarchar(128)  NULL,
            NatureResult              varchar(32)    NOT NULL,
            RepairMaintenanceIndicator bit           NOT NULL,
            NeverCapitalizeHit        bit            NOT NULL,
            UnitCostMeetsThreshold    bit            NULL,
            ThresholdAmount           decimal(19, 4) NULL,
            ThresholdCurrency         char(3)        NULL,
            Recommendation            varchar(16)    NOT NULL,
            OutcomeCategory           nvarchar(128)  NOT NULL,
            BundlingDisposition       varchar(32)    NOT NULL,
            BundleGroupId             nvarchar(128)  NULL,
            MissingInputs             nvarchar(max)  NOT NULL,
            ReviewReasons             nvarchar(max)  NOT NULL,
            Rationale                 nvarchar(max)  NOT NULL,
            PolicyCitations           nvarchar(max)  NOT NULL,
            UsedLlm                   bit            NOT NULL,
            ModelVersion              nvarchar(256)  NULL,
            PolicyVersion             nvarchar(256)  NULL,
            CreatedAtUtc              datetime2(3)   NOT NULL,
            InsertedAtUtc             datetime2(3)   NOT NULL
                CONSTRAINT DF_Assessment_InsertedAtUtc
                DEFAULT (SYSUTCDATETIME()),

            CONSTRAINT PK_Assessment
                PRIMARY KEY (AssessmentId),

            CONSTRAINT UQ_Assessment_Run_Package_Line
                UNIQUE (WorkflowRunId, PackageId, LineId),

            CONSTRAINT FK_Assessment_AssessmentRun
                FOREIGN KEY (WorkflowRunId)
                REFERENCES capex.AssessmentRun (WorkflowRunId),

            CONSTRAINT CK_Assessment_SchemaVersion
                CHECK (SchemaVersion = '2.0'),

            CONSTRAINT CK_Assessment_NatureResult
                CHECK
                (
                    NatureResult IN
                    (
                        'CAPITALIZABLE',
                        'OPEX_BY_NATURE',
                        'REVIEW'
                    )
                ),

            CONSTRAINT CK_Assessment_Recommendation
                CHECK (Recommendation IN ('CAPEX', 'OPEX', 'REVIEW')),

            CONSTRAINT CK_Assessment_BundlingDisposition
                CHECK
                (
                    BundlingDisposition IN
                    (
                        'NOT_IN_SCOPE',
                        'NOT_ELIGIBLE',
                        'CANDIDATE',
                        'GROUPED',
                        'REVIEW'
                    )
                ),

            CONSTRAINT CK_Assessment_Threshold
                CHECK
                (
                    (
                        ThresholdAmount IS NULL
                        AND ThresholdCurrency IS NULL
                    )
                    OR
                    (
                        ThresholdAmount IS NOT NULL
                        AND
                        ThresholdAmount >= 0
                        AND ThresholdCurrency IS NOT NULL
                    )
                ),

            CONSTRAINT CK_Assessment_ThresholdCurrency
                CHECK
                (
                    ThresholdCurrency IS NULL
                    OR ThresholdCurrency COLLATE Latin1_General_100_BIN2
                        NOT LIKE '%[^A-Z]%'
                ),

            CONSTRAINT CK_Assessment_GroupReference
                CHECK
                (
                    (
                        BundlingDisposition = 'GROUPED'
                        AND BundleGroupId IS NOT NULL
                    )
                    OR
                    (
                        BundlingDisposition <> 'GROUPED'
                        AND BundleGroupId IS NULL
                    )
                ),

            CONSTRAINT CK_Assessment_ModelVersion
                CHECK
                (
                    UsedLlm = 0
                    OR ModelVersion IS NOT NULL
                ),

            CONSTRAINT CK_Assessment_Json
                CHECK
                (
                    ISJSON(MissingInputs) = 1
                    AND ISJSON(ReviewReasons) = 1
                    AND ISJSON(PolicyCitations) = 1
                )
        );
    END;

    IF OBJECT_ID(N'capex.BundleCandidateGroup', N'U') IS NULL
    BEGIN
        CREATE TABLE capex.BundleCandidateGroup
        (
            BundleCandidateGroupId uniqueidentifier NOT NULL
                CONSTRAINT DF_BundleCandidateGroup_Id
                DEFAULT (NEWSEQUENTIALID()),
            WorkflowRunId          nvarchar(128)  NOT NULL,
            SchemaVersion          varchar(16)    NOT NULL,
            BundleGroupId          nvarchar(128)  NOT NULL,
            SuggestedTaskCode      nvarchar(128)  NOT NULL,
            SiteId                 nvarchar(128)  NULL,
            ProjectId              nvarchar(128)  NULL,
            Currency               char(3)        NOT NULL,
            TotalAmount            decimal(19, 4) NOT NULL,
            ThresholdAmount        decimal(19, 4) NULL,
            Disposition            varchar(32)    NOT NULL,
            MissingInputs          nvarchar(max)  NOT NULL,
            Rationale              nvarchar(max)  NOT NULL,
            PolicyCitations        nvarchar(max)  NOT NULL,
            PolicyVersion          nvarchar(256)  NULL,
            CreatedAtUtc           datetime2(3)   NOT NULL,
            InsertedAtUtc          datetime2(3)   NOT NULL
                CONSTRAINT DF_BundleCandidateGroup_InsertedAtUtc
                DEFAULT (SYSUTCDATETIME()),

            CONSTRAINT PK_BundleCandidateGroup
                PRIMARY KEY (BundleCandidateGroupId),

            CONSTRAINT UQ_BundleCandidateGroup_Run_Group
                UNIQUE (WorkflowRunId, BundleGroupId),

            CONSTRAINT FK_BundleCandidateGroup_AssessmentRun
                FOREIGN KEY (WorkflowRunId)
                REFERENCES capex.AssessmentRun (WorkflowRunId),

            CONSTRAINT CK_BundleCandidateGroup_SchemaVersion
                CHECK (SchemaVersion = '2.0'),

            CONSTRAINT CK_BundleCandidateGroup_Amounts
                CHECK
                (
                    TotalAmount >= 0
                    AND
                    (
                        ThresholdAmount IS NULL
                        OR ThresholdAmount >= 0
                    )
                ),

            CONSTRAINT CK_BundleCandidateGroup_Currency
                CHECK
                (
                    Currency COLLATE Latin1_General_100_BIN2
                        NOT LIKE '%[^A-Z]%'
                ),

            CONSTRAINT CK_BundleCandidateGroup_Disposition
                CHECK
                (
                    Disposition IN ('CANDIDATE', 'GROUPED', 'REVIEW')
                ),

            CONSTRAINT CK_BundleCandidateGroup_Context
                CHECK
                (
                    Disposition = 'REVIEW'
                    OR
                    (
                        SiteId IS NOT NULL
                        AND ProjectId IS NOT NULL
                    )
                ),

            CONSTRAINT CK_BundleCandidateGroup_Json
                CHECK
                (
                    ISJSON(MissingInputs) = 1
                    AND ISJSON(PolicyCitations) = 1
                )
        );
    END;

    IF OBJECT_ID(N'capex.BundleCandidateMember', N'U') IS NULL
    BEGIN
        CREATE TABLE capex.BundleCandidateMember
        (
            WorkflowRunId nvarchar(128)  NOT NULL,
            BundleGroupId nvarchar(128)  NOT NULL,
            PackageId     nvarchar(128)  NOT NULL,
            InvoiceId     nvarchar(128)  NOT NULL,
            LineId        nvarchar(256)  NOT NULL,
            Quantity      decimal(19, 4) NOT NULL,
            UnitCost      decimal(19, 4) NOT NULL,
            LineAmount    decimal(19, 4) NOT NULL,
            InsertedAtUtc datetime2(3)   NOT NULL
                CONSTRAINT DF_BundleCandidateMember_InsertedAtUtc
                DEFAULT (SYSUTCDATETIME()),

            CONSTRAINT PK_BundleCandidateMember
                PRIMARY KEY
                (
                    WorkflowRunId,
                    BundleGroupId,
                    PackageId,
                    LineId
                ),

            CONSTRAINT UQ_BundleCandidateMember_Run_Line
                UNIQUE (WorkflowRunId, PackageId, LineId),

            CONSTRAINT FK_BundleCandidateMember_Group
                FOREIGN KEY (WorkflowRunId, BundleGroupId)
                REFERENCES capex.BundleCandidateGroup
                    (WorkflowRunId, BundleGroupId),

            CONSTRAINT FK_BundleCandidateMember_Assessment
                FOREIGN KEY (WorkflowRunId, PackageId, LineId)
                REFERENCES capex.Assessment
                    (WorkflowRunId, PackageId, LineId),

            CONSTRAINT CK_BundleCandidateMember_Amounts
                CHECK
                (
                    Quantity > 0
                    AND UnitCost >= 0
                    AND LineAmount >= 0
                )
        );
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'capex.Assessment')
          AND name = N'IX_Assessment_InvoiceId'
    )
    BEGIN
        CREATE INDEX IX_Assessment_InvoiceId
            ON capex.Assessment (InvoiceId, CreatedAtUtc);
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'capex.Assessment')
          AND name = N'IX_Assessment_Recommendation'
    )
    BEGIN
        CREATE INDEX IX_Assessment_Recommendation
            ON capex.Assessment (Recommendation, CreatedAtUtc);
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'capex.Assessment')
          AND name = N'IX_Assessment_BundleGroupId'
    )
    BEGIN
        CREATE INDEX IX_Assessment_BundleGroupId
            ON capex.Assessment (WorkflowRunId, BundleGroupId)
            WHERE BundleGroupId IS NOT NULL;
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
GO

CREATE OR ALTER VIEW pub.v1_Assessment
AS
    SELECT
        assessment.SchemaVersion AS schemaVersion,
        assessment.WorkflowRunId AS workflowRunId,
        assessment.PackageId AS packageId,
        assessment.InvoiceId AS invoiceId,
        assessment.LineId AS lineId,
        assessment.SuggestedTaskCode AS suggestedTaskCode,
        assessment.NatureResult AS natureResult,
        assessment.RepairMaintenanceIndicator
            AS repairMaintenanceIndicator,
        assessment.NeverCapitalizeHit AS neverCapitalizeHit,
        assessment.UnitCostMeetsThreshold AS unitCostMeetsThreshold,
        assessment.ThresholdAmount AS thresholdAmount,
        assessment.ThresholdCurrency AS thresholdCurrency,
        assessment.Recommendation AS recommendation,
        assessment.OutcomeCategory AS outcomeCategory,
        assessment.BundlingDisposition AS bundlingDisposition,
        assessment.BundleGroupId AS bundleGroupId,
        JSON_QUERY(assessment.MissingInputs) AS missingInputs,
        JSON_QUERY(assessment.ReviewReasons) AS reviewReasons,
        assessment.Rationale AS rationale,
        JSON_QUERY(assessment.PolicyCitations) AS policyCitations,
        assessment.UsedLlm AS usedLlm,
        assessment.ModelVersion AS modelVersion,
        assessment.PolicyVersion AS policyVersion,
        assessment.CreatedAtUtc AS createdAtUtc,
        bundle.SuggestedTaskCode AS bundleTaskCode,
        bundle.SiteId AS bundleSiteId,
        bundle.ProjectId AS bundleProjectId,
        bundle.Currency AS bundleCurrency,
        bundle.TotalAmount AS bundleTotalAmount,
        bundle.ThresholdAmount AS bundleThresholdAmount,
        bundle.Disposition AS bundleDisposition,
        (
            SELECT COUNT_BIG(*)
            FROM capex.BundleCandidateMember AS member
            WHERE
                member.WorkflowRunId = assessment.WorkflowRunId
                AND member.BundleGroupId = assessment.BundleGroupId
        ) AS bundleMemberCount
    FROM capex.Assessment AS assessment
    LEFT JOIN capex.BundleCandidateGroup AS bundle
        ON bundle.WorkflowRunId = assessment.WorkflowRunId
        AND bundle.BundleGroupId = assessment.BundleGroupId;
GO
