from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quotes", "0010_propuesta_tecnica_lineas_comerciales"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="lineacomercialpresupuesto",
            name="lineacomercial_no_opcional_y_no_incluida",
        ),
        migrations.AlterField(
            model_name="presupuesto",
            name="direccion",
            field=models.CharField(
                blank=True,
                help_text="Ubicación/dirección de la obra para este presupuesto puntual.",
                max_length=255,
            ),
        ),
        migrations.AddConstraint(
            model_name="lineacomercialpresupuesto",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("opcional", True),
                    ("incluido", True),
                    _connector="OR",
                ),
                name="lineacomercial_no_opcional_y_no_incluida",
            ),
        ),
    ]
