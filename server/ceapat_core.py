"""
Núcleo del Asistente CEAPAT: descarga del catálogo, construcción de la base
SQLite y funciones de consulta. Solo usa la biblioteca estándar de Python (3.9+).

Fuente: Catálogo de Productos de Apoyo del Ceapat-Imserso
https://catalogoceapat.imserso.es
"""

import gzip
import html
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = "https://catalogoceapat.imserso.es"
FOTOS_URL = BASE + "/resources/img/productos/"
CATEGORIAS_RAIZ = [10, 90, 240, 430, 500, 620, 780, 820, 930]
PAUSA = 0.5            # segundos entre peticiones a CEAPAT
DIAS_CADUCIDAD = 7     # actualización semanal
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 asistente-ceapat")

PAQUETE = Path(__file__).resolve().parent.parent
DB_INCLUIDA = PAQUETE / "data" / "ceapat.db"
DIR_DATOS = Path(os.environ.get("CEAPAT_DATA_DIR", Path.home() / ".ceapat-asistente"))
DB = DIR_DATOS / "ceapat.db"
LOG = DIR_DATOS / "actualizacion.log"

_lock = threading.Lock()
estado_actualizacion = {"en_curso": False, "ultimo_resultado": None}


def log(msg):
    DIR_DATOS.mkdir(parents=True, exist_ok=True)
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


# ------------------------------------------------------------------ utilidades
def sin_tildes(t):
    t = unicodedata.normalize("NFD", t or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


def limpiar(txt):
    if not txt:
        return ""
    txt = html.unescape(str(txt)).replace("\r\n", "\n").replace("\r", "\n")
    txt = re.sub(r"[ \t]+", " ", txt)
    return re.sub(r"\n\s*\n+", "\n", txt).strip()


def iso_formato(cod):
    cod = str(cod)
    return ".".join(cod[i:i + 2] for i in range(0, len(cod), 2))


def legible(nombre):
    """'RANGO_ALTURA_ASIENTO' -> 'Rango altura asiento'"""
    n = re.sub(r"_\d+$", "", nombre or "").replace("_", " ").strip().lower()
    return n[:1].upper() + n[1:]


def http_get(url, intentos=4):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=180) as r:
                datos = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    datos = gzip.decompress(datos)
                return datos
        except Exception as e:  # noqa: BLE001
            log(f"reintento {i + 1} {url}: {e}")
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"No se pudo descargar {url}")


# ------------------------------------------------------------------- descarga
def parse_empresa(texto_html, id_ins):
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", texto_html, flags=re.S)
    m = re.search(r"Ficha de Empresa.*?(Nombre:.*?)c/ Los Extreme(?:ñ|&ntilde;)os", body, flags=re.S)
    bloque = m.group(1) if m else ""
    lineas = [html.unescape(l).strip() for l in re.sub(r"<[^>]+>", "\n", bloque).split("\n")]
    etiquetas = {"Nombre:": "nombre", "Página Web:": "web", "Correo:": "email",
                 "Dirección:": "direccion", "Teléfono:": "telefono", "Fax:": "fax"}
    emp = {k: "" for k in ("nombre", "web", "email", "direccion", "telefono", "fax")}
    actual = None
    for l in (x for x in lineas if x):
        if l in etiquetas:
            actual = etiquetas[l]
        elif actual:
            emp[actual] = f"{emp[actual]} {l}".strip()
    nombre, _, acr = emp["nombre"].partition("Acrónimo:")
    emp.update(id=id_ins, nombre=nombre.strip(), acronimo=acr.strip(),
               url_ficha=f"{BASE}/empresas/empresa/{id_ins}")
    return emp


