from django.conf import settings
from django.db import models


class EstadoTrabajo(models.TextChoices):
    PENDIENTE = "pendiente", "Pendiente"
    PREPARANDO_MATERIALES = "preparando_materiales", "Preparando materiales"
    LISTO = "listo", "Listo"
    EN_EJECUCION = "en_ejecucion", "En ejecución"
    TERMINADO = "terminado", "Terminado"
    CANCELADO = "cancelado", "Cancelado"


# Orden real de avance (regla de negocio 10). El estado de un Trabajo
# avanza o retrocede dentro de esta secuencia (ver
# apps.jobs.services.cambiar_estado_trabajo) comparando posición en la
# lista, sin necesitar un grafo de pares explícito.
#
# CANCELADO queda deliberadamente AFUERA de esta secuencia: no es "más
# adelante" ni "más atrás" que ningún estado, es una salida terminal
# aparte alcanzable desde cualquier estado no resuelto (mismo criterio
# que Cancelado en Presupuesto) — se gestiona con su propia función,
# apps.jobs.services.cancelar_trabajo(), no con cambiar_estado_trabajo().
ORDEN_ESTADOS = [
    EstadoTrabajo.PENDIENTE,
    EstadoTrabajo.PREPARANDO_MATERIALES,
    EstadoTrabajo.LISTO,
    EstadoTrabajo.EN_EJECUCION,
    EstadoTrabajo.TERMINADO,
]


class Trabajo(models.Model):
    """
    Regla de negocio 10: un Trabajo nace de un Presupuesto Aceptado
    (uno solo por presupuesto — OneToOne), heredando cliente (vía la
    relación con Presupuesto, no duplicado), dirección y observaciones
    (copiadas al crear, pero editables después de forma independiente:
    un trabajo puede necesitar una nota operativa distinta de la
    comercial). La creación NO es automática al aceptar el presupuesto
    — es una acción separada y explícita (ver
    apps.jobs.services.crear_trabajo), coherente con el resto del
    proyecto: ninguna transición de estado dispara efectos secundarios
    ocultos sin confirmación humana.
    """

    presupuesto = models.OneToOneField(
        "quotes.Presupuesto", on_delete=models.PROTECT, related_name="trabajo"
    )
    tecnico_asignado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="trabajos_asignados",
    )
    direccion = models.CharField(max_length=255, blank=True)
    observaciones = models.TextField(blank=True)
    estado = models.CharField(
        max_length=30, choices=EstadoTrabajo.choices, default=EstadoTrabajo.PENDIENTE
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="trabajos_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Trabajo"
        verbose_name_plural = "Trabajos"
        permissions = [
            (
                "manage_preparacion",
                "Puede cambiar el estado de un trabajo a Preparando materiales / Listo",
            ),
            (
                "manage_ejecucion_propia",
                "Puede cambiar el estado de sus propios trabajos asignados a En ejecución / Terminado",
            ),
        ]

    def __str__(self):
        return f"Trabajo #{self.pk} — {self.presupuesto.cliente}"


class EtapaTrabajo(models.Model):
    """
    Sub-bloque del listado de materiales, con su propia fecha
    estimada — ej. "1era etapa" (3-4 días) y "2da etapa" (el mismo
    día) dentro de UN solo Trabajo (no uno por etapa, ver Parte 1).
    Se precarga desde SeccionPresupuesto al generar el listado
    (apps.jobs.services.generar_listado_materiales), pero es
    independiente después: se pueden agregar etapas nuevas a mano
    (ej. trabajo extra descubierto en obra) sin sección de origen.
    """

    trabajo = models.ForeignKey(Trabajo, on_delete=models.CASCADE, related_name="etapas")
    titulo = models.CharField(max_length=150)
    seccion_origen = models.ForeignKey(
        "quotes.SeccionPresupuesto",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="etapas_trabajo",
    )
    fecha_estimada = models.DateField(null=True, blank=True)
    duracion_estimada_dias = models.PositiveIntegerField(null=True, blank=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["trabajo_id", "orden"]
        verbose_name = "Etapa de trabajo"
        verbose_name_plural = "Etapas de trabajo"

    def __str__(self):
        return f"{self.trabajo} — {self.titulo}"


class MaterialTrabajo(models.Model):
    """
    Línea del listado de materiales de un Trabajo — distinto de lo que
    ve el cliente en el Presupuesto (regla de negocio 13): se precarga
    desde los ítems del presupuesto de origen al generar el listado,
    pero queda editable después (agregar/quitar/ajustar cantidad) a
    medida que se prepara la obra en la realidad.

    A propósito NO es un ledger append-only como HistorialCosto/
    MovimientoStock: es un plan de trabajo que se corrige libremente,
    no un registro financiero ni el rastro real de lo que salió de
    stock (eso vive en MovimientoStock — ver Parte 3, todavía sin
    campos de cantidad enviada/usada acá, eso es su territorio).

    `producto` es opcional, igual que en ItemPresupuesto — pero por un
    motivo distinto: acá no hay conceptos no físicos como mano de
    obra, es un material real que todavía no está cargado en el
    catálogo. Sin producto, esta línea queda fuera de la conexión con
    Stock de la Parte 3 (no hay contra qué descontar) hasta que se
    cargue el producto real.
    """

    trabajo = models.ForeignKey(Trabajo, on_delete=models.CASCADE, related_name="materiales")
    etapa = models.ForeignKey(
        EtapaTrabajo,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="materiales",
    )
    producto = models.ForeignKey(
        "catalog.Producto",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="materiales_trabajo",
    )
    descripcion_manual = models.CharField(
        max_length=255,
        blank=True,
        help_text="Para un material que todavía no está cargado en el catálogo.",
    )
    item_presupuesto_origen = models.ForeignKey(
        "quotes.ItemPresupuesto",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="materiales_trabajo",
    )
    cantidad_necesaria = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["trabajo_id", "etapa_id", "orden"]
        verbose_name = "Material de trabajo"
        verbose_name_plural = "Materiales de trabajo"

    def __str__(self):
        return self.descripcion_manual or str(self.producto)
