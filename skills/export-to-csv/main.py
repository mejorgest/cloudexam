"""
Export data to CSV file in workspace

Auto-generated skill module.
"""
from typing import Any, Dict, List, Optional
import os
import sys
import csv

# Add parent path for accessing other modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Import filesystem tools for state persistence
try:
    from servers.filesystem_service.file_operations import write_file, WORKSPACE_PATH
except ImportError:
    WORKSPACE_PATH = "./workspace"
    def write_file(filename, content):
        with open(os.path.join(WORKSPACE_PATH, filename), 'w') as f:
            f.write(content)

# ============== SKILL CODE ==============

def export_to_csv(
    data: List[Dict[str, Any]], 
    filename: str, 
    fieldnames: List[str] = None
) -> str:
    """
    Export data to a CSV file in the workspace.
    
    Args:
        data: List of dictionaries to export
        filename: Output filename (relative to workspace)
        fieldnames: Optional list of column names (auto-detected if not provided)
    
    Returns:
        Path to the created file
    
    Example:
        leads = [{'id': '1', 'email': 'a@b.com'}]
        path = export_to_csv(leads, 'leads.csv')
    """
    if not data:
        write_file(filename, "")
        return f"workspace/{filename}"
    
    # Auto-detect fieldnames from first row
    if fieldnames is None:
        if isinstance(data[0], dict):
            fieldnames = list(data[0].keys())
        else:
            # List of lists - generate generic headers
            fieldnames = [f"col_{i}" for i in range(len(data[0]))]
    
    # Build CSV content
    lines = []
    lines.append(",".join(fieldnames))
    
    for row in data:
        if isinstance(row, dict):
            values = [str(row.get(field, "")) for field in fieldnames]
        else:
            values = [str(v) for v in row]
        # Escape commas and quotes
        escaped = []
        for v in values:
            if "," in v or '"' in v or "\n" in v:
                v = '"' + v.replace('"', '""') + '"'
            escaped.append(v)
        lines.append(",".join(escaped))
    
    csv_content = "\n".join(lines)
    write_file(filename, csv_content)
    
    return f"workspace/{filename}"

# ============== SKILL METADATA ==============

SKILL_NAME = "export-to-csv"
SKILL_FUNCTION = "export_to_csv"
SKILL_ASYNC = False
SKILL_DESCRIPTION = """Export data to CSV file. Use when user wants to save data as CSV, export results, or create spreadsheet files."""






