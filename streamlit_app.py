import io
import json
import csv
import re
import requests
import streamlit as st
import pandas as pd

BASE_URL = "https://app.rocketsource.io"

st.set_page_config(page_title="RocketSource Minimal", layout="centered")
st.title("RocketSource • Upload CSV → Download (VA-safe)")

# API KEY: solo da secrets (non appare mai)
api_key = st.secrets.get("ROCKETSOURCE_API_KEY", "")
if not api_key:
    st.error("API key mancante. Aggiungi ROCKETSOURCE_API_KEY nei Secrets di Streamlit.")
    st.stop()

def auth_headers():
    return {"Authorization": f"Bearer {api_key}"}

uploaded = st.file_uploader("Upload CSV", type=["csv"])
if not uploaded:
    st.stop()

file_bytes = uploaded.getvalue()
st.caption(f"File: {uploaded.name} • {len(file_bytes)/1024/1024:.2f} MB")

# Delimiter selection
delimiter_choice = st.selectbox("Delimiter CSV", ["Auto", ",", ";", "\\t (tab)", "|"], index=0)

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
title_col = st.selectbox("Titolo prodotto (obbligatorio) → pass-through", cols, index=0)

use_fixed_cost = st.checkbox("Usa COST fisso = 1 (aggiunge colonna al CSV)", value=False)
if not use_fixed_cost:
    cost_col = st.selectbox("Colonna COST (obbligatoria)", cols, index=1 if len(cols) > 1 else 0)
else:
    cost_col = None
    st.info("COST verrà scritto in una nuova colonna '__fixed_cost' con valore 1.")

with st.expander("Opzionali"):
    stock_qty_col = st.selectbox("Stock Quantity (opzionale)", ["(none)"] + cols, index=0)
    supplier_image_col = st.selectbox("Supplier Image URL (opzionale)", ["(none)"] + cols, index=0)

st.subheader("Options")
scan_name = st.text_input("Nome scan (options.name) — obbligatorio", value=f"Scan - {uploaded.name}")
marketplace_id = st.text_input("Marketplace ID", value="US")

if not scan_name.strip():
    st.warning("Il nome scan (options.name) è obbligatorio.")
    st.stop()

def safe_body(resp: requests.Response):
    ct = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" in ct:
        try:
            return resp.json()
        except Exception:
            return resp.text.strip()
    return resp.text.strip()

def extract_scan_id_from_anything(body_obj, headers_dict):
    # 1) body JSON dict
    if isinstance(body_obj, dict):
        for k in ("scan_id", "scanId", "id"):
            if body_obj.get(k):
                return str(body_obj.get(k))
        if isinstance(body_obj.get("scan"), dict):
            inner = body_obj["scan"]
            for k in ("scan_id", "scanId", "id"):
                if inner.get(k):
                    return str(inner.get(k))

    # 2) header Location: spesso contiene /scans/{id}
    loc = headers_dict.get("Location") or headers_dict.get("location")
    if loc:
        m = re.search(r"/scans/(\d+)", loc)
        if m:
            return m.group(1)
        # fallback: primo gruppo numerico lungo
        m2 = re.search(r"(\d+)", loc)
        if m2:
            return m2.group(1)

    # 3) qualsiasi header che contenga un id numerico
    for hk, hv in headers_dict.items():
        if not isinstance(hv, str):
            continue
        if "scan" in hk.lower() or "id" in hk.lower() or "location" in hk.lower():
            m = re.search(r"(\d+)", hv)
            if m:
                return m.group(1)

    # 4) body string: se è solo numero
    if isinstance(body_obj, str):
        s = body_obj.strip()
        if re.fullmatch(r"\d+", s):
            return s

    return None

def create_scan():
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
        # custom_columns deve essere presente e numerico
        "custom_columns": [working_cols.index(title_col)],
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

    r = requests.post(url, headers=auth_headers(), files=files, data=data, timeout=120)

    body = safe_body(r)
    hdrs = dict(r.headers)

    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)

    scan_id = extract_scan_id_from_anything(body, hdrs)

    return scan_id, body, hdrs

def download_export(scan_id: str, export_type: str) -> bytes:
    url = f"{BASE_URL}/api/v3/scans/{scan_id}/download"
    r = requests.post(url, headers=auth_headers(), params={"type": export_type}, timeout=300)
    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)
    return r.content

# ---- UI state ----
if "scan_id" not in st.session_state:
    st.session_state["scan_id"] = None
if "upload_debug" not in st.session_state:
    st.session_state["upload_debug"] = None

if st.button("🚀 Upload & Create Scan", type="primary"):
    try:
        scan_id, body, hdrs = create_scan()
        st.session_state["scan_id"] = scan_id
        st.session_state["upload_debug"] = {"body": body, "headers": hdrs}

        if not scan_id:
            st.error("Upload OK ma non riesco a trovare lo scan_id nella risposta/headers.")
            st.info("Apri Debug e dimmi cosa c'è in 'headers' (specie Location).")
        else:
            st.success(f"Upload OK ✅  • Scan ID: {scan_id}")

        with st.expander("Debug upload (body + headers)"):
            dbg = st.session_state["upload_debug"]
            st.write("Body type:", type(dbg["body"]).__name__)
            st.json(dbg["body"]) if isinstance(dbg["body"], dict) else st.write(dbg["body"])
            st.write("Headers:")
            st.json(dbg["headers"])

    except requests.HTTPError as e:
        st.error("Errore HTTP durante upload.")
        st.code(getattr(e.response, "text", str(e)))
    except Exception as e:
        st.error(f"Errore: {e}")

scan_id = st.session_state.get("scan_id")

if scan_id:
    st.divider()
    st.subheader("Download (VA)")

    c1, c2 = st.columns(2)

    with c1:
        if st.button("⬇️ Genera CSV"):
            try:
                b = download_export(scan_id, "csv")
                st.download_button("Salva CSV", data=b, file_name=f"{scan_id}.csv", mime="text/csv")
            except requests.HTTPError as e:
                st.error("Download CSV fallito (scan non pronta o errore API).")
                st.code(getattr(e.response, "text", str(e)))

    with c2:
        if st.button("⬇️ Genera XLSX"):
            try:
                b = download_export(scan_id, "xlsx")
                st.download_button(
                    "Salva XLSX",
                    data=b,
                    file_name=f"{scan_id}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except requests.HTTPError as e:
                st.error("Download XLSX fallito (scan non pronta o errore API).")
                st.code(getattr(e.response, "text", str(e)))
