# ARQCLIMA — Sistema interno de gestión

## Estado de avance del proyecto

**Última actualización: 2026-08-28**, al cierre de la Etapa 8 (rama `feature/etapa-8-trabajos`, todavía sin mergear a `main`). Esta sección se actualiza al cerrar cada etapa para que una sesión nueva no tenga que reconstruir el contexto a mano.

### Etapas cerradas

- **Etapa 1 (Base)**: login, usuarios (Diego/Rodrigo/Gabriel/Contri/Andrés) con rol + overrides individuales, guard de permisos, pantalla de administración de permisos, auditoría genérica, dashboard base. `apps.accounts`, `apps.audit`, `apps.dashboard`.
- **Etapa 2 (Catálogo)**: productos (marca+código), marcas, categorías, proveedores, línea de repuestos de Gabriel. `apps.catalog`.
- **Etapa 3 (Precios)**: historial de costos inmutable (trigger de Postgres), márgenes configurables por producto/marca/categoría/general, flete, costo financiero, `margen_mano_obra`. `apps.pricing`.
- **Etapa 4 (Importaciones)**: carga de listas de precios (Excel) con vista previa antes de confirmar. `apps.imports`.
- **Etapa 5 (Clientes + Presupuestos)**: **cerrada en 4 partes** — ver decisiones abajo. `apps.clients`, `apps.quotes`. Mergeada a `main` en `e2ffc05` (incluye el fix de `revert_presupuesto_aceptado` en `7542e76`).
- **Etapa 6 (Tareas)**: modelo `Tarea` (regla 14), máquina de estados propia, permisos y alcance de visibilidad por rol, integrada al dashboard de la Etapa 1. `apps.tasks`. Mergeada a `main` en `80f4750`. Ver decisiones abajo.
- **Etapa 7 (Stock)**: modelo `MovimientoStock` (reglas 11-12), ledger append-only, "pendiente de devolución" derivado del ledger, alertas de stock mínimo. `apps.stock`. Ver decisiones abajo.
- **Etapa 8 (Trabajos)**: **cerrada en 4 partes** — ver decisiones abajo. `apps.jobs` (Trabajo/EtapaTrabajo/MaterialTrabajo) + `apps.purchasing` (OrdenDeCompra/LineaOrdenCompra, regla de negocio 7). Cierra las dos notas pendientes que había dejado la Etapa 7: `MovimientoStock` ganó FKs reales `trabajo`/`material_trabajo`/`orden_compra`/`linea_orden_compra` (decisiones 35 y 48; nunca se usó `GenericForeignKey`), y el alcance de Andrés en stock general quedó **parcialmente** resuelto, no cerrado del todo — ver decisión 42bis, es la única nota que pasa a la Etapa 9. **264 tests en total en el proyecto, todos verdes desde una base de datos de test creada de cero.**

**Siguiente etapa según el plan original: Etapa 9 (Reportes y automatizaciones) — la última del plan original.**

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
14. **Permiso custom `quotes.revert_presupuesto_aceptado`, solo para Administrador**: revertir un Aceptado (→Cancelado, la única salida de Aceptado en el grafo — no hay `Aceptado→Borrador`) es una decisión de negocio (puerta de entrada a que nazca un Trabajo en Etapa 8), no un trámite de rutina de Rodrigo.
    - **Bug encontrado y corregido al revisar antes de mergear**: como `Aceptado→Cancelado` está en `TRANSICIONES_VALIDAS`, esa misma transición también era alcanzable desde `CancelarPresupuestoView` (el botón genérico "Cancelar", gateado solo por `change_presupuesto`, que Rodrigo también tiene) sin pasar por `revert_presupuesto_aceptado` — Rodrigo podía revertir un Aceptado pegándole directo a `/presupuestos/<pk>/cancelar/`, esquivando el permiso pensado para Diego. Fix: `CancelarPresupuestoView.test_func()` ahora exige además `self.presupuesto.estado != EstadoPresupuesto.ACEPTADO` (tests de regresión en `PresupuestoViewsTests`/`RevertirAceptadoPermisosTests`). Lección: cuando dos vistas distintas pueden llegar al mismo par (origen, destino) del grafo, cada una necesita su propio chequeo de permiso — `cambiar_estado()` valida el grafo, pero no "quién puede disparar cuál transición desde qué vista".
