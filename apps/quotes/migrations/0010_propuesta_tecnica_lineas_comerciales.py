from django.db import migrations, models
import django.db.models.deletion


CREAR_TRIGGER_SQL = """
CREATE TRIGGER lineacomercial_bloquear_fuera_de_borrador
BEFORE INSERT OR UPDATE OR DELETE ON quotes_lineacomercialpresupuesto
FOR EACH ROW EXECUTE FUNCTION quotes_bloquear_edicion_fuera_de_borrador();
"""

ELIMINAR_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS lineacomercial_bloquear_fuera_de_borrador
ON quotes_lineacomercialpresupuesto;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("quotes", "0009_validaciones_numericas"),
    ]

    operations = [
        migrations.AddField(
            model_name="presupuesto",
            name="obra",
            field=models.CharField(
                blank=True,
                help_text="Nombre del proyecto u obra, por ejemplo 'Proyecto 3 casas'.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="referencia",
            field=models.TextField(
                blank=True,
                help_text="Resumen comercial de lo que se está cotizando.",
            ),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="titulo_propuesta",
            field=models.CharField(
                blank=True,
                help_text="Título visible del alcance, por ejemplo 'PISO RADIANTE'.",
                max_length=180,
            ),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="alcance_tecnico",
            field=models.TextField(
                blank=True,
                help_text="Descripción técnica general visible para el cliente.",
            ),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="notas_cliente",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="forma_pago",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="garantia",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="exclusiones",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="firma_texto",
            field=models.CharField(
                blank=True,
                help_text="Responsable visible al pie del PDF, por ejemplo 'Arq. Diego Ventin Ponte'.",
                max_length=180,
            ),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="importes_por_unidad",
            field=models.BooleanField(
                default=False,
                help_text="Los importes comerciales se expresan por unidad/casa.",
            ),
        ),
        migrations.AddField(
            model_name="presupuesto",
            name="mostrar_total_general",
            field=models.BooleanField(
                default=True,
                help_text="Mostrar o no un total general en el PDF del cliente.",
            ),
        ),
        migrations.AddField(
            model_name="seccionpresupuesto",
            name="descripcion_publica",
            field=models.TextField(
                blank=True,
                help_text="Un punto técnico por línea. Se muestra en el PDF del cliente.",
            ),
        ),
        migrations.CreateModel(
            name="LineaComercialPresupuesto",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("etiqueta", models.CharField(max_length=150)),
                ("descripcion", models.CharField(blank=True, max_length=255)),
                ("monto", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "tipo_iva",
                    models.CharField(
                        choices=[
                            ("incluido", "IVA incluido"),
                            ("mas_iva", "+ IVA"),
                        ],
                        default="incluido",
                        max_length=20,
                    ),
                ),
                ("opcional", models.BooleanField(default=False)),
                ("incluido", models.BooleanField(default=True)),
                ("recomendado", models.BooleanField(default=False)),
                ("orden", models.PositiveIntegerField(default=0)),
                (
                    "presupuesto",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lineas_comerciales",
                        to="quotes.presupuesto",
                    ),
                ),
                (
                    "seccion",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lineas_comerciales",
                        to="quotes.seccionpresupuesto",
                    ),
                ),
            ],
            options={
                "verbose_name": "Línea comercial de presupuesto",
                "verbose_name_plural": "Líneas comerciales de presupuesto",
                "ordering": ["presupuesto_id", "seccion_id", "orden", "pk"],
            },
        ),
        migrations.AddConstraint(
            model_name="lineacomercialpresupuesto",
            constraint=models.CheckConstraint(
                condition=models.Q(("monto__gte", 0)),
                name="lineacomercial_monto_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="lineacomercialpresupuesto",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("opcional", True)),
                    models.Q(("incluido", True)),
                    _connector="OR",
                ),
                name="lineacomercial_no_opcional_y_no_incluida",
            ),
        ),
        migrations.RunSQL(
            sql=CREAR_TRIGGER_SQL,
            reverse_sql=ELIMINAR_TRIGGER_SQL,
        ),
    ]
