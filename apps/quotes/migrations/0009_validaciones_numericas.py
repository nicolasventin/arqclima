# Generated for ARQCLIMA Etapa 10C

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0008_itempresupuesto_itempresupuesto_producto_xor_descripcion_manual"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="presupuesto",
            constraint=models.CheckConstraint(
                condition=models.Q(("cantidad_unidades__gt", 0)),
                name="presupuesto_cantidad_unidades_positiva",
            ),
        ),
        migrations.AddConstraint(
            model_name="presupuesto",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("descuento_general_tipo", "porcentaje"),
                        ("descuento_general_valor__gte", 0),
                        ("descuento_general_valor__lte", 100),
                    ),
                    models.Q(
                        ("descuento_general_tipo", "monto"),
                        ("descuento_general_valor__gte", 0),
                    ),
                    _connector="OR",
                ),
                name="presupuesto_descuento_general_valido",
            ),
        ),
        migrations.AddConstraint(
            model_name="itempresupuesto",
            constraint=models.CheckConstraint(
                condition=models.Q(("cantidad__gt", 0)),
                name="itempresupuesto_cantidad_positiva",
            ),
        ),
        migrations.AddConstraint(
            model_name="itempresupuesto",
            constraint=models.CheckConstraint(
                condition=models.Q(("precio_unitario__gte", 0)),
                name="itempresupuesto_precio_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="itempresupuesto",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("costo_unitario__isnull", True),
                    ("costo_unitario__gte", 0),
                    _connector="OR",
                ),
                name="itempresupuesto_costo_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="itempresupuesto",
            constraint=models.CheckConstraint(
                condition=models.Q(("descuento_pct__gte", 0), ("descuento_pct__lte", 100)),
                name="itempresupuesto_descuento_pct_0_100",
            ),
        ),
    ]
