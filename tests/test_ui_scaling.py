from photoselect.ui_scaling import AppScaler


def test_full_hd_at_100_percent_keeps_base_sizes():
    scaler = AppScaler()
    scaler.set_tk_scaling(96 / 72)
    scaler.update(1920, 1080)
    assert scaler.ratio == 1.0
    assert scaler.scale(250) == 250
    assert scaler.font(10) == 10


def test_small_screen_fonts_never_below_floor():
    scaler = AppScaler()
    scaler.set_tk_scaling(96 / 72)
    scaler.update(1366, 600)
    assert scaler.ratio < 0.6
    assert scaler.font(10) == 8
    assert scaler.font(8) == 8


def test_windows_150_percent_uses_logical_pixels():
    # Écran 2880x1620 physique à 150 % = 1920x1080 logiques : même mise en page qu'en Full HD 100 %.
    scaler = AppScaler()
    scaler.set_tk_scaling(2.0)
    scaler.update(2880, 1620)
    assert abs(scaler.ratio - 1.0) < 1e-9
    assert scaler.font(10) == 10  # Tk applique lui-même le DPI aux points
    assert scaler.scale(250) == 375  # pixels physiques


def test_manual_multiplier_is_clamped():
    scaler = AppScaler()
    scaler.set_manual_multiplier(5.0)
    assert scaler.manual_multiplier == 1.20
    scaler.set_manual_multiplier(0.1)
    assert scaler.manual_multiplier == 0.70


def test_scale_minimum_follows_dpi():
    scaler = AppScaler()
    scaler.set_tk_scaling(2.0)
    scaler.update(800, 600)
    assert scaler.scale(250, minimum=96) == 144
