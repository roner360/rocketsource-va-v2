import io
import json
import csv
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

# “Titolo prodotto” obbligatorio -> pass-through come custom column (solo numeri)
title_col = st.selectbox("Titolo prodotto (obbligatorio)", cols, index=0)

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

# options.name = nome scan (obbligatorio)
default_scan_name = f"Scan - {uploaded.name}"
scan_name = st.text_input("Nome scan (options.name) — obbligatorio", value=default_scan_name)
marketplace_id = st.text_input("Marketplace ID", value="US")

if not scan_name.strip():
    st.warning("Il nome scan (options.name) è obbligatorio.")
    st.stop()

def safe_parse_response(resp: requests.Response):
    # alcuni endpoint potrebbero non tornare JSON
    ct = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" in ct:
        try:
            return resp.json()
        except Exception:
            return resp.text.strip()
    return resp.text.strip()

def extract_scan_id(resp_obj):
    if isinstance(resp_obj, str):
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

    upload_bytes = file_bytes
    upload_filename = uploaded.name

    # Se COST fisso => riscrivi CSV completo con colonna __fixed_cost
    if use_fixed_cost:
        df_full = pd.read_csv(io.BytesIO(file_bytes), sep=sep)
        df_full["__fixed_cost"] = 1
        upload_bytes = df_full.to_csv(index=False).encode("utf-8")

        if upload_filename.lower().endswith(".csv"):
            upload_filename = upload_filename[:-4] + "_fixed_cost.csv"
        else:
            upload_filename = upload_filename + "_fixed_cost.csv"

        working_cols = list(df_full.columns)
    else:
        working_cols = cols

    mapping = {
        "id": working_cols.index(id_col),
        "cost": working_cols.index("__fixed_cost") if use_fixed_cost else working_cols.index(cost_col),
        # custom columns (pass-through) – SOLO numeri
        "custom_columns": [working_cols.index(title_col)],
    }

    if stock_qty_col != "(none)":
        mapping["stock_quantity"] = working_cols.index(stock_qty_col)

    if supplier_image_col != "(none)":
        mapping["supplier_image"] = working_cols.index(supplier_image_col)

    options = {
        "marketplace_id": marketplace_id,
        "name": scan_name,
    }

    attributes = {"mapping": mapping, "options": options}

    files = {"file": (upload_filename, upload_bytes)}
    data = {"attributes": json.dumps(attributes)}

    r = requests.post(url, headers=auth_headers(), files=files, data=data, timeout=120)

    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)

    return safe_parse_response(r)

def check_results(scan_id: str):
    # POST /api/v3/scans/{scan_id} (usiamo per capire se ci sono risultati)
    url = f"{BASE_URL}/api/v3/scans/{scan_id}"
    payload = {"page": 0, "per_page": 1, "tableType": "products"}
    r = requests.post(
        url,
        headers={**auth_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=60
    )
    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)
    return safe_parse_response(r)

def download_export(scan_id: str, export_type: str) -> bytes:
    # POST /api/v3/scans/{scan_id}/download?type=csv|xlsx
    url = f"{BASE_URL}/api/v3/scans/{scan_id}/download"
    r = requests.post(url, headers=auth_headers(), params={"type": export_type}, timeout=300)
    if r.status_code >= 400:
        # ritorniamo errore leggibile (non blocchiamo)
        raise requests.HTTPError(r.text, response=r)
    return r.content

# ---- UI state ----
if "scan_id" not in st.session_state:
    st.session_state["scan_id"] = None
if "scan_ready" not in st.session_state:
    st.session_state["scan_ready"] = False
if "last_resp" not in st.session_state:
    st.session_state["last_resp"] = None

if st.button("🚀 Upload & Create Scan", type="primary"):
    try:
        resp = create_scan()
        scan_id = extract_scan_id(resp)

        st.session_state["scan_id"] = scan_id
        st.session_state["scan_ready"] = False
        st.session_state["last_resp"] = resp

        st.success("Upload OK ✅")
        st.write("Scan ID:", scan_id if scan_id else "(non trovato)")

        with st.expander("Debug (risposta API)"):
            st.write("Tipo risposta:", type(resp).__name__)
            st.json(resp) if isinstance(resp, dict) else st.write(resp)

    except requests.HTTPError as e:
        st.error("Errore HTTP durante upload.")
        if getattr(e, "response", None) is not None:
            st.code(e.response.text)
        else:
            st.code(str(e))
    except Exception as e:
        st.error(f"Errore: {e}")

scan_id = st.session_state.get("scan_id")

if scan_id:
    st.divider()
    st.subheader("Stato scan & Download")

    colA, colB = st.columns(2)

    with colA:
        if st.button("🔎 Check status"):
            try:
                res = check_results(scan_id)
                # euristica semplice: se la risposta contiene una lista non vuota, consideriamo pronta
                ready = False
                if isinstance(res, dict):
                    for k in ("products", "rows", "items", "data"):
                        if isinstance(res.get(k), list) and len(res[k]) > 0:
                            ready = True
                            break
                st.session_state["scan_ready"] = ready
                st.success("✅ Pronta" if ready else "⏳ Ancora in elaborazione (riprovare tra poco)")
                with st.expander("Debug (results)"):
                    st.json(res) if isinstance(res, dict) else st.write(res)
            except requests.HTTPError as e:
                st.error("Errore status.")
                st.code(getattr(e.response, "text", str(e)))
            except Exception as e:
                st.error(f"Errore: {e}")

    ready = st.session_state.get("scan_ready", False)
    st.caption("Se il download fallisce, premi prima **Check status** e riprova.")

    # Download buttons (semplici)
    d1, d2 = st.columns(2)

    with d1:
        if st.button("⬇️ Download CSV"):
            try:
                b = download_export(scan_id, "csv")
                st.download_button(
                    "Salva CSV",
                    data=b,
                    file_name=f"{scan_id}.csv",
                    mime="text/csv"
                )
            except requests.HTTPError as e:
                st.error("Download CSV fallito (probabilmente scan non pronta).")
                st.code(getattr(e.response, "text", str(e)))
            except Exception as e:
                st.error(f"Errore: {e}")

    with d2:
        if st.button("⬇️ Download XLSX"):
            try:
                b = download_export(scan_id, "xlsx")
                st.download_button(
                    "Salva XLSX",
                    data=b,
                    file_name=f"{scan_id}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except requests.HTTPError as e:
                st.error("Download XLSX fallito (probabilmente scan non pronta).")
                st.code(getattr(e.response, "text", str(e)))
            except Exception as e:
                st.error(f"Errore: {e}")