def caracteristicas(p):
    out = []

    def add(nombre, valor, unidad="", tipo=""):
        out.append({"nombre": legible(nombre), "valor": valor, "unidad": unidad or "", "tipo": tipo})

    for v in (p.get("valoresFlo") or []) + (p.get("valoresNum") or []):
        val = v["valor"]
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        add(v["id"]["nombre"], val, v.get("unidad"), "numero")
    for v in p.get("valoresBoo") or []:
        add(v["id"]["nombre"], "Sí" if v["valor"] == "S" else "No", "", "si_no")
    for v in p.get("valoresCad") or []:
        add(v["id"]["nombre"], limpiar(v["valor"]), "", "texto")
    for v in p.get("valoresRag") or []:
        a, b = v["valor1"], v["valor2"]
        a = int(a) if isinstance(a, float) and a.is_integer() else a
        b = int(b) if isinstance(b, float) and b.is_integer() else b
        add(v["id"]["nombre"], f"{a}–{b}", v.get("unidad"), "rango")
    for v in p.get("valoresRef") or []:
        add("Referencia", limpiar(v["valor"]), "", "referencia")
    return out


def descargar(empresas_previas=None, progreso=None):
    """Descarga el catálogo completo. Reutiliza las empresas ya conocidas."""
    productos = {}
    for cid in CATEGORIAS_RAIZ:
        url = f"{BASE}/servicios/unacategoria/{cid}?page=0&size=5&all=true"
        data = json.loads(http_get(url))
        for p in data["content"]:
            productos[p["id_prod"]] = p
        if progreso:
            progreso(f"categoría {cid}: {data['totalElements']} productos")
        time.sleep(PAUSA)
    if len(productos) < 500:
        raise RuntimeError(f"Respuesta sospechosa: solo {len(productos)} productos")

    empresas = dict(empresas_previas or {})
    ids = {pi["id_ins"] for p in productos.values() for pi in (p.get("productoInstitucion") or [])}
    nuevas = sorted(ids - set(empresas))
    for n, i in enumerate(nuevas, 1):
        try:
            empresas[i] = parse_empresa(http_get(f"{BASE}/empresas/empresa/{i}").decode("utf-8", "replace"), i)
        except RuntimeError:
            empresas[i] = {"id": i, "nombre": "", "acronimo": "", "web": "", "email": "", "direccion": "",
                           "telefono": "", "fax": "", "url_ficha": f"{BASE}/empresas/empresa/{i}"}
        if progreso and n % 50 == 0:
            progreso(f"empresas nuevas {n}/{len(nuevas)}")
        time.sleep(PAUSA)
    return productos, {i: empresas[i] for i in ids}


# ------------------------------------------------------------- base de datos
ESQUEMA = """
CREATE TABLE meta (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE productos (
  id INTEGER PRIMARY KEY, nombre TEXT, modelo TEXT, descripcion TEXT, observaciones TEXT,
  gratuito INTEGER, fecha_alta TEXT, fecha_modificacion TEXT, categorias TEXT,
  categorias_principales TEXT, iso TEXT, empresas TEXT, n_fotos INTEGER, url TEXT,
  busqueda TEXT, ficha TEXT);
CREATE VIRTUAL TABLE productos_fts USING fts5(
  nombre, modelo, categorias, iso, descripcion, empresas, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE categorias (id INTEGER PRIMARY KEY, padre INTEGER, nombre TEXT, ruta TEXT, nivel INTEGER, n_productos INTEGER);
CREATE TABLE producto_categoria (id_prod INTEGER, id_cat INTEGER);
CREATE TABLE iso (codigo TEXT PRIMARY KEY, nombre TEXT, n_productos INTEGER);
CREATE TABLE producto_iso (id_prod INTEGER, codigo TEXT);
CREATE TABLE empresas (id INTEGER PRIMARY KEY, nombre TEXT, acronimo TEXT, web TEXT, email TEXT,
  direccion TEXT, telefono TEXT, fax TEXT, url_ficha TEXT, n_productos INTEGER);
CREATE TABLE producto_empresa (id_prod INTEGER, id_emp INTEGER, roles TEXT, venta_internet INTEGER, venta_tienda INTEGER);
CREATE INDEX i_pc ON producto_categoria(id_cat);
CREATE INDEX i_pi ON producto_iso(codigo);
CREATE INDEX i_pe ON producto_empresa(id_emp);
"""


