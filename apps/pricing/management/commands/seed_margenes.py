from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.catalog.models import Categoria
from apps.pricing.models import ConfiguracionGeneral

# Valores reales provistos por Diego (no son un ejemplo/placeholder).
# Categorías genéricas por TIPO de producto, no por marca (confirmado
# explícitamente): "Equipos de calefacción" y "Repuestos de calefacción"
# aplican a cualquier marca, no solo a Peisa/Caldaia — si mañana se carga
# una caldera de otra marca, entra en la misma categoría con el mismo
# margen, en vez de necesitar una categoría nueva por marca.
MARGENES_POR_CATEGORIA = {
    "Equipos de calefacción": Decimal("26.00"),
    "Repuestos de calefacción": Decimal("40.00"),
    "Cañería fusión y cobre": Decimal("30.00"),
    "Aislantes de piso": Decimal("20.00"),
}

MARGEN_MANO_OBRA = Decimal("40.00")


class Command(BaseCommand):
    help = (
        "Carga/actualiza los márgenes iniciales por categoría y el margen "
        "de mano de obra con los valores reales que definió Diego. Es "
        "idempotente: correrlo de nuevo no duplica categorías, y SÍ "
        "actualiza el margen si ya existían (para poder ajustar esta lista "
        "y volver a correrlo)."
    )

    def handle(self, *args, **options):
        for nombre, margen in MARGENES_POR_CATEGORIA.items():
            categoria, creada = Categoria.objects.get_or_create(nombre=nombre)
            categoria.margen = margen
            categoria.save()
            accion = "creada" if creada else "actualizada"
            self.stdout.write(
                self.style.SUCCESS(f"Categoría '{nombre}' {accion} con margen {margen}%")
            )

        config = ConfiguracionGeneral.obtener()
        config.margen_mano_obra = MARGEN_MANO_OBRA
        config.save()
        self.stdout.write(
            self.style.SUCCESS(f"Margen de mano de obra actualizado a {MARGEN_MANO_OBRA}%")
        )

        self.stdout.write("")
        self.stdout.write(
            "Estos valores ahora se editan desde la pantalla 'Configuración "
            "de precios' (solo Diego) — este comando es únicamente para la "
            "carga inicial, no hace falta volver a correrlo salvo que "
            "quieras resetear estos valores puntuales a los de arranque."
        )
