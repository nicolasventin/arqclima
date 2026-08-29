from django.db import migrations, models


def marcar_envios_historicos(apps, schema_editor):
    OrdenDeCompra = apps.get_model("purchasing", "OrdenDeCompra")
    OrdenDeCompra.objects.filter(enviada_en__isnull=False).update(estado_envio="enviado")


def revertir_estado_envio(apps, schema_editor):
    OrdenDeCompra = apps.get_model("purchasing", "OrdenDeCompra")
    OrdenDeCompra.objects.update(estado_envio="pendiente")


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0007_flujo_sin_aprobacion"),
    ]

    operations = [
        migrations.AddField(
            model_name="ordendecompra",
            name="enviada_a",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="estado_envio",
            field=models.CharField(
                choices=[
                    ("pendiente", "Pendiente"),
                    ("enviando", "Enviando"),
                    ("enviado", "Enviado"),
                    ("error", "Error"),
                ],
                default="pendiente",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="ultimo_intento_envio_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="ultimo_error_envio",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="ordendecompra",
            name="pdf_generado",
            field=models.FileField(blank=True, upload_to="ordenes_compra/"),
        ),
        migrations.RunPython(marcar_envios_historicos, revertir_estado_envio),
    ]