def construir_db(productos, empresas, destino):
    nodos = {}
    for p in productos.values():
        for c in p["categorias"]:
            nodos[c["id_jer"]] = (c["id_jer_padre"], c["jer"])

    def ruta(i):
        partes = []
        while i in nodos:
            partes.append(nodos[i][1])
            i = nodos[i][0]
        return list(reversed(partes))

    if destino.exists():
        destino.unlink()
    con = sqlite3.connect(destino)
    con.executescript(ESQUEMA)
    n_cat, n_iso, n_emp, iso_nombres = {}, {}, {}, {}
    for pid, p in sorted(productos.items()):
        ids_cat = {c["id_jer"] for c in p["categorias"]}
        padres = {c["id_jer_padre"] for c in p["categorias"]}
        rutas = sorted(" > ".join(ruta(c)) for c in ids_cat if c not in padres)
        principales = sorted({r.split(" > ")[0] for r in rutas})
        isos = [{"codigo": iso_formato(i["id_n1n2n3"]), "nombre": limpiar(i["n1n2n3"])}
                for i in p.get("iso_n1n2n3") or []]
        emps = []
        for pi in p.get("productoInstitucion") or []:
            e = empresas.get(pi["id_ins"], {})
            roles = [r for r, k in (("fabricante", "esFabricante"), ("distribuidor", "esDistribuidor"),
                                    ("diseñador", "esDisenador")) if pi.get(k) == "SI"]
            emps.append({"id": pi["id_ins"], "nombre": e.get("nombre", ""), "web": e.get("web", ""),
                         "email": e.get("email", ""), "telefono": e.get("telefono", ""),
                         "roles": roles, "venta_internet": pi.get("ventaInternet") == "SI",
                         "venta_tienda": pi.get("ventaTienda") == "SI", "ficha_ceapat": e.get("url_ficha", "")})
        fotos = sorted(p.get("productoFotos") or [], key=lambda f: f["id"]["orden"])
        ficha = {
            "id": pid, "url": f"{BASE}/productos/producto/{pid}", "nombre": limpiar(p["nombre"]),
            "modelo": limpiar(p["modelo"]), "descripcion": limpiar(p["descripcion"]),
            "observaciones": limpiar(p.get("observaciones")), "gratuito": p.get("gratuito") == "SI",
            "fecha_alta": (p.get("fecha_alta") or "")[:10],
            "fecha_modificacion": (p.get("fecha_modif") or "")[:10],
            "categorias": rutas, "iso": isos,
            "caracteristicas": caracteristicas(p), "empresas": emps,
            "fotos": [{"url": FOTOS_URL + f["nombreArchivo"], "descripcion": limpiar(f.get("descripcionFoto"))}
                      for f in fotos],
            "videos_enlaces": [e["enlace"] for e in p.get("enlaces") or []],
            "n_documentos": len(p.get("productoDocumento") or []),
        }
        iso_txt = " | ".join(f"{i['codigo']} {i['nombre']}" for i in isos)
        emp_txt = " | ".join(e["nombre"] for e in emps)
        cat_txt = " | ".join(rutas)
        busq = sin_tildes(" ".join([ficha["nombre"], ficha["modelo"], cat_txt, iso_txt, emp_txt, ficha["descripcion"]]))
        con.execute("INSERT INTO productos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            pid, ficha["nombre"], ficha["modelo"], ficha["descripcion"], ficha["observaciones"],
            int(ficha["gratuito"]), ficha["fecha_alta"], ficha["fecha_modificacion"], cat_txt,
            " | ".join(principales), iso_txt, emp_txt, len(fotos), ficha["url"], busq,
            json.dumps(ficha, ensure_ascii=False)))
        con.execute("INSERT INTO productos_fts(rowid,nombre,modelo,categorias,iso,descripcion,empresas) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (pid, ficha["nombre"], ficha["modelo"], cat_txt, iso_txt, ficha["descripcion"], emp_txt))
        for c in ids_cat:
            con.execute("INSERT INTO producto_categoria VALUES (?,?)", (pid, c))
            n_cat[c] = n_cat.get(c, 0) + 1
        for i in isos:
            con.execute("INSERT INTO producto_iso VALUES (?,?)", (pid, i["codigo"]))
            n_iso[i["codigo"]] = n_iso.get(i["codigo"], 0) + 1
            iso_nombres[i["codigo"]] = i["nombre"]
        for e in emps:
            con.execute("INSERT INTO producto_empresa VALUES (?,?,?,?,?)",
                        (pid, e["id"], ", ".join(e["roles"]), int(e["venta_internet"]), int(e["venta_tienda"])))
            n_emp[e["id"]] = n_emp.get(e["id"], 0) + 1
    for i, (padre, nombre) in nodos.items():
        con.execute("INSERT INTO categorias VALUES (?,?,?,?,?,?)",
                    (i, padre, nombre, " > ".join(ruta(i)), len(ruta(i)), n_cat.get(i, 0)))
    for cod, nom in iso_nombres.items():
        con.execute("INSERT INTO iso VALUES (?,?,?)", (cod, nom, n_iso[cod]))
    for i, e in empresas.items():
        con.execute("INSERT INTO empresas VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (i, e["nombre"], e.get("acronimo", ""), e["web"], e["email"], e["direccion"],
                     e["telefono"], e.get("fax", ""), e["url_ficha"], n_emp.get(i, 0)))
    for k, v in {"fecha_descarga": datetime.now().isoformat(timespec="seconds"),
                 "total_productos": len(productos), "total_empresas": len(empresas),
                 "fuente": BASE}.items():
        con.execute("INSERT INTO meta VALUES (?,?)", (k, str(v)))
    con.commit()
    con.execute("VACUUM")
    con.close()


