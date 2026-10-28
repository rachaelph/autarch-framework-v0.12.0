-- Migration 006: Align TaskSuggestion with line-level task determination

IF COL_LENGTH(N'[task].[TaskSuggestion]', N'eventId') IS NULL
    ALTER TABLE [task].[TaskSuggestion]
        ADD [eventId] NVARCHAR(256) NULL
GO

IF COL_LENGTH(N'[task].[TaskSuggestion]', N'resultStatus') IS NULL
    ALTER TABLE [task].[TaskSuggestion]
        ADD [resultStatus] NVARCHAR(50) NULL
GO

IF COL_LENGTH(N'[task].[TaskSuggestion]', N'modelDeployment') IS NULL
    ALTER TABLE [task].[TaskSuggestion]
        ADD [modelDeployment] NVARCHAR(256) NULL
GO

IF COL_LENGTH(N'[task].[TaskSuggestion]', N'promptVersion') IS NULL
    ALTER TABLE [task].[TaskSuggestion]
        ADD [promptVersion] NVARCHAR(128) NULL
GO

IF COL_LENGTH(N'[task].[TaskSuggestion]', N'taskMasterSha256') IS NULL
    ALTER TABLE [task].[TaskSuggestion]
        ADD [taskMasterSha256] CHAR(64) NULL
GO

UPDATE [task].[TaskSuggestion]
SET [resultStatus] =
    CASE
        WHEN [taskCode] IS NULL THEN 'UNRESOLVED'
        ELSE 'SUGGESTED'
    END
WHERE [resultStatus] IS NULL
GO

ALTER TABLE [task].[TaskSuggestion]
    ALTER COLUMN [resultStatus] NVARCHAR(50) NOT NULL
GO

DECLARE @recommendationConstraint SYSNAME
DECLARE @dropRecommendationConstraintSql NVARCHAR(MAX)

SELECT @recommendationConstraint = [name]
FROM sys.check_constraints
WHERE
    [parent_object_id] = OBJECT_ID(N'[task].[TaskSuggestion]')
    AND [definition] LIKE '%recommendation%'
    AND [definition] LIKE '%CAPEX%'

IF @recommendationConstraint IS NOT NULL
BEGIN
    SET @dropRecommendationConstraintSql = CONCAT
    (
        N'ALTER TABLE [task].[TaskSuggestion] DROP CONSTRAINT ',
        QUOTENAME(@recommendationConstraint)
    )

    EXEC sp_executesql @dropRecommendationConstraintSql
END
GO

ALTER TABLE [task].[TaskSuggestion]
    ALTER COLUMN [recommendation] NVARCHAR(50) NULL
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.check_constraints
    WHERE
        [parent_object_id] = OBJECT_ID(N'[task].[TaskSuggestion]')
        AND [name] = N'CK_TaskSuggestion_Recommendation'
)
BEGIN
    ALTER TABLE [task].[TaskSuggestion]
        ADD CONSTRAINT [CK_TaskSuggestion_Recommendation]
        CHECK
        (
            [recommendation] IS NULL
            OR [recommendation] IN ('CAPEX', 'OPEX', 'REVIEW')
        )
END
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.check_constraints
    WHERE
        [parent_object_id] = OBJECT_ID(N'[task].[TaskSuggestion]')
        AND [name] = N'CK_TaskSuggestion_ResultStatus'
)
BEGIN
    ALTER TABLE [task].[TaskSuggestion]
        ADD CONSTRAINT [CK_TaskSuggestion_ResultStatus]
        CHECK ([resultStatus] IN ('SUGGESTED', 'UNRESOLVED'))
END
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.check_constraints
    WHERE
        [parent_object_id] = OBJECT_ID(N'[task].[TaskSuggestion]')
        AND [name] = N'CK_TaskSuggestion_TaskResult'
)
BEGIN
    ALTER TABLE [task].[TaskSuggestion]
        ADD CONSTRAINT [CK_TaskSuggestion_TaskResult]
        CHECK
        (
            (
                [resultStatus] = 'SUGGESTED'
                AND [taskCode] IS NOT NULL
            )
            OR
            (
                [resultStatus] = 'UNRESOLVED'
                AND [taskCode] IS NULL
            )
        )
END
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.check_constraints
    WHERE
        [parent_object_id] = OBJECT_ID(N'[task].[TaskSuggestion]')
        AND [name] = N'CK_TaskSuggestion_TaskMasterSha256'
)
BEGIN
    ALTER TABLE [task].[TaskSuggestion]
        ADD CONSTRAINT [CK_TaskSuggestion_TaskMasterSha256]
        CHECK
        (
            [taskMasterSha256] IS NULL
            OR
            (
                LEN([taskMasterSha256]) = 64
                AND [taskMasterSha256] NOT LIKE '%[^0-9A-F]%'
            )
        )
END
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE
        [object_id] = OBJECT_ID(N'[task].[TaskSuggestion]')
        AND [name] = N'UX_TaskSuggestion_EventLine'
)
BEGIN
    CREATE UNIQUE INDEX [UX_TaskSuggestion_EventLine]
        ON [task].[TaskSuggestion]([eventId], [lineId])
        WHERE [eventId] IS NOT NULL
END
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE
        [object_id] = OBJECT_ID(N'[task].[TaskSuggestion]')
        AND [name] = N'IX_TaskSuggestion_TaskCode'
)
BEGIN
    CREATE INDEX [IX_TaskSuggestion_TaskCode]
        ON [task].[TaskSuggestion]([taskCode])
        WHERE [taskCode] IS NOT NULL
END
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE
        [object_id] = OBJECT_ID(N'[task].[TaskSuggestion]')
        AND [name] = N'IX_TaskSuggestion_ResultStatus'
)
BEGIN
    CREATE INDEX [IX_TaskSuggestion_ResultStatus]
        ON [task].[TaskSuggestion]([resultStatus])
END
GO