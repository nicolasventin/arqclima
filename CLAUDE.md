# ARQCLIMA — Sistema interno de gestión

## Estado de avance del proyecto

**Última actualización: 2026-08-26**, al cierre de la Etapa 5 (rama `feature/etapa-5-clientes-presupuestos`, a punto de mergearse a `main`). Esta sección se actualiza al cerrar cada etapa para que una sesión nueva no tenga que reconstruir el contexto a mano.

### Etapas cerradas

- **Etapa 1 (Base)**: login, usuarios (Diego/Rodrigo/Gabriel/Contri/Andrés) con rol + overrides individuales, guard de permisos, pantalla de administración de permisos, auditoría genérica, dashboard base. `apps.accounts`, `apps.audit`, `apps.dashboard`.
- **Etapa 2 (Catálogo)**: productos (marca+código), marcas, categorías, proveedores, línea de repuestos de Gabriel. `apps.catalog`.
- **Etapa 3 (Precios)**: historial de costos inmutable (trigger de Postgres), márgenes configurables por producto/marca/categoría/general, flete, costo financiero, `margen_mano_obra`. `apps.pricing`.
- **Etapa 4 (Importaciones)**: carga de listas de precios (Excel) con vista previa antes de confirmar. `apps.imports`.
- **Etapa 5 (Clientes + Presupuestos)**: **cerrada en 4 partes** — ver decisiones abajo. `apps.clients`, `apps.quotes`. 40 tests nuevos (81 en total en el proyecto, todos verdes desde una base de datos de test creada de cero).

**Siguiente etapa según el plan original: Etapa 6 (Tareas).**

### Decisiones de diseño puntuales tomadas en el camino (no estaban en la versión original de este archivo)

Reglas de negocio 1-15 más abajo siguen siendo la fuente de verdad general; esto son decisiones concretas de implementación que las completan, tomadas sesión a sesión:

1. **La dirección de obra vive en `Presupuesto`, no en `Cliente`**: un mismo cliente puede pedir presupuestos para distintas direcciones.
2. **`PlantillaCondiciones` es un modelo separado** con `predeterminada` (se precarga en presupuestos nuevos) y `activa` (retirar una plantilla vieja sin borrarla). Un `UniqueConstraint` condicional (`fields=["predeterminada"], condition=Q(predeterminada=True)`) garantiza en la base — no en validación de aplicación — que solo pueda haber una predeterminada a la vez.
3. **`ItemPresupuesto.presupuesto` es obligatorio; `.seccion` es opcional** (no fuerza una sección invisible en presupuestos simples).
4. **`ItemPresupuesto.opcional` e `.incluido` son booleanos independientes**, con un `CheckConstraint` que prohíbe `opcional=False AND incluido=False` (un ítem no opcional no puede quedar excluido del total).
5. **`Presupuesto.numero` sale de una secuencia de Postgres** (`quotes_presupuesto_numero_seq`, vía `db_default` + `nextval`), nunca de `max()+1` — evita choques con creaciones concurrentes.
6. **`iva_pct` se agregó a `pricing.ConfiguracionGeneral`** (junto a flete/financiero) para poder calcular el IVA de ítems con `tipo_iva='+ IVA'`.
7. **`ItemPresupuesto.costo_unitario`** (opcional): costo congelado del ítem. Sin él, el ítem queda fuera del chequeo de margen bajo. Para el concepto manual "Mano de obra", `sugerir_costo_mano_obra()` lo estima a partir de `ConfiguracionGeneral.margen_mano_obra` (Etapa 3) — que hasta la Parte 2 de esta etapa no se usaba en ningún lado.
8. **Cálculo de totales** (`calcular_totales`): el descuento general porcentual se aplica ANTES de multiplicar por `cantidad_unidades`; el de monto fijo se resta UNA sola vez sobre el total ya multiplicado (si se restara antes, un descuento fijo de $100.000 con `cantidad_unidades=3` terminaría restando $300.000).
9. **`margen_item()`** calcula markup sobre costo (mismo criterio que `pricing.services.calcular_precio_venta`, no margen bruto sobre precio), evaluado ítem por ítem, no como un número agregado del presupuesto.
10. **`enviar_presupuesto()`**: un margen bajo NO bloquea el envío (regla 6) pero se audita, en CADA transición a Enviado, incluidos reenvíos tras editar un borrador ya enviado.
11. **Máquina de estados formal** (`TRANSICIONES_VALIDAS` + `cambiar_estado()` en `apps/quotes/services.py`) es el único punto de entrada para mover el estado de un presupuesto:
    - `Borrador → {Enviado, Cancelado}`
    - `Enviado → {Aceptado, Rechazado, Vencido, Cancelado, Borrador}`
    - `Rechazado → {Borrador}`
    - `Vencido → {Borrador}`
    - `Aceptado → {Cancelado}`
    - `Cancelado → {}` (terminal, sin salidas)
