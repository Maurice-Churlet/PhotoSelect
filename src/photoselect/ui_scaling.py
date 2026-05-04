from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LayoutRect:
    x: int
    y: int
    width: int
    height: int


class AppScaler:
    """Centralise le scaling de l'UI principale sur une base 1920x1080."""

    def __init__(
        self,
        base_width: int = 1920,
        base_height: int = 1080,
        min_ratio: float = 0.1,
        max_ratio: float = 2.0,
    ) -> None:
        self.base_width = max(1, int(base_width))
        self.base_height = max(1, int(base_height))
        self.min_ratio = float(min_ratio)
        self.max_ratio = float(max_ratio)
        self._fit_ratio = 1.0
        self._manual_multiplier = 1.0
        self._content_rect = LayoutRect(0, 0, self.base_width, self.base_height)

    @property
    def ratio(self) -> float:
        return self._fit_ratio * self._manual_multiplier

    @property
    def manual_multiplier(self) -> float:
        return self._manual_multiplier

    @property
    def content_rect(self) -> LayoutRect:
        return self._content_rect

    def set_manual_multiplier(self, value: float) -> None:
        self._manual_multiplier = max(0.70, min(1.20, float(value)))

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

        ratio_w = ratio_width / float(self.base_width)
        ratio_h = ratio_height / float(self.base_height)
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
        return max(minimum, int(round(float(value) * self.ratio)))

    def font(self, size: int, minimum: int = 8) -> int:
        return max(minimum, self.scale(size, minimum=minimum))