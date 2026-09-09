"""
Migracion de una sola vez: hashea con bcrypt las contraseñas de usuarios
existentes que todavia estan en texto plano (passwordvar sin prefijo
bcrypt $2*$).

Decision tomada: hashear el valor actual EN SU LUGAR, no forzar un reset
de contraseña. En texto plano, passwordvar YA ES la contraseña real del
usuario -- hashearla in situ preserva el acceso sin interrumpir a nadie
la noche antes de la entrega. Un reset forzado habria sido mas "correcto"
en un proyecto con usuarios reales en produccion, pero aqui son cuentas
de prueba del equipo/demo: no vale el costo de bloquear a alguien por
error de deteccion.

Idempotente: si se corre dos veces, la segunda vez no encuentra nada que
migrar (todo ya empieza por $2).

Uso:
    python -m scripts.migrate_hash_passwords          # aplica los cambios
    python -m scripts.migrate_hash_passwords --dry-run  # solo reporta
"""
import argparse
import sys

from db.session import SessionLocal
from models.mapcalor_user import MapCalorUser
from services.auth_service import hashear_password

# Prefijo comun a todas las variantes de bcrypt ($2a$, $2b$, $2x$, $2y$)
BCRYPT_PREFIX = "$2"


def parece_hash_bcrypt(valor: str) -> bool:
    return valor.startswith(BCRYPT_PREFIX)


def main(dry_run: bool) -> int:
    db = SessionLocal()
    try:
        usuarios = db.query(MapCalorUser).all()

        pendientes = [
            u for u in usuarios
            if u.passwordvar and not parece_hash_bcrypt(u.passwordvar)
        ]

        print(f"Usuarios totales: {len(usuarios)}")
        print(f"Con contraseña en texto plano (a migrar): {len(pendientes)}")

        if not pendientes:
            print("Nada que hacer.")
            return 0

        if dry_run:
            for usuario in pendientes:
                print(f"  - id={usuario.id} correo={usuario.correo!r} namevar={usuario.namevar!r}")
            print("\n--dry-run: no se escribio nada en la base de datos.")
            return 0

        # Un commit POR FILA, no uno solo al final -- si hashear_password()
        # o el commit fallan para un usuario puntual, esa fila queda sin
        # migrar (logueada como error) pero no arrastra abajo a las demas
        # que ya se procesaron bien. rollback() acota el daño de la fila
        # fallida a su propia transaccion antes de seguir con la siguiente.
        migrados = 0
        errores = 0
        for usuario in pendientes:
            try:
                usuario.passwordvar = hashear_password(usuario.passwordvar.strip())
                db.commit()
                migrados += 1
                print(f"  OK id={usuario.id} correo={usuario.correo!r} namevar={usuario.namevar!r}")
            except Exception as exc:
                db.rollback()
                errores += 1
                print(f"  ERROR id={usuario.id} correo={usuario.correo!r}: {exc!r}")

        print(f"\nMigrados: {migrados}. Errores: {errores}. Total procesados: {len(pendientes)}.")
        return 1 if errores else 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo reporta cuantos usuarios se migrarian, sin escribir en la DB.",
    )
    args = parser.parse_args()
    sys.exit(main(args.dry_run))