15. **Management command `vencer_presupuestos`**: pasa Enviado→Vencido según `fecha_vencimiento`. No hay scheduler (Celery/etc.) en la infraestructura todavía, se corre por cron del SO o a mano; es idempotente.
16. **`duplicar_presupuesto()`**: nace siempre en Borrador; ítems de catálogo recalculan precio/costo desde el MISMO `producto_proveedor` del original (si no tiene costo cargado, se mantienen los valores congelados para revisar a mano); ítems manuales recalculan el costo con `sugerir_costo_mano_obra()` usando el `margen_mano_obra` ACTUAL; la plantilla de condiciones es la misma del original pero su texto se refresca.
17. **Alta de ítems de catálogo en la UI**: se elige directamente la combinación producto+proveedor en un solo `<select>` (sin cascada de dropdowns ni JavaScript — el proyecto no usa ningún framework de frontend), para no violar la regla de "nunca auto-elegir proveedor". El precio/costo sugerido se trae vía un GET con `?producto_proveedor=<id>` y queda editable antes de guardar.
18. **`xhtml2pdf`** elegido para exportar presupuestos a PDF (puro Python, sin dependencias de sistema como pango/cairo) en vez de WeasyPrint (mejor fidelidad CSS pero requiere librerías nativas) o ReportLab (más control pero sin templates HTML).
19. **Apps separadas por concepto**, mismo criterio que el resto del proyecto: `apps/clients` (Cliente) y `apps/quotes` (Presupuesto/Sección/Ítem/PlantillaCondiciones) — no una única `apps/sales`.

**Etapa 6 (Tareas):**

20. **`Tarea` es de una sola persona** (`asignado_a` FK, no M2M): con más de un asignado se diluye la responsabilidad; si dos personas tienen que trabajar lo mismo, se crean dos tareas.
21. **Sin estado "Vencida"**: a diferencia de `Presupuesto` (donde "Vencido" ya estaba en la enumeración original de estados, regla 9), la regla 14 define la máquina de estados de Tarea con solo tres valores. `Tarea.esta_vencida` es una property calculada (`fecha_limite < hoy AND estado != Completada`), sin persistir ni requerir ningún management command — no hay ningún estado al que transicionar.
22. **Sin tabla de historial de estados propia para Tarea**: se reutiliza el `AuditLog` genérico de la Etapa 1 vía `cambiar_estado_tarea()`, mismo patrón que `Presupuesto.cambiar_estado()`. `Tarea.completada_en` es la única excepción — un campo denormalizado de lectura para no tener que ir a buscar el `AuditLog` para mostrar la fecha de cierre en un listado.
23. **Transiciones de Tarea**: `Pendiente → {En proceso, Completada}` (se puede saltar directo a Completada, una tarea de dos minutos no debería obligar el paso intermedio), `En proceso → {Completada}`, `Completada` terminal (sin reabrir — igual que `Cancelado` en Presupuesto).
24. **`Tarea.asignado_a` con `on_delete=SET_NULL`** (a diferencia de las FKs "núcleo" de Etapa 5 como `Presupuesto.cliente`, que son `PROTECT`): si se borra el usuario, la tarea queda sin asignar en vez de bloquear el borrado — decisión explícita del usuario, mismo criterio que `creado_por`/`asignado_por` en el resto del proyecto.
25. **Permisos de Tarea**: Administrador/Ventas y Presupuestos/Service y Repuestos comparten `add_tarea`/`change_tarea` (cualquiera de los tres puede crear y reasignar cualquier tarea, no solo las que creó). Depósito/Técnico de Campo NO tienen esos permisos, pero sí pueden mover el estado de una tarea propia libremente (Pendiente↔En proceso↔Completada) vía un chequeo de fila (`asignado_a == request.user`), sin necesidad de un permiso Django de por medio.
26. **Alcance de visibilidad de listados de Tarea** (`queryset_tareas_visibles`): Diego ve las de todo el equipo (`view_all_tareas`); Rodrigo/Gabriel ven lo que ellos asignaron + lo que tienen asignado a sí mismos; Contri/Andrés ven solo lo propio.

