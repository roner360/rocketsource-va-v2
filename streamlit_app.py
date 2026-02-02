import io
import json
import csv
import requests
import streamlit as st
import pandas as pd

BASE_URL = "https://app.rocketsource.io"

st.set_page_config(page_title="RocketSource Minimal", layout="centered")
st.title("RocketSource • Upload CSV (Minimal)")

# API KEY: solo da secrets (non appare mai)
api_key = st.secrets.get("ROCKETSOURCE_API_KEY", "")
if not api_key:
    st.error("API key mancante. Aggiungi ROCKETSOURCE_API_KEY nei Secrets di Streamlit.")
    st.stop()

uploaded = st.file_uploader("Upload CSV", type=["csv"])
if not uploaded:
    st.stop()

file_bytes = uploaded.getvalue()
st.caption(f"File: {uploaded.name} • {len(file_bytes)/1024/1024:.2f} MB")

# Delimiter selection
delimiter_choice = st.selectbox(
    "Delimiter CSV",
    options=["Auto", ",", ";", "\\t (tab)", "|"],
    index=0
)

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

st.caption(f"Delimiter usato: `{repr(sep)}`")

# Preview safe (solo 10 righe)
try:
    df_preview = pd.read_csv(io.BytesIO(file_bytes), sep=sep, nrows=10)
except Exception as e:
    st.error(f"Impossibile leggere il CSV con delimiter {repr(sep)}.\nErrore: {e}")
    st.stop()

cols = list(df_preview.columns)
st.dataframe(df_preview, use_container_width=True)

st.subheader("Mapping")

id_col = st.selectbox("Colonna ID (obbligatoria)", cols, index=0)

# ✅ “Titolo prodotto” (obbligatorio) -> lo passiamo come custom column
title_col = st.selectbox("Titolo prodotto (obbligatorio)", cols, index=0)

use_fixed_cost = st.checkbox('Usa COST fisso = 1 (aggiunge colonna al CSV)', value=False)
if not use_fixed_cost:
    cost_col = st.selectbox("Colonna COST (obbligatoria)", cols, index=1 if len(cols) > 1 else 0)
else:
    cost_col = None
    st.info("COST verrà scritto in una nuova colonna '__fixed_cost' con valore 1.")

with st.expander("Opzionali"):
    stock_qty_col = st.selectbox("Stock Quantity (opzionale)", ["(none)"] + cols, index=0)
    supplier_image_col = st.selectbox("Supplier Image URL (opzionale)", ["(none)"] + cols, index=0)

st.subheader("Options")

# ✅ options.name = nome scan (obbligatorio da docs)
default_scan_name = f"Scan - {uploaded.name}"
scan_name = st.text_input("Nome scan (options.name) — obbligatorio", value=default_scan_name)

marketplace_id = st.text_input("Marketplace ID", value="US")

if not scan_name.strip():
    st.warning("Il nome scan (options.name) è obbligatorio.")
    st.stop()

def safe_parse_response(resp_text: str):
    """Tenta JSON, altrimenti ritorna stringa."""
    try:
        return json.loads(resp_text)
    except Exception:
        return resp_text.strip()

def extract_scan_id(resp_obj):
    """
    Gestisce:
    - dict JSON: {id/scan_id/...}
    - stringa: potrebbe essere scan_id diretto
    """
    if isinstance(resp_obj, str):
        # se è una stringa, la trattiamo come scan_id
        return resp_obj.strip() if resp_obj.strip() else None

    if isinstance(resp_obj, dict):
        for k in ("scan_id", "scanId", "id"):
            v = resp_obj.get(k)
            if v:
                return str(v)
        inner = resp_obj.get("scan")
        if isinstance(inner, dict):
            for k in ("scan_id", "scanId", "id"):
                v = inner.get(k)
                if v:
                    return str(v)
    return None

def create_scan():
    url = f"{BASE_URL}/api/v3/scans"
    headers = {"Authorization": f"Bearer {api_key}"}

    # working file: originale o riscritto con __fixed_cost
    upload_bytes = file_bytes
    upload_filename = uploaded.name

    if use_fixed_cost:
        # qui sì: leggiamo tutto il CSV SOLO quando serve
        df_full = pd.read_csv(io.BytesIO(file_bytes), sep=sep)
        df_full["__fixed_cost"] = 1
        upload_bytes = df_full.to_csv(index=False).encode("utf-8")
        if upload_filename.lower().endswith(".csv"):
            upload_filename = upload_filename[:-4] + "_fixed_cost.csv"
        else:
            upload_filename = upload_filename + "_fixed_cost.csv"

        working_cols = list(df_full.columns)
    else:
        working_cols = cols  # bastano i nomi colonna della preview

    mapping = {
        "id": working_cols.index(id_col),
        "cost": working_cols.index("__fixed_cost") if use_fixed_cost else working_cols.index(cost_col),
        # ✅ sempre presente (da schema): custom_columns
        "custom_columns": [working_cols.index(title_col)],
    }

    # opzionali
    if stock_qty_col != "(none)":
        mapping["stock_quantity"] = working_cols.index(stock_qty_col)

    if supplier_image_col != "(none)":
        mapping["supplier_image"] = working_cols.index(supplier_image_col)

    options = {
        "marketplace_id": marketplace_id,
        "name": scan_name,  # nome scan
        # ✅ etichetta per custom columns, stesso ordine di mapping.custom_columns
        
    }

    attributes = {"mapping": mapping, "options": options}

    files = {"file": (upload_filename, upload_bytes)}
    data = {"attributes": json.dumps(attributes)}

    r = requests.post(url, headers=headers, files=files, data=data, timeout=120)

    # invece di r.json(), gestiamo anche risposte non-JSON
    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)

    content_type = (r.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type:
        try:
            return r.json()
        except Exception:
            return safe_parse_response(r.text)
    else:
        return safe_parse_response(r.text)

if st.button("🚀 Upload & Create Scan", type="primary"):
    try:
        resp = create_scan()
        scan_id = extract_scan_id(resp)

        st.success("Upload OK ✅")
        st.write("Scan ID:", scan_id if scan_id else "(non trovato)")

        with st.expander("Debug (risposta API)"):
            st.write("Tipo risposta:", type(resp).__name__)
            st.json(resp) if isinstance(resp, dict) else st.write(resp)

        st.markdown("### Scarica output")
        st.write("Apri RocketSource e scarica da lì.")
        st.link_button("Apri RocketSource", "https://app.rocketsource.io")

    except requests.HTTPError as e:
        st.error("Errore HTTP durante upload.")
        if getattr(e, "response", None) is not None:
            st.code(e.response.text)
        else:
            st.code(str(e))
    except Exception as e:
        st.error(f"Errore: {e}")