def empresas_de_db(ruta):
    if not ruta.exists():
        return {}
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    try:
        return {r["id"]: dict(r) for r in con.execute("SELECT * FROM empresas WHERE nombre != ''")}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def actualizar(destino=None, progreso=None):
    """Descarga todo y sustituye la base de forma atómica. Devuelve un resumen."""
    destino = Path(destino or DB)
    destino.parent.mkdir(parents=True, exist_ok=True)
    progreso = progreso or log
    with _lock:
        estado_actualizacion["en_curso"] = True
        try:
            antes = set()
            if destino.exists():
                c = sqlite3.connect(destino)
                antes = {r[0] for r in c.execute("SELECT id FROM productos")}
                c.close()
            productos, empresas = descargar(empresas_de_db(destino), progreso)
            tmp = destino.with_suffix(".tmp")
            construir_db(productos, empresas, tmp)
            os.replace(tmp, destino)
            ahora = set(productos)
            res = {"ok": True, "total_productos": len(ahora), "total_empresas": len(empresas),
                   "altas": len(ahora - antes) if antes else None,
                   "bajas": len(antes - ahora) if antes else None,
                   "fecha": datetime.now().isoformat(timespec="seconds")}
            progreso(f"actualización completada: {res}")
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": str(e), "fecha": datetime.now().isoformat(timespec="seconds")}
            progreso(f"ERROR en la actualización: {e}")
        finally:
            estado_actualizacion["en_curso"] = False
        estado_actualizacion["ultimo_resultado"] = res
        return res


def asegurar_db():
    """Copia la base incluida en el paquete si el usuario aún no tiene una."""
    if not DB.exists() and DB_INCLUIDA.exists():
        DIR_DATOS.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DB_INCLUIDA, DB)
    if not DB.exists():
        return False
    # Si la del paquete es más reciente (p. ej. tras actualizar el plugin), usarla
    if DB_INCLUIDA.exists() and fecha_descarga(DB_INCLUIDA) > fecha_descarga(DB):
        shutil.copy2(DB_INCLUIDA, DB)
    return True