**Etapa 7 (Stock):**

27. **`MovimientoStock` es un ledger append-only**, mismo criterio que `HistorialCosto` (Etapa 3): el stock actual nunca es un campo que se pisa, es `SUM(cantidad)` de los movimientos de ese producto+depósito. Trigger de Postgres bloquea `UPDATE`/`DELETE`. `cantidad` se guarda CON SIGNO (positivo suma, negativo resta); `tipo` (Entrada/Salida/Ajuste/Devolución) es una etiqueta de categorización, no algo que haya que reinterpretar en cada consulta — un `CheckConstraint` en la base valida que el signo sea coherente con el tipo.
28. **Un solo modelo con campo `deposito`** (General/Repuestos), no dos modelos paralelos: un mismo `Producto` puede tener stock en los dos depósitos a la vez (no son excluyentes, igual que `es_repuesto`), la separación es sobre qué pool de unidades físicas, no sobre la identidad del producto. La "separación de controles" (regla 12) se resuelve con permisos por depósito, no con tablas separadas.
29. **"Pendiente de devolución" (regla 11) es un estado DERIVADO del ledger, no un campo mutable**: `requiere_devolucion` se fija al crear la Salida de repuestos (inmutable desde ahí), y `cantidad_pendiente_devolucion()` resta las Devoluciones ya registradas (`salida_relacionada`) — mismo patrón que "stock actual" y que `Tarea.esta_vencida` (Etapa 6): no se introdujo una cuarta forma de modelar estado en el proyecto. **Corrección de una premisa falsa**: no existía ningún código de la Etapa 2 para este mecanismo (se grepeó todo `apps/` y no hay una sola mención a "devolución") — lo que existía era solo la frase en la descripción de rol de Gabriel del CLAUDE.md original, texto de especificación, no código.
30. **Permisos de Stock**, mismo idioma que `pricing.puede_registrar_costo` (permiso genérico de "pase libre" + permiso custom acotado por rol): Diego con `add_movimientostock` (entrada/salida en ambos depósitos) + `ajustar_stock_general` + `manage_stock_minimo`. Contri con `manage_stock_general` + `ajustar_stock_general`. Gabriel con `manage_stock_repuestos`. Rodrigo solo `view_movimientostock`. **Andrés con `manage_stock_general` (entrada Y salida, sin ajuste) — ampliación real respecto de la matriz de permisos original** (que solo lo listaba para "salida"): la regla de negocio 11 dice que el sobrante que retira "vuelve a stock" (una entrada), así que necesita ambas acciones. Pendiente para la Etapa 8: evaluar si conviene acotar esto a "solo movimientos de sus propios trabajos" en vez de dejarlo general para siempre.
31. **`stock_minimo_general`/`stock_minimo_repuestos`**: campos nullable directos en `catalog.Producto` (mismo patrón que `margen`, agregado por `pricing` en la Etapa 3 al mismo modelo desde otra app) — no un modelo de configuración separado. Las alertas se calculan al mostrar (mismo criterio que `Tarea.esta_vencida`), sin persistir historial de alertas: la regla de negocio no lo pide, a diferencia de la regla 6 de márgenes que sí pide auditar explícitamente el envío con margen bajo.
32. **`MovimientoStock.referencia_libre`** (texto simple) es el único vínculo con "de dónde salió" por ahora — **se descartó a propósito usar `GenericForeignKey`** (Content Types) para esto. Se había armado con esa idea (mismo mecanismo que `AuditLog.objeto`) para que la Etapa 8 se conectara "sin ninguna migración nueva", pero un GFK es `content_type_id` + `object_id` sin ningún `FOREIGN KEY` real: Postgres no puede verificar que el objeto referenciado exista ni protegerlo de un borrado, exactamente lo opuesto al criterio del resto del proyecto (la garantía vive en la base). El caso de `AuditLog` es distinto y sí está bien: es un registro histórico de "esto pasó" — si el objeto se borra, `on_delete=SET_NULL` + `objeto_repr` (snapshot de texto) alcanza, porque nada depende de que ese vínculo siga siendo válido. `MovimientoStock` sí necesitaría esa garantía (ej. "todos los movimientos de este trabajo", o no poder borrar un Trabajo con movimientos asociados). Se corrigió antes de mergear: la Etapa 8 va a agregar una FK real y nullable (`trabajo`, y `orden_compra` si corresponde) con una migración normal — mismo camino que ya se usó con `ItemPresupuesto.producto_proveedor` en la Etapa 5, que también se agregó con su propia migración dedicada, no estaba preparado desde el día 0.

