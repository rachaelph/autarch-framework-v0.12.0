SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'prep.InvoiceOutbox', N'U') IS NULL
    BEGIN
        CREATE TABLE prep.InvoiceOutbox
        (
            EventId            nvarchar(128)     NOT NULL,
            PackageId          nvarchar(128)     NOT NULL,
            EventType          nvarchar(128)     NOT NULL,
            EventVersion       varchar(16)       NOT NULL,
            CorrelationId      nvarchar(128)     NOT NULL,
            Payload            nvarchar(max)     NOT NULL,

            OutboxStatus       varchar(16)       NOT NULL
                CONSTRAINT DF_InvoiceOutbox_Status
                DEFAULT ('PENDING'),

            AttemptCount       int               NOT NULL
                CONSTRAINT DF_InvoiceOutbox_AttemptCount
                DEFAULT (0),

            AvailableAtUtc     datetimeoffset(3) NOT NULL
                CONSTRAINT DF_InvoiceOutbox_AvailableAtUtc
                DEFAULT (SYSUTCDATETIME()),

            LockedAtUtc        datetimeoffset(3) NULL,
            PublishedAtUtc     datetimeoffset(3) NULL,
            LastError          nvarchar(2000)    NULL,

            InsertedAtUtc      datetimeoffset(3) NOT NULL
                CONSTRAINT DF_InvoiceOutbox_InsertedAtUtc
                DEFAULT (SYSUTCDATETIME()),

            UpdatedAtUtc       datetimeoffset(3) NOT NULL
                CONSTRAINT DF_InvoiceOutbox_UpdatedAtUtc
                DEFAULT (SYSUTCDATETIME()),

            CONSTRAINT PK_InvoiceOutbox
                PRIMARY KEY (EventId),

            CONSTRAINT UQ_InvoiceOutbox_Package_Event
                UNIQUE
                (
                    PackageId,
                    EventType,
                    EventVersion
                ),

            CONSTRAINT FK_InvoiceOutbox_InvoicePackage
                FOREIGN KEY (PackageId)
                REFERENCES prep.InvoicePackage (PackageId)
                ON DELETE CASCADE,

            CONSTRAINT CK_InvoiceOutbox_EventType
                CHECK (LEN(EventType) >= 1),

            CONSTRAINT CK_InvoiceOutbox_EventVersion
                CHECK (LEN(EventVersion) >= 1),

            CONSTRAINT CK_InvoiceOutbox_CorrelationId
                CHECK (LEN(CorrelationId) >= 1),

            CONSTRAINT CK_InvoiceOutbox_Payload
                CHECK (ISJSON(Payload) = 1),

            CONSTRAINT CK_InvoiceOutbox_Status
                CHECK
                (
                    OutboxStatus IN
                    (
                        'PENDING',
                        'PUBLISHING',
                        'PUBLISHED',
                        'FAILED'
                    )
                ),

            CONSTRAINT CK_InvoiceOutbox_AttemptCount
                CHECK (AttemptCount >= 0),

            CONSTRAINT CK_InvoiceOutbox_AvailableAtUtc
                CHECK (DATEPART(TZOFFSET, AvailableAtUtc) = 0),

            CONSTRAINT CK_InvoiceOutbox_LockedAtUtc
                CHECK
                (
                    LockedAtUtc IS NULL
                    OR DATEPART(TZOFFSET, LockedAtUtc) = 0
                ),

            CONSTRAINT CK_InvoiceOutbox_PublishedAtUtc
                CHECK
                (
                    PublishedAtUtc IS NULL
                    OR DATEPART(TZOFFSET, PublishedAtUtc) = 0
                ),

            CONSTRAINT CK_InvoiceOutbox_PublishedState
                CHECK
                (
                    (
                        OutboxStatus = 'PUBLISHED'
                        AND PublishedAtUtc IS NOT NULL
                    )
                    OR
                    (
                        OutboxStatus <> 'PUBLISHED'
                        AND PublishedAtUtc IS NULL
                    )
                )
        );
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'prep.InvoiceOutbox')
          AND name = N'IX_InvoiceOutbox_Dispatch'
    )
    BEGIN
        CREATE INDEX IX_InvoiceOutbox_Dispatch
            ON prep.InvoiceOutbox
            (
                OutboxStatus,
                AvailableAtUtc,
                InsertedAtUtc
            )
            INCLUDE
            (
                PackageId,
                EventType,
                EventVersion,
                CorrelationId,
                AttemptCount
            );
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
