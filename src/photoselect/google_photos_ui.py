"""Intégration Tkinter de l'envoi Google Photos (mixin de ControlPanel).

Attend de la classe hôte : tree, db, config_data, _save_config, _get_photo_by_id, _abs_path,
_get_id_from_path, _get_active_virtual_album, _is_virtual_album_scope_active,
_get_selected_virtual_album_sidebar_row.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

logger = logging.getLogger(__name__)

_CFG_GPS = "google_photos_gps_mode"
_CFG_LAST_ALBUM = "google_photos_last_album"


class GooglePhotosMixin:
    _gphotos_busy: bool = False

    # ------------------------------------------------------------------ menus
    def _gphotos_install_menus(self) -> None:
        """Ajoute les entrées de menu (tableau, albums virtuels, visionneuses)."""
        tree_menu = getattr(self, "_tree_ctx_menu", None)
        if tree_menu is not None:
            tree_menu.add_separator()
            tree_menu.add_command(label="Envoyer vers Google Photos...", command=self._gphotos_send_table_selection)
            tree_menu.add_command(label="Google Photos : déconnecter le compte", command=self._gphotos_disconnect)
        album_menu = getattr(self, "_virtual_album_ctx_menu", None)
        if album_menu is not None:
            album_menu.add_separator()
            album_menu.add_command(
                label="Envoyer l'album vers Google Photos...", command=self._gphotos_send_sidebar_album
            )
        for name in ("viewer", "viewer_photo", "viewer_grid"):
            viewer = getattr(self, name, None)
            menu = getattr(viewer, "_ctx_menu", None)
            if viewer is None or menu is None or getattr(viewer, "_gphotos_menu_added", False):
                continue
            menu.add_separator()
            menu.add_command(
                label="Envoyer vers Google Photos...",
                command=lambda v=viewer: self._gphotos_send_viewer_photo(v),
            )
            viewer._gphotos_menu_added = True

    # ------------------------------------------------------------------ points d'entrée
    def _gphotos_send_table_selection(self) -> None:
        selection = tuple(self.tree.selection()) if hasattr(self, "tree") else ()
        ids: list[int] = []
        for iid in selection:
            try:
                ids.append(int(iid))
            except (TypeError, ValueError):
                continue
        if not ids:
            messagebox.showinfo("Google Photos", "Sélectionnez au moins une photo dans le tableau.", parent=self)
            return
        default_title = ""
        try:
            if self._is_virtual_album_scope_active():
                album = self._get_active_virtual_album()
                default_title = str((album or {}).get("name") or "")
        except Exception:
            default_title = ""
        self._gphotos_send_photo_ids(ids, default_title)

    def _gphotos_send_sidebar_album(self) -> None:
        if not getattr(self, "db", None):
            return
        row = self._get_selected_virtual_album_sidebar_row()
        if not row:
            return
        album_id = int(row.get("id") or 0)
        ids = list(self.db.get_virtual_album_photo_ids(album_id)) if album_id > 0 else []
        if not ids:
            messagebox.showinfo("Google Photos", "Cet album est vide.", parent=self)
            return
        self._gphotos_send_photo_ids(ids, str(row.get("name") or ""))

    def _gphotos_send_viewer_photo(self, viewer: Any) -> None:
        path = getattr(viewer, "_ctx_target_path", None) or getattr(viewer, "_current_path", None)
        if not path:
            return
        pid = self._get_id_from_path(Path(path))
        if pid:
            self._gphotos_send_photo_ids([int(pid)], "")
        else:  # photo hors base : envoi sans GPS de la base
            self._gphotos_start([self._gphotos_item_from_path(Path(path))], "")

    def _gphotos_disconnect(self) -> None:
        from photoselect import google_photos as gp

        if not messagebox.askyesno(
            "Google Photos",
            "Oublier l'autorisation Google Photos enregistrée sur ce PC ?\n\n"
            "Pour la révoquer aussi côté Google : myaccount.google.com/permissions",
            parent=self,
        ):
            return
        try:
            gp.disconnect()
            messagebox.showinfo("Google Photos", "Compte déconnecté de PhotoSelect.", parent=self)
        except gp.GooglePhotosError as exc:
            messagebox.showerror("Google Photos", str(exc), parent=self)

    # ------------------------------------------------------------------ construction des éléments
    def _gphotos_item_from_path(self, path: Path) -> Any:
        from photoselect import google_photos as gp

        return gp.UploadItem(path=path)

    def _gphotos_send_photo_ids(self, photo_ids: list[int], default_title: str) -> None:
        from photoselect import google_photos as gp

        items: list[gp.UploadItem] = []
        missing = 0
        seen: set[int] = set()
        for pid in photo_ids:
            if pid in seen:
                continue
            seen.add(pid)
            row = self._get_photo_by_id(int(pid))
            path = self._abs_path(str(row.get("filepath") or "")) if row else None
            if not row or not path:
                missing += 1
                continue
            items.append(gp.UploadItem(
                path=Path(path),
                latitude=row.get("latitude"),
                longitude=row.get("longitude"),
                description=gp.build_description(row.get("country"), row.get("region"), row.get("city")),
            ))
        if not items:
            messagebox.showwarning("Google Photos", "Aucune photo exploitable dans la sélection.", parent=self)
            return
        if missing:
            logger.warning("[gphotos] %d photo(s) introuvable(s) en base ignorée(s)", missing)
        self._gphotos_start(items, default_title)

    # ------------------------------------------------------------------ dialogue
    def _gphotos_start(self, items: list[Any], default_title: str) -> None:
        from photoselect import google_photos as gp

        if self._gphotos_busy:
            messagebox.showinfo("Google Photos", "Un envoi est déjà en cours.", parent=self)
            return
        err = gp.dependencies_error()
        if err:
            messagebox.showerror("Google Photos", err, parent=self)
            return
        try:
            known = [t for t in self._gphotos_known_albums() if t]
        except Exception:
            known = []
        title = (
            default_title
            or str(self.config_data.get(_CFG_LAST_ALBUM) or "")
            or f"PhotoSelect {date.today():%Y-%m-%d}"
        )
        choice = self._gphotos_ask_options(len(items), title, known)
        if choice is None:
            return
        album_title, gps_mode = choice
        if gps_mode == gp.GPS_STRIP:
            non_jpeg = sum(1 for it in items if Path(it.path).suffix.lower() not in (".jpg", ".jpeg"))
            if non_jpeg and not messagebox.askyesno(
                "Google Photos",
                f"{non_jpeg} fichier(s) ne sont pas des JPEG (HEIC, PNG, RAW...) : le GPS ne peut pas en être "
                "retiré, ils ne seront pas envoyés.\n\nContinuer avec les autres ?",
                parent=self,
            ):
                return
        self.config_data[_CFG_GPS] = gps_mode
        self.config_data[_CFG_LAST_ALBUM] = album_title
        try:
            self._save_config()
        except Exception:
            pass
        self._gphotos_run(items, album_title, gps_mode)

    def _gphotos_known_albums(self) -> list[str]:
        import contextlib
        import sqlite3

        from photoselect import google_photos as gp

        path = gp.default_ledger_path()
        if not path.exists():
            return []
        with contextlib.closing(sqlite3.connect(path)) as c:
            return [r[0] for r in c.execute("SELECT title FROM albums ORDER BY created_at DESC")]

    def _gphotos_ask_options(self, count: int, title: str, known: list[str]) -> tuple[str, str] | None:
        from photoselect import google_photos as gp

        dlg = tk.Toplevel(self)
        dlg.title("Envoyer vers Google Photos")
        dlg.transient(self)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"{count} photo(s) à envoyer.").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text="Album Google Photos :").grid(row=1, column=0, sticky="w", pady=(10, 2))
        var_title = tk.StringVar(value=title)
        combo = ttk.Combobox(frm, textvariable=var_title, values=known, width=42)
        combo.grid(row=2, column=0, columnspan=2, sticky="we")
        ttk.Label(
            frm, text="Un album déjà créé par PhotoSelect avec ce titre est complété, sinon il est créé.",
            foreground="#555",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 8))

        ttk.Label(frm, text="Position GPS :").grid(row=4, column=0, sticky="w")
        var_gps = tk.StringVar(value=str(self.config_data.get(_CFG_GPS) or gp.GPS_KEEP))
        ttk.Radiobutton(
            frm, text="Conserver (et compléter depuis la base si le fichier n'en a pas)",
            variable=var_gps, value=gp.GPS_KEEP,
        ).grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(
            frm, text="Retirer (recommandé pour un album partagé publiquement)",
            variable=var_gps, value=gp.GPS_STRIP,
        ).grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text="Les fichiers originaux ne sont jamais modifiés.", foreground="#555").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(6, 10))

        result: dict[str, tuple[str, str] | None] = {"v": None}

        def ok(_e: Any = None) -> None:
            t = var_title.get().strip()
            if not t:
                messagebox.showwarning("Google Photos", "Titre d'album requis.", parent=dlg)
                return
            result["v"] = (t, var_gps.get())
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, columnspan=2, sticky="e")
        ttk.Button(btns, text="Annuler", command=dlg.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Envoyer", command=ok).pack(side=tk.RIGHT, padx=(0, 8))
        dlg.bind("<Return>", ok)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        combo.focus_set()
        combo.icursor(tk.END)
        dlg.grab_set()
        self.wait_window(dlg)
        return result["v"]

    # ------------------------------------------------------------------ exécution
    def _gphotos_run(self, items: list[Any], album_title: str, gps_mode: str) -> None:
        from photoselect import google_photos as gp

        self._gphotos_busy = True
        q: queue.Queue[tuple[str, Any]] = queue.Queue()
        cancel = threading.Event()

        win = tk.Toplevel(self)
        win.title("Google Photos")
        win.transient(self)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)
        var_msg = tk.StringVar(value="Connexion au compte Google (le navigateur peut s'ouvrir)...")
        ttk.Label(frm, textvariable=var_msg, width=60).pack(anchor="w")
        bar = ttk.Progressbar(frm, mode="indeterminate", length=420, maximum=max(1, len(items)))
        bar.pack(fill=tk.X, pady=8)
        bar.start(12)
        # Pendant l'autorisation, le thread attend le navigateur (jusqu'à AUTH_TIMEOUT_S) et ne peut
        # pas être interrompu : on libère alors l'interface tout de suite et on ignore son résultat.
        state = {"connected": False, "abandoned": False}

        def on_cancel() -> None:
            cancel.set()
            if state["connected"]:
                var_msg.set("Annulation...")
                return
            state["abandoned"] = True
            self._gphotos_busy = False
            try:
                win.destroy()
            except tk.TclError:
                pass

        btn = ttk.Button(frm, text="Annuler", command=on_cancel)
        btn.pack(anchor="e")
        win.protocol("WM_DELETE_WINDOW", on_cancel)

        def worker() -> None:
            try:
                creds = gp.authenticate()
                if cancel.is_set():
                    q.put(("cancelled", None))
                    return
                q.put(("connected", None))
                client = gp.make_client(creds)
                ledger = gp.UploadLedger()
                report = gp.upload_items(
                    client, ledger, items, album_title, gps_mode=gps_mode,
                    progress=lambda d, t, n: q.put(("progress", (d, t, n))), cancel=cancel,
                )
                q.put(("done", report))
            except gp.GooglePhotosError as exc:
                q.put(("error", str(exc)))
            except Exception as exc:  # erreur inattendue : message sans données sensibles
                logger.exception("[gphotos] échec")
                q.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, name="gphotos-upload", daemon=True).start()

        def poll() -> None:
            try:
                while True:
                    kind, payload = q.get_nowait()
                    if state["abandoned"]:
                        if kind in ("done", "error", "cancelled"):
                            return  # envoi abandonné pendant l'autorisation : rien à afficher
                        continue
                    if kind == "connected":
                        state["connected"] = True
                        bar.stop()
                        bar.configure(mode="determinate", value=0)
                        var_msg.set("Envoi en cours...")
                    elif kind == "progress":
                        done, total, name = payload
                        bar.configure(maximum=max(1, total), value=done)
                        if name:
                            var_msg.set(f"Envoi {done + 1}/{total} : {name}")
                    else:
                        self._gphotos_busy = False
                        try:
                            win.destroy()
                        except tk.TclError:
                            pass
                        self._gphotos_finish(kind, payload)
                        return
            except queue.Empty:
                pass
            try:
                self.after(150, poll)
            except tk.TclError:  # application fermée pendant l'envoi
                pass

        self.after(150, poll)

    def _gphotos_finish(self, kind: str, payload: Any) -> None:
        if kind == "error":
            messagebox.showerror("Google Photos", payload, parent=self)
            return
        if kind == "cancelled":
            messagebox.showinfo("Google Photos", "Envoi annulé.", parent=self)
            return
        rep = payload
        lines = [f"Album : {rep.album_title}", f"Envoyées : {rep.uploaded}"]
        if rep.skipped_already:
            lines.append(f"Déjà présentes dans l'album : {rep.skipped_already}")
        if rep.skipped_unsupported:
            lines.append(f"Format non pris en charge : {rep.skipped_unsupported}")
        if rep.failed:
            lines.append(f"Échecs : {rep.total_failed}")
            for path, reason in rep.failed[:8]:
                lines.append(f"  • {Path(path).name} — {reason}")
            if rep.total_failed > 8:
                lines.append(f"  … et {rep.total_failed - 8} autre(s) (voir runtime.log)")
            for path, reason in rep.failed:
                logger.warning("[gphotos] échec %s : %s", path, reason)
        if rep.aborted:
            lines.append(f"Envoi arrêté : {rep.aborted}")
        if rep.cancelled:
            lines.append("Envoi interrompu à votre demande.")
        msg = "\n".join(lines)
        if rep.album_url:
            if messagebox.askyesno(
                "Google Photos",
                msg + "\n\nOuvrir l'album dans Google Photos pour le partager ?",
                parent=self,
            ):
                webbrowser.open(rep.album_url)
        else:
            messagebox.showinfo("Google Photos", msg, parent=self)