**Etapa 8 — Parte 1 (Trabajo, base):**

33. **`crear_trabajo(presupuesto, usuario)`** nace siempre en `Pendiente` y hereda cliente, dirección y notas del `Presupuesto` Aceptado de origen — pero la copia es independiente después: editar `Trabajo.direccion` no toca el presupuesto ni viceversa (mismo criterio que precios congelados de la Etapa 5, regla 8: una vez heredado, el dato vive su propia vida).
34. **`Trabajo.estado` usa una lista ordenada (`ORDEN_ESTADOS`), no un grafo `TRANSICIONES_VALIDAS`** como Presupuesto/Tarea: Pendiente → Preparando materiales → Listo → En ejecución → Terminado es una secuencia real de obra, no un conjunto de aristas con ramas alternativas. `cambiar_estado_trabajo()` permite moverse a cualquier posición de la lista, adelante o atrás (retroceder para corregir un estado mal marcado), rechazando solo quedarse en el mismo estado — se corrigió durante el diseño un primer intento que solo permitía avanzar, que hubiera hecho imposible deshacer un error de tipeo del estado sin pasar por soporte.
35. **`EstadoTrabajo.CANCELADO` es una rama aparte, fuera de `ORDEN_ESTADOS`** (terminal, no forma parte de la secuencia de avance) — se agregó específicamente porque `Presupuesto.Aceptado → Cancelado` (decisión 14) ya podía dejar un Trabajo huérfano sin una forma de reflejar "esto no va más". `cancelar_trabajo()` es la única vía. `puede_revertir_aceptado()` (quotes) se extendió: revertir un Presupuesto Aceptado ahora exige además que el Trabajo vinculado (si existe) esté él mismo en Cancelado — no se puede revertir el presupuesto "por abajo" de un trabajo todavía en curso.
36. **`Trabajo.tecnico_asignado` es exclusivo de Diego para asignar/reasignar** (`AsignarTecnicoView` separada, permiso propio), a diferencia del resto del formulario de creación que Rodrigo también puede completar — corrección respecto de un primer borrador donde el campo quedaba editable para cualquiera con `add_trabajo`, en contra de la matriz de roles original.

**Etapa 8 — Parte 2 (listado de materiales):**

