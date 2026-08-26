from django.db import migrations

CREAR_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION stock_movimientostock_bloquear_modificacion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'MovimientoStock es de solo lectura una vez creado: no se permite UPDATE ni DELETE (el stock actual se calcula sumando el ledger, nunca se corrige pisando una fila — se registra un Ajuste nuevo).';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER movimientostock_bloquear_update
BEFORE UPDATE ON stock_movimientostock
FOR EACH ROW EXECUTE FUNCTION stock_movimientostock_bloquear_modificacion();

CREATE TRIGGER movimientostock_bloquear_delete
BEFORE DELETE ON stock_movimientostock
FOR EACH ROW EXECUTE FUNCTION stock_movimientostock_bloquear_modificacion();
"""

ELIMINAR_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS movimientostock_bloquear_update ON stock_movimientostock;
DROP TRIGGER IF EXISTS movimientostock_bloquear_delete ON stock_movimientostock;
DROP FUNCTION IF EXISTS stock_movimientostock_bloquear_modificacion();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("stock", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREAR_TRIGGER_SQL, reverse_sql=ELIMINAR_TRIGGER_SQL),
    ]
