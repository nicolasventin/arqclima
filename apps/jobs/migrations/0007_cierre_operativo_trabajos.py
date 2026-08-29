from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


TRIGGERS_SQL = r"""
CREATE OR REPLACE FUNCTION jobs_validar_ciclo_trabajo()
RETURNS trigger AS $$
DECLARE
    tiene_pendientes boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.estado <> 'pendiente' THEN
            RAISE EXCEPTION 'Un trabajo nuevo debe comenzar en Pendiente';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.estado = OLD.estado THEN
        RETURN NEW;
    END IF;

    IF OLD.estado IN ('terminado', 'cancelado') THEN
        RAISE EXCEPTION 'Un trabajo cerrado no puede cambiar de estado';
    END IF;

    IF NEW.estado = 'terminado' THEN
        IF OLD.estado <> 'en_ejecucion' THEN
            RAISE EXCEPTION 'Terminado solo puede alcanzarse desde En ejecución';
        END IF;

        IF NEW.tecnico_asignado_id IS NULL THEN
            RAISE EXCEPTION 'No se puede terminar un trabajo sin técnico asignado';
        END IF;

        IF NEW.terminado_en IS NULL OR NEW.terminado_por_id IS NULL THEN
            RAISE EXCEPTION 'Terminado requiere actor y fecha de cierre';
        END IF;

        SELECT EXISTS(
            SELECT 1
            FROM jobs_materialtrabajo mt
            WHERE mt.trabajo_id = NEW.id
              AND mt.producto_id IS NOT NULL
              AND COALESCE(
                    (
                        SELECT -SUM(ms.cantidad)
                        FROM stock_movimientostock ms
                        WHERE ms.material_trabajo_id = mt.id
                          AND ms.tipo = 'salida'
                    ),
                    0
                  ) < mt.cantidad_necesaria
        ) INTO tiene_pendientes;

        IF tiene_pendientes THEN
            RAISE EXCEPTION 'No se puede terminar un trabajo con materiales pendientes de envío';
        END IF;

        RETURN NEW;
    END IF;

    IF NEW.estado = 'cancelado' THEN
        IF NEW.cancelado_en IS NULL OR NEW.cancelado_por_id IS NULL
           OR btrim(COALESCE(NEW.motivo_cancelacion, '')) = '' THEN
            RAISE EXCEPTION 'Cancelar requiere actor, fecha y motivo';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.estado NOT IN (
        'pendiente',
        'preparando_materiales',
        'listo',
        'en_ejecucion'
    ) THEN
        RAISE EXCEPTION 'Estado de trabajo inválido: %', NEW.estado;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS jobs_validar_ciclo_trabajo_before_write
ON jobs_trabajo;

CREATE TRIGGER jobs_validar_ciclo_trabajo_before_write
BEFORE INSERT OR UPDATE ON jobs_trabajo
FOR EACH ROW
EXECUTE FUNCTION jobs_validar_ciclo_trabajo();


CREATE OR REPLACE FUNCTION jobs_bloquear_detalle_cerrado()
RETURNS trigger AS $$
DECLARE
    estado_trabajo varchar;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT estado INTO estado_trabajo
        FROM jobs_trabajo
        WHERE id = OLD.trabajo_id;

        IF estado_trabajo IN ('terminado', 'cancelado') THEN
            RAISE EXCEPTION 'No se puede modificar el detalle operativo de un trabajo cerrado';
        END IF;
    END IF;

    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT estado INTO estado_trabajo
        FROM jobs_trabajo
        WHERE id = NEW.trabajo_id;

        IF estado_trabajo IN ('terminado', 'cancelado') THEN
            RAISE EXCEPTION 'No se puede modificar el detalle operativo de un trabajo cerrado';
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS jobs_bloquear_material_cerrado
ON jobs_materialtrabajo;
CREATE TRIGGER jobs_bloquear_material_cerrado
BEFORE INSERT OR UPDATE OR DELETE ON jobs_materialtrabajo
FOR EACH ROW
EXECUTE FUNCTION jobs_bloquear_detalle_cerrado();

DROP TRIGGER IF EXISTS jobs_bloquear_etapa_cerrada
ON jobs_etapatrabajo;
CREATE TRIGGER jobs_bloquear_etapa_cerrada
BEFORE INSERT OR UPDATE OR DELETE ON jobs_etapatrabajo
FOR EACH ROW
EXECUTE FUNCTION jobs_bloquear_detalle_cerrado();


CREATE OR REPLACE FUNCTION jobs_bloquear_movimiento_stock_cerrado()
RETURNS trigger AS $$
DECLARE
    estado_trabajo varchar;
BEGIN
    IF NEW.trabajo_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT estado INTO estado_trabajo
    FROM jobs_trabajo
    WHERE id = NEW.trabajo_id;

    IF estado_trabajo IN ('terminado', 'cancelado') THEN
        RAISE EXCEPTION 'No se puede registrar stock contra un trabajo cerrado';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS jobs_bloquear_movimiento_stock_cerrado_before_insert
ON stock_movimientostock;
CREATE TRIGGER jobs_bloquear_movimiento_stock_cerrado_before_insert
BEFORE INSERT ON stock_movimientostock
FOR EACH ROW
EXECUTE FUNCTION jobs_bloquear_movimiento_stock_cerrado();


CREATE OR REPLACE FUNCTION jobs_bloquear_edicion_cabecera_cerrada()
RETURNS trigger AS $$
BEGIN
    IF OLD.estado IN ('terminado', 'cancelado') AND (
        NEW.tecnico_asignado_id IS DISTINCT FROM OLD.tecnico_asignado_id
        OR NEW.direccion IS DISTINCT FROM OLD.direccion
        OR NEW.observaciones IS DISTINCT FROM OLD.observaciones
        OR NEW.presupuesto_id IS DISTINCT FROM OLD.presupuesto_id
    ) THEN
        RAISE EXCEPTION 'No se puede modificar la operación de un trabajo cerrado';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS jobs_bloquear_edicion_cabecera_cerrada_before_update
ON jobs_trabajo;
CREATE TRIGGER jobs_bloquear_edicion_cabecera_cerrada_before_update
BEFORE UPDATE ON jobs_trabajo
FOR EACH ROW
EXECUTE FUNCTION jobs_bloquear_edicion_cabecera_cerrada();
"""

