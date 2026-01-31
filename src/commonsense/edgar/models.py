"""Optional schemas for Parquet output (filing metadata, statement types)."""

# Filing metadata columns written to Parquet (for documentation).
FILING_META_COLUMNS = [
    "ticker",
    "cik",
    "company",
    "form",
    "filing_date",
    "accession_no",
    "period_of_report",
]

# Financial statement table names we write.
INCOME_STATEMENT_TABLE = "income_statement"
BALANCE_SHEET_TABLE = "balance_sheet"
CASH_FLOW_TABLE = "cash_flow"
