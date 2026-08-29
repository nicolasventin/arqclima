# Generated for ARQCLIMA Etapa 10C

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0005_materialtrabajo_materialtrabajo_producto_xor_descripcion_manual"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="etapatrabajo",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("duracion_estimada_dias__isnull", True),
                    ("duracion_estimada_dias__gt", 0),
                    _connector="OR",
                ),
                name="etapatrabajo_duracion_positiva",
            ),
        ),
        migrations.AddConstraint(
            model_name="materialtrabajo",
            constraint=models.CheckConstraint(
                condition=models.Q(("cantidad_necesaria__gt", 0)),
                name="materialtrabajo_cantidad_positiva",
            ),
        ),
    ]
