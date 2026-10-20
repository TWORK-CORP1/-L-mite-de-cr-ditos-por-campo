
# E3 - Límite de créditos por campo


# 1) INFRAESTRUCTURA  ->  acom/selectors.py

from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

# AJUSTA: importa tu modelo y la constante de estado "validado"
# from acom.models import RegistroCredito, ESTADO_VALIDADO


# CAMBIO: selector nuevo. Suma los créditos VALIDADOS del estudiante en un campo.
def total_validado_por_campo(estudiante, campo) -> Decimal:
    resultado = RegistroCredito.objects.filter(          # AJUSTA: modelo
        estudiante=estudiante,
        estado=ESTADO_VALIDADO,                          # AJUSTA: solo validados
        actividad__categoria=campo,                      # AJUSTA: relación al campo
    ).aggregate(
        # CAMBIO: Coalesce evita que devuelva None cuando no hay registros
        total=Coalesce(
            Sum("creditos"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=5, decimal_places=2),
        )
    )
    return resultado["total"]



# 2) DOMINIO  ->  acom/dominio/reglas.py   (sin imports de Django)


# CAMBIO: constante de la regla de negocio (siempre Decimal, nunca float)
MAX_CREDITOS_POR_CAMPO = Decimal("2.00")


# CAMBIO: excepción de dominio nueva
class LimiteCampoError(Exception):
    """El estudiante excedería el máximo de créditos permitido en un campo."""


# CAMBIO: función pura nueva. Con ">" llegar a 2.00 es válido; solo se
# bloquea al pasarse (2.00 + 0.50 -> True, 1.00 + 1.00 -> False).
def excede_limite_campo(total_campo: Decimal, creditos_nuevos: Decimal) -> bool:
    return total_campo + creditos_nuevos > MAX_CREDITOS_POR_CAMPO



# 3) APLICACIÓN  ->  acom/servicios.py

from django.db import transaction

# CAMBIO: nuevos imports
# from acom.dominio.reglas import LimiteCampoError, excede_limite_campo
# from acom.selectors import total_validado_por_campo


def validar_y_registrar(estudiante_id, actividad):       # AJUSTA: firma real
    with transaction.atomic():
        # Ya existía: bloquea la fila del estudiante
        estudiante = Estudiante.objects.select_for_update().get(pk=estudiante_id)

        # CAMBIO: la revisión va DESPUÉS del bloqueo, así dos validaciones
        # simultáneas del mismo estudiante no leen el mismo total.
        total_campo = total_validado_por_campo(estudiante, actividad.categoria)
        if excede_limite_campo(total_campo, actividad.creditos):
            raise LimiteCampoError(
                f"El estudiante ya tiene {total_campo} créditos en "
                f"'{actividad.categoria}'. El máximo por campo es "
                f"{MAX_CREDITOS_POR_CAMPO}."
            )

    


# 4) PRESENTACIÓN


# ---- 4a) Panel del departamento  ->  acom/views.py -------------------------
from django.contrib import messages
from django.shortcuts import redirect


def validar_actividad_panel(request, actividad_id):      # AJUSTA: vista real
    actividad = Actividad.objects.get(pk=actividad_id)
    try:
        validar_y_registrar(actividad.estudiante_id, actividad)
        messages.success(request, "Actividad validada.")
    # CAMBIO: capturar el error y mostrarlo al usuario del panel
    except LimiteCampoError as e:
        messages.error(request, str(e))
    return redirect("panel_departamento")                # AJUSTA: nombre de URL


# ---- 4b) API  ->  acom/api/views.py (Django REST Framework) ----------------
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class ValidarActividadAPI(APIView):                      # AJUSTA: vista real
    def post(self, request, actividad_id):
        actividad = Actividad.objects.get(pk=actividad_id)
        try:
            validar_y_registrar(actividad.estudiante_id, actividad)
        # CAMBIO: la API responde 400 con el detalle del error
        except LimiteCampoError as e:
            return Response({"detalle": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True}, status=status.HTTP_200_OK)



# 5) TESTS

# ---- 5a) Fixture  ->  acom/tests/base.py -----------------------------------
# CAMBIO: nuevo parámetro `categoria`. Valor por defecto INVESTIGACION para
# NO romper los tests existentes (hoy todo se crea en ese campo).
def crear_actividad(creditos=Decimal("1.00"), categoria="INVESTIGACION", **kwargs):
    return Actividad.objects.create(                     # AJUSTA: modelo real
        creditos=creditos,
        categoria=categoria,                             # CAMBIO: antes estaba fijo
        **kwargs,
    )


# ---- 5b) Tests de dominio  ->  acom/tests/test_dominio.py (sin BD) ---------
from django.test import SimpleTestCase


# CAMBIO: archivo nuevo
class ExcedeLimiteCampoTests(SimpleTestCase):
    def test_no_excede_desde_cero(self):
        self.assertFalse(excede_limite_campo(Decimal("0.00"), Decimal("1.00")))

    def test_llegar_justo_al_limite_es_valido(self):
        self.assertFalse(excede_limite_campo(Decimal("1.00"), Decimal("1.00")))

    def test_excede_si_ya_esta_en_el_limite(self):
        self.assertTrue(excede_limite_campo(Decimal("2.00"), Decimal("0.50")))

    def test_excede_si_cruza_el_limite(self):
        self.assertTrue(excede_limite_campo(Decimal("1.50"), Decimal("1.00")))


# ---- 5c) Tests de integración  ->  acom/tests/test_limite_campo.py ---------
from django.test import TestCase


# CAMBIO: archivo nuevo
class LimiteCampoIntegracionTests(TestCase):
    def setUp(self):
        self.estudiante = crear_estudiante()             # AJUSTA: tu fixture

    def test_dos_en_campo_a_se_registran(self):
        for _ in range(2):
            act = crear_actividad(estudiante=self.estudiante, categoria="INVESTIGACION")
            validar_y_registrar(self.estudiante.id, act)
        self.assertEqual(
            total_validado_por_campo(self.estudiante, "INVESTIGACION"),
            Decimal("2.00"),
        )

    def test_tercera_en_campo_a_se_bloquea(self):
        for _ in range(2):
            act = crear_actividad(estudiante=self.estudiante, categoria="INVESTIGACION")
            validar_y_registrar(self.estudiante.id, act)
        tercera = crear_actividad(estudiante=self.estudiante, categoria="INVESTIGACION")
        with self.assertRaises(LimiteCampoError):
            validar_y_registrar(self.estudiante.id, tercera)

    # CRITERIO DE ACEPTACIÓN: bloquea en un campo pero permite en otro
    def test_en_otro_campo_si_se_permite(self):
        for _ in range(2):
            act = crear_actividad(estudiante=self.estudiante, categoria="INVESTIGACION")
            validar_y_registrar(self.estudiante.id, act)
        otro = crear_actividad(estudiante=self.estudiante, categoria="DEPORTES")  # AJUSTA: categoría real
        validar_y_registrar(self.estudiante.id, otro)    # no debe lanzar error

    def test_api_responde_400(self):
        for _ in range(2):
            act = crear_actividad(estudiante=self.estudiante, categoria="INVESTIGACION")
            validar_y_registrar(self.estudiante.id, act)
        tercera = crear_actividad(estudiante=self.estudiante, categoria="INVESTIGACION")
        resp = self.client.post(f"/api/actividades/{tercera.id}/validar/")  # AJUSTA: URL real
        self.assertEqual(resp.status_code, 400)
