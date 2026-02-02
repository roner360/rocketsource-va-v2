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

def safe_json(resp: requests.Response):
    # prova JSON, altrimenti text
    try:
        return resp.json()
    except Exception:
        return resp.text

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

# Preview (solo 10 righe)
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

debug_mode = bool(st.secrets.get("DEBUG_MODE", False))


if not scan_name.strip():
    st.warning("Il nome scan (options.name) è obbligatorio.")
    st.stop()

# ---------- API calls ----------
def upload_scan():
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
    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)

    return r.text  # nel tuo caso: "ok"

def list_completed_scans(page: int = 1):
    # docs: GET /api/v3/scans (completed scans)
    url = f"{BASE_URL}/api/v3/scans"
    r = requests.get(url, headers=auth_headers(), params={"page": page}, timeout=60)
    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)
    return safe_json(r)

def find_latest_scan_id(scans_json, target_name: str):
    """
    Trova lo scan più recente che matcha options.name == target_name.
    Funziona anche se la risposta è lista o dict.
    """
    # Normalizza: lista di scans
    if isinstance(scans_json, list):
        scans = scans_json
    elif isinstance(scans_json, dict):
        # comune: { "scans": [...] } oppure { "data": [...] }
        if isinstance(scans_json.get("scans"), list):
            scans = scans_json["scans"]
        elif isinstance(scans_json.get("data"), list):
            scans = scans_json["data"]
        else:
            # fallback: prova a prendere il primo array trovato
            scans = None
            for v in scans_json.values():
                if isinstance(v, list):
                    scans = v
                    break
            scans = scans or []
    else:
        scans = []

    # Filtra per name
    matches = []
    for s in scans:
        if not isinstance(s, dict):
            continue
        # prova più strutture: s["options"]["name"] oppure s["name"]
        name = None
        if isinstance(s.get("options"), dict):
            name = s["options"].get("name")
        if name is None:
            name = s.get("name")
        if name == target_name:
            matches.append(s)

    # Ordina: preferisci created_at, altrimenti id numerico
    def sort_key(s):
        created = s.get("created_at") or s.get("createdAt") or ""
        sid = s.get("id") or s.get("scan_id") or s.get("scanId") or 0
        try:
            sid_num = int(sid)
        except Exception:
            sid_num = 0
        return (created, sid_num)

    matches.sort(key=sort_key, reverse=True)

    if matches:
        s = matches[0]
        sid = s.get("id") or s.get("scan_id") or s.get("scanId")
        if sid is not None:
            return str(sid)

    # se non trovi match per name, niente
    return None

def download_export(scan_id: str, export_type: str) -> bytes:
    url = f"{BASE_URL}/api/v3/scans/{scan_id}/download"
    r = requests.post(url, headers=auth_headers(), params={"type": export_type}, timeout=300)
    if r.status_code >= 400:
        raise requests.HTTPError(r.text, response=r)
    return r.content

# ---------- UI state ----------
if "scan_id" not in st.session_state:
    st.session_state["scan_id"] = None
if "last_upload_resp" not in st.session_state:
    st.session_state["last_upload_resp"] = None
if "last_scans_json" not in st.session_state:
    st.session_state["last_scans_json"] = None

# ---------- Actions ----------
if st.button("🚀 Upload & Create Scan", type="primary"):
    try:
        resp_text = upload_scan()
        st.session_state["last_upload_resp"] = resp_text
        st.session_state["scan_id"] = None  # reset finché non lo troviamo

        st.success(f"Upload OK ✅ (server reply: {resp_text})")
        st.info("Ora premi **Find my scan (latest completed)** per recuperare lo scan_id (compare solo quando è COMPLETATO).")

    except requests.HTTPError as e:
        st.error("Errore HTTP durante upload.")
        st.code(getattr(e.response, "text", str(e)))
    except Exception as e:
        st.error(f"Errore: {e}")

if st.button("🔎 Find my scan (latest completed)"):
    try:
        scans_json = list_completed_scans(page=1)
        st.session_state["last_scans_json"] = scans_json

scan_id = find_latest_scan_id(scans_json, scan_name)

if scan_id:
    # recupera anche lo status dallo stesso JSON
    status = None
    scans = scans_json.get("data", []) if isinstance(scans_json, dict) else []
    for s in scans:
        if isinstance(s, dict) and str(s.get("id")) == str(scan_id):
            status = s.get("status")
            break

    st.session_state["scan_id"] = scan_id

    if status:
        st.info(f"Scan trovata • ID: {scan_id} • Status: {status}")
        if status.lower() in ["inprogress", "in_progress"]:
            st.warning("⏳ Scan ancora in elaborazione. Attendere e riprovare.")
        elif status.lower() in ["success", "completed"]:
            st.success("✅ Scan completata. Puoi procedere con il download.")
    else:
        st.success(f"Trovata scan • ID: {scan_id}")
else:
    st.warning("Non trovata ancora. Probabile che la scan NON sia completata. Riprova tra poco.")


        if debug_mode:
            with st.expander("Debug GET /api/v3/scans JSON"):
                st.json(scans_json) if isinstance(scans_json, (dict, list)) else st.write(scans_json)

    except requests.HTTPError as e:
        st.error("Errore HTTP su GET /api/v3/scans.")
        st.code(getattr(e.response, "text", str(e)))
    except Exception as e:
        st.error(f"Errore: {e}")

scan_id = st.session_state.get("scan_id")
if scan_id:
    st.divider()
    st.subheader("Download (da Streamlit)")

    c1, c2 = st.columns(2)

    with c1:
        if st.button("⬇️ Download CSV"):
            try:
                b = download_export(scan_id, "csv")
                st.download_button("Salva CSV", data=b, file_name=f"{scan_id}.csv", mime="text/csv")
            except requests.HTTPError as e:
                st.error("Download CSV fallito.")
                st.code(getattr(e.response, "text", str(e)))

    with c2:
        if st.button("⬇️ Download XLSX"):
            try:
                b = download_export(scan_id, "xlsx")
                st.download_button(
                    "Salva XLSX",
                    data=b,
                    file_name=f"{scan_id}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except requests.HTTPError as e:
                st.error("Download XLSX fallito.")
                st.code(getattr(e.response, "text", str(e)))