def fecha_descarga(ruta):
    try:
        c = sqlite3.connect(ruta)
        v = c.execute("SELECT valor FROM meta WHERE clave='fecha_descarga'").fetchone()
        c.close()
        return datetime.fromisoformat(v[0]) if v else datetime.min
    except (sqlite3.Error, ValueError):
        return datetime.min


def caducada():
    return datetime.now() - fecha_descarga(DB) > timedelta(days=DIAS_CADUCIDAD)


def actualizar_en_segundo_plano():
    if estado_actualizacion["en_curso"]:
        return False
    threading.Thread(target=actualizar, daemon=True).start()
    return True


# ------------------------------------------------------------------ consultas
VACIAS = set("""a al algo algun alguna alguno algunos con contra de del desde el en entre es esta este
esto hay la las le les lo los me mi mis muy necesito necesita para pero por porque que quiero
quisiera se ser si sin sobre su sus te tiene tengo un una unas uno unos y o u ya cosa cosas
producto productos apoyo ayuda ayudas tipo algo poder puede pueda""".split())


def _terminos(texto):
    palabras = re.findall(r"[0-9a-zñ]+", sin_tildes(texto))
    out = []
    for w in palabras:
        if w in VACIAS or len(w) < 2:
            continue
        if re.fullmatch(r"\d+", w):
            out.append(w)
            continue
        raiz = w
        for suf in ("ciones", "cion", "es", "s"):
            if len(raiz) > len(suf) + 3 and raiz.endswith(suf):
                raiz = raiz[: -len(suf)]
                break
        out.append(raiz)
    return list(dict.fromkeys(out))


def conectar():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def buscar(consulta, categoria=None, iso=None, empresa=None, gratuito=None, todas=False, limite=8, desde=0):
    terms = _terminos(consulta or "")
    con = conectar()
    try:
        if terms:
            fts = (" AND " if todas else " OR ").join(f'"{t}"*' for t in terms)
            sql = ("SELECT p.*, bm25(productos_fts, 10.0, 2.0, 4.0, 4.0, 1.0, 1.0) AS score "
                   "FROM productos_fts JOIN productos p ON p.id = productos_fts.rowid "
                   "WHERE productos_fts MATCH ? ORDER BY score")
            filas = con.execute(sql, (fts,)).fetchall()
        else:
            filas = con.execute("SELECT p.*, 0 AS score FROM productos p ORDER BY fecha_alta DESC").fetchall()
    finally:
        con.close()

    def pasa(r):
        if categoria and sin_tildes(categoria) not in sin_tildes(r["categorias"]):
            return False
        if iso:
            cods = [x.split(" ")[0] for x in r["iso"].split(" | ") if x]
            q = iso.strip()
            if not any(c.startswith(q) or c.replace(".", "").startswith(q.replace(".", "")) for c in cods):
                return False
        if empresa and sin_tildes(empresa) not in sin_tildes(r["empresas"]):
            return False
        if gratuito is not None and bool(r["gratuito"]) != bool(gratuito):
            return False
        return True

    filas = [r for r in filas if pasa(r)]
    total = len(filas)
    res = []
    for r in filas[desde: desde + limite]:
        d = r["descripcion"]
        res.append({
            "id": r["id"], "nombre": r["nombre"], "modelo": r["modelo"],
            "descripcion": d if len(d) <= 350 else d[:350].rsplit(" ", 1)[0] + "…",
            "categorias": r["categorias"], "iso": r["iso"], "empresas": r["empresas"],
            "gratuito": bool(r["gratuito"]), "n_fotos": r["n_fotos"], "url": r["url"],
        })
    return {"consulta_interpretada": terms, "total_coincidencias": total, "mostrando": len(res),
            "desde": desde, "resultados": res}


