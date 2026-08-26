from django.db import migrations

CREAR_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION quotes_bloquear_edicion_fuera_de_borrador()
RETURNS TRIGGER AS $$
DECLARE
    v_presupuesto_id bigint;
    v_estado varchar;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_presupuesto_id := OLD.presupuesto_id;
    ELSE
        v_presupuesto_id := NEW.presupuesto_id;
    END IF;

    SELECT estado INTO v_estado FROM quotes_presupuesto WHERE id = v_presupuesto_id;

    IF v_estado IS DISTINCT FROM 'borrador' THEN
        RAISE EXCEPTION
            'No se pueden modificar secciones/ítems de un presupuesto que no está en Borrador (estado actual: %). Reabrilo a Borrador primero.',
            v_estado;
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER seccionpresupuesto_bloquear_fuera_de_borrador
BEFORE INSERT OR UPDATE OR DELETE ON quotes_seccionpresupuesto
FOR EACH ROW EXECUTE FUNCTION quotes_bloquear_edicion_fuera_de_borrador();

CREATE TRIGGER itempresupuesto_bloquear_fuera_de_borrador
BEFORE INSERT OR UPDATE OR DELETE ON quotes_itempresupuesto
FOR EACH ROW EXECUTE FUNCTION quotes_bloquear_edicion_fuera_de_borrador();
"""

ELIMINAR_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS seccionpresupuesto_bloquear_fuera_de_borrador ON quotes_seccionpresupuesto;
DROP TRIGGER IF EXISTS itempresupuesto_bloquear_fuera_de_borrador ON quotes_itempresupuesto;
DROP FUNCTION IF EXISTS quotes_bloquear_edicion_fuera_de_borrador();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("quotes", "0005_alter_presupuesto_options_and_more"),
    ]

    operations = [
        migrations.RunSQL(sql=CREAR_TRIGGER_SQL, reverse_sql=ELIMINAR_TRIGGER_SQL),
    ]
