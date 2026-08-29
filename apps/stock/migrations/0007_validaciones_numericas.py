# Generated for ARQCLIMA Etapa 10C

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stock", "0006_revoca_stock_general_tecnico_campo"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="movimientostock",
            name="movimientostock_signo_coherente_con_tipo",
        ),
        migrations.AddConstraint(
            model_name="movimientostock",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("cantidad__gt", 0), ("tipo", "entrada")),
                    models.Q(("cantidad__lt", 0), ("tipo", "salida")),
                    models.Q(("cantidad__gt", 0), ("tipo", "devolucion")),
                    models.Q(("cantidad__gt", 0), ("tipo", "ajuste")),
                    models.Q(("cantidad__lt", 0), ("tipo", "ajuste")),
                    _connector="OR",
                ),
                name="movimientostock_signo_coherente_con_tipo",
            ),
        ),
    ]
