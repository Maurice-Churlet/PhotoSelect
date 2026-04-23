# PhotoSelect — Fichier de Passation

## Vue d'Ensemble

**PhotoSelect** est une application de tri photographique en Python/Tkinter avec double fenêtre :
- **Panneau de contrôle** : tableau interactif avec métadonnées EXIF, géolocalisation et gestion des fichiers.
- **Visionneuse** : affichage plein écran sans bordure, synchronisée avec le tableau.

L'application cible **Windows** avec support multi-écrans et DPI hétérogènes.

---

## Architecture

```
c:\Dev\PhotoSelect\
├── src/photoselect/
│   ├── __init__.py          # Version du package
│   ├── __main__.py          # Point d'entrée (python -m photoselect)
│   ├── app.py               # GUI principale (~850 lignes) — ControlPanel + FullscreenViewer
│   ├── database.py          # Couche SQLite — CRUD photos, collection_names, copied state, albums virtuels, tags IA
│   ├── exif_reader.py       # Extraction EXIF + GPS + reverse geocoding (offline via reverse_geocoder)
│   ├── file_manager.py      # Utilitaires fichiers (listage JPG, taille, sécurité dossiers)
│   ├── theme_engine.py      # Moteur d'analyse thématique IA (OpenCV + ONNX)
│   └── face_engine.py       # Moteur de reconnaissance faciale (Mediapipe/InsightFace)
├── tests/
│   └── __init__.py
├── pyproject.toml            # Config projet + dépendances
├── requirements.txt          # Dépendances pip
├── README.md                 # Documentation utilisateur
└── photoselect_config.json   # Config persistante (chemins, géométrie fenêtres) — généré à l'exécution
```

## Modules Clés

### `app.py` — Interface Graphique