def fichas(ids):
    con = conectar()
    try:
        out = []
        for i in ids:
            r = con.execute("SELECT ficha FROM productos WHERE id = ?", (int(i),)).fetchone()
            out.append(json.loads(r["ficha"]) if r else {"id": i, "error": "No existe en la base de datos"})
        return out
    finally:
        con.close()


def categorias(ruta=None):
    con = conectar()
    try:
        filas = [dict(r) for r in con.execute("SELECT * FROM categorias ORDER BY ruta")]
    finally:
        con.close()
    if not ruta:
        return [{"categoria": f["ruta"], "n_productos": f["n_productos"]} for f in filas if f["nivel"] == 1]
    q = sin_tildes(ruta)
    base = ([f for f in filas if sin_tildes(f["ruta"]) == q]
            or [f for f in filas if sin_tildes(f["nombre"]) == q]
            or [f for f in filas if sin_tildes(f["ruta"]).endswith(q)]
            or [f for f in filas if q in sin_tildes(f["nombre"])])
    if not base:
        return {"error": f"No encuentro la categoría '{ruta}'", "categorias_principales":
                [f["ruta"] for f in filas if f["nivel"] == 1]}
    out = []
    for b in base[:5]:
        hijos = [{"categoria": f["ruta"], "n_productos": f["n_productos"]} for f in filas if f["padre"] == b["id"]]
        out.append({"categoria": b["ruta"], "n_productos": b["n_productos"], "subcategorias": hijos})
    return out


def isos(texto, limite=25):
    con = conectar()
    try:
        filas = [dict(r) for r in con.execute("SELECT * FROM iso ORDER BY codigo")]
    finally:
        con.close()
    q = (texto or "").strip()
    if re.fullmatch(r"[\d.\s]+", q):
        cod = q.replace(" ", "")
        sel = [f for f in filas if f["codigo"].startswith(cod) or f["codigo"].replace(".", "").startswith(cod.replace(".", ""))]
    else:
        terms = _terminos(q)
        sel = [f for f in filas if all(t in sin_tildes(f["nombre"]) for t in terms)] or \
              [f for f in filas if any(t in sin_tildes(f["nombre"]) for t in terms)]
    return [{"codigo": f["codigo"], "nombre": f["nombre"], "n_productos": f["n_productos"]} for f in sel[:limite]]


def empresas(texto=None, id_empresa=None, limite=20):
    con = conectar()
    try:
        if id_empresa:
            e = con.execute("SELECT * FROM empresas WHERE id = ?", (int(id_empresa),)).fetchone()
            if not e:
                return {"error": "Empresa no encontrada"}
            prods = con.execute(
                "SELECT p.id, p.nombre, p.url, pe.roles FROM producto_empresa pe JOIN productos p ON p.id = pe.id_prod "
                "WHERE pe.id_emp = ? ORDER BY p.nombre", (int(id_empresa),)).fetchall()
            return {**dict(e), "productos": [dict(p) for p in prods]}
        filas = [dict(r) for r in con.execute("SELECT * FROM empresas ORDER BY nombre")]
    finally:
        con.close()
    q = sin_tildes(texto or "")
    sel = [f for f in filas if q in sin_tildes(f"{f['nombre']} {f['acronimo']} {f['direccion']}")]
    return {"total": len(sel), "empresas": sel[:limite]}


def estado():
    if not DB.exists():
        return {"base_de_datos": "no disponible"}
    con = conectar()
    try:
        meta = {r["clave"]: r["valor"] for r in con.execute("SELECT * FROM meta")}
    finally:
        con.close()
    f = fecha_descarga(DB)
    return {**meta, "dias_desde_actualizacion": (datetime.now() - f).days, "ruta_base_de_datos": str(DB),
            "actualizacion_en_curso": estado_actualizacion["en_curso"],
            "ultimo_resultado_actualizacion": estado_actualizacion["ultimo_resultado"],
            "actualizacion_automatica_cada_dias": DIAS_CADUCIDAD}
