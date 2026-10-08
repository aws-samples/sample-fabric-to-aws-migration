#!/usr/bin/env bash
# End-to-end live Fabric test for fabric-assess.
# Creates an F2 capacity + workspace + warehouse, seeds schema, runs the scan,
# and DELETES the capacity on exit (even on error) so billing stops immediately.
#
# Run on YOUR Mac (needs your Entra login). Expected cost: a few minutes of F2
# (~$0.36/hr) => well under $1. Requires: az (logged in), the fabric-venv,
# ODBC Driver 18, OpenSSL on DYLD path.
set -uo pipefail

# ---- config ----
SUB="962d182e-07a5-4324-baa9-d2954df898a4"      # Azure subscription 1
RG="fabric-assess-test-rg"
LOCATION="eastus"
CAP="faassess$RANDOM"                            # capacity name (lowercase/digits)
WS_NAME="fabric-assess-test-ws"
WH_NAME="SalesWH"
REPO="$HOME/repos/sample-fabric-to-aws-migration"
VENV="$HOME/fabric-venv"
API="https://api.fabric.microsoft.com/v1"

export DYLD_LIBRARY_PATH="$(brew --prefix openssl@3)/lib:${DYLD_LIBRARY_PATH:-}"

ADMIN_UPN="$(az account show --query user.name -o tsv)"
echo "Admin UPN: $ADMIN_UPN"

# ---- cleanup trap: always delete the capacity (the billable thing) ----
CAP_CREATED=0
cleanup() {
  echo ""
  echo "=== CLEANUP: deleting Fabric capacity to stop billing ==="
  if [ "$CAP_CREATED" = "1" ]; then
    az fabric capacity delete --resource-group "$RG" --capacity-name "$CAP" --yes 2>/dev/null \
      && echo "capacity $CAP deleted" || echo "WARN: delete the capacity manually: az fabric capacity delete -g $RG -n $CAP --yes"
  fi
  # optional: drop the resource group too (also removes capacity)
  echo "If anything remains, run: az group delete -n $RG --yes --no-wait"
}
trap cleanup EXIT

set -e

echo "=== 1. register provider + create resource group + F2 capacity ==="
az provider register -n Microsoft.Fabric --wait
az group create -n "$RG" -l "$LOCATION" -o none
az extension add -n microsoft-fabric -y 2>/dev/null || true
az fabric capacity create \
  --resource-group "$RG" --capacity-name "$CAP" \
  --location "$LOCATION" --sku '{"name":"F2","tier":"Fabric"}' \
  --administration "{\"members\":[\"$ADMIN_UPN\"]}" -o none
CAP_CREATED=1
CAP_ID="$(az fabric capacity show -g "$RG" -n "$CAP" --query id -o tsv)"
echo "capacity: $CAP_ID"

echo "=== 2. get Fabric API token ==="
TOKEN="$(az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv)"
H=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

echo "=== 3. create workspace ==="
WS_ID="$(curl -s "${H[@]}" -X POST "$API/workspaces" \
  -d "{\"displayName\":\"$WS_NAME\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')"
echo "workspace: $WS_ID"

echo "=== 4. assign workspace to capacity ==="
CAP_GUID="$(az fabric capacity show -g "$RG" -n "$CAP" --query 'properties.id // name' -o tsv 2>/dev/null || echo "$CAP")"
curl -s "${H[@]}" -X POST "$API/workspaces/$WS_ID/assignToCapacity" \
  -d "{\"capacityId\":\"${CAP_ID##*/}\"}" ; echo

echo "=== 5. create warehouse ==="
curl -s "${H[@]}" -X POST "$API/workspaces/$WS_ID/warehouses" \
  -d "{\"displayName\":\"$WH_NAME\"}" ; echo
echo "waiting for warehouse provisioning (60s)..."; sleep 60

echo "=== 6. find SQL analytics endpoint ==="
WH_JSON="$(curl -s "${H[@]}" "$API/workspaces/$WS_ID/warehouses")"
echo "$WH_JSON"
ENDPOINT="$(echo "$WH_JSON" | python3 -c 'import sys,json
d=json.load(sys.stdin); w=d["value"][0]
print(w.get("properties",{}).get("connectionString",""))')"
echo "endpoint: $ENDPOINT"

echo "=== 7. seed schema (T-SQL over the endpoint) ==="
"$VENV/bin/python" - "$ENDPOINT" "$WH_NAME" <<'PY'
import sys, struct, pyodbc
from azure.identity import AzureCliCredential
server, db = sys.argv[1], sys.argv[2]
tok = AzureCliCredential().get_token("https://database.windows.net/.default").token
tb = tok.encode("utf-16-le"); ba = struct.pack("<i", len(tb)) + tb
cs = f"Driver={{ODBC Driver 18 for SQL Server}};Server={server},1433;Database={db};Encrypt=yes;TrustServerCertificate=no;"
cn = pyodbc.connect(cs, attrs_before={1256: ba}); cn.autocommit = True
cur = cn.cursor()
for stmt in [
  "CREATE TABLE dbo.customers (id INT NOT NULL, name VARCHAR(100), created_at DATETIME2)",
  "CREATE TABLE dbo.orders (id BIGINT NOT NULL, customer_id INT, amount DECIMAL(19,4), updated_at DATETIME2)",
  "CREATE VIEW dbo.v_sales AS SELECT TOP 100 c.name, STRING_AGG(CAST(o.id AS VARCHAR), ',') ids FROM dbo.customers c CROSS APPLY (SELECT * FROM dbo.orders o WHERE o.customer_id=c.id) o GROUP BY c.name",
]:
    try: cur.execute(stmt); print("ok:", stmt[:40])
    except Exception as e: print("skip:", stmt[:40], "->", e)
print("seed done")
PY

echo "=== 8. RUN fabric-assess against live Fabric ==="
"$VENV/bin/fabric-assess" assess --server "$ENDPOINT" --warehouse "$WH_NAME" --out "$REPO/reports-fabric" --format both
echo ""
echo "=== REPORT written to $REPO/reports-fabric ==="
echo "=== done; cleanup trap will delete the capacity now ==="
