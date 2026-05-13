"""
fetch_datos.py — Carga datos a Supabase desde fuentes públicas.

Fuentes:
  - Yahoo Finance (yfinance): mercados globales, commodities, crypto
  - dolarapi.com: tipos de cambio ARS
  - api.bcra.gob.ar: BADLAR, riesgo país
  - fyo.com.ar: precios granos Rosario (scraping)

Uso:
  pip install yfinance requests beautifulsoup4 python-dotenv supabase
  python fetch_datos.py
"""

import os
import sys
import json
import time
import datetime
import requests
import yfinance as yf
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # service_role key para escribir

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HOY = datetime.date.today().isoformat()

# ── helpers ───────────────────────────────────────────────────────────────────

def upsert(tabla: str, rows: list[dict], conflict: str):
    if not rows:
        return
    try:
        sb.table(tabla).upsert(rows, on_conflict=conflict).execute()
        print(f"  ✓ {tabla}: {len(rows)} filas")
    except Exception as e:
        print(f"  ✗ {tabla}: {e}")

def get_id(tabla: str, campo: str, valor: str) -> int | None:
    res = sb.table(tabla).select("id").eq(campo, valor).limit(1).execute()
    return res.data[0]["id"] if res.data else None

# ── 1. MERCADOS GLOBALES (Yahoo Finance) ──────────────────────────────────────

YAHOO_SYMBOLS = {
    "sp500":       "^GSPC",
    "dow_jones":   "^DJI",
    "nasdaq":      "^IXIC",
    "vix":         "^VIX",
    "merval":      "^MERV",
    "soja_chicago":"ZS=F",   # cents/bushel → se convierte a USD/tn
    "maiz_chicago":"ZC=F",   # cents/bushel → USD/tn
    "trigo_chicago":"ZW=F",  # cents/bushel → USD/tn
    "petroleo_wti":"CL=F",
    "oro":         "GC=F",
    "plata":       "SI=F",
    "cobre":       "HG=F",   # USD/lb
    "eur_usd":     "EURUSD=X",
    "usd_brl":     "BRL=X",
    "dxy":         "DX-Y.NYB",
    "btc_usd":     "BTC-USD",
    "eth_usd":     "ETH-USD",
}

# Conversión bushel→tonelada (granos)
BUSHEL_TO_TN = {
    "soja_chicago":  0.027216,  # 1 bushel soja = 27.216 kg  → × price_cents/100 / kg_por_tn
    "maiz_chicago":  0.025401,  # 1 bushel maíz = 25.401 kg
    "trigo_chicago": 0.027216,  # 1 bushel trigo ≈ 27.216 kg
}

def fetch_mercados_globales():
    print("→ Mercados globales (Yahoo Finance)")
    rows = []

    indicadores_ids = {}
    res = sb.table("indicadores").select("id,codigo").execute()
    for r in res.data:
        indicadores_ids[r["codigo"]] = r["id"]

    for codigo, symbol in YAHOO_SYMBOLS.items():
        ind_id = indicadores_ids.get(codigo)
        if not ind_id:
            print(f"  ! indicador '{codigo}' no encontrado en DB, omitido")
            continue
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if hist.empty:
                print(f"  ! {symbol}: sin datos")
                continue
            for fecha, row in hist.iterrows():
                valor = float(row["Close"])
                # Convertir granos de cents/bushel a USD/tn
                if codigo in BUSHEL_TO_TN:
                    valor = round(valor * BUSHEL_TO_TN[codigo] * 10, 2)
                else:
                    valor = round(valor, 4)
                fecha_str = fecha.date().isoformat()
                rows.append({
                    "indicador_id": ind_id,
                    "fecha": fecha_str,
                    "valor": valor,
                })
        except Exception as e:
            print(f"  ! {symbol}: {e}")
        time.sleep(0.3)

    upsert("precios_globales", rows, "indicador_id,fecha")

# ── 2. DÓLAR (dolarapi.com) ────────────────────────────────────────────────────

DOLAR_CODIGOS = {
    "oficial": "usd_ars_oficial",
    "mep":     "usd_ars_mep",
    "blue":    "usd_ars_blue",
    "ccl":     "usd_ars_ccl",
}