12. **`ItemPresupuesto.producto_proveedor`** (nuevo): registra qué proveedor puntual se usó para fijar precio/costo, para que "duplicar y recalcular" pueda refrescar el costo desde ESE proveedor sin auto-elegir uno nuevo (regla de negocio 2).
13. **Trigger de Postgres** bloquea INSERT/UPDATE/DELETE en `SeccionPresupuesto`/`ItemPresupuesto` si el presupuesto dueño no está en Borrador — hay que reabrirlo primero (transición auditada). Mismo criterio que el trigger de `HistorialCosto` en `pricing`: la garantía vive en la base. Efecto secundario detectado: un presupuesto `Cancelado` (terminal) con ítems no se puede borrar ni en cascada, solo queda cancelado para siempre.
14. **Permiso custom `quotes.revert_presupuesto_aceptado`, solo para Administrador**: revertir un Aceptado (→Cancelado) es una decisión de negocio (puerta de entrada a que nazca un Trabajo en Etapa 8), no un trámite de rutina de Rodrigo.
15. **Management command `vencer_presupuestos`**: pasa Enviado→Vencido según `fecha_vencimiento`. No hay scheduler (Celery/etc.) en la infraestructura todavía, se corre por cron del SO o a mano; es idempotente.
16. **`duplicar_presupuesto()`**: nace siempre en Borrador; ítems de catálogo recalculan precio/costo desde el MISMO `producto_proveedor` del original (si no tiene costo cargado, se mantienen los valores congelados para revisar a mano); ítems manuales recalculan el costo con `sugerir_costo_mano_obra()` usando el `margen_mano_obra` ACTUAL; la plantilla de condiciones es la misma del original pero su texto se refresca.
17. **Alta de ítems de catálogo en la UI**: se elige directamente la combinación producto+proveedor en un solo `<select>` (sin cascada de dropdowns ni JavaScript — el proyecto no usa ningún framework de frontend), para no violar la regla de "nunca auto-elegir proveedor". El precio/costo sugerido se trae vía un GET con `?producto_proveedor=<id>` y queda editable antes de guardar.
18. **`xhtml2pdf`** elegido para exportar presupuestos a PDF (puro Python, sin dependencias de sistema como pango/cairo) en vez de WeasyPrint (mejor fidelidad CSS pero requiere librerías nativas) o ReportLab (más control pero sin templates HTML).
19. **Apps separadas por concepto**, mismo criterio que el resto del proyecto: `apps/clients` (Cliente) y `apps/quotes` (Presupuesto/Sección/Ítem/PlantillaCondiciones) — no una única `apps/sales`.

## Contexto

Estoy desarrollando un sistema interno de gestión para ARQCLIMA, una empresa de climatización (venta e instalación de calefacción, piso radiante, calderas, service y repuestos). NO es una web pública — es una herramienta interna para el equipo.

Quiero que armes la base del proyecto y empecemos por la Etapa 1. Antes de escribir código, proponeme el stack técnico y la estructura de carpetas, y esperá mi confirmación.

## Objetivo general del sistema

Centralizar: productos, proveedores, precios/costos, stock, clientes, presupuestos, trabajos y tareas del equipo, todo relacionado entre sí y con trazabilidad de quién hizo cada cosa.

Flujo de negocio completo (para que entiendas el objetivo final, no se construye todo ahora):

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

Fuera de alcance (decisión tomada): **no hay módulo de facturación**. Eso se sigue manejando por fuera del sistema.

## Personas y roles reales del equipo

Estos son los usuarios reales de ARQCLIMA, no roles genéricos. El modelo de permisos debe ser: **cada usuario tiene un rol base con permisos por defecto, y el Administrador puede otorgarle a cualquier usuario permisos puntuales adicionales por fuera de su rol**, sin tener que crear un rol nuevo cada vez. Es decir: permisos = rol base + overrides individuales configurables por el admin.

