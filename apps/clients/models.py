from django.db import models


class Cliente(models.Model):
    """
    La dirección NO vive acá: un mismo cliente puede pedir presupuestos
    para distintas obras/direcciones, así que la dirección es un campo
    propio de cada Presupuesto (ver apps.quotes.models.Presupuesto).
    """

    class Tipo(models.TextChoices):
        PARTICULAR = "particular", "Particular"
        EMPRESA = "empresa", "Empresa"

    nombre = models.CharField(max_length=255)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.PARTICULAR)
    cuit_dni = models.CharField(max_length=20, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    notas = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"

    def __str__(self):
        return self.nombre
