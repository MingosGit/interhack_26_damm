"""Excepciones específicas del proyecto."""
from __future__ import annotations


class DammSmartTruckError(Exception):
    """Base class para errores del proyecto."""


class ETLError(DammSmartTruckError):
    """Fallo durante la construcción del dataset canónico."""


class VolumenExcedidoError(DammSmartTruckError):
    """La carga supera la capacidad del camión solicitado."""


class GeocodingFailedError(DammSmartTruckError):
    """No se pudo geocodificar una dirección."""


class MatrixProviderError(DammSmartTruckError):
    """Error consultando el proveedor de matriz tiempo/distancia."""
