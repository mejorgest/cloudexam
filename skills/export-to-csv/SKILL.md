---
name: export-to-csv
description: Export data to CSV file. Use when user wants to save data as CSV, export results, or create spreadsheet files.
created: 2025-01-01T00:00:00
function_name: export_to_csv
is_async: false
---

# Export to CSV

## Description

Export data (list of dictionaries or list of lists) to a CSV file in the workspace.
Use this skill when the user wants to:
- Save data as a spreadsheet
- Export results to CSV
- Create downloadable data files

## Instructions

### Quick Start

```python
from skills.export_to_csv import export_to_csv

# Export list of dicts
data = [
    {"id": 1, "name": "Alice", "email": "alice@example.com"},
    {"id": 2, "name": "Bob", "email": "bob@example.com"}
]
filepath = export_to_csv(data, "contacts.csv")
print(f"Saved to: {filepath}")
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| data | list | Yes | List of dicts or list of lists |
| filename | str | Yes | Output filename (saved to workspace) |
| fieldnames | list | No | Column names (auto-detected if not provided) |

## Examples

### Export list of dictionaries

```python
from skills.export_to_csv import export_to_csv

leads = [
    {"id": "L001", "email": "user1@example.com", "status": "new"},
    {"id": "L002", "email": "user2@example.com", "status": "contacted"}
]
path = export_to_csv(leads, "leads.csv")
```

### Export with custom columns

```python
from skills.export_to_csv import export_to_csv

data = [{"a": 1, "b": 2, "c": 3}]
path = export_to_csv(data, "output.csv", fieldnames=["a", "c"])  # Only exports a and c
```






