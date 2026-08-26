def puede_crear_producto(user):
    return user.has_perm("catalog.add_producto") or user.has_perm("catalog.manage_repuestos")


def puede_editar_producto(user, producto=None):
    """
    Única fuente de verdad para saber si un usuario puede crear/editar un
    producto puntual. No se duplica este chequeo en ningún otro lado
    (por eso Producto no está registrado en el admin de Django: ahí este
    permiso a nivel de fila no se podría hacer cumplir).

    - 'catalog.change_producto' (rol Administrador): acceso total.
    - 'catalog.manage_repuestos' (rol Service y Repuestos, Gabriel): solo
      alcanza a productos con es_repuesto=True. El chequeo es sobre el
      estado ACTUAL del producto, no sobre quién lo creó: si un admin le
      saca la marca de "repuesto" a un producto, Gabriel pierde acceso a
      partir de ese momento. Es intencional (ver test
      test_diego_puede_desmarcar_es_repuesto_y_gabriel_pierde_acceso).
    """
    if user.has_perm("catalog.change_producto"):
        return True
    if user.has_perm("catalog.manage_repuestos"):
        return producto is None or producto.es_repuesto
    return False
