import io
import json
import csv
from datetime import datetime
import requests
import streamlit as st
import pandas as pd

BASE_URL = "https://app.rocketsource.io"

# --- App config ---
st.set_page_config(page_title="Product Scanner", layout="centered")
st.title("Product Scanner")

# --- Secrets ---
api_key = st.secrets.get("ROCKETSOURCE_API_KEY", "")
if not api_key:
    st.error("Missing API key. Add ROCKETSOURCE_API_KEY to Streamlit Secrets.")
    st.stop()

DEBUG_MODE = bool(st.secrets.get("DEBUG_MODE", False))  # hidden toggle

def auth_headers():
    return {"Authorization": f"Bearer {api_key}"}

def safe_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        return resp.text

# ---------- UI ----------
uploaded = st.file_uploader("Upload CSV", type=["csv"])
if not uploaded:
    st.stop()

file_bytes = uploaded.getvalue()
st.caption(f"File: {uploaded.name} • {len(file_bytes)/1024/1024:.2f} MB")

delimiter_choice = st.selectbox("CSV delimiter", ["Auto", ",", ";", "\\t (tab)", "|"], index=0)

def detect_delimiter(sample_text: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample_text, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except Exception:
        return ","

sample = file_bytes[:50_000].decode("utf-8", errors="ignore")
if delimiter_choice == "Auto":
    sep = detect_delimiter(sample)
else:
    sep = "\t" if delimiter_choice.startswith("\\t") else delimiter_choice

st.caption(f"Delimiter in use: `{repr(sep)}`")

# Preview (safe)
try:
    df_preview = pd.read_csv(io.BytesIO(file_bytes), sep=sep, nrows=10)
except Exception as e:
    st.error(f"Cannot read CSV with delimiter {repr(sep)}.\nError: {e}")
    st.stop()

cols = list(df_preview.columns)
st.dataframe(df_preview, use_container_width=True)

st.subheader("Column mapping")

id_col = st.selectbox("Identifier column (required)", cols, index=0)
title_col = st.selectbox("Product title column (required, pass-through)", cols, index=0)

use_fixed_cost = st.checkbox("Use fixed COST = 1 (adds a column)", value=False)
if not use_fixed_cost:
    cost_col = st.selectbox("Cost column (required)", cols, index=1 if len(cols) > 1 else 0)
else:
    cost_col = None
    st.info("Cost will be written to a new column '__fixed_cost' with value 1.")

with st.expander("Optional fields"):
    stock_qty_col = st.selectbox("Stock quantity (optional)", ["(none)"] + cols, index=0)
    supplier_image_col = st.selectbox("Supplier image URL (optional)", ["(none)"] + cols, index=0)

st.subheader("Scan options")

# IMPORTANT: RocketSource needs options.name, and your upload response is just "ok",
# so we make name UNIQUE to avoid accidentally selecting an older scan.
unique_scan_name = f"Scan {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {uploaded.name}"
scan_name = st.text_input("Scan name (required)", value=unique_scan_name)

marketplace_id = st.text_input("Marketplace", value="Italy")  # you can change default as needed

if not scan_name.strip():
    st.warning("Scan name is required.")
    st.stop()

# ---------- API calls ----------
def upload_scan():
    """
    POST /api/v3/scans
    Your server returns "ok" and no scan_id, so we just treat it as an ACK.
    """
    url = f"{BASE_URL}/api/v3/scans"

    upload_bytes = file_bytes
    upload_filename = uploaded.name

    if use_fixed_cost:
        df_full = pd.read_csv(io.BytesIO(file_bytes), sep=sep)
        df_full["__fixed_cost"] = 1
        upload_bytes = df_full.to_csv(index=False).encode("utf-8")
        upload_filename = (upload_filename[:-4] if upload_filename.lower().endswith(".csv") else upload_filename) + "_fixed_cost.csv"
        working_cols = list(df_full.columns)
    else:
        working_cols = cols

    mapping = {
        "id": working_cols.index(id_col),
        "cost": working_cols.index("__fixed_cost") if use_fixed_cost else working_cols.index(cost_col),
        "custom_columns": [working_cols.index(title_col)],  # pass-through title
    }

    if stock_qty_col != "(none)":
        mapping["stock_quantity"] = working_cols.index(stock_qty_col)
    if supplier_image_col != "(none)":
        mapping["supplier_image"] = working_cols.index(supplier_image_col)

    attributes = {
        "mapping": mapping,
        "options": {
            "marketplace_id": marketplace_id,
            "name": scan_name,
        }
    }

    files = {"file": (upload_filename, upload_bytes)}
    data = {"attributes": json.dumps(attributes)}

    r = requests.post(url, headers=auth_headers(), files=files, data=data, timeout=180)
    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)

    return r.text  # expected: "ok"

