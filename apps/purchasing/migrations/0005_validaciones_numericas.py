# Generated for ARQCLIMA Etapa 10C

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0004_asignar_permisos_roles"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="lineaordencompra",
            constraint=models.CheckConstraint(
                condition=models.Q(("cantidad__gt", 0)),
                name="lineaordencompra_cantidad_positiva",
            ),
        ),
        migrations.AddConstraint(
            model_name="lineaordencompra",
            constraint=models.CheckConstraint(
                condition=models.Q(("costo_esperado__gte", 0)),
                name="lineaordencompra_costo_no_negativo",
            ),
        ),
    ]
