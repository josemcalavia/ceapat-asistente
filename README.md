# Asistente de productos de apoyo CEAPAT

Un asistente conversacional que te recomienda productos de apoyo del **Catálogo de Productos de Apoyo del Ceapat-Imserso** (unos 2.300 productos y 400 empresas). Le cuentas en lenguaje natural qué necesitas y te propone las opciones más adecuadas. Cada opción va comentada (por qué encaja y qué tener en cuenta), con su **enlace directo a la ficha en CEAPAT** y los datos de contacto de la empresa.

> *"Mi madre tiene 82 años y artrosis en las manos; le cuesta abrir botes y comer con los cubiertos normales."*
> → cubiertos de mango grueso, engrosadores, cubiertos con sujeción palmar y abridores de tarros, cada uno con su enlace a CEAPAT y sus características.

Funciona en **Claude Code**, **Codex** y **Claude Desktop**, o en cualquier aplicación compatible con MCP. Solo necesita **Python 3.9 o superior**, sin instalar nada más. La base de datos viene incluida y **se actualiza sola cada semana** desde CEAPAT.

---

## Instalación

Para Codex, Claude Desktop o el uso sin instalar, descarga primero la carpeta:

```bash
git clone https://github.com/josemcalavia/ceapat-asistente.git
```

(o pulsa *Code → Download ZIP* en GitHub y descomprímela).

### Claude Code (como plugin)

```
/plugin marketplace add josemcalavia/ceapat-asistente
/plugin install ceapat-asistente@ceapat-asistente
```

Reinicia Claude Code y pregúntale lo que necesites. Si has descargado la carpeta en tu ordenador, también puedes usarla sin instalar nada:

```bash
claude --plugin-dir /ruta/a/ceapat-asistente
```

### Codex

```bash
codex mcp add ceapat -- python3 /ruta/a/ceapat-asistente/server/mcp_server.py
```

Las instrucciones de cómo debe asesorar viajan con el propio servidor. Si quieres reforzarlas, copia el contenido de `AGENTS.md` en `~/.codex/AGENTS.md`. Si ejecutas `codex` dentro de esta carpeta, el servidor se carga solo gracias a `.codex/config.toml`.

### Claude Desktop

En *Ajustes → Desarrollador → Editar configuración*, añade esto a `claude_desktop_config.json` y reinicia la aplicación:

```json
{
  "mcpServers": {
    "ceapat": {
      "command": "python3",
      "args": ["/ruta/a/ceapat-asistente/server/mcp_server.py"]
    }
  }
}
```

> **Windows:** cambia `python3` por `python` y usa la ruta completa, por ejemplo `C:\\Users\\tu-nombre\\ceapat-asistente\\server\\mcp_server.py`.

---

## Qué puede hacer

| Herramienta | Para qué sirve |
|---|---|
| `buscar_productos` | Busca por palabras clave, sin importar tildes ni plurales. Filtra por categoría, código ISO 9999, empresa o gratuidad. |
| `ver_producto` | Muestra la ficha completa: descripción, medidas y características técnicas, empresas, fotos, vídeos y enlace. Sirve para comparar hasta 6 productos. |
| `explorar_categorias` | Recorre el árbol de categorías de CEAPAT (9 categorías principales y 80 subcategorías). |
| `buscar_iso` | Busca clases de la norma ISO 9999 por texto o por código. |
| `buscar_empresa` | Muestra los datos de contacto de fabricantes y distribuidores, y todos sus productos. |
| `estado_base_datos` / `actualizar_base_datos` | Indica la fecha de los datos y permite forzar una actualización. |

## Actualización de los datos

- La primera vez que se usa, la base incluida se copia a `~/.ceapat-asistente/ceapat.db`.
- Cada vez que se arranca el asistente, si los datos tienen **más de 7 días**, se descargan de nuevo desde CEAPAT en segundo plano (tarda unos minutos y no interrumpe la conversación). Los datos de las empresas ya conocidas se reutilizan, para no cargar el servidor de CEAPAT.
- Actualización manual: `python3 server/actualizar.py`
- Para desactivar la actualización automática, define la variable `CEAPAT_AUTO_UPDATE=0`. Para guardar los datos en otra carpeta, usa `CEAPAT_DATA_DIR=/otra/ruta`.
- El registro de las actualizaciones se guarda en `~/.ceapat-asistente/actualizacion.log`.

## Aviso

- Los datos proceden del catálogo público del Ceapat-Imserso ([catalogoceapat.imserso.es](https://catalogoceapat.imserso.es)) y pertenecen a sus titulares. Este proyecto **no es oficial** y no tiene relación con el Imserso.
- El catálogo **no incluye precios ni disponibilidad**: hay que consultarlos con cada empresa.
- El asistente orienta, pero **no sustituye la valoración de un profesional** (por ejemplo, un/a terapeuta ocupacional). Para dudas concretas puedes contactar con el servicio de información del Ceapat: ceapat@imserso.es · 917 033 100.

## Estructura

```
ceapat-asistente/
├── server/
│   ├── mcp_server.py     servidor MCP (stdio), sin dependencias
│   ├── ceapat_core.py    descarga, base SQLite y búsquedas
│   └── actualizar.py     actualización manual
├── data/ceapat.db        base de datos incluida (SQLite con búsqueda de texto completo)
├── skills/asesor-ceapat/ instrucciones del asesor (Claude Code)
├── AGENTS.md             instrucciones del asesor (Codex)
├── .claude-plugin/       manifiesto del plugin y del marketplace
├── .mcp.json             servidor para Claude Code
└── .codex/config.toml    servidor para Codex (al trabajar en esta carpeta)
```