### Diego — Administrador / Dirección
Venta, visitas de obra, control de stock, coordinación de obras, aprobación de presupuestos y autorización de descuentos, verificación y aprobación de órdenes de compra antes de enviarlas. Acceso total al sistema, incluyendo gestión de usuarios/roles/permisos y auditoría.

### Rodrigo — Ventas y Presupuestos
Maneja presupuestos y venta, crea órdenes de compra, entrega a depósito el listado de materiales a separar para una obra o venta de mostrador. Acceso a: clientes, presupuestos (ciclo completo), catálogo (solo lectura), proveedores (crear fichas y órdenes de compra, no aprobarlas), precios (solo ver costo/venta, no configurar márgenes), trabajos (crear a partir de presupuesto aceptado y ver materiales), stock (solo ver).

### Gabriel — Service y Repuestos
Maneja el service técnico, venta de repuestos, pasa precios de repuestos a los técnicos, controla el stock de repuestos, genera órdenes de compra de repuestos. Es un módulo bastante autónomo y separado del resto del catálogo/obra. Puede crear/editar productos de su línea, gestionar su propio stock de repuestos (entradas y salidas), y marcar material de service como "pendiente de devolución" (para controlar lo que se lleva y no siempre vuelve).

### Contri — Depósito
Entrega material según el listado que le pasan Rodrigo o Diego. Controla stock general (no repuestos). Ve trabajos para saber qué preparar, puede cambiar el estado de un trabajo a "Preparando materiales" / "Listo", registrar salidas de stock y hacer ajustes.

### Andrés — Técnico de campo
Instalación (termostatos, puesta en marcha, pruebas de presión), busca materiales en proveedores locales (puede crear órdenes de compra), lleva material a obra y retira el sobrante que vuelve a stock. Ve sus trabajos asignados, cambia su estado, edita la cantidad de material realmente utilizado (lo enviado se asume usado salvo que él corrija el número), y puede registrar salida/entrada de stock relacionada a sus propios trabajos.

## Reglas de negocio clave (importante que las tengas en cuenta desde el modelo de datos)

1. **Identificación de productos**: Marca + Código oficial de la marca. No se inventa un código propio de ARQCLIMA.
2. **Multi-proveedor**: un mismo producto puede tener varios proveedores con distintos costos. El sistema debe poder mostrar cuál es el más conveniente, pero no asumir automáticamente cuál usar.
3. **Historial de precios**: nunca se borra un costo viejo, se guarda como historial con fecha y proveedor.
4. **Importación de listas de proveedores** (Excel primero, después PDF/Word): nunca se actualizan productos automáticamente sin mostrar antes una vista previa de los cambios (nuevos, existentes, para revisar, errores) para que el usuario confirme.
5. **Márgenes configurables**: general, por categoría, por marca o por producto específico. Se calcula: costo + flete + costo financiero/tarjeta + margen = precio de venta. Solo Diego configura márgenes.
6. **Margen bajo por descuento**: si un descuento deja el margen por debajo del mínimo configurado, el sistema muestra una alerta visual (⚠️), pero NO bloquea el envío del presupuesto. Se registra en auditoría igual (queda constancia de quién lo envió con margen bajo).
7. **Órdenes de compra con aprobación**: Rodrigo, Gabriel y Andrés pueden crear órdenes de compra, pero Diego debe aprobarlas antes de que se envíen al proveedor. Esto SÍ es un bloqueo real (a diferencia del punto anterior sobre márgenes).
8. **Presupuestos con precios congelados**: una vez creado, el presupuesto conserva los precios del momento aunque después cambien los costos. Se puede duplicar y recalcular con precios actuales si se quiere.
9. **Estructura simplificada de presupuesto** (sin "etapas" rígidas como entidad del sistema):
   - Un presupuesto tiene **secciones opcionales** (agrupadores con título libre, ej. "1era etapa", "2da etapa", o ninguna si es simple).
   - Cada sección tiene **ítems** (producto de catálogo o concepto manual como mano de obra/instalación), con cantidad, precio unitario, descuento y un campo simple de **tipo de IVA** (incluido / + IVA).
   - Ítems pueden marcarse como **opcionales** (no suman al total salvo que se activen) — para alternativas tipo "mejora recomendada".
   - Campo de **cantidad de unidades** a nivel presupuesto, para multiplicar el total cuando se cotiza "por unidad habitacional".
   - **Notas generales** en texto libre.
   - **Bloque de condiciones** (exclusiones, garantía, forma de pago) precargado desde una plantilla editable por presupuesto.
   - Descuento general (% o monto fijo) y descuento por ítem.
   - Estados: Borrador, Enviado, Aceptado, Rechazado, Vencido, Cancelado.
