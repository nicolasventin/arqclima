import os
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.catalog.models import Marca, Producto, ProductoProveedor, Proveedor
from apps.stock.models import Deposito

from .documents import generar_pdf_orden, numero_documento_orden
from .mailing import EnvioOrdenCompraError, enviar_orden_por_email
from .models import (
    EstadoEnvioOrdenCompra,
    EstadoOrdenCompra,
    LineaOrdenCompra,
)
from .services import TransicionInvalidaError, cambiar_estado_orden, crear_orden


class EnvioEmailOrdenCompraTests(TestCase):
    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name="Administrador")
        self.usuario = User.objects.create_user(
            username="diego_11l",
            password="clave12345",
        )
        self.usuario.groups.add(grupo)

        self.proveedor = Proveedor.objects.create(
            nombre_comercial="Proveedor 11L",
            razon_social="Proveedor Once L SA",
            cuit="30-12345678-9",
            contacto_nombre="Compras",
            telefono="2610000000",
            email="compras@proveedor.test",
        )
        marca = Marca.objects.create(nombre="Marca 11L")
        producto = Producto.objects.create(
            marca=marca,
            codigo="11L-001",
            nombre="Válvula de prueba",
        )
        self.pp = ProductoProveedor.objects.create(
            producto=producto,
            proveedor=self.proveedor,
            codigo_proveedor="PROV-11L",
        )
        self.orden = crear_orden(
            self.proveedor,
            Deposito.GENERAL,
            self.usuario,
            notas="Entregar coordinando previamente.",
        )
        LineaOrdenCompra.objects.create(
            orden=self.orden,
            producto_proveedor=self.pp,
            cantidad=Decimal("3"),
            costo_esperado=Decimal("125.50"),
        )
        cambiar_estado_orden(
            self.orden,
            EstadoOrdenCompra.EMITIDA,
            self.usuario,
        )

        self._media = tempfile.TemporaryDirectory()
        self._settings = override_settings(
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            DEFAULT_FROM_EMAIL="ARQCLIMA <compras@arqclima.test>",
            MEDIA_ROOT=self._media.name,
            ARQCLIMA_COMPANY={
                "nombre": "ARQCLIMA",
                "tagline": "climatizando arquitectura",
                "razon_social": "",
                "cuit": "",
                "direccion": "",
                "telefono": "",
                "email": "compras@arqclima.test",
            },
        )
        self._settings.enable()
        self.addCleanup(self._settings.disable)
        self.addCleanup(self._media.cleanup)
        if hasattr(mail, "outbox"):
            mail.outbox.clear()

    def test_pdf_oficial_se_genera_con_numero_estable(self):
        contenido = generar_pdf_orden(self.orden)

        self.assertTrue(contenido.startswith(b"%PDF"))
        self.assertRegex(numero_documento_orden(self.orden), r"^OC-\d{4}-\d{4,}$")

    def test_envio_exitoso_adjunta_pdf_y_guarda_trazabilidad(self):
        confirmada = enviar_orden_por_email(self.orden, self.usuario)
        confirmada.refresh_from_db()

        self.assertEqual(confirmada.estado, EstadoOrdenCompra.ENVIADA)
        self.assertEqual(confirmada.estado_envio, EstadoEnvioOrdenCompra.ENVIADO)
        self.assertEqual(confirmada.enviada_a, "compras@proveedor.test")
        self.assertEqual(confirmada.enviada_por, self.usuario)
        self.assertIsNotNone(confirmada.enviada_en)
        self.assertTrue(confirmada.pdf_generado.name)
        self.assertTrue(os.path.exists(confirmada.pdf_generado.path))

        self.assertEqual(len(mail.outbox), 1)
        mensaje = mail.outbox[0]
        self.assertEqual(mensaje.to, ["compras@proveedor.test"])
        self.assertIn(numero_documento_orden(confirmada), mensaje.subject)
        self.assertEqual(len(mensaje.attachments), 1)
        self.assertEqual(mensaje.attachments[0][2], "application/pdf")
        self.assertTrue(mensaje.attachments[0][1].startswith(b"%PDF"))

        self.assertTrue(
            AuditLog.objects.filter(
                accion="enviar_orden_compra_proveedor",
                object_id=str(confirmada.pk),
            ).exists()
        )

    def test_sin_email_de_proveedor_no_envia_ni_cambia_estado(self):
        self.proveedor.email = ""
        self.proveedor.save(update_fields=["email"])

        with self.assertRaisesMessage(EnvioOrdenCompraError, "no tiene un email"):
            enviar_orden_por_email(self.orden, self.usuario)

        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.EMITIDA)
        self.assertEqual(self.orden.estado_envio, EstadoEnvioOrdenCompra.PENDIENTE)
        self.assertEqual(len(mail.outbox), 0)

    def test_error_smtp_deja_orden_emitida_y_habilita_reintento(self):
        with patch(
            "apps.purchasing.mailing.EmailMessage.send",
            side_effect=RuntimeError("SMTP no disponible"),
        ):
            with self.assertRaises(EnvioOrdenCompraError):
                enviar_orden_por_email(self.orden, self.usuario)

        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, EstadoOrdenCompra.EMITIDA)
        self.assertEqual(self.orden.estado_envio, EstadoEnvioOrdenCompra.ERROR)
        self.assertIn("SMTP no disponible", self.orden.ultimo_error_envio)
        self.assertTrue(self.orden.pdf_generado.name)

        nombre_pdf = self.orden.pdf_generado.name
        enviar_orden_por_email(self.orden, self.usuario)
        self.orden.refresh_from_db()

        self.assertEqual(self.orden.estado, EstadoOrdenCompra.ENVIADA)
        self.assertEqual(self.orden.estado_envio, EstadoEnvioOrdenCompra.ENVIADO)
        self.assertEqual(self.orden.pdf_generado.name, nombre_pdf)
        self.assertEqual(len(mail.outbox), 1)

    def test_no_permite_doble_envio_de_una_orden_ya_enviada(self):
        enviar_orden_por_email(self.orden, self.usuario)

        with self.assertRaisesMessage(EnvioOrdenCompraError, "Emitida"):
            enviar_orden_por_email(self.orden, self.usuario)

        self.assertEqual(len(mail.outbox), 1)

    def test_envio_en_curso_bloquea_reapertura(self):
        self.orden.estado_envio = EstadoEnvioOrdenCompra.ENVIANDO
        self.orden.save(update_fields=["estado_envio"])

        with self.assertRaisesMessage(TransicionInvalidaError, "envío de correo en curso"):
            cambiar_estado_orden(
                self.orden,
                EstadoOrdenCompra.BORRADOR,
                self.usuario,
            )

    def test_vista_pdf_y_detalle_muestran_flujo_de_email(self):
        self.client.login(username="diego_11l", password="clave12345")

        detalle = self.client.get(
            reverse("purchasing:detalle", args=[self.orden.pk])
        )
        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, "Enviar por email")
        self.assertContains(detalle, "Ver PDF")
        self.assertContains(detalle, "compras@proveedor.test")

        pdf = self.client.get(reverse("purchasing:pdf", args=[self.orden.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
