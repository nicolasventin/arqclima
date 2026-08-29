from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


DROP_TRANSITION_TRIGGER_SQL = r"""
DROP TRIGGER IF EXISTS purchasing_validar_transicion_orden_before_update
ON purchasing_ordendecompra;
"""


NEW_TRIGGER_SQL = r"""
CREATE OR REPLACE FUNCTION purchasing_validar_transicion_orden()
RETURNS trigger AS $$
DECLARE
    tiene_lineas boolean;
    tiene_recepciones boolean;
    tiene_pendientes boolean;
BEGIN
    IF NEW.estado = OLD.estado THEN
        RETURN NEW;
    END IF;

    IF NOT (
        (OLD.estado = 'borrador' AND NEW.estado = 'emitida')
        OR (OLD.estado = 'emitida' AND NEW.estado IN ('borrador', 'enviada', 'cancelada'))
        OR (OLD.estado = 'enviada' AND NEW.estado IN ('recepcion_parcial', 'recibida', 'cancelada'))
        OR (OLD.estado = 'recepcion_parcial' AND NEW.estado IN ('recibida', 'cerrada'))
        OR (OLD.estado = 'recibida' AND NEW.estado = 'cerrada')
    ) THEN
        RAISE EXCEPTION
            'Transición inválida de orden de compra: % -> %',
            OLD.estado, NEW.estado;
    END IF;

    SELECT EXISTS(
        SELECT 1 FROM purchasing_lineaordencompra
        WHERE orden_id = NEW.id
    ) INTO tiene_lineas;

    IF NEW.estado = 'emitida' THEN
        IF NOT tiene_lineas THEN
            RAISE EXCEPTION 'No se puede emitir una orden sin líneas';
        END IF;
        IF NEW.emitida_en IS NULL OR NEW.emitida_por_id IS NULL THEN
            RAISE EXCEPTION 'La emisión requiere usuario y fecha';
        END IF;
    END IF;

    IF NEW.estado = 'enviada' THEN
        IF NEW.enviada_en IS NULL OR NEW.enviada_por_id IS NULL THEN
            RAISE EXCEPTION 'Marcar enviada requiere usuario y fecha';
        END IF;
    END IF;

    SELECT EXISTS(
        SELECT 1
        FROM stock_movimientostock
        WHERE orden_compra_id = NEW.id
          AND linea_orden_compra_id IS NOT NULL
          AND tipo = 'entrada'
          AND cantidad > 0
    ) INTO tiene_recepciones;

    SELECT EXISTS(
        SELECT 1
        FROM purchasing_lineaordencompra l
        WHERE l.orden_id = NEW.id
          AND COALESCE(
                (
                    SELECT SUM(m.cantidad)
                    FROM stock_movimientostock m
                    WHERE m.linea_orden_compra_id = l.id
                      AND m.tipo = 'entrada'
                      AND m.cantidad > 0
                ),
                0
              ) < l.cantidad
    ) INTO tiene_pendientes;

    IF NEW.estado = 'recepcion_parcial' THEN
        IF NOT tiene_recepciones THEN
            RAISE EXCEPTION 'Recepción parcial requiere al menos una recepción real';
        END IF;
        IF NOT tiene_pendientes THEN
            RAISE EXCEPTION 'Una orden sin pendientes debe quedar como Recibida';
        END IF;
        IF NEW.primera_recepcion_en IS NULL THEN
            RAISE EXCEPTION 'Recepción parcial requiere fecha de primera recepción';
        END IF;
    END IF;

    IF NEW.estado = 'recibida' THEN
        IF NOT tiene_lineas OR NOT tiene_recepciones OR tiene_pendientes THEN
            RAISE EXCEPTION 'Una orden solo puede quedar Recibida si todas sus líneas fueron recibidas';
        END IF;
        IF NEW.primera_recepcion_en IS NULL OR NEW.recibida_en IS NULL THEN
            RAISE EXCEPTION 'Orden Recibida requiere fechas de recepción';
        END IF;
    END IF;

    IF NEW.estado = 'cancelada' THEN
        IF tiene_recepciones THEN
            RAISE EXCEPTION 'Una orden con recepciones no puede cancelarse; debe cerrarse';
        END IF;
        IF NEW.cancelada_en IS NULL OR NEW.cancelada_por_id IS NULL
           OR btrim(COALESCE(NEW.motivo_cancelacion, '')) = '' THEN
            RAISE EXCEPTION 'La cancelación requiere usuario, fecha y motivo';
        END IF;
    END IF;

    IF NEW.estado = 'cerrada' THEN
        IF NEW.cerrada_en IS NULL OR NEW.cerrada_por_id IS NULL THEN
            RAISE EXCEPTION 'El cierre requiere usuario y fecha';
        END IF;
        IF OLD.estado = 'recepcion_parcial'
           AND btrim(COALESCE(NEW.motivo_cierre, '')) = '' THEN
            RAISE EXCEPTION 'Cerrar una recepción parcial requiere motivo';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER purchasing_validar_transicion_orden_before_update
BEFORE UPDATE OF estado ON purchasing_ordendecompra
FOR EACH ROW
EXECUTE FUNCTION purchasing_validar_transicion_orden();
"""


