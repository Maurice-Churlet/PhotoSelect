from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LayoutRect:
    x: int
    y: int
    width: int
    height: int


# Valeur de `tk scaling` pour un écran Windows à 100 % (96 dpi / 72 points).
TK_SCALING_96_DPI = 96.0 / 72.0


class AppScaler:
    """Centralise le scaling de l'UI principale sur une base logique 1920x1080.

    Deux grandeurs distinctes :
    - ``ratio`` : facteur d'adaptation à la taille de la fenêtre (en pixels logiques,
      c.-à-d. indépendants du zoom Windows 100/125/150 %). Sert aux polices, exprimées
      en points : Tk applique lui-même le DPI via ``tk scaling``, qu'on ne modifie plus.
    - ``scale(px)`` : tailles en pixels physiques = valeur de base x ratio x facteur DPI,
      pour rester cohérent avec les polices sur un écran à 125/150 %.

    Les polices ne descendent jamais sous ``min_font_pt`` : sur petit écran, mieux vaut
    un texte lisible et un défilement qu'une interface illisible.
    """

    def __init__(
        self,
        base_width: int = 1920,
        base_height: int = 1080,
        min_ratio: float = 0.1,
        max_ratio: float = 2.0,
        min_font_pt: int = 8,
    ) -> None:
        self.base_width = max(1, int(base_width))
        self.base_height = max(1, int(base_height))
        self.min_ratio = float(min_ratio)
        self.max_ratio = float(max_ratio)
        self.min_font_pt = max(1, int(min_font_pt))
        self._fit_ratio = 1.0
        self._manual_multiplier = 1.0
        self._dpi_factor = 1.0
        self._content_rect = LayoutRect(0, 0, self.base_width, self.base_height)

    @property
    def ratio(self) -> float:
        return self._fit_ratio * self._manual_multiplier

    @property
    def manual_multiplier(self) -> float:
        return self._manual_multiplier

    @property
    def dpi_factor(self) -> float:
        return self._dpi_factor

    @property
    def content_rect(self) -> LayoutRect:
        return self._content_rect

    def set_manual_multiplier(self, value: float) -> None:
        self._manual_multiplier = max(0.70, min(1.20, float(value)))

    def set_tk_scaling(self, tk_scaling: float) -> None:
        """Déduit le facteur DPI (1.0 à 100 %, 1.5 à 150 %) depuis `tk scaling` d'origine."""
        try:
            factor = float(tk_scaling) / TK_SCALING_96_DPI
        except (TypeError, ValueError):
            factor = 1.0
        self._dpi_factor = max(0.5, min(4.0, factor))

    def update(
        self,
        target_width: int,
        target_height: int,
        fit_width: int | None = None,
        fit_height: int | None = None,
    ) -> LayoutRect:
        safe_width = max(1, int(target_width))
        safe_height = max(1, int(target_height))
        ratio_width = safe_width if fit_width is None else max(1, int(fit_width))
        ratio_height = safe_height if fit_height is None else max(1, int(fit_height))

        # Les dimensions reçues sont en pixels physiques : on raisonne en pixels logiques.
        ratio_w = ratio_width / self._dpi_factor / float(self.base_width)
        ratio_h = ratio_height / self._dpi_factor / float(self.base_height)
        ratio = min(ratio_w, ratio_h)
        ratio = max(self.min_ratio, min(self.max_ratio, ratio))

        self._fit_ratio = ratio
        self._content_rect = LayoutRect(
            x=0,
            y=0,
            width=safe_width,
            height=safe_height,
        )
        return self._content_rect

    def scale(self, value: int | float, minimum: int = 1) -> int:
        """Taille en pixels physiques ; ``minimum`` est en pixels logiques (ajusté au DPI)."""
        physical_min = int(round(minimum * self._dpi_factor))
        return max(physical_min, int(round(float(value) * self.ratio * self._dpi_factor)))

    def font(self, size: int, minimum: int | None = None) -> int:
        """Taille de police en points (Tk applique le DPI)."""
        floor = self.min_font_pt if minimum is None else max(1, int(minimum))
        return max(floor, int(round(float(size) * self.ratio)))
