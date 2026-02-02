import io, json
import requests
import streamlit as st
import pandas as pd

BASE_URL = "https://app.rocketsource.io"

st.set_page_config(page_title="RocketSource Minimal", layout="centered")
st.title("RocketSource • Upload CSV (Minimal)")

api_key = st.text_input(
    "API Key",
    value=st.secrets.get("ROCKETSOURCE_API_KEY", ""),
    type="password"
)

uploaded = st.file_uploader("Upload CSV", type=["csv"])

if not api_key or not uploaded:
    st.stop()

file_bytes = uploaded.getvalue()
st.caption(f"File: {uploaded.name} • {len(file_bytes)/1024/1024:.2f} MB")

# Leggi SOLO l'header e poche righe (evita blocchi con CSV grandi)
df = pd.read_csv(io.BytesIO(file_bytes), nrows=10)
cols = list(df.columns)

st.dataframe(df, use_container_width=True)

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
    attributes = {"mapping": mapping, "options": {"marketplace_id": marketplace_id}}

    files = {"file": (uploaded.name, file_bytes)}
    data = {"attributes": json.dumps(attributes)}

    # timeout corto: se deve bloccarsi, meglio fallire veloce
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

        # Mostra risposta grezza per debug leggero
        with st.expander("Debug (risposta API)"):
            st.json(resp)

        # Niente download via API: solo aprire RocketSource
        st.markdown("### Scarica output")
        st.write("Apri RocketSource e scarica da lì (zero blocchi nell’app).")

        # Link generico: almeno porta l’utente nell’app
        st.link_button("Apri RocketSource", "https://app.rocketsource.io")

    except requests.HTTPError as e:
        st.error("Errore HTTP durante upload.")
        if e.response is not None:
            st.code(e.response.text)
    except Exception as e:
        st.error(f"Errore: {e}")
