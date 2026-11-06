#!/usr/bin/env python3
"""
Servidor MCP (stdio) del Asistente de Productos de Apoyo CEAPAT.
Funciona con Claude Code, Claude Desktop, Codex y cualquier cliente MCP.
Sin dependencias: solo Python 3.9+.

    python3 server/mcp_server.py
"""

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ceapat_core as core  # noqa: E402

NOMBRE = "ceapat"
VERSION = "1.0.0"

INSTRUCCIONES = """\
Eres un asesor de productos de apoyo que trabaja con el Catálogo de Productos de Apoyo del Ceapat-Imserso
(catalogoceapat.imserso.es, unos 2.300 productos). Responde en el idioma del usuario, con lenguaje claro.

Cómo trabajar:
1. Entiende la necesidad en términos de actividad (qué quiere hacer la persona y qué le cuesta), la persona
   (edad aproximada, capacidades: fuerza, uso de una mano, equilibrio, visión, audición, cognición...) y el
   entorno (casa, baño, trabajo, colegio, calle, quién le ayuda). Si la petición es vaga, haz como máximo 2-3
   preguntas breves; si ya es concreta, busca directamente.
2. Busca con varias consultas cortas de palabras clave (sinónimos, nombre del producto, la actividad) usando
   buscar_productos. Usa explorar_categorias y buscar_iso para orientarte y filtrar. Lanza varias búsquedas
   si hace falta: los nombres del catálogo son técnicos (p. ej. "calzamedias", "abrochabotones", "grúa").
3. Antes de recomendar, consulta la ficha completa (ver_producto) de los candidatos para no inventar nada.
4. Presenta de 3 a 6 opciones ordenadas por adecuación. Para cada una: nombre enlazado a su ficha en CEAPAT
   ([Nombre](url)), para qué sirve y por qué encaja con esta persona, qué tener en cuenta (limitaciones,
   requisitos, alternativas) y quién lo fabrica o distribuye. Agrupa por tipo de solución si ayuda.
5. Responde a cualquier pregunta sobre productos, empresas (web, email, teléfono), características técnicas,
   fotos o códigos ISO 9999 usando las herramientas.

Reglas:
- Usa solo datos devueltos por las herramientas; no inventes modelos, medidas, empresas ni enlaces.
- El catálogo no incluye precios ni stock: dilo si te preguntan y remite a la empresa.
- No sustituyes una valoración profesional: para decisiones relevantes (sillas de ruedas, grúas,
  posicionamiento, seguridad, prescripción) recomienda la valoración de un/a terapeuta ocupacional u otro
  profesional, y menciona el servicio de asesoría del Ceapat (ceapat@imserso.es, 917 033 100).
- Si no encuentras nada adecuado, dilo con claridad y sugiere búsquedas o categorías alternativas.
- Si estado_base_datos indica más de 7 días sin actualizar, la actualización se lanza sola; puedes
  mencionarlo si es relevante.
"""


def _txt(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1)