def fetch_dolar():
    print("→ Tipos de cambio (dolarapi.com)")
    try:
        r = requests.get("https://dolarapi.com/v1/dolares", timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ dolarapi: {e}")
        return

    res = sb.table("indicadores").select("id,codigo").execute()
    ind_ids = {r["codigo"]: r["id"] for r in res.data}

    rows = []
    for item in data:
        nombre = item.get("nombre", "").lower().replace(" ", "_")
        # Mapear nombre de dolarapi al código interno
        mapping = {
            "oficial":        "usd_ars_oficial",
            "bolsa":          "usd_ars_mep",
            "contado_con_liquidación": "usd_ars_ccl",
            "blue":           "usd_ars_blue",
            "mayorista":      None,
            "tarjeta":        None,
        }
        # Buscar por casa o nombre
        casa = item.get("casa", "").lower()
        codigo_ind = mapping.get(casa) or mapping.get(nombre)
        if not codigo_ind:
            continue
        ind_id = ind_ids.get(codigo_ind)
        if not ind_id:
            continue
        valor = item.get("venta") or item.get("compra")
        if valor:
            rows.append({
                "indicador_id": ind_id,
                "fecha": HOY,
                "valor": float(valor),
            })

    upsert("precios_globales", rows, "indicador_id,fecha")

# ── 3. BCRA — BADLAR y riesgo país —————————————————————————————————————————

BCRA_VARIABLES = {
    6:   "badlar_total",      # BADLAR total (bancos privados + públicos)
    7:   "badlar_privados",   # BADLAR bancos privados
    5:   "riesgo_pais",       # EMBI+ Argentina (bps)
}

def fetch_bcra():
    print("→ BCRA (BADLAR, riesgo país)")
    base = "https://api.bcra.gob.ar/estadisticas/v2.0"
    desde = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

    series_rows = []
    ind_res = sb.table("indicadores").select("id,codigo").execute()
    ind_ids = {r["codigo"]: r["id"] for r in ind_res.data}
    riesgo_id = ind_ids.get("riesgo_pais")

    glob_rows = []

    for var_id, codigo in BCRA_VARIABLES.items():
        try:
            url = f"{base}/datosvariable/{var_id}/{desde}/{HOY}"
            r = requests.get(url, timeout=15, verify=False)
            if not r.ok:
                print(f"  ! BCRA var {var_id}: HTTP {r.status_code}")
                continue
            payload = r.json()
            resultados = payload.get("results", [])
            for item in resultados:
                fecha = item.get("fecha", "")[:10]
                valor = item.get("valor")
                if valor is None:
                    continue
                if codigo.startswith("badlar"):
                    series_rows.append({"codigo": codigo, "fecha": fecha, "valor": float(valor)})
                elif codigo == "riesgo_pais" and riesgo_id:
                    glob_rows.append({"indicador_id": riesgo_id, "fecha": fecha, "valor": float(valor)})
        except Exception as e:
            print(f"  ! BCRA var {var_id}: {e}")
        time.sleep(0.5)

    upsert("series_diarias", series_rows, "codigo,fecha")
    upsert("precios_globales", glob_rows, "indicador_id,fecha")

# ── 4. MERVAL (refuerzo con BCRA si Yahoo falla) ——————————————————————————

def fetch_merval_bcra():
    """BCRA variable 16 = Índice Merval (cierre diario)."""
    print("→ Merval (BCRA variable 16)")
    ind_res = sb.table("indicadores").select("id,codigo").execute()
    ind_ids = {r["codigo"]: r["id"] for r in ind_res.data}
    merval_id = ind_ids.get("merval")
    if not merval_id:
        return

    desde = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    try:
        url = f"https://api.bcra.gob.ar/estadisticas/v2.0/datosvariable/16/{desde}/{HOY}"
        r = requests.get(url, timeout=15, verify=False)
        if not r.ok:
            return
        rows = []
        for item in r.json().get("results", []):
            rows.append({"indicador_id": merval_id, "fecha": item["fecha"][:10], "valor": float(item["valor"])})
        upsert("precios_globales", rows, "indicador_id,fecha")
    except Exception as e:
        print(f"  ! Merval BCRA: {e}")

# ── 5. PRECIOS GRANOS — BCR API GIX (oficial) ————————————————————————————

BCR_GIX_BASE = "https://api.bcr.com.ar/gix"

# ID de grano según documentación API GIX → código en nuestra DB
GRANOS_GIX = {
    1:  "trigo_rosario",
    2:  "maiz_rosario",
    3:  "sorgo_rosario",
    20: "girasol_rosario",
    21: "soja_rosario",
}

def _bcr_gix_token(api_key: str, secret: str) -> str | None:
    """Obtiene Bearer Token de la API GIX. Válido 24 horas."""
    try:
        r = requests.post(
            f"{BCR_GIX_BASE}/Login",
            json={"api_key": api_key, "secret": secret},
            timeout=15,
        )
        r.raise_for_status()
        token = r.json().get("data", {}).get("token", "")
        return token or None
    except Exception as e:
        print(f"  ! BCR GIX login: {e}")
        return None

def fetch_granos_fyo():
    """Obtiene precios de granos Rosario desde BCR API GIX.
    Si no hay credenciales configuradas, informa y omite.
    Registrarse en: https://api.bcr.com.ar/form
    Secrets requeridos: BCR_GIX_API_KEY y BCR_GIX_SECRET
    """
    print("→ Precios granos (BCR API GIX)")

    api_key = os.environ.get("BCR_GIX_API_KEY", "")
    secret  = os.environ.get("BCR_GIX_SECRET", "")
    if not api_key or not secret:
        print("  ! BCR GIX: credenciales no configuradas.")
        print("    Registrate en https://api.bcr.com.ar/form y agregá")
        print("    BCR_GIX_API_KEY y BCR_GIX_SECRET como secrets de GitHub.")
        return

    token = _bcr_gix_token(api_key, secret)
    if not token:
        print("  ! BCR GIX: no se pudo obtener token")
        return

    headers_auth = {"Authorization": token}

    cult_res = sb.table("cultivos").select("id,codigo").execute()
    cult_ids = {r["codigo"]: r["id"] for r in cult_res.data}

    rows_db = []
    for id_grano, codigo_cultivo in GRANOS_GIX.items():
        cult_id = cult_ids.get(codigo_cultivo)
        if not cult_id:
            continue
        try:
            url = (
                f"{BCR_GIX_BASE}/PreciosCamara"
                f"?idGrano={id_grano}"
                f"&fechaConcertacionDesde={HOY}"
                f"&fechaConcertacionHasta={HOY}"
            )
            r = requests.get(url, headers=headers_auth, timeout=15)
            if not r.ok:
                print(f"  ! BCR GIX grano {id_grano}: HTTP {r.status_code}")
                continue
            data = r.json().get("data", [])
            if not data:
                continue
            ultimo = sorted(data, key=lambda x: x.get("fecha_Operacion_Pizarra", ""))[-1]
            precio_ars = ultimo.get("precio_Cotizacion")
            precio_usd = ultimo.get("precio_Dolar")
            fecha      = (ultimo.get("fecha_Operacion_Pizarra") or HOY)[:10]
            if precio_ars:
                rows_db.append({
                    "cultivo_id": cult_id,
                    "fecha":      fecha,
                    "precio_ars": float(precio_ars),
                    "precio_usd": float(precio_usd) if precio_usd else None,
                    "zona":       "rosario",
                })
        except Exception as e:
            print(f"  ! BCR GIX grano {id_grano}: {e}")
        time.sleep(0.3)

    if rows_db:
        upsert("precios_agro", rows_db, "cultivo_id,fecha")
    else:
        print("  ! BCR GIX: sin datos para hoy")

# ── 6. COMBUSTIBLES (ENARSA/SE) ————————————————————————————————————————————

def fetch_combustibles():
    """
    La Secretaría de Energía publica precios de combustibles.
    API: https://datos.gob.ar/dataset/energia-precios-surtidor
    """
    print("→ Combustibles (datos.gob.ar)")
    try:
        url = (
            "https://datos.gob.ar/api/3/action/datastore_search"
            "?resource_id=80ac25de-a44a-4445-9215-0ecf39661517"
            "&limit=5000"
            "&filters={\"producto\":\"Gas Oil Grado 2\"}"
        )
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        records = r.json().get("result", {}).get("records", [])
    except Exception as e:
        print(f"  ✗ combustibles: {e}")
        return

    # Agrupar por provincia + mes
    from collections import defaultdict
    agrupado = defaultdict(list)
    hoy = datetime.date.today()
    for rec in records:
        try:
            precio = float(rec.get("precio", 0))
            provincia = rec.get("provincia", "").upper().strip()
            fecha_str = rec.get("fecha_desde", "") or rec.get("fecha_vigencia", "")
            if not fecha_str or not provincia or precio <= 0:
                continue
            fecha = datetime.date.fromisoformat(fecha_str[:10])
            key = (provincia, fecha.year, fecha.month)
            agrupado[key].append(precio)
        except Exception:
            continue

    rows = []
    for (prov, anio, mes), precios in agrupado.items():
        rows.append({
            "provincia": prov,
            "producto": "Gas Oil Grado 2",
            "anio": anio,
            "mes": mes,
            "precio_promedio": round(sum(precios) / len(precios), 2),
            "cantidad_eess": len(precios),
        })

    upsert("combustibles_provincia", rows, "provincia,producto,anio,mes")

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print(f"  AgroNúmeros — fetch_datos.py  [{HOY}]")
    print(f"{'='*55}\n")

    fetch_mercados_globales()
    fetch_dolar()
    fetch_bcra()
    fetch_merval_bcra()
    fetch_granos_fyo()
    fetch_combustibles()

    print(f"\n✅ Listo.\n")

if __name__ == "__main__":
    main()
