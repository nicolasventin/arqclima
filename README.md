# ARQCLIMA — Sistema interno de gestión

Sistema interno de gestión para ARQCLIMA (venta e instalación de calefacción,
piso radiante, calderas, service y repuestos). Django + PostgreSQL. No es una
web pública.

Para el contexto completo del proyecto — stack, estado de avance por etapa,
reglas de negocio y decisiones de diseño — ver `CLAUDE.md`.

## Comandos periódicos (cron)

El proyecto **no tiene ningún scheduler propio** (Celery, django-crontab,
etc.) todavía — es una decisión tomada en la Etapa 5 y sostenida desde
entonces. Los siguientes management commands están escritos para correrse
solos por cron del sistema operativo (o a mano si hace falta): todos son
**idempotentes** — correrlos de más, o dos veces seguidas, no duplica nada ni
rompe datos.

**Nadie los tiene programados todavía.** Al desplegar a producción, hay que
darlos de alta en el crontab del servidor (o el mecanismo equivalente si se
usa otro hosting) con esta frecuencia recomendada:

| Comando | Qué hace | Frecuencia recomendada |
|---|---|---|
| `vencer_presupuestos` | Pasa a Vencido los presupuestos Enviados cuya `fecha_vencimiento` ya pasó. | Diaria (ej. 06:00) |
| `generar_seguimiento_presupuestos` | Crea una tarea de seguimiento para presupuestos Enviados sin respuesta hace N días (`ConfiguracionGeneral.dias_seguimiento_presupuesto_enviado`, editable por Diego). | Diaria (ej. 06:05, después de `vencer_presupuestos`) |
| `avisar_presupuestos_por_vencer` | Avisa al vendedor cuando un presupuesto Enviado está por vencer (`ConfiguracionGeneral.dias_aviso_presupuesto_por_vencer`). | Diaria (ej. 06:10) |
| `generar_tareas_stock_minimo` | Genera una tarea de reposición por cada producto+depósito por debajo de su stock mínimo configurado. | Diaria (ej. 06:15) — no hace falta más seguido: el stock no cambia tan rápido como para justificarlo |

Los tres primeros comparten razón de ser (dependen de `fecha_vencimiento`/
`AuditLog` que no cambian salvo una vez al día en la práctica de la empresa)
y conviene correrlos en ese orden relativo (`vencer_presupuestos` antes de
`generar_seguimiento_presupuestos`), aunque ninguno depende estrictamente del
resultado del anterior.

Ejemplo de entrada de crontab (ajustar la ruta al `venv` y al proyecto):

```cron
0  6 * * * cd /ruta/al/proyecto && venv/bin/python manage.py vencer_presupuestos >> /var/log/arqclima/cron.log 2>&1
5  6 * * * cd /ruta/al/proyecto && venv/bin/python manage.py generar_seguimiento_presupuestos >> /var/log/arqclima/cron.log 2>&1
10 6 * * * cd /ruta/al/proyecto && venv/bin/python manage.py avisar_presupuestos_por_vencer >> /var/log/arqclima/cron.log 2>&1
15 6 * * * cd /ruta/al/proyecto && venv/bin/python manage.py generar_tareas_stock_minimo >> /var/log/arqclima/cron.log 2>&1
```
