from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ClickableRowsTemplateTests(SimpleTestCase):
    TARGET_TEMPLATES = (
        "apps/clients/templates/clients/cliente_list.html",
        "apps/quotes/templates/quotes/presupuesto_list.html",
        "apps/jobs/templates/jobs/trabajo_list.html",
        "apps/tasks/templates/tasks/tarea_list.html",
        "apps/catalog/templates/catalog/producto_list.html",
        "apps/catalog/templates/catalog/proveedor_list.html",
        "apps/purchasing/templates/purchasing/orden_list.html",
        "apps/imports/templates/imports/lista.html",
    )

    def test_listados_principales_declaran_navegacion_por_fila(self):
        for relative_path in self.TARGET_TEMPLATES:
            with self.subTest(template=relative_path):
                source = (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")
                self.assertIn("data-row-href", source)

    def test_script_no_intercepta_controles_internos(self):
        source = (
            Path(settings.BASE_DIR) / "static/js/app.js"
        ).read_text(encoding="utf-8")

        for selector in ("a", "button", "input", "select", "textarea", "form"):
            with self.subTest(selector=selector):
                self.assertIn(f'"{selector}"', source)

        self.assertIn("clickedInteractiveControl", source)
        self.assertIn("hasTextSelection", source)