REVERSE_TRIGGERS_SQL = r"""
DROP TRIGGER IF EXISTS jobs_bloquear_edicion_cabecera_cerrada_before_update
ON jobs_trabajo;
DROP FUNCTION IF EXISTS jobs_bloquear_edicion_cabecera_cerrada();

DROP TRIGGER IF EXISTS jobs_bloquear_movimiento_stock_cerrado_before_insert
ON stock_movimientostock;
DROP FUNCTION IF EXISTS jobs_bloquear_movimiento_stock_cerrado();

DROP TRIGGER IF EXISTS jobs_bloquear_etapa_cerrada
ON jobs_etapatrabajo;
DROP TRIGGER IF EXISTS jobs_bloquear_material_cerrado
ON jobs_materialtrabajo;
DROP FUNCTION IF EXISTS jobs_bloquear_detalle_cerrado();

DROP TRIGGER IF EXISTS jobs_validar_ciclo_trabajo_before_write
ON jobs_trabajo;
DROP FUNCTION IF EXISTS jobs_validar_ciclo_trabajo();
"""


def normalizar_cierres_historicos(apps, schema_editor):
    Trabajo = apps.get_model("jobs", "Trabajo")
    AuditLog = apps.get_model("audit", "AuditLog")
    ContentType = apps.get_model("contenttypes", "ContentType")

    content_type = ContentType.objects.filter(
        app_label="jobs",
        model="trabajo",
    ).first()

    def ultimo_log(trabajo, acciones):
        if content_type is None:
            return None
        return (
            AuditLog.objects.filter(
                content_type_id=content_type.pk,
                object_id=str(trabajo.pk),
                accion__in=acciones,
            )
            .order_by("-creado_en")
            .first()
        )

    for trabajo in Trabajo.objects.filter(estado="terminado").iterator():
        log = ultimo_log(
            trabajo,
            ["finalizar_trabajo", "cambiar_estado_trabajo"],
        )
        trabajo.terminado_en = (
            log.creado_en if log is not None else trabajo.creado_en
        )
        trabajo.terminado_por_id = (
            log.usuario_id if log is not None and log.usuario_id
            else trabajo.creado_por_id
        )
        trabajo.save(
            update_fields=["terminado_en", "terminado_por"]
        )

    for trabajo in Trabajo.objects.filter(estado="cancelado").iterator():
        log = ultimo_log(trabajo, ["cancelar_trabajo"])
        trabajo.cancelado_en = (
            log.creado_en if log is not None else trabajo.creado_en
        )
        trabajo.cancelado_por_id = (
            log.usuario_id if log is not None and log.usuario_id
            else trabajo.creado_por_id
        )
        trabajo.motivo_cancelacion = (
            (log.detalle or "").strip()
            if log is not None
            else "Migrado desde cierre histórico"
        ) or "Migrado desde cierre histórico"
        trabajo.save(
            update_fields=[
                "cancelado_en",
                "cancelado_por",
                "motivo_cancelacion",
            ]
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0006_validaciones_numericas"),
        ("stock", "0008_control_stock_negativo"),
        ("audit", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="trabajo",
            name="terminado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="trabajos_terminados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="trabajo",
            name="terminado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trabajo",
            name="observaciones_cierre",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="trabajo",
            name="cancelado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="trabajos_cancelados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="trabajo",
            name="cancelado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trabajo",
            name="motivo_cancelacion",
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(
            normalizar_cierres_historicos,
            noop_reverse,
        ),
        migrations.AddConstraint(
            model_name="trabajo",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ~models.Q(("estado", "terminado")),
                    models.Q(("terminado_en__isnull", False)),
                    _connector="OR",
                ),
                name="trabajo_terminado_requiere_metadata",
            ),
        ),
        migrations.AddConstraint(
            model_name="trabajo",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ~models.Q(("estado", "cancelado")),
                    models.Q(
                        models.Q(("cancelado_en__isnull", False)),
                        ~models.Q(("motivo_cancelacion", "")),
                    ),
                    _connector="OR",
                ),
                name="trabajo_cancelado_requiere_metadata",
            ),
        ),
        migrations.RunSQL(
            TRIGGERS_SQL,
            REVERSE_TRIGGERS_SQL,
        ),
    ]
