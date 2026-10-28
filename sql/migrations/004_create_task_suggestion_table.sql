-- Migration 004: Create task schema and TaskSuggestion table
-- This table stores task suggestions from the Foundry Invoice Task Determination Agent

-- Create schema if it doesn't exist
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'task')
BEGIN
    EXEC sp_executesql N'CREATE SCHEMA [task] AUTHORIZATION [dbo]'
END
GO

-- Create TaskSuggestion table
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[task].[TaskSuggestion]') AND type in (N'U'))
BEGIN
    CREATE TABLE [task].[TaskSuggestion]
    (
        [taskSuggestionId] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY DEFAULT (NEWID()),
        [packageId] NVARCHAR(256) NOT NULL,
        [invoiceId] NVARCHAR(256) NOT NULL,
        [runId] NVARCHAR(256) NOT NULL,
        [correlationId] NVARCHAR(256) NOT NULL,
        [lineId] NVARCHAR(256) NULL,
        [recommendation] NVARCHAR(50) NOT NULL CHECK ([recommendation] IN ('CAPEX', 'OPEX', 'REVIEW')),
        [taskCode] NVARCHAR(256) NULL,
        [assetClassCode] NVARCHAR(256) NULL,
        [confidence] DECIMAL(5, 4) NOT NULL CHECK ([confidence] BETWEEN 0.0 AND 1.0),
        [rationale] NVARCHAR(MAX) NOT NULL,
        [evidenceRefs] NVARCHAR(MAX) NULL, -- JSON array of evidence references
        [policyIds] NVARCHAR(MAX) NULL, -- JSON array of policy IDs
        [agentExecutionId] NVARCHAR(256) NULL,
        [agentSessionId] NVARCHAR(256) NULL,
        [createdAtUtc] DATETIME2(3) NOT NULL DEFAULT (GETUTCDATE()),
        [processedAtUtc] DATETIME2(3) NULL,
        [status] NVARCHAR(50) NOT NULL DEFAULT 'PENDING' CHECK ([status] IN ('PENDING', 'ACCEPTED', 'REVIEWED', 'REJECTED', 'APPLIED')),
        [notes] NVARCHAR(MAX) NULL
    )

    CREATE INDEX [IX_TaskSuggestion_PackageId] ON [task].[TaskSuggestion]([packageId])
    CREATE INDEX [IX_TaskSuggestion_InvoiceId] ON [task].[TaskSuggestion]([invoiceId])
    CREATE INDEX [IX_TaskSuggestion_CorrelationId] ON [task].[TaskSuggestion]([correlationId])
    CREATE INDEX [IX_TaskSuggestion_RunId] ON [task].[TaskSuggestion]([runId])
    CREATE INDEX [IX_TaskSuggestion_Status] ON [task].[TaskSuggestion]([status])
    CREATE INDEX [IX_TaskSuggestion_Recommendation] ON [task].[TaskSuggestion]([recommendation])
    CREATE INDEX [IX_TaskSuggestion_CreatedAtUtc] ON [task].[TaskSuggestion]([createdAtUtc])
END
GO
