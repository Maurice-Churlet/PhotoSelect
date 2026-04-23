# Copyright (C) 2026 Maurice
# This program is free software under the GNU GPL v3 license.
# See the LICENSE file for details.

import multiprocessing
from photoselect.app import main

if __name__ == "__main__":
    # Nécessaire pour éviter les récursions infinies de fenêtres avec PyInstaller
    multiprocessing.freeze_support()
    main()
