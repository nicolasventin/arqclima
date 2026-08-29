from django.db import migrations, models

from apps.core.migration_utils import crear_permisos_pendientes


TRIGGER_SQL = r"""
CREATE OR REPLACE FUNCTION stock_validar_saldo_no_negativo()
RETURNS trigger AS $$
DECLARE
    saldo_actual numeric;
    saldo_nuevo numeric;
BEGIN
    -- La fila Producto es el mutex estable compartido con el servicio
    -- Python. Serializa INSERT concurrentes incluso si alguien evita
    -- registrar_movimiento() y escribe directo por ORM/SQL.
    PERFORM 1
    FROM catalog_producto
    WHERE id = NEW.producto_id
    FOR UPDATE;

    SELECT COALESCE(SUM(cantidad), 0)
    INTO saldo_actual
    FROM stock_movimientostock
    WHERE producto_id = NEW.producto_id
      AND deposito = NEW.deposito;

    saldo_nuevo := saldo_actual + NEW.cantidad;

    IF NEW.cantidad < 0 AND saldo_nuevo < 0 THEN
        IF NOT NEW.forzado_stock_negativo THEN
            RAISE EXCEPTION
                'Stock insuficiente: disponible %, movimiento %, saldo resultante %',
                saldo_actual, NEW.cantidad, saldo_nuevo;
        END IF;

        IF btrim(COALESCE(NEW.motivo_forzado, '')) = '' THEN
            RAISE EXCEPTION
                'Una salida forzada con stock negativo requiere motivo';
        END IF;
    ELSIF NEW.forzado_stock_negativo THEN
        RAISE EXCEPTION
            'forzado_stock_negativo solo puede marcar movimientos que dejan saldo negativo';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS stock_validar_saldo_no_negativo_before_insert
ON stock_movimientostock;

CREATE TRIGGER stock_validar_saldo_no_negativo_before_insert
BEFORE INSERT ON stock_movimientostock
FOR EACH ROW
EXECUTE FUNCTION stock_validar_saldo_no_negativo();
"""

REVERSE_TRIGGER_SQL = r"""
DROP TRIGGER IF EXISTS stock_validar_saldo_no_negativo_before_insert
ON stock_movimientostock;
DROP FUNCTION IF EXISTS stock_validar_saldo_no_negativo();
"""


def asignar_permiso_forzado(apps, schema_editor):
    crear_permisos_pendientes(apps, ["stock"])

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    admin = Group.objects.get(name="Administrador")
    permiso = Permission.objects.get(
        content_type__app_label="stock",
        codename="force_negative_stock",
    )
    admin.permissions.add(permiso)


def revocar_permiso_forzado(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    admin = Group.objects.get(name="Administrador")
    permiso = Permission.objects.filter(
        content_type__app_label="stock",
        codename="force_negative_stock",
    ).first()
    if permiso:
        admin.permissions.remove(permiso)


class Migration(migrations.Migration):

    dependencies = [
        ("stock", "0007_validaciones_numericas"),
        ("accounts", "0002_crear_grupos"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimientostock",
            name="forzado_stock_negativo",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Verdadero solo cuando una salida se autorizó explícitamente aun dejando "
                    "el depósito en negativo. Requiere permiso especial y motivo."
                ),
            ),
        ),
        migrations.AddField(
            model_name="movimientostock",
            name="motivo_forzado",
            field=models.TextField(
                blank=True,
                help_text="Motivo obligatorio cuando forzado_stock_negativo=True.",
            ),
        ),
        migrations.AlterModelOptions(
            name="movimientostock",
            options={
                "ordering": ["-creado_en"],
                "permissions": [
                    (
                        "manage_stock_general",
                        "Puede registrar entradas y salidas en el stock general (obra)",
                    ),
                    (
                        "ajustar_stock_general",
                        "Puede hacer ajustes manuales en el stock general",
                    ),
                    (
                        "manage_stock_repuestos",
                        "Puede registrar entradas y salidas en el stock de repuestos (service)",
                    ),
                    (
                        "manage_stock_minimo",
                        "Puede configurar el stock mínimo de alerta por producto",
                    ),
                    (
                        "force_negative_stock",
                        "Puede forzar una salida aunque deje el stock en negativo",
                    ),
                ],
                "verbose_name": "Movimiento de stock",
                "verbose_name_plural": "Movimientos de stock",
            },
        ),
        migrations.AddConstraint(
            model_name="movimientostock",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("forzado_stock_negativo", False),
                        ("motivo_forzado", ""),
                    ),
                    models.Q(
                        ("cantidad__lt", 0),
                        ("forzado_stock_negativo", True),
                    ),
                    _connector="OR",
                ),
                name="movimientostock_forzado_solo_resta",
            ),
        ),
        migrations.RunPython(asignar_permiso_forzado, revocar_permiso_forzado),
        migrations.RunSQL(TRIGGER_SQL, REVERSE_TRIGGER_SQL),
    ]