| Classe | Rôle |
|---|---|
| `_get_monitor_rect(x, y)` | Détecte le moniteur Windows via `ctypes.windll` (API `MonitorFromPoint` + `GetMonitorInfoW`) |
| `FullscreenViewer(tk.Toplevel)` | Visionneuse photo — plein écran via `overrideredirect(True)` (pas `-fullscreen` qui force l'écran principal) |
| `ControlPanel(tk.Tk)` | Fenêtre principale — tableau, boutons, barre de progression, logique de scan |

**Points critiques :**
- Le plein écran utilise `overrideredirect(True)` + détection du moniteur courant via `ctypes` pour rester sur le bon écran.
- `SetProcessDpiAwareness(2)` est appelé dans `main()` avant toute fenêtre pour la cohérence des coordonnées sur écrans avec DPI différents.
- Le scan s'exécute dans un thread séparé (`daemon=True`) avec `_stop_event` pour l'interruption propre.
- La persistance des fenêtres est gérée séparément : la visionneuse sauvegarde immédiatement à chaque fermeture (via `_on_closing`).

### `database.py` — Base de Données SQLite

Fichier : `.photoselect.db` dans le dossier source scanné.

| Méthode | Description |
|---|---|
| `insert_photos(batch)` | Insertion par lots (`INSERT OR IGNORE`) |
| `update_collection_names()` | Calcul des noms de collection (avec/sans GPS, doublons `-XXX`) |
| `set_copied_state(id, bool)` | Marquer une photo comme exportée |
| `delete_photo(id)` | Supprimer une entrée |
| `update_photo_path(id, path, name)` | Mettre à jour après renommage physique |
| `get_copied_count()` | Compteur des exports |

**Format des noms de collection :**
- Avec GPS : `AAMMJJ_Pays_Region_Ville-XXX` 
- Sans GPS : `AAMMJJ_NomFichierOriginal-XXX`
- Doublons gérés par suffixe `-XXX` (3 chiffres, commence à `-000`)

### `exif_reader.py` — Extraction de Métadonnées

- Lecture EXIF standard via `Pillow` (date, modèle appareil)
- Extraction GPS via `IFD.GPSInfo` + conversion DMS → décimal
- Géocodage inverse **hors-ligne** via `reverse_geocoder` (pays, région, ville)

### `file_manager.py` — Utilitaires Fichiers

- `list_jpg_files(path)` : Recherche récursive de `.jpg` / `.jpeg`
- `is_safe_output_dir(in, out)` : Vérifie que la sortie n'est pas dans la source
- `format_size(bytes)` : Formatage lisible (Ko, Mo, Go)

---

## Flux Utilisateur

```
1. Lancement → Restauration géométrie des 2 fenêtres + auto-chargement DB existante
2. Scanner / Reprendre → Thread de scan (reprise intelligente via comparaison fichiers/DB)
     ├─ Extraction EXIF + GPS par fichier
     ├─ Insertion par lots de 100
     ├─ Progression temps réel (vitesse, ETA)
     └─ update_collection_names() à la fin ou à l'interruption
3. Navigation → Flèches/Molette dans le tableau ↔ image dans la visionneuse
4. Espace → Copie le fichier vers dossier sortie (renommé collection_name.ext)
5. Suppr → Si copié : corbeille de la copie / Si non copié : corbeille de l'original + suppression DB
6. Renommer originaux → Renomme physiquement tous les fichiers source avec leur collection_name
```

## Dépendances

| Package | Usage |
|---|---|
| `Pillow>=10.0` | Lecture images + EXIF |
| `reverse_geocoder>=1.5.1` | Géocodage GPS hors-ligne |
| `send2trash>=1.8` | Suppression vers la corbeille Windows |
| `opencv-python>=4.10.0` | Moteur thématique IA |

## Configuration Persistante (`photoselect_config.json`)

```json
{
  "last_in": "F:\\Photos",
  "last_out": "E:\\_PhotosSelect",
  "geometry_main": "1920x1009-24+24",
  "state_main": "zoomed",
  "geometry_viewer": "1280x649-1854+55",
  "state_viewer_fullscreen": true
}
```

## Points d'Attention / Bugs Connus

1. **Fichiers non-image** : Les fichiers `.jpg` corrompus ou non-images (ex: fichiers renommés en .jpg) génèrent une erreur dans la visionneuse mais ne bloquent pas le scan.
2. **Fichiers `config_main.json` et `config_viewer.json`** : Fichiers orphelins dans la racine, inutilisés (vestiges d'un ancien système de persistance). Peuvent être supprimés.
3. **Migration DB** : La colonne `is_copied` est ajoutée par migration automatique (`ALTER TABLE`) si absente.
4. **`overrideredirect(True)`** : Quand la visionneuse est en plein écran, elle n'a pas de barre de titre. F11/F12/Escape/Double-clic permettent d'en sortir. Si le focus est perdu, il faut Alt+Tab.

## Mise à Jour — 22 avril 2026

### Watcher / gel UI

1. **Cause confirmée du blocage watcher**
- Le watcher filtrait déjà les créations/déplacements via `is_ignored_scan_path(...)`, mais les suppressions (`on_deleted`) ne passaient pas par cette exclusion.
- Résultat: des suppressions de fichiers techniques sous `_PhotoSelect` pouvaient remonter jusqu'à la synchro DB/UI.
- La méthode de suppression rechargeait ensuite le tableau même quand aucune ligne DB n'était réellement supprimée.
- Les logs montraient une rafale de `Sync suppression` suivie d'un `Chargement tableau` quasi systématique, ce qui figeait l'interface.

2. **Correctifs appliqués**
- Ajout d'un filtre unique `PhotoWatcher._should_ignore_path(...)` utilisé aussi sur `on_deleted`.
- Garde supplémentaire dans `_sync_single_file_from_watcher(...)` et `_sync_deletion_from_watcher(...)` pour ignorer toute opération située dans `_PhotoSelect`.
- `_sync_deletion_from_watcher(...)` ne recharge plus l'UI si `delete_photos_by_filepaths(...)` ne supprime aucune ligne.
- Ajout d'un refresh UI watcher regroupé (`_schedule_watcher_ui_refresh`) pour éviter un rechargement complet à chaque événement.

3. **Validation**
- Compilation OK: `c:/python314/python.exe -m py_compile src/photoselect/app.py`
- Snippet Python validé: une suppression sous `_PhotoSelect/Illisibles` n'enregistre plus de callback watcher, alors qu'un fichier source normal continue d'être pris en compte.

4. **Point d'attention code**
- `src/photoselect/app.py` contient plusieurs méthodes dupliquées dans la même classe.
- Les définitions les plus basses dans le fichier sont les définitions effectives au runtime; pour le watcher, les méthodes actives de synchro sont celles proches de la fin du fichier.

## Mise à Jour (19 avril 2026)

### Fiabilité / Performance UI

1. **Audit intégrité asynchrone**
- `_run_integrity_audit_and_reconcile()` lance maintenant un worker de fond (`_integrity_audit_worker`).
- La phase d'analyse n'est plus bloquante pour l'UI même sur très gros volumes (ex: 350k photos).
- La progression est mise à jour en thread-safe via `_tick_photo_progress_threadsafe()`.
- Une ETA est affichée dès que le temps moyen/photo devient exploitable.

2. **Stop global pour actions longues**
- Ajout de `_begin_long_action()` et `_end_long_action()` pour normaliser l'état UI.
- Le bouton "Stopper l'action" est actif pendant:
  - scan,
  - normalisation lieux,
  - audit/réconciliation,
  - collage géolocalisation,
  - édition batch.
- L'annulation repose sur `_stop_event` avec sorties propres et messages utilisateur.

3. **Disposition boutons (demande utilisateur)**
- `Aide (F1)` et `Exporter journal` ont été permutés.
- `Audit intégrité` et `Stopper l'action` ont été permutés.

4. **Bloc haut reformaté**
- Champs `Dossier Source`, `Dossier Sortie`, `Rechercher` compactés.
- Largeur des champs réduite, boutons `Parcourir`/`Effacer` alignés juste à droite.
- Boutons d'action regroupés en 3 colonnes.
- Le logo reste ancré en haut de la fenêtre.

5. **Fenêtre de guidage au démarrage**
- Affichage en fin d'initialisation, au premier plan.
- Contient des consignes de placement des 3 fenêtres.
- Option `Ne plus afficher` persistée en config (`show_startup_layout_info`).

6. **Bascule Photo <-> Mosaïque optimisée**
- Plusieurs gardes-fous ajoutés pour éviter les rechargements inutiles lors des bascules.
- En mode `photo`, la mosaïque cachée n'est plus resynchronisée au clic tableau.
- En mode `multiple`, la mosaïque se met à jour seulement si la photo cible n'est pas déjà dans la page affichée.

7. **Clic Google Maps restreint**
- Le clic qui ouvre Maps dans le tableau est limité à la colonne `Coordonnées GPS`.
- Retrait du déclenchement sur `Pays`, `Région`, `Ville`.

8. **Ordre des colonnes**
- `Taille` déplacée juste avant `Appareil`.

### Packaging

- Le spec PyInstaller inclut déjà le README via:
  - `datas = [('README.md', '.')]`
- L'aide intégrée lit `README.md` via `get_resource_path(...)`, compatible exécutable PyInstaller.

## Raccourcis Clavier

| Touche | Action |
|---|---|
| `Flèches` / `Molette` | Navigation photos |
| `Espace` | Exporter (copier vers sortie) |
| `Suppr` / `Delete` | Supprimer (copie ou original → corbeille) |
| `F11` / `F12` | Basculer plein écran visionneuse |
| `Double-clic` | Basculer plein écran visionneuse |
| `Escape` | Sortir du plein écran |

---

## Mise a Jour - 20 avril 2026

### Travaux realises

1. **Mode Affichage Sélection**
- Ajout d'un 3eme mode sur la ligne Affichage: `Sélection`.
- En mode Sélection, les visionneuses chargent les images du dossier choisi dans `Dossier sélection`.
- Le changement de la combobox `Dossier sélection` declenche un rafraichissement immediat si le mode Sélection est actif.
- La navigation clavier gauche/droite de la visionneuse suit l'ordre du dossier de sélection choisi.

2. **Champs dossiers transformes en selecteurs persistants**
- `Dossier Source` et `Dossier sélection` sont maintenant des combobox triées alphabetiquement.
- Les chemins choisis via `Parcourir` sont memorises dans une base locale dediee (`_path_registry.sqlite3`).
- Le label de sortie a ete clarifie en `Dossier sélection`.

3. **Planisphere interactif renforce**
- Rendu des terres via `assets/world_countries.geojson` (contours pays detailles).
- Zoom molette sur la carte avec viewport lon/lat et limites.
- Zoom auto sur la zone selectionnee (bbox), recentrage et conservation des points GPS visibles.
- Reinitialisation de zone via double-clic carte ou clic logo.

4. **Correctifs robustesse workflow data**
- Correctif copie image vers presse-papiers Windows pour clic droit visionneuses/tableau.
- Purge durcie: suppression de la base courante et de l'ancienne base legacy `.photoselect.db`.
- Re-scan apres purge force un depart propre sans migration legacy implicite.

### Validation
- Compilation Python validee sur `src/photoselect/app.py`.

---

## Mise a Jour - 21 avril 2026 (passation scan rapide / visages)

### Contexte du signalement
- Signalement utilisateur: `scan rapide et 0 resultat` + erreurs autour de l'arret moteur visages avant classification themes.
- Contrainte demandee: ne pas lancer classification avant arret complet du moteur visages, avec attente en tache de fond.

### Diagnostic verifie

1. **Faux “nouveaux fichiers” initialement detectes**
- Analyse hors UI: ~824 fichiers “nouveaux” detectes precedemment.
- Repartition:
  - 821 sous `_PhotoSelect/Visages` (miniatures),
  - 3 sous `_PhotoSelect/Selection`.
- Cause: dossier technique `_PhotoSelect` non exclu assez tot du scan recursif.

2. **Etat reel apres correctif exclusion**
- Verification directe (snippet Python):
  - `disk_count=33658`
  - `db_count=33658`
  - `delta=0`
- Donc `Scanner / Reprendre` rapide avec 0 nouveau est legitime dans cet etat.

3. **Perception UX trompeuse**
- Le message explicatif de fin de scan etait ecrase rapidement par le compteur contextuel de visionneuse.

### Correctifs appliques

1. **Exclusion du dossier technique PhotoSelect**
- Fichier: `src/photoselect/file_manager.py`
- Ajout de `_photoselect` dans `IGNORED_SCAN_DIRS`.
- Effet: plus aucun fichier sous `_PhotoSelect` dans `list_jpg_files(...)`.

2. **Arret worker visages avant classification themes (asynchrone)**
- Fichier: `src/photoselect/app.py`
- Ajouts:
  - etat `self._theme_classify_waiting_for_face_stop`,
  - polling non bloquant `_poll_face_worker_stop_then_start_theme(...)`,
  - lancement differe `_launch_theme_classification_worker(...)` uniquement apres arret reel,
  - timeout d'attente => annulation propre.

3. **Robustesse scan: erreur fichier non bloquante**
- Fichier: `src/photoselect/app.py`
- `process_file(...)` retourne `(meta|None, error_text|None)`.
- Les erreurs metadata sont loggees et comptabilisees, sans arreter tout le scan.

4. **Logging runtime persistant**
- Fichier: `src/photoselect/app.py` (`main()`)
- Ajout `FileHandler` vers `%APPDATA%/PhotoSelect/logs/runtime.log` en plus de la sortie console.

5. **Message de fin scan collant**
- Fichier: `src/photoselect/app.py`
- Ajout:
  - `self._counter_sticky_until`,
  - `_set_sticky_counter_message(...)`,
  - garde dans `_refresh_contextual_counter(...)`.
- Le resume de fin scan reste visible (TTL 10-12s) et n'est plus ecrase immediatement.

### Traces runtime utiles (a surveiller)
- `[scan] Demande scan: ...`
- `[scan] Worker démarré: ...`
- `[scan] Fichiers trouvés sur disque: bruts=... exclus=... gardés=...`
- `[scan] Mode reprise: en_base=... nouveaux=... total_disque=...`
- `[scan] Fin: detectés=... traités=... erreurs=...`
- `[faces] Stop demandé ...` / `[faces] Stop résultat ...`
- `[theme] Moteur visages arrêté, lancement classification.`

### Etat courant pour reprise
- Le moteur visages peut rester desactive pour tests scan purs (`face_recognition_enabled=false`) sans impacter le diagnostic scan.
- Sur la machine de reference, le scan source est aligne a la base (delta 0).
- Le prochain controle doit etre UX: confirmer en conditions reelles que le message collant de fin scan est bien visible et compris.

### Etape suivante recommandee
1. Si l'utilisateur confirme encore une ambiguite, ajouter une popup resume de fin en mode reprise (`nouveaux`, `deja en base`, `erreurs`).
2. Decider produit: conserver mode reprise hybride (decouverte + DB) ou basculer vers mode DB-only strict sur `Scanner / Reprendre`.

---

## Mise a Jour - 21 avril 2026 (batch visages, doublons a la demande, startup accelere)

### Travaux realises

1. **Batch visages marque puis execute**
- L'onglet `Visages` ne fait plus les operations sensibles immediatement au clic droit.
- Les actions `renommer` et `supprimer vignette` sont placees dans une file locale `_face_edit_queue`, visualisees par badge sur chaque vignette et executees ensuite par `Executer batch`.
- Ajout d'un editeur inline clavier/souris:
  - frappe alphanumerique sur une vignette selectionnee => ouverture rapide du renommage,
  - suggestions de noms existants hors libelles generiques `Personne N`,
  - `Entree` applique le marquage, `Echap` annule.
- Le worker batch tourne dans `_run_faces_batch_worker`, remonte sa progression dans la barre partagee et applique:
  - suppression de vignette + `db.delete_face_by_thumb(...)`,
  - renommage/reaffectation via `resolve_face_person_target(...)` + `reassign_face_by_thumb(...)`.
- A la fermeture, `_face_batch_stop` et `join()` evitent de laisser un thread batch orphelin.

2. **Exclusions scan source fiabilisees**
- Nouvelle methode `_is_path_excluded_from_source_scan(...)`.
- Elle exclut de facon coherente:
  - les chemins deja ignores par `is_ignored_scan_path(...)`,
  - le dossier de sélection,
  - le dossier visages,
  - le dossier doublons visages.
- Cette meme regle sert maintenant pour:
  - le scan global,
  - le comptage de photos,
  - le nettoyage DB des entrees absentes/dupliquees.

3. **Politique doublons simplifiee**
- Le recalcul ou la preparation de l'index doublons ne part plus automatiquement:
  - au demarrage,
  - en fin de scan,
  - apres suppression standard,
  - pendant le refresh statistiques source.
- Le recalcul est reserve a l'audit et au scope `Doublons`.
- Quand le cache n'est pas pret, l'indicateur statistiques affiche explicitement `Non calcule (audit ou mode Doublons)`.

4. **Scan GPS local force + barre de progression robuste**
- Le scan global n'affiche plus de popup de choix GPS.
- `_start_scan()` force `scan_geo_mode = "local"` pour le scan/re-scan global.
- Toutes les mises a jour de progression de scan reconfigurent explicitement la barre partagee (`mode`, `maximum`, `value`) pour reprendre la main meme si une autre operation l'a modifiee entre-temps.

5. **Acceleration du demarrage**
- `_bootstrap_startup_db_load()` ne fait plus qu'une initialisation DB legere puis lance un snapshot differe.
- `_init_db(...)` accepte des flags pour desactiver, selon le contexte:
  - nettoyage source,
  - demarrage worker visages,
  - mise a jour compteur exports,
  - preload visages.
- Nouveau pipeline startup:
  - `_startup_snapshot_worker(...)` ouvre une DB dediee, met a jour les `collection_name` si besoin, charge les lignes triees et construit un payload de stats.
  - `_on_startup_snapshot_ready(...)` bascule soit sur un chargement complet classique, soit sur un remplissage progressif si le contexte est simple.
  - `_continue_startup_tree_load(...)` insere par paquets de 250 lignes sur le thread UI, alimente les points carte et les filtres pays, puis finalise le compteur.
- Les services secondaires sont reportes via `_schedule_delayed_startup_services(...)`:
  - worker visages,
  - preload visages,
  - rendu carte,
  - nettoyage source differe.

6. **Carte GeoJSON retiree du chemin critique**
- `_load_map_shapes()` est maintenant asynchrone et attend `_startup_hydration_complete`.
- Tant que le GeoJSON n'est pas pret, `_render_map()` conserve un fallback simple et peut afficher `Carte en preparation...`.
- `_set_map_points()` n'impose plus de rendu complet pendant l'hydratation initiale du tableau.

7. **Nettoyage source differe**
- La logique de purge DB source a ete factorisee dans `_collect_db_cleanup_ids(...)`.
- `_deferred_startup_cleanup_worker(...)` reutilise cette logique sur une DB dediee apres ouverture de l'application.
- En cas de suppressions effectives, l'UI est rafraichie seulement si aucun chargement startup/scan n'est encore en cours.

8. **Correctif runtime mineur**
- `_count_files_worker()` bind maintenant correctement le message d'exception dans son callback `after(...)` (`lambda msg=str(e): ...`) pour eviter le bug de capture tardive.

### Validation
- Compilation Python validee sur `src/photoselect/app.py`.
- Lancements manuels valides avec `PYTHONPATH=src; python -m photoselect`.
- Sur la base utilisateur de reference (~32998 photos), les logs confirment l'ordre suivant:
  - snapshot startup charge,
  - puis seulement apres, chargement GeoJSON de la carte.

### Etat courant recommande pour reprise
- Le prochain gain startup le plus rentable est probablement de rendre la visionneuse mosaïque lazy au lieu de l'instancier dans `ControlPanel.__init__`.
- Ensuite, deplacer aussi le chargement/redimensionnement du logo hors du chemin critique.
- Enfin, remplacer une partie du snapshot statistiques par des agregats SQL cibles si le volume continue de croitre.

---

## Mise a Jour - 20 avril 2026 (passation avant nouveau fil)

### Travaux realises

1. **Audit integrite: options persistantes + auto-correction**
- Persistance en config des choix de la fenetre `Audit integrite - Fonctions`.
- Persistance des choix de la fenetre `Audit integrite & reconciliation`.
- Ajout de l'option `Corriger les defauts detectes (si possible)` dans la fenetre de selection des fonctions.
- Si cette option est active, l'etape de correction est appliquee automatiquement (sans attendre une validation manuelle).
- Exclusivite conservee entre les directions EXIF->DB et DB->EXIF.

2. **Diagnostics et robustesse audit GPS sans lieu**
- Correction du test de presence GPS DB (conversion float robuste au lieu d'un simple test `is not None`).
- Ajout de stats de diagnostic: eligibles, resolues localement, non resolues, coordonnees invalides.
- Affichage de ces stats dans la fenetre de resultat d'audit.

3. **Centrage des fenetres**
- Centrage harmonise des fenetres d'audit/reconciliation et de la fenetre BTC.
- Recentrage renforce apres rendu pour reduire les derives de position.

4. **Statistiques: dernier audit integre**
- Ajout d'un champ `last_integrity_audit` dans l'onglet Statistiques.
- Resume du dernier audit stocke dans `config_data["stats_last_integrity_audit"]`.
- Mise a jour sur audit annule, audit non applique, et audit corrige.

5. **Doublons: status de preparation**
- Correctif du message `Preparation doublons...` qui restait affiche apres fin de traitement.
- Memorisation/restauration du message compteur precedent hors scope Doublons.

6. **Visionneuse: terminologie et status**
- Remplacement des libelles utilisateur `Multiple` par `Mosaic`/`Mosaic` visible sous la forme `Mosaic` dans l'UI configuree.
- Messages de status adaptes (`Visionneuse Mosaic: ...`).
- Correctif du status stale lors du retour en mode Photo.

7. **Orientation photo (nouvelle fonctionnalite)**
- Menus clic droit orientation ajoutes sur:
  - visionneuse Photo,
  - visionneuse Mosaic,
  - vignette active du slot logo.
- Actions dispo:
  - normaliser via EXIF,
  - rotation 90 droite,
  - rotation 90 gauche,
  - rotation 180.
- Ecriture du fichier corrige + `EXIF Orientation = 1`.
- Rafraichissement immediat:
  - des 2 visionneuses,
  - de la vignette logo,
  - du tableau UI.
- Journalisation audit ajoutee (`orientation_fix`).
- Raccourcis claviers ajoutes en visionneuse:
  - `Ctrl+R` (90 droite),
  - `Ctrl+L` (90 gauche),
  - `Ctrl+U` (normaliser EXIF).

8. **Bloc selecteurs haut UI**
- Les 3 lignes `Dossier Source / Dossier sélection / Rechercher` ont ete encapsulees dans un sous-frame dedie pour garantir un empilement haut avec interligne normal, sans effet de centrage vertical.

### Fichiers principalement impactes sur ce lot
- `src/photoselect/app.py`
- `src/photoselect/database.py`
- `ENDOFF.md`
- `HANDOFF.md`

### Validation
- Compilation Python validee sur `src/photoselect/app.py` et `src/photoselect/database.py`.

---

## Mise a Jour - 20 avril 2026 (coffee prompt + QR BTC)

### Travaux realises

1. **Message centre aleatoire dans la barre de conversation**
- Insertion d'un texte court aleatoire lu depuis `src/photoselect/_resources/coffee.md`.
- Prefixe impose: `Clic ici `.
- Positionnement centre, sans deplacement de la mise en page.
- La phrase est maintenant renouvelee aleatoirement a chaque interaction utilisateur.

2. **Interaction au clic**
- Un clic sur le texte ouvre une fenetre de soutien BTC.
- Texte affiche:
  - "Si ce logiciel vous a fait gagner du temps et de la clarté dans vos photos,"
  - "vous pouvez m’offrir un café ☕(ou plus selon votre karma)"
  - "C’est entièrement libre — et toujours très apprécié."
  - "Don uniquement en BTC"
- Affichage du QR code: `src/photoselect/assets/QR_BTC.jpg`.

### Validation
- Compilation Python validee sur `src/photoselect/app.py`.

---

## Mise a Jour - 20 avril 2026 (mode Doublons + indexation)

### Travaux realises

1. **Index de doublons persistant (base SQLite)**
- Ajout de tables d'index de doublons de signature (`taille + titre + date`).
- Construction automatique de l'index en fin de scan/re-scan.
- Compatibilite anciennes bases: reconstruction auto au lancement avec progression.

2. **4e scope Affichage: Doublons**
- Ajout du bouton radio `Doublons`.
- Tableau + visionneuses affichent uniquement les photos du sous-ensemble doublons.

3. **Audit + Statistiques**
- L'audit affiche le volume de doublons signature (photos + groupes).
- Les statistiques affichent un indicateur dedie `Doublons signature`.

4. **Suppression coherente avec les doublons**
- Apres suppression d'originaux (DB), l'index doublons est reconstruit.
- Le scope `Doublons` est rafraichi immediatement pour retirer les lignes qui ne sont plus doublons.

5. **Exclusion Sélection dans les doublons**
- Les fichiers du dossier `Dossier sélection` sont exclus du calcul des doublons actifs.
- Cette exclusion est appliquee au scope `Doublons` et aux statistiques.

### Validation
- Compilation Python validee sur `src/photoselect/app.py` et `src/photoselect/database.py`.

---

## Mise a Jour - 20 avril 2026 (survol repertoire)

### Travaux realises

1. **Zone compteurs enrichie au survol**
- Survol d'une photo dans la visionneuse Photo: affichage du repertoire de la photo dans la zone compteur.
- Survol d'une miniature dans la visionneuse Multiple: affichage du repertoire de la miniature survolee.
- Survol de la miniature active remplaçant le logo: affichage du repertoire de la photo active.
- En sortie de survol, le texte compteur precedent est restaure automatiquement.

### Validation
- Compilation Python validee sur `src/photoselect/app.py`.

---

## Mise a Jour - 20 avril 2026 (optimisation demarrage)

### Travaux realises

1. **Affichage UI prioritaire**
- L'autochargement de la base au lancement est différé juste après le rendu initial de l'interface.
- Le bloc lourd de chargement n'empêche plus l'apparition de la fenêtre principale.

2. **Doublons en fond au démarrage**
- La préparation/reconstruction des doublons au lancement passe entièrement en asynchrone.
- Suppression du traitement synchrone d'index doublons qui pouvait bloquer au boot.

3. **Requêtes startup allégées**
- Ajout de méthodes DB dédiées (`get_photo_count`, `has_missing_collection_names`) pour éviter de charger toutes les lignes juste pour des checks de démarrage.

### Validation
- Compilation Python validée sur `src/photoselect/app.py` et `src/photoselect/database.py`.

---

## Mise a Jour - 20 avril 2026 (tri + audit GPS sans lieu)

### Travaux realises

1. **Tri tableau**
- Après tri sur une colonne, sélection automatique de la première ligne.
- Le tableau est recentré/affiché sur cette première ligne.

2. **Audit intégrité enrichi**
- Détection des photos ayant GPS mais aucun champ `Pays/Région/Ville`.
- Ajout d'une action de correction offline (base locale uniquement, sans service en ligne).
- Ajout d'une option indépendante pour écrire l'EXIF lors de ce complément offline.
- Les options d'audit/réconciliation restent activables individuellement via cases à cocher.

3. **Audit rapide constaté (base utilisateur)**
- Sur la base active: volume important de photos avec GPS mais sans lieu en DB.
- Cause probable: géocodage local indisponible/partiel au moment de certains scans.

### Validation
- Compilation Python validée sur `src/photoselect/app.py`.

---

## Mise a Jour - 20 avril 2026 (mode GPS par scan)

### Travaux realises

1. **Choix operateur au lancement scan/re-scan**
- A chaque clic sur `Scanner / Reprendre` ou `Vider / Re-scanner`, une boite de dialogue demande le mode GPS:
  - Oui = local,
  - Non = en ligne,
  - Annuler = abandon du scan.

2. **Retour automatique en ligne apres scan**
- A la fin du scan (ou re-scan), la selection UI repasse automatiquement sur `En ligne`.
- Le mode est egalement persisté en config (`geo_mode = online`).

### Validation
- Compilation Python validee sur `src/photoselect/app.py`.

### Impact utilisateur
- Navigation et tri plus coherents avec les selections reelles exportees.
- Moins de ressaisies de chemins grace aux listes persistantes.
- Carte nettement plus utile pour filtrer et explorer des lots geolocalises.

---

## Mise a Jour - 21 avril 2026 (chantier Albums virtuels)

### Objectif fonctionnel couvert
- Ajouter un mode Albums virtuels de type Picasa:
  - albums logiques (sans duplication physique),
  - appartenance multiple d'une photo,
  - ajout depuis menus contextuels image/tableau,
  - renommage/suppression d'albums,
  - suppression en vue Albums = retrait de l'album, pas suppression fichier.

### Architecture et data model

1. **Schema SQLite (base source active)**
- `virtual_albums`:
  - `id`, `name`, `normalized_name`, `created_at`, `updated_at`.
- `virtual_album_members`:
  - `album_id`, `photo_id`, `sort_order`, `added_at`, PK `(album_id, photo_id)`.
- Index dedies:
  - nom album (tri/recherche),
  - ordre des membres par album,
  - lookup albums par photo.

2. **API DB ajoutee (`database.py`)**
- Lecture:
  - `list_virtual_albums()`,
  - `get_virtual_album(...)`,
  - `get_virtual_album_by_name(...)`,
  - `get_virtual_album_photo_ids(...)`,
  - `get_virtual_albums_for_photo(...)`.
- Ecriture:
  - `create_virtual_album(...)`,
  - `rename_virtual_album(...)`,
  - `delete_virtual_album(...)`,
  - `add_photos_to_virtual_album(...)`,
  - `remove_photo_from_virtual_album(...)`.
- Nettoyage:
  - suppression des memberships dans `delete_photo(...)` pour eviter les orphelins.

### UI et comportements

1. **Sidebar Albums (entre logo et carte)**
- Nouvelle colonne `Albums` dans `frame_config`.
- Listbox largeur 25 caracteres + scrollbar.
- Scroll molette + clavier.
- La carte est deplacee sur la colonne suivante et s'adapte a l'espace restant.

2. **Scope Affichage `virtual_album`**
- Radio `Albums` ajoute dans la ligne Affichage.
- Clic sur un album dans la sidebar:
  - active l'album,
  - bascule le scope sur `virtual_album`,
  - recharge tableau + visionneuses.
- Chargement des lignes via `photo_id` DB reel (pas de rows negatives comme le mode Sélection physique).

3. **Menus contextuels: Ajouter a l'album**
- Ajout de l'action `Ajouter à l'album...` dans:
  - menu clic droit visionneuse,
  - menu clic droit tableau,
  - menu orientation vignette/logo.
- Dialogue ajoute:
  - filtre texte,
  - liste cliquable des albums,
  - creation a la volee si nom non present.

4. **Gestion Albums dans la sidebar**
- Menu contextuel liste Albums:
  - `Renommer l'album (F2)`,
  - `Supprimer l'album`.
- Raccourcis F2/Suppr sur la listbox Albums.
- Suppression d'album non destructive pour les fichiers source.

5. **Suppression en vue Albums**
- `delete_current()` route vers `_remove_selected_from_current_virtual_album()` quand scope `virtual_album`.
- Le fichier source reste en place; seule l'appartenance album est retiree.
- Les labels de menu suppr deviennent dynamiques (`Retirer de l'album` en vue Albums).

### Fichiers impactes (lot Albums virtuels)
- `src/photoselect/database.py`
- `src/photoselect/app.py`
- `README.md`

### Validation executee
- Compilation Python validee sur:
  - `src/photoselect/app.py`,
  - `src/photoselect/database.py`.
- Scenarios verifies:
  - instanciation UI avec sidebar Albums,
  - chargement d'un album virtuel depuis DB temporaire,
  - bascule scope via clic sidebar,
  - retrait non destructif par Suppr en vue Albums,
  - renommage/suppression d'album via wrappers UI.

### Etat de reprise pour prochain fil
- Le chantier Albums virtuels est fonctionnel et pret a etre etendu.
- Suites logiques recommandees:
  1. reordonnancement manuel intra-album (drag/drop ou commandes de deplacement),
  2. outils de gestion multi-albums (ajout/retrait en masse),
  3. enrichissement visuel sidebar (badges, compteur actif, tri configurable).

---

## Mise a Jour - 20 avril 2026 (logo dynamique)

### Travaux realises

1. **Slot logo remplace par miniature active**
- Une fois l'application initialisee, la zone logo affiche la photo active (miniature) a la place du logo statique.
- La miniature est contrainte a la meme taille que le logo d'origine.
- Aucun deplacement de layout: meme colonne, meme cadre, meme emprise UI.

2. **Sync miniature active**
- Mise a jour lors des changements de photo (tableau, navigation, visionneuses, mode mosaïque/photo).
- Retour au logo par defaut quand il n'y a plus de photo active.

3. **Rappel process documentation**
- README, HANDOFF et ENDOFF doivent etre mis a jour a chaque ajout de fonctionnalite utilisateur.

4. **Reindex sélection: nettoyage des dossiers inexistants**
- Pendant `Réindexer sélection`, les repertoires de sélection qui n'existent plus sont purges automatiquement:
  - du registre `saved_paths` (kind=`album_output`),
  - du selecteur `Dossier sélection`,
  - des tables `album_index` et `album_audit_state`.
- Si le dossier actuellement selectionne n'existe plus, la selection est basculee sur le premier dossier valide (ou vide si aucun).

### Validation
- Compilation Python validee sur `src/photoselect/app.py`.
---

## Mise a Jour - 21 avril 2026 (moteur thématique IA)

### Travaux réalisés

1. **Moteur d'Analyse Thématique (`theme_engine.py`)**
- Intégration d'un moteur basé sur OpenCV et des modèles ONNX (MobileNetV2) pour la classification ImageNet.
- Mapping des labels ImageNet vers 5 thèmes principaux: `Animaux`, `Insectes`, `Fleurs`, `Temples`, `Eglises`.
- Analyse asynchrone lancée via le bouton `Classifier themes IA`.
- Seuil de confiance configurable (défaut 0.50).

2. **Intégration UI / Workflow**
- Bouton dédié dans le panneau de contrôle.
- Arrêt automatique du moteur de visages avant lancement (pour éviter surcharge CPU/GPU).
- Création automatique des albums virtuels portant le préfixe `[IA]`.
- Progression affichée dans la barre d'état avec ETA.

### Points d'attention déploiement
- Les modèles ONNX et labels JSON doivent être présents dans `src/photoselect/assets/models/`.
- Le spec PyInstaller est configuré pour inclure ces fichiers.
- Dépendance `opencv-python` ajoutée au workflow de build.

---

## Mise a Jour - 22 avril 2026 (synchronisation mosaique / sidebar visages)

### Diagnostic

Audit complet de la synchronisation entre la mosaique (onglet Visages), les sidebars et la selection active.
5 bugs racines identifies:
1. `winfo_y()` retourne 0 quand l'onglet Visages est masque (widgets non mappes par Tkinter).
2. Deux systemes de sidebar paralleles desynchronises (Listbox principale vs Treeview onglet Visages).
3. `_on_faces_sidebar_select` (Treeview) ne declenchait pas de changement de personne active.
4. Delais `after(100/300)` fragiles pour le defilement post-reconstruction.
5. Reconstruction de la sidebar Treeview declenchant des evenements parasites `<<TreeviewSelect>>`.

### Correctifs appliques

1. **Scroll differe via `<Map>` (remplacement des `after()`)** 
   - Fichier: `src/photoselect/app.py`
   - Ajout de `_pending_scroll_person_id` pour memoriser la cible de scroll.
   - Ajout du handler `_on_faces_tab_mapped()` lie a l'evenement `<Map>` sur `faces_tab_grid`.
   - Quand l'onglet Visages est masque, le scroll est differe automatiquement.
   - Quand l'onglet devient visible, `<Map>` declenche le scroll via `after_idle`.
   - Suppression des `after(100)` (L7399) et `after(300)` (L8340) fragiles.

2. **Guard `winfo_ismapped()` dans `_scroll_to_face_person_block`**
   - Le scroll est refuse si le canvas n'est pas mappe (retourne des coordonnees nulles).
   - Guard supplementaire sur `h_grid <= 0` (layout pas encore calcule).
   - En cas de refus, la cible est memorisee dans `_pending_scroll_person_id`.

3. **Unification des sidebars (Phase 2)**
   - `_on_faces_sidebar_select` (Treeview de l'onglet Visages) declenche maintenant:
     - la mise a jour de `_active_face_person_id`,
     - le scroll de la mosaique,
     - la synchronisation de la sidebar principale.
   - Ajout de `get_face_person_by_name()` dans `database.py` pour la resolution nom -> ID.

4. **Guard `_faces_sidebar_syncing` (Phase 3)**
   - Nouveau flag `_faces_sidebar_syncing` initialise dans `__init__`.
   - Active dans `_refresh_faces_tab` pendant le rebuild du Treeview.
   - `_on_faces_sidebar_select` ignore les evenements quand ce flag est actif.
   - Supprime les sauts de selection parasites pendant les insertions.

### Fichiers impactes
- `src/photoselect/app.py` (6 zones modifiees)
- `src/photoselect/database.py` (1 methode ajoutee)

### Validation
- Compilation Python validee sur `app.py` et `database.py`.

### Etat pour reprise
- Le systeme de scroll differe via `<Map>` est robuste et ne depend plus de delais arbitraires.
- La sidebar Treeview de l'onglet Visages est maintenant un point de navigation actif.
- Les deux sidebars (principale et onglet Visages) restent synchronisees.
- Points d'evolution possibles:
  1. Reordonnancement drag/drop intra-album.
  2. Consolidation du double-sidebar en sidebar unique partagee.

---

## Mise a Jour - 22 avril 2026 (reprise synchro mosaique en mode photo / visages)

### Contexte
- Correction interrompue reprise sur le point suivant: en mode `photo`, la mosaique pouvait rester stale apres changement de selection dans le scope `Visages`.

### Correctif applique
- Fichier: `src/photoselect/app.py`
- Dans `_display_on_viewers(...)`, branche `mode=photo`:
  - ajout d'un marquage `self._grid_scope_dirty = True` quand `viewer_scope == "faces"`.
  - effet: au prochain passage en mode `multiple`, `_on_viewer_mode_changed()` invalide puis reconstruit la mosaique sur la selection courante.

### Validation
- Verification statique: aucune erreur dans `src/photoselect/app.py` apres patch.

### Point de verification UX
1. Se placer en scope `Visages`, mode `photo`.
2. Changer de ligne dans le tableau (ou de personne via sidebar) sans passer tout de suite en mosaique.
3. Basculer en mode `multiple`.
4. Confirmer que la mosaique se recale bien sur la photo/portee active actuelle.

### Durcissement supplementaire (22 avril 2026 - selection aleatoire)
- Fichier: `src/photoselect/app.py`
- Correctif anti-saut de selection dans l'onglet Visages:
  - restauration de la selection Treeview par **ID/personne active** (plus par nom precedent),
  - nettoyage explicite des selections residuelles si la cible n'existe plus,
  - verrou pendant rebuild pour eviter les evenements parasites.
- Effet attendu: plus de bascule spontanee sur la derniere ligne quand la reconnaissance de fond est active.

---

## Mise a Jour - 22 avril 2026 (GPS lien + bug mosaïque première page)

### Travaux realises

1. **Colonne GPS — correction couleur toute-ligne (bug)**
- Le tag `gps_link` avec `foreground="#1d4ed8"` colorait **toute la ligne** car les tags ttk.Treeview sont niveau-ligne.
- Correction: suppression du `tag_configure("gps_link", foreground=...)`.
- Ajout d'un préfixe `🔗` dans le texte de la cellule GPS dans `_build_tree_values` (branche `album_virtual=False`).
- Même préfixe dans la mise à jour inline `vals[col_idx["gps_coords"]]` dans `_rename_single`.
- Résultat: seule la cellule GPS affiche visuellement l'indicateur lien.
- Le curseur `hand2` sur survol colonne GPS et l'en-tête `🌐` restent inchangés.
- Limitation connue documentée: ttk.Treeview ne supporte pas l'attribut `underline` par cellule.

2. **Bug mosaïque — première page vide en mode Continu (non résolu)**
- Symptôme rapporté: en mode Mosaïc + affichage Continu, la première page ne se remplit pas.
- Second symptôme: il manque également une image sur la deuxième page.
- Investigation réalisée (lecture code) mais **aucun correctif appliqué** dans cette session.
- Piste identifiée: `_display_multiple_grid` calcule `canvas_w/canvas_h` via `winfo_width()/winfo_height()` — ces méthodes retournent 1 si la fenêtre n'est pas encore rendue → `page_size = 1` → première page quasi vide.

### Fichiers impactes
- `src/photoselect/app.py` (préfixe 🔗 GPS dans `_build_tree_values` et `_rename_single`)

### Validation
- Verification statique: aucune erreur compile dans `src/photoselect/app.py`.

### Point de reprise prioritaire (mosaïque)
1. Clarifier avec l'utilisateur ce que signifie "Continuer" — bouton, PageDown, ou clic dans la visionneuse.
2. Vérifier `_display_multiple_grid` lignes ~1220 : si `canvas_w <= 1`, utiliser `winfo_screenwidth()` ou forcer `deiconify()` + `update_idletasks()` avant le calcul de layout.
3. Vérifier si `page_size` est bien cohérent avec les deux symptômes (première page sous-chargée, deuxième aussi incomplète).
4. Confirmer que le fix ne casse pas le comportement normal (fenêtre déjà visible).

---

## Mise a Jour - 22 avril 2026 (resolution mosaïque sous-remplie)

### Diagnostic verifie

1. **La cause n'etait pas le layout initial**
- Verification via `runtime.log`: la mosaïque se construisait bien en `1920x1080`.
- Les sous-remplissages venaient de fichiers `.jpg` presents dans le scope mais illisibles par Pillow.

2. **Cause directe du sous-remplissage**
- Dans `_display_multiple_grid`, la page etait tranchee avant rendu (`page_images = images[start:end]`).
- Si une miniature echouait au chargement, le code faisait `continue` sans aller chercher l'image valide suivante.
- Effet visible: 36 photos sur la premiere page, puis 39 sur la suivante, selon le nombre de JPG illisibles dans chaque tranche.

### Correctif applique

1. **Cache local des chemins illisibles en mosaïque**
- Ajout de `self._grid_unreadable_paths` dans `FullscreenViewer`.

2. **Pagination/rendu mosaïque durcis**
- Filtrage des chemins deja reconnus illisibles avant navigation mosaïque.
- Resolution de l'ancre sur la photo lisible la plus proche si la photo cible est elle-meme illisible.
- Boucle de rendu modifiee pour completer la page avec les images valides suivantes jusqu'a remplir l'ecran.

3. **Logs enrichis**
- Le log `build_grid` indique maintenant `raw_count`, `displayable_count` et `unreadable_cached`.

### Validation

- `python -m py_compile src/photoselect/app.py` : OK.
- Harness Python cible sur `_display_multiple_grid` : avec 42 fichiers dont 2 faux JPG dans les 40 premiers, la page affiche bien 40 miniatures et backfill avec les suivantes.

---

## Mise a Jour - 22 avril 2026 (audit illisibles via DB)

### Besoin utilisateur

- Ajouter dans l'audit intégrité une ligne dédiée aux fichiers illisibles.
- Pouvoir copier ces fichiers vers un dossier `_PhotoSelect/Illisibles`.
- Proposer en option une suppression définitive de l'original après copie.
- S'appuyer sur la base SQLite pour localiser les fichiers, sans rescanner l'arborescence source.

### Correctif applique

1. **Détection illisibles intégrée à l'audit principal**
- Nouvelle option d'audit: `Vérifier fichiers illisibles par la visionneuse (via la base)`.
- L'audit passe sur `rows = self.db.get_all_photos(...)` filtrées par `_get_rows_for_scope_audit`, puis teste la lisibilité avec `Pillow.verify()`.
- Résultat transporté dans `unreadable_entries` jusqu'à la phase de réconciliation.

2. **Quarantaine `_PhotoSelect/Illisibles`**
- Ajout des helpers `_get_unreadable_audit_dir`, `_ensure_unreadable_audit_dir` et `_build_unreadable_copy_target`.
- Les copies conservent le chemin relatif source sous `_PhotoSelect/Illisibles/...` pour faciliter la localisation.
- En cas de collision, suffixe `__bad-0001`, `__bad-0002`, etc.

3. **Réconciliation**
- Nouvelle case: `Copier les fichiers illisibles vers _PhotoSelect/Illisibles`.
- Sous-option non persistante: `Supprimer définitivement les originaux illisibles après copie`.
- Si suppression activée: suppression disque définitive + `db.delete_photo(photo_id)`.
- Les doublons à déplacer sont filtrés pour ne pas retraiter les mêmes `photo_id` quand une action illisible a été choisie.

4. **Résumé / stats**
- Le résumé d'audit affiche maintenant `Fichiers illisibles visionneuse`.
- Le message final de réconciliation détaille: détectés, copiés, suppressions définitives, suppressions DB, échecs copie/suppression.
- `stats_last_integrity_audit` inclut maintenant le compteur `illisibles` corrigés.

### Validation

- `python -m py_compile src/photoselect/app.py` : OK.
- Harness Python ciblé:
  - un JPG corrompu est bien détecté dans `unreadable_entries`,
  - le chemin de copie est construit sous `_PhotoSelect/Illisibles/<chemin_relatif>`.
