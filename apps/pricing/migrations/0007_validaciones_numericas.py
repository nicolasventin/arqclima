# Generated for ARQCLIMA Etapa 10C

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pricing", "0006_configuraciongeneral_dias_aviso_presupuesto_por_vencer_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="historialcosto",
            constraint=models.CheckConstraint(
                condition=models.Q(("costo__gte", 0)),
                name="historialcosto_costo_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="configuraciongeneral",
            constraint=models.CheckConstraint(
                condition=models.Q(("margen_general__gte", 0)),
                name="config_margen_general_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="configuraciongeneral",
            constraint=models.CheckConstraint(
                condition=models.Q(("margen_mano_obra__gte", 0)),
                name="config_margen_mano_obra_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="configuraciongeneral",
            constraint=models.CheckConstraint(
                condition=models.Q(("flete_pct__gte", 0)),
                name="config_flete_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="configuraciongeneral",
            constraint=models.CheckConstraint(
                condition=models.Q(("costo_financiero_pct__gte", 0)),
                name="config_costo_financiero_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="configuraciongeneral",
            constraint=models.CheckConstraint(
                condition=models.Q(("margen_minimo_alerta__gte", 0)),
                name="config_margen_alerta_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="configuraciongeneral",
            constraint=models.CheckConstraint(
                condition=models.Q(("iva_pct__gte", 0), ("iva_pct__lte", 100)),
                name="config_iva_pct_0_100",
            ),
        ),
        migrations.AddConstraint(
            model_name="configuraciongeneral",
            constraint=models.CheckConstraint(
                condition=models.Q(("dias_seguimiento_presupuesto_enviado__gt", 0)),
                name="config_dias_seguimiento_positivo",
            ),
        ),
        migrations.AddConstraint(
            model_name="configuraciongeneral",
            constraint=models.CheckConstraint(
                condition=models.Q(("dias_aviso_presupuesto_por_vencer__gt", 0)),
                name="config_dias_aviso_positivo",
            ),
        ),
    ]
