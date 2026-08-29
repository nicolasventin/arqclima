# Generated for ARQCLIMA Etapa 10C

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_producto_stock_minimo_general_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="marca",
            constraint=models.CheckConstraint(
                condition=models.Q(("margen__isnull", True), ("margen__gte", 0), _connector="OR"),
                name="marca_margen_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="categoria",
            constraint=models.CheckConstraint(
                condition=models.Q(("margen__isnull", True), ("margen__gte", 0), _connector="OR"),
                name="categoria_margen_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="producto",
            constraint=models.CheckConstraint(
                condition=models.Q(("margen__isnull", True), ("margen__gte", 0), _connector="OR"),
                name="producto_margen_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="producto",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("stock_minimo_general__isnull", True),
                    ("stock_minimo_general__gte", 0),
                    _connector="OR",
                ),
                name="producto_stock_min_general_no_negativo",
            ),
        ),
        migrations.AddConstraint(
            model_name="producto",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("stock_minimo_repuestos__isnull", True),
                    ("stock_minimo_repuestos__gte", 0),
                    _connector="OR",
                ),
                name="producto_stock_min_repuestos_no_negativo",
            ),
        ),
    ]
