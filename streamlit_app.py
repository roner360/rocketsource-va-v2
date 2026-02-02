import io
import json
import csv
import requests
import streamlit as st
import pandas as pd

BASE_URL = "https://app.rocketsource.io"

st.set_page_config(page_title="RocketSource Minimal", layout="centered")
st.title("Scanner • Upload CSV (Minimal)")

# API KEY only from secrets (never shown)
api_key = st.secrets.get("ROCKETSOURCE_API_KEY", "")
if not api_key:
    st.error("API key mancante. Aggiungi ROCKETSOURCE_API_KEY nei Secrets di Streamlit.")
    st.stop()

uploaded = st.file_uploader("Upload CSV", type=["csv"])
if not uploaded:
    st.stop()

file_bytes_original = uploaded.getvalue()
st.caption(f"File: {uploaded.name} • {len(file_bytes_original)/1024/1024:.2f} MB")

# Delimiter selection
delimiter_choice = st.selectbox("Delimiter CSV", ["Auto", ",", ";", "\\t (tab)", "|"], index=0)

def detect_delimiter(sample_text: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample_text, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except Exception:
        return ","

sample = file_bytes_original[:50_000].decode("utf-8", errors="ignore")

if delimiter_choice == "Auto":
    sep = detect_delimiter(sample)
else:
    sep = "\t" if delimiter_choice.startswith("\\t") else delimiter_choice

st.caption(f"Delimiter usato: `{repr(sep)}`")

# Read preview (first 10 rows only)
try:
    df_preview = pd.read_csv(io.BytesIO(file_bytes_original), sep=sep, nrows=10)
except Exception as e:
    st.error(f"Impossibile leggere il CSV con delimiter {repr(sep)}.\nErrore: {e}")
    st.stop()

cols = list(df_preview.columns)

st.subheader("Preview")
st.dataframe(df_preview, use_container_width=True)

st.subheader("Mapping")

# Required: ID
id_col = st.selectbox("Colonna ID (obbligatoria)", cols, index=0)

# Optional: COST fixed = 1
cost_is_one = st.checkbox('Usa COST fisso = "1" (fringe cases)', value=False)

if not cost_is_one:
    cost_col = st.selectbox("Colonna COST (obbligatoria)", cols, index=1 if len(cols) > 1 else 0)
else:
    cost_col = None

# Optional mappings
stock_qty_col = st.selectbox("Stock Quantity (opzionale)", ["(none)"] + cols, index=0)
supplier_image_col = st.selectbox("Supplier Image URL (opzionale)", ["(none)"] + cols, index=0)

st.subheader("Options")

# REQUIRED: name
scan_name = st.text_input("Name (obbligatorio)", value=f"scan-{uploaded.name}")

marketplace_id = st.text_input("Marketplace ID", value="US")

if not scan_name.strip():
    st.warning("Il campo Name è obbligatorio.")
    st.stop()

def create_scan(file_bytes_to_send: bytes, filename_to_send: str, mapping_cols: list[str]):
    url = f"{BASE_URL}/api/v3/scans"
    headers = {"Authorization": f"Bearer {api_key}"}

    def idx(colname: str) -> int:
        return mapping_cols.index(colname)

    mapping = {"id": idx(id_col)}

    # cost mapping
    if cost_is_one:
        mapping["cost"] = idx("__cost")
    else:
        mapping["cost"] = idx(cost_col)

    # optional mappings
    if stock_qty_col != "(none)":
        mapping["stock_quantity"] = idx(stock_qty_col)

    if supplier_image_col != "(none)":
        mapping["supplier_image"] = idx(supplier_image_col)

    attributes = {
        "mapping": mapping,
        "options": {
            "marketplace_id": marketplace_id,
            "name": scan_name,  # REQUIRED
        },
    }

    files = {"file": (filename_to_send, file_bytes_to_send)}
    data = {"attributes": json.dumps(attributes)}

    r = requests.post(url, headers=headers, files=files, data=data, timeout=60)
    r.raise_for_status()
    return r.json() if r.content else {}

def extract_scan_id(resp: dict):
    for k in ("scan_id", "scanId", "id"):
        if resp.get(k):
            return str(resp[k])
    if isinstance(resp.get("scan"), dict):
        for k in ("scan_id", "scanId", "id"):
            if resp["scan"].get(k):
                return str(resp["scan"][k])
    return None

def normalize_to_comma_csv(original_bytes: bytes, sep_in: str, add_cost_one: bool):
    """
    Read full CSV with detected separator, optionally add __cost=1,
    then re-export as comma-separated CSV bytes.
    This avoids RocketSource delimiter ambiguity and keeps mapping consistent.
    """
    df_full = pd.read_csv(io.BytesIO(original_bytes), sep=sep_in)
    if add_cost_one:
        df_full["__cost"] = 1
    out = io.StringIO()
    df_full.to_csv(out, index=False)  # default sep=","
    return out.getvalue().encode("utf-8"), df_full.columns.tolist()

if st.button("🚀 Upload & Create Scan", type="primary"):
    try:
        # Normalize CSV to comma, and optionally inject __cost
        file_bytes_to_send, mapping_cols = normalize_to_comma_csv(
            file_bytes_original, sep, cost_is_one
        )

        filename_to_send = uploaded.name
        if not filename_to_send.lower().endswith(".csv"):
            filename_to_send += ".csv"

        resp = create_scan(file_bytes_to_send, filename_to_send, mapping_cols)
        scan_id = extract_scan_id(resp)

        st.success("Upload OK ✅")
        st.write("Scan ID:", scan_id if scan_id else "(non trovato nella risposta)")

        with st.expander("Debug (risposta API)"):
            st.json(resp)

        st.markdown("### Scarica output")
        st.write("Apri RocketSource e scarica da lì.")
        st.link_button("Apri RocketSource", "https://app.rocketsource.io")

    except requests.HTTPError as e:
        st.error("Errore HTTP durante upload.")
        if e.response is not None:
            st.code(e.response.text)
    except Exception as e:
        st.error(f"Errore: {e}")
