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

# ── 7. GANADERÍA — MAG Mercado Agroganadero (scraping) ───────────────────────

MAG_URL = "https://mercadoagroganadero.com.ar/dll/hacienda1.dll/haciinfo000502"

# Mapeo: fragmento del texto de categoría → (categoria, subcategoria, nombre_display)
MAG_CAT_MAP = [
    ("novillos esp.joven + 430",  "novillo",    "novillo_esp_joven_430",  "Novillo Esp. Joven +430"),
    ("novillos esp.joven",        "novillo",    "novillo_esp_joven",      "Novillo Esp. Joven"),
    ("novillos regular h 430",    "novillo",    "novillo_regular_h430",   "Novillo Regular h.430"),
    ("novillos regular + 430",    "novillo",    "novillo_regular_430",    "Novillo Regular +430"),
    ("novillos regular",          "novillo",    "novillo_regular",        "Novillo Regular"),
    ("novillos",                  "novillo",    "novillo",                "Novillo"),
    ("novillitos esp. h 390",     "novillito",  "novillito_esp_h390",     "Novillito Esp. h.390"),
    ("novillitos esp. + 390",     "novillito",  "novillito_esp_390",      "Novillito Esp. +390"),
    ("novillitos esp.",           "novillito",  "novillito_esp",          "Novillito Esp."),
    ("novillitos regular",        "novillito",  "novillito_regular",      "Novillito Regular"),
    ("novillitos",                "novillito",  "novillito",              "Novillito"),
    ("vaquillonas esp. h 390",    "vaquillona", "vaquillona_esp_h390",    "Vaquillona Esp. h.390"),
    ("vaquillonas esp. + 390",    "vaquillona", "vaquillona_esp_390",     "Vaquillona Esp. +390"),
    ("vaquillonas esp.",          "vaquillona", "vaquillona_esp",         "Vaquillona Esp."),
    ("vaquillonas regular",       "vaquillona", "vaquillona_regular",     "Vaquillona Regular"),
    ("vaquillonas",               "vaquillona", "vaquillona",             "Vaquillona"),
    ("vacas esp.joven h 430",     "vaca",       "vaca_esp_joven_h430",    "Vaca Esp. Joven h.430"),
    ("vacas esp.joven + 430",     "vaca",       "vaca_esp_joven_430",     "Vaca Esp. Joven +430"),
    ("vacas esp.joven",           "vaca",       "vaca_esp_joven",         "Vaca Esp. Joven"),
    ("vacas regular",             "vaca",       "vaca_regular",           "Vaca Regular"),
    ("vacas conserva buena",      "vaca",       "vaca_conserva_buena",    "Vaca Conserva Buena"),
    ("vacas conserva inferior",   "vaca",       "vaca_conserva_inf",      "Vaca Conserva Inf."),
    ("vacas conserva",            "vaca",       "vaca_conserva",          "Vaca Conserva"),
    ("vacas",                     "vaca",       "vaca",                   "Vaca"),
    ("toros",                     "toro",       "toro",                   "Toro"),
    ("mej",                       "mej",        "mej",                    "MeJ"),
]

def _mag_match_categoria(nombre: str):
    """Busca la categoría MAG que mejor coincide con el nombre dado (orden: más específico primero)."""
    n = nombre.lower().strip()
    for kw, categoria, subcategoria, display in MAG_CAT_MAP:
        if kw in n:
            return categoria, subcategoria, display
    return None, None, None

def _mag_parse_num(texto: str) -> float | None:
    """Convierte '4.356.966' o '4356966' a float. Devuelve None si no es parseable."""
    limpio = texto.replace(".", "").replace(",", "").replace("$", "").replace(" ", "").strip()
    try:
        val = float(limpio)
        return val if val > 0 else None
    except ValueError:
        return None