10. **Trabajos nacen de presupuestos aceptados**, heredando cliente, dirección, productos y observaciones. Estados: Pendiente, Preparando materiales, Listo, En ejecución, Terminado.
11. **Material enviado vs. utilizado**: al enviar materiales a un trabajo, el sistema asume que se usó todo. El técnico solo edita el número si sobró algo, y el sobrante puede volver a stock. Para repuestos de service, hay un control más estricto: el material queda "pendiente de devolución" hasta que se registre qué volvió.
12. **Stock separado**: stock general de obra (Contri) vs. stock de repuestos de service (Gabriel), son controles distintos aunque puedan compartir el mismo catálogo de productos.
13. **Auditoría**: cada acción importante (crear presupuesto, cargar lista de precios, modificar stock, cambiar margen, aprobar orden de compra, etc.) queda registrada con usuario, acción y fecha/hora.
14. **Tareas/To-Do**: asignables a empleados (Diego, Rodrigo y Gabriel pueden asignar), con fecha límite, prioridad y estado (Pendiente → En proceso → Completada). Cada empleado ve "Mis tareas".
15. **Permisos con overrides**: además del permiso base por rol, el Administrador (Diego) debe poder otorgar permisos puntuales adicionales a cualquier usuario sin cambiarle el rol. El modelo de datos de permisos tiene que contemplar esto desde el diseño (rol → permisos por defecto, y una tabla de excepciones/overrides por usuario).

## Cómo lo vamos a construir (por etapas — hoy arrancamos con la Etapa 1)

1. **Base**: login, usuarios, roles/permisos (con overrides por usuario), auditoría.
2. **Catálogo**: productos, marcas, categorías, proveedores (incluye línea de repuestos de Gabriel).
3. **Precios**: costos, márgenes, flete, financiero, descuentos, historial de precios.
4. **Importaciones**: Excel primero, después PDF y Word.
5. **Clientes + presupuestos**: presupuestador completo, descuentos, exportar a PDF.
6. **Tareas**: asignación y seguimiento por empleado.
7. **Stock**: entradas, salidas, ajustes, alertas de stock mínimo (stock general y stock de repuestos).
8. **Trabajos**: presupuesto aceptado → trabajo → materiales → consumo real. Incluye órdenes de compra con aprobación de Diego.
9. **Reportes y automatizaciones**: dashboards, seguimiento automático de presupuestos, métricas comerciales/rentabilidad/stock/clientes/empleados.

## Lo que necesito que hagas ahora (Etapa 1)

1. Proponeme un stack técnico simple y mantenible (pensá en que soy quien más lo va a mantener, priorizá algo estándar y bien documentado antes que algo exótico). Necesito: backend + base de datos relacional + frontend + autenticación.
2. Proponeme la estructura de carpetas del proyecto.
3. Una vez que confirme, implementá:
   - Login con usuario/contraseña.
   - Modelo de usuarios con los 5 usuarios reales (Diego, Rodrigo, Gabriel, Contri, Andrés) y su rol correspondiente.
   - Modelo de permisos: rol base + tabla de overrides individuales por usuario (para que el admin pueda sumarle permisos puntuales a alguien sin cambiarle el rol).
   - Middleware/guard de permisos que chequee rol + overrides.
   - Pantalla de administración simple donde Diego pueda ver y tildar permisos por usuario (base para más adelante, no hace falta que sea sofisticada).
   - Tabla y lógica de auditoría genérica (usuario, acción, entidad afectada, detalle, fecha) reutilizable desde cualquier módulo futuro.
   - Un dashboard vacío/base por usuario, donde después conectemos los widgets según lo definido en la sección de roles de arriba.
4. No implementes todavía productos, presupuestos, stock, etc. — eso es de etapas posteriores. Enfocate solo en Etapa 1.

Antes de programar, decime si tenés alguna duda sobre el modelo de datos, los roles o las reglas de negocio de arriba.