HERRAMIENTAS = [
    {
        "name": "buscar_productos",
        "description": ("Busca productos de apoyo en el catálogo CEAPAT por palabras clave (nombre, descripción, "
                        "categoría, ISO, empresa). Ignora tildes y plurales. Devuelve resultados resumidos con su "
                        "enlace a CEAPAT. Usa consultas cortas de palabras clave y lanza varias con sinónimos."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "consulta": {"type": "string", "description": "Palabras clave, p. ej. 'calzador medias' o 'silla ducha'. Puede ir vacía si se usan filtros."},
                "categoria": {"type": "string", "description": "Filtro: texto contenido en la ruta de categoría, p. ej. 'Sillas de ruedas motorizadas' o 'Vestirse'."},
                "iso": {"type": "string", "description": "Filtro: prefijo de código ISO 9999, p. ej. '12.22' o '09.33.03'."},
                "empresa": {"type": "string", "description": "Filtro: parte del nombre de la empresa."},
                "gratuito": {"type": "boolean", "description": "Filtro: solo productos gratuitos (true) o de pago (false)."},
                "todas_las_palabras": {"type": "boolean", "description": "true = exigir todas las palabras (más preciso). Por defecto basta con alguna, ordenado por relevancia."},
                "limite": {"type": "integer", "description": "Número de resultados (1-25, por defecto 8)."},
                "desde": {"type": "integer", "description": "Desplazamiento para paginar (por defecto 0)."},
            },
        },
    },
    {
        "name": "ver_producto",
        "description": ("Ficha completa de uno o varios productos (máx. 6): descripción entera, modelo, categorías, "
                        "códigos ISO, características técnicas, empresas con web/email/teléfono y papel "
                        "(fabricante/distribuidor), fotos, vídeos y enlace a CEAPAT. Úsalo para comparar."),
        "inputSchema": {
            "type": "object",
            "properties": {"ids": {"type": "array", "items": {"type": "integer"}, "description": "IDs de producto."}},
            "required": ["ids"],
        },
    },
    {
        "name": "explorar_categorias",
        "description": ("Árbol de categorías del catálogo con número de productos. Sin argumento devuelve las 9 "
                        "categorías principales; con una categoría devuelve sus subcategorías."),
        "inputSchema": {
            "type": "object",
            "properties": {"categoria": {"type": "string", "description": "Nombre o ruta de categoría, p. ej. 'Aseo y cuidado personal' o 'Sillas de ruedas'."}},
        },
    },
    {
        "name": "buscar_iso",
        "description": "Busca clases de la norma ISO 9999 por texto ('andador') o por código ('12.06'), con el número de productos de cada una.",
        "inputSchema": {
            "type": "object",
            "properties": {"texto": {"type": "string"}},
            "required": ["texto"],
        },
    },
    {
        "name": "buscar_empresa",
        "description": ("Busca empresas (fabricantes/distribuidores) por nombre o localidad, o devuelve la ficha "
                        "de una empresa con sus datos de contacto y todos sus productos si se indica id_empresa."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Parte del nombre, acrónimo o dirección (p. ej. 'Zaragoza')."},
                "id_empresa": {"type": "integer"},
            },
        },
    },
    {
        "name": "estado_base_datos",
        "description": "Fecha de la última actualización desde CEAPAT, totales y estado de la actualización automática.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "actualizar_base_datos",
        "description": ("Lanza en segundo plano la descarga completa del catálogo desde CEAPAT (tarda unos minutos). "
                        "Se hace sola cada 7 días; úsala solo si el usuario lo pide."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


for _h in HERRAMIENTAS:
    _h["annotations"] = {"readOnlyHint": _h["name"] != "actualizar_base_datos", "openWorldHint": False}


def ejecutar(nombre, a):
    if nombre == "buscar_productos":
        lim = max(1, min(int(a.get("limite") or 8), 25))
        r = core.buscar(a.get("consulta", ""), a.get("categoria"), a.get("iso"), a.get("empresa"),
                        a.get("gratuito"), bool(a.get("todas_las_palabras")), lim, int(a.get("desde") or 0))
        if r["total_coincidencias"] == 0:
            r["sugerencia"] = ("Sin resultados. Prueba otras palabras o sinónimos, quita filtros, o usa "
                               "explorar_categorias / buscar_iso para encontrar el nombre técnico.")
        return r
    if nombre == "ver_producto":
        ids = a.get("ids") or []
        if isinstance(ids, (int, str)):
            ids = [ids]
        out = core.fichas(ids[:6])
        for f in out:
            if len(f.get("fotos", [])) > 4:
                f["fotos_total"] = len(f["fotos"])
                f["fotos"] = f["fotos"][:4]
        return out
    if nombre == "explorar_categorias":
        return core.categorias(a.get("categoria"))
    if nombre == "buscar_iso":
        return core.isos(a.get("texto", ""))
    if nombre == "buscar_empresa":
        return core.empresas(a.get("texto"), a.get("id_empresa"))
    if nombre == "estado_base_datos":
        return core.estado()
    if nombre == "actualizar_base_datos":
        lanzada = core.actualizar_en_segundo_plano()
        return {"lanzada": lanzada, "mensaje": "Actualización iniciada en segundo plano; consulta estado_base_datos en unos minutos."
                if lanzada else "Ya hay una actualización en curso."}
    raise ValueError(f"Herramienta desconocida: {nombre}")


def responder(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def manejar(msg):
    metodo, id_ = msg.get("method"), msg.get("id")
    if metodo == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        responder(id_, {"protocolVersion": version,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": NOMBRE, "version": VERSION},
                        "instructions": INSTRUCCIONES})
    elif metodo == "tools/list":
        responder(id_, {"tools": HERRAMIENTAS})
    elif metodo == "tools/call":
        p = msg.get("params") or {}
        try:
            if not core.DB.exists():
                raise RuntimeError("El catálogo del Ceapat se está descargando por primera vez (tarda unos minutos). "
                                   "Vuelve a intentarlo en un rato. Si no avanza, ejecuta: python3 server/actualizar.py")
            res = ejecutar(p.get("name"), p.get("arguments") or {})
            responder(id_, {"content": [{"type": "text", "text": _txt(res)}], "isError": False})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            responder(id_, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
    elif metodo == "ping":
        responder(id_, {})
    elif id_ is not None and metodo in ("resources/list", "prompts/list"):
        responder(id_, {"resources": []} if metodo == "resources/list" else {"prompts": []})
    elif id_ is not None:
        responder(id_, error={"code": -32601, "message": f"Método no soportado: {metodo}"})
    # las notificaciones (sin id) se ignoran


def main():
    core.asegurar_db()
    if os.environ.get("CEAPAT_AUTO_UPDATE", "1") != "0" and (not core.DB.exists() or core.caducada()):
        core.actualizar_en_segundo_plano()
    for linea in sys.stdin:
        linea = linea.strip()
        if not linea:
            continue
        try:
            msg = json.loads(linea)
        except json.JSONDecodeError:
            responder(None, error={"code": -32700, "message": "JSON inválido"})
            continue
        for m in msg if isinstance(msg, list) else [msg]:
            manejar(m)


if __name__ == "__main__":
    main()