def list_completed_scans(page: int = 1):
    """
    GET /api/v3/scans
    Returns completed scans (and sometimes InProgress in your payload).
    """
    url = f"{BASE_URL}/api/v3/scans"
    r = requests.get(url, headers=auth_headers(), params={"page": page}, timeout=60)
    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)
    return safe_json(r)

def find_latest_by_name(scans_json, target_name: str):
    """
    Your response shape: { "data": [ ... ] }
    We pick:
      1) newest Success with matching name
      2) else newest InProgress with matching name
      3) else newest anything with matching name
    Returns the scan dict or None.
    """
    if not isinstance(scans_json, dict):
        return None
    scans = scans_json.get("data", [])
    if not isinstance(scans, list) or not scans:
        return None

    same = [s for s in scans if isinstance(s, dict) and s.get("name") == target_name]
    if not same:
        return None

    def created_at(s):
        return s.get("created_at", "") if isinstance(s, dict) else ""

    success = [s for s in same if s.get("status") == "Success"]
    inprog = [s for s in same if s.get("status") == "InProgress"]

    if success:
        return sorted(success, key=created_at, reverse=True)[0]
    if inprog:
        return sorted(inprog, key=created_at, reverse=True)[0]

    return sorted(same, key=created_at, reverse=True)[0]

def download_export(scan_id: str, export_type: str) -> bytes:
    """
    POST /api/v3/scans/{scan_id}/download?type=csv|xlsx
    """
    url = f"{BASE_URL}/api/v3/scans/{scan_id}/download"
    r = requests.post(url, headers=auth_headers(), params={"type": export_type}, timeout=300)
    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)
    return r.content

# ---------- State ----------
if "scan_id" not in st.session_state:
    st.session_state["scan_id"] = None
if "scan_status" not in st.session_state:
    st.session_state["scan_status"] = None
if "upload_ack" not in st.session_state:
    st.session_state["upload_ack"] = None
if "last_scans_json" not in st.session_state:
    st.session_state["last_scans_json"] = None

# ---------- Actions ----------
st.divider()

col1, col2 = st.columns(2)

with col1:
    if st.button("🚀 Upload & start scan", type="primary"):
        try:
            ack = upload_scan()
            st.session_state["upload_ack"] = ack
            st.session_state["scan_id"] = None
            st.session_state["scan_status"] = None
            st.success("Uploaded ✅ Scan started.")
        except requests.HTTPError as e:
            st.error("Upload failed.")
            st.code(getattr(e.response, "text", str(e)))
        except Exception as e:
            st.error(f"Error: {e}")

with col2:
    if st.button("🔎 Find scan (latest)"):
        try:
            scans_json = list_completed_scans(page=1)
            st.session_state["last_scans_json"] = scans_json

            scan = find_latest_by_name(scans_json, scan_name)
            if not scan:
                st.warning("Not found yet. If the scan is still running, try again in a bit.")
            else:
                st.session_state["scan_id"] = str(scan.get("id"))
                st.session_state["scan_status"] = scan.get("status")
                st.success(f"Found ✅  ID: {scan.get('id')}  • Status: {scan.get('status')}")

        except requests.HTTPError as e:
            st.error("Fetch scans failed.")
            st.code(getattr(e.response, "text", str(e)))
        except Exception as e:
            st.error(f"Error: {e}")

# Hidden debug (only if DEBUG_MODE secret true)
if DEBUG_MODE:
    with st.expander("DEBUG"):
        st.write("Upload ack:", st.session_state.get("upload_ack"))
        st.write("Scan name used:", scan_name)
        st.write("Found scan_id:", st.session_state.get("scan_id"))
        st.write("Found status:", st.session_state.get("scan_status"))
        sj = st.session_state.get("last_scans_json")
        if sj is not None:
            st.json(sj)

# ---------- Download ----------
scan_id = st.session_state.get("scan_id")
scan_status = st.session_state.get("scan_status")

if scan_id:
    st.subheader("Download results")

    if scan_status != "Success":
        st.info(f"Current status: {scan_status}. If not Success yet, click 'Find scan (latest)' again shortly.")

    d1, d2 = st.columns(2)

    with d1:
        if st.button("⬇️ Download CSV"):
            try:
                b = download_export(scan_id, "csv")
                st.download_button("Save CSV", data=b, file_name=f"{scan_id}.csv", mime="text/csv")
            except requests.HTTPError as e:
                st.error("CSV download failed.")
                st.code(getattr(e.response, "text", str(e)))

    with d2:
        if st.button("⬇️ Download XLSX"):
            try:
                b = download_export(scan_id, "xlsx")
                st.download_button(
                    "Save XLSX",
                    data=b,
                    file_name=f"{scan_id}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except requests.HTTPError as e:
                st.error("XLSX download failed.")
                st.code(getattr(e.response, "text", str(e)))
else:
    st.caption("After upload, click **Find scan (latest)**, then download will appear here.")
