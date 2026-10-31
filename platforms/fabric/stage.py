"""Upload the frozen canonical Parquet to the staging Lakehouse's Files area over OneLake.

OneLake speaks the ADLS Gen2 API, so this is the Fabric counterpart of `databricks fs cp` into a volume
or Snowflake `PUT` into a stage. Auth is your `az login` (DefaultAzureCredential); nothing is stored.

    python stage.py ../../data/frozen/canonical

Env: FABRIC_WORKSPACE (workspace name, default oag-reference), FABRIC_LAKEHOUSE (default oag_staging).
"""

import os
import sys
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient

workspace = os.environ.get("FABRIC_WORKSPACE", "oag-reference")
lakehouse = os.environ.get("FABRIC_LAKEHOUSE", "oag_staging")
src = Path(sys.argv[1] if len(sys.argv) > 1 else "../../data/frozen/canonical")

service = DataLakeServiceClient("https://onelake.dfs.fabric.microsoft.com", credential=DefaultAzureCredential())
fs = service.get_file_system_client(workspace)
files = sorted(src.glob("*.parquet"))
if not files:
    sys.exit(f"no Parquet files in {src}")
for f in files:
    path = f"{lakehouse}.Lakehouse/Files/canonical/{f.name}"
    fs.get_file_client(path).upload_data(f.read_bytes(), overwrite=True)
    print(f"{f.name} -> {workspace}/{path}")