def fetch_liniers():
    """Obtiene precios del día del Mercado Agroganadero (MAG, Cañuelas).
    La URL haciinfo000502 sirve los precios provisorios del día actual.
    precio almacenado = ARS/kg = promedio_por_cabeza / kg_promedio
    """
    print("→ Precios hacienda MAG (mercadoagroganadero.com.ar)")
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "es-AR,es;q=0.9",
    }

    try:
        resp = requests.get(MAG_URL, headers=headers, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ! MAG fetch: {e}")
        return

    diario_rows = []
    semanal_acum = {}
    fecha = datetime.date.today()
    fecha_str = fecha.isoformat()

    for tabla in soup.find_all("table"):
        for tr in tabla.find_all("tr"):
            celdas = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if len(celdas) < 7:
                continue

            nombre_raw = celdas[0].strip()
            if not nombre_raw or "------" in nombre_raw or nombre_raw.upper() in ("CATEGORÍA", "CATEGORIA", ""):
                continue

            categoria, subcategoria, display = _mag_match_categoria(nombre_raw)
            if not categoria:
                continue

            # Columnas: Categoría | Mínimo | Máximo | Promedio | Mediana | Cabezas | Importe | Kgs | Prom.Kgs
            promedio = _mag_parse_num(celdas[3]) if len(celdas) > 3 else None
            cabezas  = _mag_parse_num(celdas[5]) if len(celdas) > 5 else None
            kg_prom  = _mag_parse_num(celdas[8]) if len(celdas) > 8 else None

            if not promedio or not kg_prom or kg_prom <= 0:
                continue

            precio_kg = round(promedio / kg_prom, 2)  # ARS/kg

            diario_rows.append({
                "fecha":              fecha_str,
                "categoria":          categoria,
                "subcategoria":       subcategoria,
                "subcategoria_nombre": display,
                "precio":             precio_kg,
                "cabezas":            int(cabezas) if cabezas else None,
                "kg_prom":            kg_prom,
            })

            # Acumular semanal ponderado por cabezas
            sem = (fecha - datetime.timedelta(days=fecha.weekday())).isoformat()
            k = (sem, categoria)
            if k not in semanal_acum:
                semanal_acum[k] = {"sum_p": 0.0, "sum_cab": 0}
            peso = int(cabezas) if cabezas else 1
            semanal_acum[k]["sum_p"]   += precio_kg * peso
            semanal_acum[k]["sum_cab"] += peso

    if not diario_rows:
        print("  ! MAG: sin datos (día sin operaciones o estructura cambió)")
        return

    upsert("liniers_diario", diario_rows, "fecha,subcategoria")

    sem_rows = []
    for (sem, categoria), v in semanal_acum.items():
        denom = v["sum_cab"] if v["sum_cab"] > 0 else 1
        sem_rows.append({
            "semana":          sem,
            "categoria":       categoria,
            "precio_promedio": round(v["sum_p"] / denom, 2),
            "cabezas_total":   v["sum_cab"] or None,
            "fuente":          "mag",
        })
    upsert("precios_ganaderia_semanal", sem_rows, "semana,categoria,fuente")

# ── 8. GANADERÍA — EXISTENCIAS BOVINAS (SENASA / datos.gob.ar) ───────────────

def fetch_existencias_bovinas():
    """Carga el censo anual de existencias bovinas desde datos.gob.ar (SENASA)."""
    print("→ Existencias bovinas (SENASA / datos.gob.ar)")

    paquetes = [
        "senasa-existencias-bovinas",
        "agroindustria-senasa-existencias-bovinas",
        "senasa-bovinos",
    ]
    recurso_id = None
    for pkg_id in paquetes:
        try:
            r = requests.get(
                f"https://datos.gob.ar/api/3/action/package_show?id={pkg_id}",
                timeout=15,
            )
            if r.ok and r.json().get("success"):
                for res in r.json()["result"].get("resources", []):
                    if res.get("format", "").upper() in ("CSV", "JSON"):
                        recurso_id = res["id"]
                        break
        except Exception:
            pass
        if recurso_id:
            break

    if not recurso_id:
        # Búsqueda dinámica
        try:
            r = requests.get(
                "https://datos.gob.ar/api/3/action/package_search"
                "?q=existencias+bovinas+provincia&fq=organization:senasa&rows=5",
                timeout=15,
            )
            if r.ok:
                for pkg in r.json().get("result", {}).get("results", []):
                    for res in pkg.get("resources", []):
                        if res.get("format", "").upper() in ("CSV", "JSON"):
                            recurso_id = res["id"]
                            break
                    if recurso_id:
                        break
        except Exception as e:
            print(f"  ! Existencias bovinas búsqueda: {e}")

    if not recurso_id:
        print("  ! Existencias bovinas: recurso no encontrado en datos.gob.ar")
        return

    try:
        url = (
            f"https://datos.gob.ar/api/3/action/datastore_search"
            f"?resource_id={recurso_id}&limit=5000"
        )
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        records = r.json().get("result", {}).get("records", [])
    except Exception as e:
        print(f"  ! Existencias bovinas fetch: {e}")
        return

    rows = []
    CAT_MAP = {
        "vacas": ["vaca", "vacas"],
        "vaquillonas": ["vaquillona", "vaquillonas"],
        "novillos": ["novillo", "novillos"],
        "novillitos": ["novillito", "novillitos"],
        "terneros": ["ternero", "terneros"],
        "terneras": ["ternera", "terneras"],
        "toros": ["toro", "toros"],
        "toritos": ["torito", "toritos"],
        "bueyes": ["buey", "bueyes"],
    }

    for rec in records:
        try:
            anio = int(rec.get("anio") or rec.get("año") or rec.get("year") or 0)
            if not anio:
                continue
            prov = (rec.get("provincia") or rec.get("province") or "").upper().strip()
            dpto = (rec.get("departamento") or rec.get("partido") or "").upper().strip()

            row = {"anio": anio, "provincia": prov, "departamento": dpto}
            for col, aliases in CAT_MAP.items():
                for alias in aliases:
                    val = rec.get(alias) or rec.get(col)
                    if val is not None:
                        try:
                            row[col] = int(float(str(val).replace(",", ".")))
                        except (ValueError, TypeError):
                            pass
                        break
            rows.append(row)
        except Exception:
            continue

    upsert("ganaderia_existencias_bovinas", rows, "anio,provincia,departamento")

# ── 9. GANADERÍA — ROSGAN (scraping rosgan.com.ar) ────────────────────────────

def fetch_rosgan():
    """Intenta obtener índices de Rosgan (Cría/Invernada) desde rosgan.com.ar."""
    print("→ Rosgan Cría/Invernada (rosgan.com.ar)")
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "es-AR,es;q=0.9",
    }

    MAPA_CATS = {
        "vaca cría":       ("cria",      "vaca_cria"),
        "vaca de cría":    ("cria",      "vaca_cria"),
        "ternero":         ("cria",      "ternero"),
        "ternera":         ("cria",      "ternera"),
        "vaquillona":      ("cria",      "vaquillona_cria"),
        "novillo":         ("invernada", "novillo"),
        "novillito":       ("invernada", "novillito"),
        "vaquillona inv":  ("invernada", "vaquillona_inv"),
    }

    try:
        resp = requests.get("https://rosgan.com.ar/indices", headers=headers, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ! Rosgan: {e}")
        return

    rows = []
    for tabla in soup.find_all("table"):
        for tr in tabla.find_all("tr"):
            celdas = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if len(celdas) < 2:
                continue
            nombre = celdas[0].lower().strip()
            for kw, (tipo, cat) in MAPA_CATS.items():
                if kw in nombre:
                    for celda in celdas[1:]:
                        limpio = celda.replace(",", ".").replace("$", "").replace("U$S", "").strip()
                        try:
                            precio = float(limpio)
                            if 0.1 < precio < 10000:
                                rows.append({
                                    "fecha": HOY,
                                    "tipo": tipo,
                                    "categoria": cat,
                                    "precio": precio,
                                })
                                break
                        except ValueError:
                            continue
                    break

    if rows:
        upsert("rosgan_precios", rows, "fecha,tipo,categoria")
    else:
        print("  ! Rosgan: sin datos (sitio puede usar JS rendering)")

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
    fetch_liniers()
    fetch_existencias_bovinas()
    fetch_rosgan()

    print(f"\n✅ Listo.\n")

if __name__ == "__main__":
    main()