OLD_TRIGGER_SQL = r"""
CREATE OR REPLACE FUNCTION purchasing_validar_transicion_orden()
RETURNS trigger AS $$
BEGIN
    IF NEW.estado = OLD.estado THEN
        RETURN NEW;
    END IF;

    IF NOT (
        (OLD.estado = 'borrador' AND NEW.estado = 'pendiente_aprobacion')
        OR (OLD.estado = 'pendiente_aprobacion' AND NEW.estado IN ('aprobada', 'rechazada', 'borrador', 'cancelada'))
        OR (OLD.estado = 'rechazada' AND NEW.estado = 'borrador')
        OR (OLD.estado = 'aprobada' AND NEW.estado IN ('enviada', 'cancelada'))
        OR (OLD.estado = 'enviada' AND NEW.estado IN ('recepcion_parcial', 'recibida', 'cancelada'))
        OR (OLD.estado = 'recepcion_parcial' AND NEW.estado IN ('recibida', 'cerrada'))
        OR (OLD.estado = 'recibida' AND NEW.estado = 'cerrada')
    ) THEN
        RAISE EXCEPTION
            'Transición inválida de orden de compra: % -> %',
            OLD.estado, NEW.estado;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER purchasing_validar_transicion_orden_before_update
BEFORE UPDATE OF estado ON purchasing_ordendecompra
FOR EACH ROW
EXECUTE FUNCTION purchasing_validar_transicion_orden();
"""


def migrar_flujo_sin_aprobacion(apps, schema_editor):
    Orden = apps.get_model("purchasing", "OrdenDeCompra")
    Permission = apps.get_model("auth", "Permission")

    for orden in Orden.objects.all().iterator():
        update_fields = []

        # Aprovechamos los hitos legacy para reconstruir cuándo quedó
        # congelada oficialmente una orden antes de 11K.
        emitida_por_id = orden.aprobada_por_id or orden.solicitud_aprobacion_por_id
        emitida_en = orden.aprobada_en or orden.solicitud_aprobacion_en
        if emitida_por_id and emitida_en:
            orden.emitida_por_id = emitida_por_id
            orden.emitida_en = emitida_en
            update_fields += ["emitida_por", "emitida_en"]

        if orden.estado in ("pendiente_aprobacion", "aprobada"):
            orden.estado = "emitida"
            update_fields.append("estado")
        elif orden.estado == "rechazada":
            # Una rechazada era una orden que debía corregirse antes de
            # seguir; en el flujo nuevo vuelve a Borrador. El motivo y
            # usuario legacy se conservan en sus columnas históricas.
            orden.estado = "borrador"
            orden.emitida_por_id = None
            orden.emitida_en = None
            update_fields += ["estado", "emitida_por", "emitida_en"]

        if update_fields:
            orden.save(update_fields=list(dict.fromkeys(update_fields)))

    Permission.objects.filter(
        content_type__app_label="purchasing",
        codename="approve_ordendecompra",
    ).delete()


def revertir_flujo(apps, schema_editor):
    Orden = apps.get_model("purchasing", "OrdenDeCompra")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    for orden in Orden.objects.filter(estado="emitida").iterator():
        orden.estado = "aprobada"
        if orden.aprobada_por_id is None:
            orden.aprobada_por_id = orden.emitida_por_id
        if orden.aprobada_en is None:
            orden.aprobada_en = orden.emitida_en
        orden.save(update_fields=["estado", "aprobada_por", "aprobada_en"])

    content_type = ContentType.objects.get(app_label="purchasing", model="ordendecompra")
    Permission.objects.get_or_create(
        content_type=content_type,
        codename="approve_ordendecompra",
        defaults={"name": "Puede aprobar o rechazar una orden de compra"},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0006_ciclo_orden_compra"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL(
            DROP_TRANSITION_TRIGGER_SQL,
            reverse_sql=OLD_TRIGGER_SQL,
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="emitida_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="emitida_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ordenes_compra_emitidas",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="ordendecompra",
            name="estado",
            field=models.CharField(
                choices=[
                    ("borrador", "Borrador"),
                    ("emitida", "Emitida"),
                    ("enviada", "Enviada"),
                    ("recepcion_parcial", "Recepción parcial"),
                    ("recibida", "Recibida"),
                    ("cerrada", "Cerrada"),
                    ("cancelada", "Cancelada"),
                ],
                default="borrador",
                max_length=30,
            ),
        ),
        migrations.AlterModelOptions(
            name="ordendecompra",
            options={
                "ordering": ["-numero"],
                "verbose_name": "Orden de compra",
                "verbose_name_plural": "Órdenes de compra",
            },
        ),
        migrations.RunPython(migrar_flujo_sin_aprobacion, revertir_flujo),
        migrations.RunSQL(
            NEW_TRIGGER_SQL,
            reverse_sql=DROP_TRANSITION_TRIGGER_SQL,
        ),
    ]
