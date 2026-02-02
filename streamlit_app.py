import io
import json
import csv
import requests
import streamlit as st
import pandas as pd

BASE_URL = "https://app.rocketsource.io"

st.set_page_config(page_title="RocketSource Minimal", layout="centered")
st.title("RocketSource • Upload CSV (Minimal)")

# ✅ API KEY: SOLO da secrets (non appare mai in chiaro)
api_key = st.secrets.get("ROCKETSOURCE_API_KEY", "")
if not api_key:
    st.error("API key mancante. Aggiungi ROCKETSOURCE_API_KEY nei Secrets di Streamlit.")
    st.stop()

uploaded = st.file_uploader("Upload CSV", type=["csv"])
if not uploaded:
    st.stop()

file_bytes = uploaded.getvalue()
st.caption(f"File: {uploaded.name} • {len(file_bytes)/1024/1024:.2f} MB")

# 1) ✅ Selezione delimiter
delimiter_choice = st.selectbox(
    "Delimiter CSV",
    options=["Auto", ",", ";", "\\t (tab)", "|"],
    index=0
)

def detect_delimiter(sample_text: str) -> str:
    # Prova a indovinare il separatore su un campione
    try:
        dialect = csv.Sniffer().sniff(sample_text, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except Exception:
        # fallback: virgola
        return ","

# Prepara sample per sniff (non leggere tutto)
sample = file_bytes[:50_000].decode("utf-8", errors="ignore")

if delimiter_choice == "Auto":
    sep = detect_delimiter(sample)
else:
    sep = "\t" if delimiter_choice.startswith("\\t") else delimiter_choice

st.caption(f"Delimiter usato: `{repr(sep)}`")

# Leggi SOLO header + poche righe per preview (anti-blocco)
try:
    df_preview = pd.read_csv(io.BytesIO(file_bytes), sep=sep, nrows=10)
except Exception as e:
    st.error(f"Impossibile leggere il CSV con delimiter {repr(sep)}.\nErrore: {e}")
    st.stop()

cols = list(df_preview.columns)
st.dataframe(df_preview, use_container_width=True)

id_col = st.selectbox("Colonna ID (obbligatoria)", cols, index=0)
cost_col = st.selectbox("Colonna COST (obbligatoria)", cols, index=1 if len(cols) > 1 else 0)

marketplace_id = st.text_input("Marketplace ID", value="US")

def create_scan():
    url = f"{BASE_URL}/api/v3/scans"
    headers = {"Authorization": f"Bearer {api_key}"}

    mapping = {
        "id": cols.index(id_col),     # 0-indexed
        "cost": cols.index(cost_col), # 0-indexed
    }
    attributes = {
        "mapping": mapping,
        "options": {
            "marketplace_id": marketplace_id,
            # opzionale: se RocketSource supporta opzioni per il delimiter,
            # qui potresti passarle; per ora lo usiamo solo per leggere/preview.
        },
    }

    files = {"file": (uploaded.name, file_bytes)}
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

if st.button("🚀 Upload & Create Scan", type="primary"):
    try:
        resp = create_scan()
        scan_id = extract_scan_id(resp)

        st.success("Upload OK ✅")
        st.write("Scan ID:", scan_id if scan_id else "(non trovato nella risposta)")

        # Debug senza rischio: NON stampiamo headers / api key (non presente qui)
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
