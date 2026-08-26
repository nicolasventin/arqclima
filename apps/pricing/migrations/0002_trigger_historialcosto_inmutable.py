from django.db import migrations

CREAR_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION pricing_historialcosto_bloquear_modificacion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'HistorialCosto es de solo lectura una vez creado: no se permite UPDATE ni DELETE (regla de negocio: el historial de costos nunca se borra).';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER historialcosto_bloquear_update
BEFORE UPDATE ON pricing_historialcosto
FOR EACH ROW EXECUTE FUNCTION pricing_historialcosto_bloquear_modificacion();

CREATE TRIGGER historialcosto_bloquear_delete
BEFORE DELETE ON pricing_historialcosto
FOR EACH ROW EXECUTE FUNCTION pricing_historialcosto_bloquear_modificacion();
"""

ELIMINAR_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS historialcosto_bloquear_update ON pricing_historialcosto;
DROP TRIGGER IF EXISTS historialcosto_bloquear_delete ON pricing_historialcosto;
DROP FUNCTION IF EXISTS pricing_historialcosto_bloquear_modificacion();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("pricing", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREAR_TRIGGER_SQL, reverse_sql=ELIMINAR_TRIGGER_SQL),
    ]
