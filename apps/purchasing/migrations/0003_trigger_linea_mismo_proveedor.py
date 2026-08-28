from django.db import migrations

CREAR_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION purchasing_linea_mismo_proveedor_que_orden()
RETURNS TRIGGER AS $$
DECLARE
    v_proveedor_orden bigint;
    v_proveedor_linea bigint;
BEGIN
    SELECT proveedor_id INTO v_proveedor_orden
        FROM purchasing_ordendecompra WHERE id = NEW.orden_id;
    SELECT proveedor_id INTO v_proveedor_linea
        FROM catalog_productoproveedor WHERE id = NEW.producto_proveedor_id;

    IF v_proveedor_orden IS DISTINCT FROM v_proveedor_linea THEN
        RAISE EXCEPTION
            'La línea tiene que ser del mismo proveedor que la orden (proveedor de la orden: %, proveedor de la línea: %).',
            v_proveedor_orden, v_proveedor_linea;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER lineaordencompra_mismo_proveedor
BEFORE INSERT OR UPDATE ON purchasing_lineaordencompra
FOR EACH ROW EXECUTE FUNCTION purchasing_linea_mismo_proveedor_que_orden();
"""

ELIMINAR_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS lineaordencompra_mismo_proveedor ON purchasing_lineaordencompra;
DROP FUNCTION IF EXISTS purchasing_linea_mismo_proveedor_que_orden();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0002_trigger_bloquear_edicion_fuera_de_borrador"),
    ]

    operations = [
        migrations.RunSQL(sql=CREAR_TRIGGER_SQL, reverse_sql=ELIMINAR_TRIGGER_SQL),
    ]
