# ARQCLIMA — Sistema interno de gestión

## Qué es esto

ARQCLIMA es una empresa de climatización: vende e instala calefacción, piso
radiante y calderas, y hace service y venta de repuestos. Este proyecto es
la herramienta interna que usa el equipo para llevar todo eso ordenado y
relacionado — **no es una web pública**, nadie fuera del equipo la usa ni la
ve.

Antes de este sistema, cada cosa vivía por su lado (planillas, cuadernos,
mensajes sueltos): qué costó un producto, quién le vendió qué a quién, qué
quedó pendiente de un presupuesto, cuánto material salió a una obra y si
volvió. El objetivo es centralizar todo eso — productos, proveedores,
precios y costos, stock, clientes, presupuestos, trabajos y tareas del
equipo — con trazabilidad real de quién hizo cada cosa y cuándo.

No incluye facturación: eso se sigue manejando por fuera del sistema, a
propósito (decisión tomada desde el arranque del proyecto).

## El flujo de negocio, de punta a punta

```
Proveedor manda lista de precios
        ↓
Sistema actualiza costos (revisión manual antes de confirmar)
        ↓
Precios de venta se recalculan (costo + flete + financiero + margen)
        ↓
Cliente pide cotización
        ↓
Se arma un presupuesto (con posible descuento)
        ↓
Presupuesto aceptado
        ↓
Se crea un trabajo a partir del presupuesto
        ↓
Se genera el listado de materiales necesarios
        ↓
Se preparan y envían materiales (se descuentan del stock)
        ↓
Ejecución del trabajo
        ↓
Se corrige si sobró material (vuelve a stock)
        ↓
Trabajo terminado
```

Encima de ese flujo, dos cosas transversales: **órdenes de compra** (cuando
falta reponer stock o comprar algo puntual para una obra, con aprobación
obligatoria de Diego antes de enviarlas al proveedor) y **tareas** del
equipo, algunas manuales y algunas que el sistema genera solo (ver más
abajo).

## Quién lo usa

Cinco personas reales, cada una con un rol fijo y permisos por defecto que
Diego puede ampliar puntualmente sin cambiarle el rol a nadie:

| Persona | Rol | Qué hace en el sistema |
|---|---|---|
| Diego | Administrador | Acceso total: usuarios/permisos, aprueba presupuestos y órdenes de compra, configura márgenes y stock mínimo, auditoría. |
| Rodrigo | Ventas y Presupuestos | Clientes, presupuestos de punta a punta, crea órdenes de compra, pide materiales a depósito. |
| Gabriel | Service y Repuestos | Línea de repuestos casi autónoma: catálogo propio, stock de repuestos, precios a técnicos, sus propias órdenes de compra. |
| Contri | Depósito | Controla stock general (no repuestos), prepara materiales para los trabajos, entradas/salidas/ajustes. |
| Andrés | Técnico de campo | Instalación en obra, sus trabajos asignados, retira/devuelve material sobrante, puede comprar en proveedores locales. |

El detalle completo de reglas de negocio y permisos por rol está en
`CLAUDE.md`.

## Stack técnico

- **Backend**: Django 5.2 (Python)
- **Base de datos**: PostgreSQL — varias garantías del proyecto (historial
  de costos inmutable, stock como ledger append-only, numeración de
  presupuestos/órdenes) viven en triggers y constraints de la base, no solo
  en el código de Django.
- **Frontend**: templates de Django + Bootstrap, sin framework de
  JavaScript — el proyecto lo mantiene una sola persona y prioriza algo
  simple y estándar por sobre algo moderno pero más complejo.
- **Autenticación**: la de Django, con roles (`django.contrib.auth.Group`)
  + overrides individuales de permisos por usuario.
- Excel de listas de precios: `openpyxl`. PDF de presupuestos: `xhtml2pdf`.

## Estructura del proyecto

Una app de Django por concepto de negocio, no por capa técnica:

- `apps/accounts` — usuarios, roles, permisos y sus overrides individuales.
- `apps/audit` — auditoría genérica (quién hizo qué, cuándo), reutilizada por el resto de las apps.
- `apps/dashboard` — pantalla de inicio por usuario.
- `apps/catalog` — productos, marcas, categorías, proveedores.
- `apps/pricing` — costos, historial de costos, márgenes, configuración general (flete, financiero, IVA, umbrales de las automatizaciones).
- `apps/imports` — carga de listas de precios de proveedores (Excel) con vista previa antes de confirmar.
- `apps/clients` — clientes.
- `apps/quotes` — presupuestos (secciones, ítems, plantillas de condiciones, máquina de estados).
- `apps/tasks` — tareas del equipo, manuales y generadas automáticamente.
- `apps/stock` — movimientos de stock (ledger), separado por depósito (general / repuestos).
- `apps/jobs` — trabajos (nacen de un presupuesto aceptado), listado de materiales, consumo real.
- `apps/purchasing` — órdenes de compra, con aprobación de Diego.

El estado de avance por etapa, con todas las decisiones de diseño tomadas en
el camino, está documentado en `CLAUDE.md` — es la fuente de verdad para
retomar el proyecto sin tener que reconstruir el contexto a mano.

## Cómo levantarlo en local

1. Base de datos (Postgres vía Docker):
   ```
   docker compose up -d
   ```
2. Entorno virtual y dependencias:
   ```
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Variables de entorno: copiar `.env.example` a `.env` y ajustar si hace
   falta (los valores por defecto ya coinciden con el `docker-compose.yml`).
4. Migraciones:
   ```
   python manage.py migrate
   ```
5. Datos iniciales — roles + los 5 usuarios reales (idempotente; genera
   contraseñas al azar en `credenciales.txt`, no versionado) y los márgenes
   por categoría de arranque:
   ```
   python manage.py seed_users
   python manage.py seed_margenes
   ```
6. Levantar el servidor:
   ```
   python manage.py runserver
   ```

Diego se crea como superusuario (`is_superuser=True`), así que también tiene
acceso al admin de Django en `/admin/` además de las pantallas propias del
sistema.

## Tests

```
python manage.py test
```

Usa una base de datos de test separada (se crea y se destruye sola). Para
reusarla entre corridas mientras se está iterando: `python manage.py test --keepdb`.

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