37. **`EtapaTrabajo` (sub-bloques por sección, con `fecha_estimada`/`duracion_estimada_dias`) y `MaterialTrabajo` son CRUD normal, NO append-only** — a diferencia de `HistorialCosto`/`MovimientoStock`, un listado de materiales planificado se edita y se borra libremente mientras el trabajo está en preparación; lo que sí queda append-only es el consumo real (Parte 3). `MaterialTrabajo` tiene un `CheckConstraint` `producto XOR descripcion_manual` (mismo patrón que `ItemPresupuesto`) — el gap gemelo en `ItemPresupuesto` (que no lo tenía) se corrigió retroactivamente en una rama y commit chicos y separados de esta etapa, con su propio test de INSERT crudo.
38. **`generar_listado_materiales(trabajo)`** copia los ítems de catálogo del presupuesto de origen a `MaterialTrabajo` — acción explícita disparada por quien prepara (Contri), nunca automática al crear el Trabajo ni al aceptar el Presupuesto (regla general del proyecto: sin efectos en cadena sin confirmación humana de por medio).

**Etapa 8 — Parte 3 (envío y consumo real de materiales — conexión con Stock):**

39. **`enviar_material()`/`enviar_materiales_pendientes()`**: al enviar, se asume enviado = usado (regla de negocio 11) — no hay una acción separada de "marcar usado". `cantidad_enviada()`, `cantidad_devuelta()`, `cantidad_pendiente_envio()` y `cantidad_usada_neta()` son todas derivadas del ledger de `MovimientoStock` vinculado por `material_trabajo` (mismo patrón que `stock_actual()`/`cantidad_pendiente_devolucion()`/`Tarea.esta_vencida` — no se persiste un cuarto lugar para este número).
40. **`registrar_sobrante()`**: el técnico corrige el número usado hacia abajo si sobró material; el sobrante genera una `Entrada` simple a `Deposito.GENERAL` — deliberadamente NO se reusa el tipo `Devolución` de `MovimientoStock`, que la Etapa 7 reservó específicamente para el circuito de repuestos de service con `requiere_devolucion` (regla 11 distingue los dos casos: "vuelve a stock" en obra general es más laxo que el control estricto de repuestos).
41. **"Marcar Listo con materiales pendientes de enviar" — solución elegida por el usuario, no una de las dos ofrecidas originalmente**: no bloquea la transición de estado, pero (a) se audita con una `accion` específica en cada ocurrencia, y (b) se muestra un banner de advertencia **calculado en vivo, no persistido** (`mostrar_advertencia_pendientes` en el contexto de la vista, listando `materiales_pendientes_de_envio()`) mientras la condición siga sin resolverse — combina "auditar pero no bloquear" (regla 6) con un aviso visual que no desaparece solo hasta que de verdad se envíe lo que falta.
42. **Permisos reutilizados, no inventados**: `puede_gestionar_materiales()` reusa `manage_preparacion` (Contri) — generar/editar el listado es la misma responsabilidad que "preparar el trabajo". `puede_registrar_consumo_material()` reusa `manage_ejecucion_propia` + un chequeo de fila (`material.trabajo.tecnico_asignado_id == user.id`) para que Andrés solo corrija el consumo de SU propio trabajo, sin necesitar un permiso Django nuevo.
    - **42bis. Nota pendiente de la Etapa 7 (decisión 30) sobre acotar a Andrés — resuelta solo a medias, queda abierta para la Etapa 9**: el permiso crudo `stock.manage_stock_general` que tiene Andrés **sigue siendo general, sin acotar a sus propios trabajos** (no se tocó `apps.stock.permissions.puede_registrar_entrada_salida`, que no sabe nada de `Trabajo`). Lo que sí quedó acotado es el flujo específico de consumo/sobrante de materiales de un trabajo (decisión 42 de arriba), porque ese pasa por `jobs`, no por el módulo de stock crudo. En la práctica: si Andrés entra directo a la pantalla de stock (fuera del flujo de un trabajo), todavía puede registrar entradas/salidas de stock general sin que estén ligadas a ningún trabajo suyo — el acotamiento real solo se dio en la puerta de entrada que se usa en la práctica (`jobs`), no en el permiso subyacente. Queda para una futura sesión decidir si vale la pena angostar también el permiso crudo o si alcanza con que el flujo real esté acotado.

**Etapa 8 — Parte 4 (Órdenes de compra con aprobación de Diego, regla de negocio 7):**

