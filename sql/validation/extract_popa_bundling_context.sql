-- Run each SELECT on its indicated server. No inserts or updates.
-- OR intentionally includes known PO/project rows even when Site_Id is missing,
-- and other rows at the requested sites for related-invoice discovery.
-- Invoice-number fallbacks are candidates; verify Supplier_Name in the results.
-- Site numbers printed on invoices may need a POPA identifier crosswalk.

-- US server: adcw16q09t
SELECT
    [System_Key],
    [System_Name],
    [Type_Order],
    [Type],
    [Project_Number],
    [Project_Name],
    [Project_Type],
    [Project_Status],
    [Project_Start_Date],
    [Task_Type],
    [Task_Description],
    [Capital_Asset_Key],
    [Asset_ID],
    [Bus_Entity],
    [Site_Id],
    [Site_Bus_Entity],
    [Total_Budget],
    [Total_Actual],
    [Supplier_Name],
    [PO_Number],
    [PO_Line_Number],
    [PO_Description],
    [PO_Amount],
    [PO_Invoice_Distribution_Number],
    [Invoice_Distribution_Type],
    [PO_Invoice_Number],
    [PO_Invoice_Date],
    [PO_Invoice_Amount],
    [Invoice_Amount],
    [WorkOrderNumber],
    [Summarized_Date],
    [Last_Updated_Date]
FROM [Projects].[dbo].[PA_Projects_Summary_All_VW]
WHERE [PO_Number] IN (
        '841305', -- Right Serv: workbook PO
        '838386', -- Innovative Painting: workbook PO
        '839218', -- Red Fox: workbook PO
        '806267', -- Fluid Interiors: workbook PO; site not established
        '838621'  -- True Manufacturing: workbook PO; PDF reference 838621-1
    )
    OR [Site_Id] IN (
        '2705367', -- Right Serv: work location
        '2706686', -- Innovative Painting: job location
        '2707005', -- Red Fox: service location
        '2709986'  -- CGI: support site in invoice description
    )
    OR [Project_Number] IN ('2729038') -- True Manufacturing: workbook AFE
    OR [PO_Invoice_Number] IN ('US002223515') -- CGI: no confirmed POPA PO
ORDER BY [Project_Number], [Site_Id], [PO_Number], [PO_Line_Number];

-- Canada server: ca1rabdw3601
SELECT
    [System_Key],
    [System_Name],
    [Type_Order],
    [Type],
    [Project_Number],
    [Project_Name],
    [Project_Type],
    [Project_Status],
    [Project_Start_Date],
    [Task_Type],
    [Task_Description],
    [Capital_Asset_Key],
    [Asset_ID],
    [Bus_Entity],
    [Site_Id],
    [Site_Bus_Entity],
    [Total_Budget],
    [Total_Actual],
    [Supplier_Name],
    [PO_Number],
    [PO_Line_Number],
    [PO_Description],
    [PO_Amount],
    [PO_Invoice_Distribution_Number],
    [Invoice_Distribution_Type],
    [PO_Invoice_Number],
    [PO_Invoice_Date],
    [PO_Invoice_Amount],
    [Invoice_Amount],
    [WorkOrderNumber],
    [Summarized_Date],
    [Last_Updated_Date]
FROM [Projects].[dbo].[PA_Projects_Summary_All_VW]
WHERE [PO_Number] IN (
        '1033971',  -- Transpal: workbook POPA PO
        '27EC0008', -- Washmatics: printed PO reference; confirm POPA meaning
        '27CE0022', -- C to C: printed PO reference; workbook identifies as AFE
        '10045COU26' -- EDP: printed project/customer-order reference; confirm mapping
    )
    OR [Site_Id] IN (
        '6000601', -- Washmatics: ship-to site
        '52768',   -- C to C: store tag, also in workbook
        '1293',    -- Transpal: printed store number
        '6001293'  -- Transpal: alternate printed reference; confirm Site_Id format
    )
    OR [Project_Number] IN ('27CE0022', '27EC0008', '10045COU26')
    OR [PO_Invoice_Number] IN (
        '24182',            -- GRC: no established PO/site
        '13723',            -- EDP: no established site; workbook says not yet processed
        '1413', '001413',    -- Transpal: workbook/PDF invoice formats
        '9770', '0000009770', -- C to C: workbook/PDF invoice formats
        '9313'              -- Washmatics
    )
ORDER BY [Project_Number], [Site_Id], [PO_Number], [PO_Line_Number];
