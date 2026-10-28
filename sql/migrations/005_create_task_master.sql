-- Migration 005: Create governed TaskMaster reference data and published view

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ref')
BEGIN
    EXEC sp_executesql N'CREATE SCHEMA [ref] AUTHORIZATION [dbo]'
END
GO

IF OBJECT_ID(N'[ref].[TaskMaster]', N'U') IS NULL
BEGIN
    CREATE TABLE [ref].[TaskMaster]
    (
        [taskMasterId] UNIQUEIDENTIFIER NOT NULL
            CONSTRAINT [DF_TaskMaster_Id] DEFAULT (NEWID()),
        [regionCode] CHAR(2) NOT NULL,
        [taskCode] NVARCHAR(64) NOT NULL,
        [description] NVARCHAR(512) NOT NULL,
        [active] BIT NOT NULL,
        [glCipMajorAccount] NVARCHAR(32) NULL,
        [glCipSubAccount] NVARCHAR(32) NULL,
        [glAssetMajorAccount] NVARCHAR(32) NULL,
        [glAssetSubAccount] NVARCHAR(32) NULL,
        [glAccumMajorAccount] NVARCHAR(32) NULL,
        [glAccumSubAccount] NVARCHAR(32) NULL,
        [glDeprMajorAccount] NVARCHAR(32) NULL,
        [glDeprSubAccount] NVARCHAR(32) NULL,
        [assetCategoryMajor] NVARCHAR(256) NULL,
        [assetCategoryMinor] NVARCHAR(256) NULL,
        [propertyType] NVARCHAR(64) NULL,
        [sourceFile] NVARCHAR(260) NOT NULL,
        [sourceSheet] NVARCHAR(128) NOT NULL,
        [sourceVersion] NVARCHAR(64) NOT NULL,
        [sourceSha256] CHAR(64) NOT NULL,
        [isCurrent] BIT NOT NULL
            CONSTRAINT [DF_TaskMaster_IsCurrent] DEFAULT (1),
        [importedAtUtc] DATETIME2(3) NOT NULL
            CONSTRAINT [DF_TaskMaster_ImportedAtUtc] DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT [PK_TaskMaster]
            PRIMARY KEY ([taskMasterId]),

        CONSTRAINT [CK_TaskMaster_RegionCode]
            CHECK ([regionCode] IN ('US', 'CN')),

        CONSTRAINT [CK_TaskMaster_TaskCode]
            CHECK (LEN(LTRIM(RTRIM([taskCode]))) > 0),

        CONSTRAINT [CK_TaskMaster_Description]
            CHECK (LEN(LTRIM(RTRIM([description]))) > 0),

        CONSTRAINT [CK_TaskMaster_SourceSha256]
            CHECK (
                LEN([sourceSha256]) = 64
                AND [sourceSha256] NOT LIKE '%[^0-9A-F]%'
            )
    )

    CREATE UNIQUE INDEX [UX_TaskMaster_SourceTask]
        ON [ref].[TaskMaster]
        (
            [regionCode],
            [taskCode],
            [sourceSha256]
        )

    CREATE UNIQUE INDEX [UX_TaskMaster_CurrentTask]
        ON [ref].[TaskMaster]
        (
            [regionCode],
            [taskCode]
        )
        WHERE [isCurrent] = 1

    CREATE INDEX [IX_TaskMaster_CurrentActive]
        ON [ref].[TaskMaster]
        (
            [regionCode],
            [isCurrent],
            [active]
        )
        INCLUDE
        (
            [taskCode],
            [description],
            [assetCategoryMajor],
            [assetCategoryMinor],
            [propertyType]
        )

    CREATE INDEX [IX_TaskMaster_SourceSha256]
        ON [ref].[TaskMaster]([sourceSha256])
END
GO

CREATE OR ALTER VIEW [pub].[v1_TaskMaster]
AS
    SELECT
        [regionCode],
        [taskCode],
        [description],
        [active],
        [glCipMajorAccount],
        [glCipSubAccount],
        [glAssetMajorAccount],
        [glAssetSubAccount],
        [glAccumMajorAccount],
        [glAccumSubAccount],
        [glDeprMajorAccount],
        [glDeprSubAccount],
        [assetCategoryMajor],
        [assetCategoryMinor],
        [propertyType],
        [sourceFile],
        [sourceSheet],
        [sourceVersion],
        [sourceSha256],
        [importedAtUtc]
    FROM [ref].[TaskMaster]
    WHERE
        [isCurrent] = 1
        AND [active] = 1
GO
