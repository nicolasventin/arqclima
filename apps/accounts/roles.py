"""
Roles reales del equipo de ARQCLIMA. Cada usuario pertenece a exactamente
uno de estos roles, modelado con el Group nativo de Django. El administrador
puede además otorgarle a cualquier usuario permisos individuales puntuales
por fuera de su rol (ver User.user_permissions y la pantalla de permisos).
"""

ADMINISTRADOR = "Administrador"
VENTAS_Y_PRESUPUESTOS = "Ventas y Presupuestos"
SERVICE_Y_REPUESTOS = "Service y Repuestos"
DEPOSITO = "Depósito"
TECNICO_DE_CAMPO = "Técnico de Campo"

ROLES = [
    ADMINISTRADOR,
    VENTAS_Y_PRESUPUESTOS,
    SERVICE_Y_REPUESTOS,
    DEPOSITO,
    TECNICO_DE_CAMPO,
]
