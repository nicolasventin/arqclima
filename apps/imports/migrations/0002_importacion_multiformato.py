from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("imports", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="importacionlistaprecios",
            name="advertencias_analisis",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="importacionlistaprecios",
            name="analizado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="importacionlistaprecios",
            name="estado_analisis",
            field=models.CharField(
                choices=[
                    ("completo", "Analizado"),
                    ("requiere_revision", "Requiere revisión"),
                ],
                default="completo",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="importacionlistaprecios",
            name="tipo_archivo",
            field=models.CharField(
                choices=[
                    ("xlsx", "Excel (.xlsx)"),
                    ("xls", "Excel antiguo (.xls)"),
                    ("csv", "CSV"),
                    ("pdf", "PDF"),
                    ("docx", "Word (.docx)"),
                ],
                default="xlsx",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="importacionfila",
            name="categoria_texto",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="importacionfila",
            name="confianza",
            field=models.CharField(
                choices=[
                    ("alta", "Alta"),
                    ("media", "Media"),
                    ("baja", "Baja"),
                    ("revisada", "Revisada por usuario"),
                ],
                default="alta",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="importacionfila",
            name="descripcion_texto",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="importacionfila",
            name="origen",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="importacionfila",
            name="unidad_texto",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AlterField(
            model_name="importacionfila",
            name="costo_texto",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name="importacionfila",
            name="detalle",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AlterModelOptions(
            name="importacionfila",
            options={
                "ordering": ["origen", "numero_fila", "pk"],
                "verbose_name": "Fila de importación",
                "verbose_name_plural": "Filas de importación",
            },
        ),
        migrations.CreateModel(
            name="ImportacionImagen",
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
                (
                    "archivo",
                    models.ImageField(upload_to="importaciones/imagenes/%Y/%m/"),
                ),
                ("origen", models.CharField(blank=True, max_length=150)),
                (
                    "numero_fila_origen",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                (
                    "nombre_original",
                    models.CharField(blank=True, max_length=255),
                ),
                ("ancho", models.PositiveIntegerField(blank=True, null=True)),
                ("alto", models.PositiveIntegerField(blank=True, null=True)),
                ("huella_sha256", models.CharField(max_length=64)),
                (
                    "importacion",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="imagenes",
                        to="imports.importacionlistaprecios",
                    ),
                ),
            ],
            options={
                "ordering": ["origen", "numero_fila_origen", "pk"],
                "verbose_name": "Imagen de importación",
                "verbose_name_plural": "Imágenes de importación",
            },
        ),
        migrations.AddConstraint(
            model_name="importacionimagen",
            constraint=models.UniqueConstraint(
                fields=("importacion", "huella_sha256"),
                name="imports_imagen_unica_por_importacion",
            ),
        ),
    ]
