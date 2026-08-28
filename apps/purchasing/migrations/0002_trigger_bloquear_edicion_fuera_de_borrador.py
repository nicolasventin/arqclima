from django.db import migrations

CREAR_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION purchasing_bloquear_edicion_fuera_de_borrador()
RETURNS TRIGGER AS $$
DECLARE
    v_orden_id bigint;
    v_estado varchar;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_orden_id := OLD.orden_id;
    ELSE
        v_orden_id := NEW.orden_id;
    END IF;

    SELECT estado INTO v_estado FROM purchasing_ordendecompra WHERE id = v_orden_id;

    IF v_estado IS DISTINCT FROM 'borrador' THEN
        RAISE EXCEPTION
            'No se pueden modificar líneas de una orden de compra que no está en Borrador (estado actual: %). Reabrila a Borrador primero.',
            v_estado;
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER lineaordencompra_bloquear_fuera_de_borrador
BEFORE INSERT OR UPDATE OR DELETE ON purchasing_lineaordencompra
FOR EACH ROW EXECUTE FUNCTION purchasing_bloquear_edicion_fuera_de_borrador();
"""

ELIMINAR_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS lineaordencompra_bloquear_fuera_de_borrador ON purchasing_lineaordencompra;
DROP FUNCTION IF EXISTS purchasing_bloquear_edicion_fuera_de_borrador();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREAR_TRIGGER_SQL, reverse_sql=ELIMINAR_TRIGGER_SQL),
    ]
