"""
Actualiza a mano la base de datos del Asistente CEAPAT.

    python3 server/actualizar.py              # actualiza ~/.ceapat-asistente/ceapat.db
    python3 server/actualizar.py --paquete    # actualiza la copia incluida en data/ (mantenedores)

El servidor MCP ya lo hace solo cuando la base tiene más de 7 días.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ceapat_core as core  # noqa: E402

if __name__ == "__main__":
    destino = core.DB_INCLUIDA if "--paquete" in sys.argv else core.DB
    if destino == core.DB:
        core.asegurar_db()
    print(f"Actualizando {destino} …")
    res = core.actualizar(destino, progreso=print)
    print(res)
    sys.exit(0 if res.get("ok") else 1)