43. **`OrdenDeCompra`/`LineaOrdenCompra`**, `numero` vía secuencia de Postgres (mismo mecanismo que `Presupuesto.numero`, decisión 5). El grafo de estados usa `TRANSICIONES_VALIDAS` (como Presupuesto), no una lista ordenada (como Trabajo): Rechazada y Cancelada son ramas laterales, no posiciones en una única secuencia de avance.
44. **Dos garantías en triggers de Postgres, ninguna en validación de aplicación**: (a) bloqueo de edición de líneas fuera de Borrador — mismo patrón que el trigger de `ItemPresupuesto`/`MaterialTrabajo`; (b) **patrón nuevo en el proyecto**: un trigger cross-table que exige `LineaOrdenCompra.producto_proveedor.proveedor == LineaOrdenCompra.orden.proveedor` — no expresable con un `CheckConstraint` de una sola tabla, primera vez que se necesitó comparar dos tablas distintas en una restricción de base.
45. **Aprobada/Enviada nunca vuelven a Borrador** (mismo motivo que `Presupuesto.Aceptado`, decisión 11): si algo hay que cambiar después de aprobada, se cancela esa orden y se crea una nueva — reabrir después de aprobada dejaría la aprobación de Diego aplicada a datos que ya cambiaron.
46. **"Marcar enviada al proveedor" queda abierto a quien gestiona la orden** (`puede_gestionar_orden`: Rodrigo/Gabriel/Andrés/Diego), **no es exclusivo de Diego** — el bloqueo real de la regla de negocio 7 es específicamente la transición Pendiente de aprobación→Aprobada (`approve_ordendecompra`), no el paso administrativo posterior de avisarle al proveedor, que en la práctica hace quien armó la orden.
47. **`recibir_linea()`**: usa el `costo_real` corregido al momento de recibir para escribir `HistorialCosto` (`origen="orden_compra"`) — nunca el `costo_esperado` original de la línea, que es solo una estimación al armar la orden. Soporta recepciones parciales: `cantidad_recibida()`/`cantidad_pendiente_recepcion()` se derivan sumando el ledger de `MovimientoStock` vinculado por `linea_orden_compra` (mismo patrón derivado que el resto del proyecto) — no hay un campo de estado persistido a nivel de línea. **`OrdenDeCompra.estado` no tiene ningún estado terminal de "Recibida"**: recibir el 100% de las líneas no dispara ninguna transición automática de la orden — se queda en Aprobada/Enviada indefinidamente hasta que alguien la cancele a mano, coherente con la regla general de "nada pasa en cadena sin una acción explícita".
48. **`MovimientoStock` gana FKs reales `orden_compra` (PROTECT) y `linea_orden_compra` (SET_NULL)** — cierra la nota pendiente de la Etapa 7 (decisión 32), mismo patrón ya usado para `trabajo`/`material_trabajo` en la Parte 3: migración dedicada, nunca `GenericForeignKey`.
49. **Gap encontrado y corregido antes de mergear**: `recibir_linea()` no validaba en sí misma que `cantidad` no superara lo pendiente — esa validación vivía solo en `RecibirLineaView`, no en el servicio. Se corrigió agregando el chequeo (`raise ValueError`) dentro del propio servicio, para que llamarlo fuera de esa vista puntual (shell, un comando futuro, otra vista) no pueda sortear el límite — la vista sigue teniendo su propio pre-chequeo para un mensaje de formulario más prolijo, pero ya no es la única barrera.
50. **Bug de codename encontrado y corregido antes de escribir la migración de permisos**: `puede_gestionar_orden()`/`puede_cancelar_orden()` referenciaban `purchasing.add_ordencompra`/`purchasing.change_ordencompra` (sin la "de" de "orden**de**compra") en vez de los codenames reales que genera Django a partir del nombre del modelo — se hubiera evaluado siempre en `False` para todo el mundo. Se corrigió antes de correr `makemigrations`/asignar permisos por rol.

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