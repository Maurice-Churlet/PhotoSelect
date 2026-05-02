# PhotoSelect — Journal de Session (17 avril 2026)

## Mise a jour — 2 mai 2026 (passation rapide)

- Correction appliquee: les photos retirees manuellement des albums auto (themes) ne sont plus re-ajoutees par les scans automatiques.
- Correction appliquee: exclusions manuelles egalement prises en compte pour les attributions visages (suppression/reaffectation).
- Correction appliquee: dedoublonnage par nom de fichier dans les albums virtuels et pour les groupes personnes visages.
- Correctif UX applique: le radio bouton scope `Visages` reste visible meme quand l'autorisation de reconnaissance faciale est decochee.

### Etat en cours
- Une boucle de synchronisation UI persiste sur le scope visages (selection tableau/sidebar repetee dans certains parcours).
- Instrumentation de debug ajoutee (`[sync]`, `[mosaic]`) pour tracer les causes (appelant + raison refresh).
- Un premier garde anti-refresh hors onglet visages est en place.
- Un garde anti-evenements de selection programmes a ete ajoute sur les sidebars visages/albums.

### Prochaine etape recommandee
- Ajouter un anti-burst final sur la selection tableau visages (debounce court sur meme `photo_id` + meme scope) si des repetitions residuelles sont confirmees.

## Résumé

Session de développement intensif portant sur l'ajout de fonctionnalités majeures et la stabilisation de l'interface multi-écrans.

### Mise à jour — 22 avril 2026
- Le gel UI avec le watcher venait d'un trou dans le filtrage: `on_deleted` ne filtrait pas `_PhotoSelect`, contrairement aux créations/déplacements.
- Les suppressions techniques déclenchaient alors des synchronisations DB inutiles puis un rechargement complet du tableau, même sans changement réel.
- Correctif appliqué dans `src/photoselect/app.py`:
	- exclusion `_PhotoSelect` aussi sur suppression watcher,
	- garde côté méthodes `_sync_*_from_watcher`,
	- pas de refresh UI si aucune ligne DB n'est supprimée,
	- refresh UI watcher regroupé pour éviter les rafales.
- Validation:
	- compilation Python OK sur `src/photoselect/app.py`,
	- snippet Python OK confirmant qu'un chemin sous `_PhotoSelect/Illisibles` est ignoré par le watcher.

## Travaux Réalisés

### 1. Barre de progression repositionnée
- Déplacée sous le tableau, occupant toute la largeur de la fenêtre.
- Les compteurs (état + exports) sont affichés en dessous.

### 2. Scan interruptible avec reprise intelligente
- **Reprise** : Compare les fichiers du disque avec ceux déjà en base SQLite. Ne scanne que les manquants.
- **Arrêt propre** : `_stop_event` signale au thread de scan de s'arrêter. Le batch en cours est sauvegardé.
- **`update_collection_names()`** est maintenant appelé même après une interruption, ce qui résout le bug des `collection_name = NULL`.
- Distinction entre `Scanner / Reprendre` (resume=True) et `Actualiser` (resume=False, efface la DB).

### 3. Statistiques en temps réel pendant le scan
- Affichage : `Traitées : X/Y | Vitesse : Z /h | Temps restant : HH:MM:SS`
- Mise à jour toutes les 10 photos.

### 4. Suppression vers la corbeille (`send2trash`)
- **Photo copiée** → supprime la copie, garde l'original, démarque dans la DB.
- **Photo non copiée** → supprime l'original vers la corbeille, supprime de la DB, navigation automatique vers la suivante.

### 5. Bouton "Renommer originaux"
- Renomme physiquement les fichiers source avec leur `collection_name`.
- Met à jour `filepath` et `filename` dans la DB.
- Confirmation obligatoire avant exécution.

### 6. Format des noms de collection corrigé
- **Avec GPS** : `AAMMJJ_Pays_Region_Ville-XXX`
- **Sans GPS** : `AAMMJJ_NomFichierOriginal-XXX`
- Doublons sur 3 chiffres (`-000`, `-001`, ...).

### 7. Interface boutons
- Tous les boutons en noir sur fond gris (pas de couleurs).
- Disposition en grille 2×2 : Scanner, Actualiser, Renommer, Stopper.
- Bouton Stop actif uniquement pendant les opérations longues.

### 8. Plein écran multi-moniteur fiable
- Abandon de `attributes("-fullscreen")` (forçait l'écran principal sous Windows).
- Remplacement par `overrideredirect(True)` + détection du moniteur via `ctypes.windll.user32.MonitorFromPoint`.
- F11, F12, Double-clic et Escape fonctionnent sur le moniteur courant.
- `SetProcessDpiAwareness(2)` activé pour la cohérence des coordonnées sur écrans avec DPI différents.

### 9. Persistance des fenêtres
- Panneau principal : sauvegarde `geometry` + `state` (zoomed/normal).
- Visionneuse : sauvegarde immédiate à chaque fermeture (croix). Position + état fullscreen.
- Les deux fenêtres se ferment ensemble quand on ferme le panneau principal.

### 10. Auto-chargement au démarrage
- Si la DB existe, elle est chargée automatiquement.
- Si des `collection_name` sont NULL, ils sont recalculés avant affichage.

### 11. Doublons (signature taille+titre+date)
- Ajout d'un index persistant des doublons en base SQLite.
- Reconstruction automatique en fin de scan et au lancement pour anciennes bases.
- Ajout d'un scope d'affichage `Doublons` (tableau + visionneuses).
- Audit intégrité enrichi avec comptage des doublons signature (photos + groupes).
- Statistiques enrichies avec indicateur de doublons signature.

### 12. Suppression et exclusion Sélection
- Après suppression d'une photo source, l'index doublons est recalculé.
- En mode `Doublons`, le tableau se rafraîchit immédiatement après suppression.
- Les photos du dossier `Dossier sélection` sont exclues du calcul des doublons actifs.

## Commits

| Hash | Message |
|---|---|
| `a0c4cc2` | feat: scan interruptible avec reprise, suppression vers corbeille, renommage, plein ecran multi-moniteur DPI-aware |
| `ecac2cb` | Initialisation du projet PhotoSelect |

## Problèmes Résolus

1. **collection_name toujours NULL** → `update_collection_names()` n'était pas appelé après interruption ni au démarrage.
2. **Visionneuse à cheval sur deux écrans** → `attributes("-fullscreen")` remplacé par `overrideredirect` + ctypes.
3. **Plein écran trop petit sur écran secondaire** → `SetProcessDpiAwareness(2)` ajouté.
4. **Tableau pas restauré en maximisé** → `state("zoomed")` sauvegardé et restauré.
5. **Erreurs sur fichiers non-image** → Affichage d'images pendant le scan retiré.

## Fichiers Modifiés

- `src/photoselect/app.py` — Refonte majeure (GUI, scan, persistance, plein écran)
- `src/photoselect/database.py` — Nouveau fichier (SQLite, collection_names, CRUD)
- `src/photoselect/exif_reader.py` — Stable (EXIF + GPS + geocoding)
- `src/photoselect/file_manager.py` — Stable (listage fichiers)
- `pyproject.toml` — Ajout dépendance `send2trash`
- `requirements.txt` — Mis à jour
- `README.md` — Avertissement suppression originaux

---

## Mise à Jour — 19 avril 2026

### Travaux réalisés

1. **Audit d'intégrité en arrière-plan**
- L'analyse d'intégrité ne bloque plus l'interface.
- Exécution déplacée en thread de fond avec remontée de progression toutes les 10 photos.
- Affichage d'une **estimation du temps restant** dès que suffisamment d'éléments ont été traités.

2. **Bouton Arrêt généralisé**
- Le bouton "Stopper l'action" ne sert plus seulement au scan.
- Annulation câblée sur les opérations longues:
	- audit / réconciliation,
	- normalisation des lieux,
	- collage géolocalisation multi-sélection,
	- édition batch.

3. **Ajustement UI des actions**
- Permutation des boutons demandée:
	- `Aide (F1)` <-> `Exporter journal`
	- `Audit intégrité` <-> `Stopper l'action`

### Validation
- Compilation Python validée sur `src/photoselect/app.py`.

---

## Mise à Jour — 21 avril 2026 (passation scan/visages)

### Résumé exécutable
- Le comportement `scan rapide / 0 résultat` est désormais **cohérent** avec l'état réel des données sur le poste de référence:
	- disque: 33658 images,
	- base: 33658 lignes,
	- delta: 0.
- Le faux delta précédemment observé provenait majoritairement des miniatures sous `_PhotoSelect/Visages`.

### Correctifs appliqués

1. **Exclusion scan des dossiers techniques PhotoSelect**
- Ajout de `_photoselect` dans les dossiers ignorés du scan récursif (`file_manager.py`).
- Effet: les miniatures visages et sorties techniques ne polluent plus le scan source.

2. **Classification thèmes: arrêt moteur visages asynchrone**
- Le lancement de classification n'est plus immédiat si le worker visages tourne.
- Nouveau flux:
	- demande d'arrêt,
	- attente en tâche de fond (poll non bloquant),
	- démarrage classification uniquement après arrêt réel,
	- timeout => annulation propre sans démarrage.

3. **Scan: robustesse aux erreurs fichier**
- Le scan ne s'arrête plus au premier fichier en erreur EXIF/métadonnées.
- Traitement par fichier avec comptage `erreurs`, logs dédiés et poursuite du lot.

4. **Logs runtime persistants**
- Ajout d'un log fichier applicatif:
	- `%APPDATA%/PhotoSelect/logs/runtime.log`
- Traces ajoutées sur:
	- démarrage app,
	- stop visages,
	- démarrage scan,
	- volumes scan (bruts/exclus/gardés),
	- reprise (en_base/nouveaux),
	- fin scan.

5. **Message de fin scan “collant”**
- Le résumé de fin scan est maintenu en barre de statut plusieurs secondes pour éviter son écrasement immédiat par le compteur contextuel.

### Validation effectuée
- Compilation OK: `app.py`, `file_manager.py`.
- Vérification hors UI (snippet Python) du volume disque/base:
	- `disk_count=33658`, `db_count=33658`, `delta=0`.

### Point de reprise (prochain fil)
1. Faire un test utilisateur final `Scanner / Reprendre` pour confirmer que le message collant est bien visible côté UI.
2. Si la perception “scan raté” persiste, ajouter une popup de fin scan explicite (mode reprise) avec:
	 - `nouveaux`, `déjà en base`, `erreurs`.
3. Option produit à arbitrer: mode `Reprise pilotée DB uniquement` (sans découverte disque de nouveaux fichiers) versus mode hybride actuel.

---

## Mise à Jour — 20 avril 2026 (passation avant nouveau fil)

### Travaux réalisés

1. **Audit intégrité / réconciliation**
- Options d'audit persistées en config (fonction par fonction).
- Ajout d'une case **"Corriger les défauts détectés (si possible)"** dans la fenêtre de sélection.
- En mode auto-correction, application automatique des corrections détectées.
- Fenêtres audit/réconciliation centrées (fiabilisation du centrage).
- Ajout d'un badge visuel "Auto-correction active".

2. **Stats du dernier audit**
- Ajout d'une ligne `Dernier audit intégrité` dans l'onglet Statistiques.
- Résumé du dernier audit persisté en config et restauré au démarrage.

3. **Doublons / status**
- Correctif du message bloqué `Préparation doublons...` après fin de préparation.
- Restauration du message compteur précédent hors scope Doublons.

4. **Visionneuse mode mosaïque**
- Libellé utilisateur `Multiple` remplacé par `Mosaïc` dans l'UI et les messages visibles.
- Correctif du status qui restait sur "Visionneuse Mosaïc" après retour en mode Photo.

5. **Orientation photo (nouvelle fonctionnalité)**
- Menu clic droit orientation ajouté sur:
	- visionneuse Photo,
	- visionneuse Mosaïc,
	- vignette photo (slot logo).
- Actions: normaliser EXIF, rotation 90° droite, 90° gauche, 180°.
- Correction écrite dans le fichier + EXIF Orientation remis à 1.
- Rafraîchissement immédiat des 2 visionneuses + logo + UI.
- Raccourcis clavier ajoutés: `Ctrl+R`, `Ctrl+L`, `Ctrl+U`.

6. **Bloc sélecteurs en haut**
- Les lignes `Dossier Source`, `Dossier sélection`, `Rechercher` restent en haut, l'une sous l'autre, avec interligne normal.

### Validation
- Compilation Python validée sur `src/photoselect/app.py` et `src/photoselect/database.py`.

---

## Mise à Jour — 20 avril 2026 (coffee prompt + QR BTC)

### Travaux réalisés

1. **Phrase aléatoire centrée**
- Dans la barre de conversation, affichage d'une phrase tirée aléatoirement depuis `src/photoselect/_resources/coffee.md`.
- Préfixe fixe: `Clic ici `.
- Renouvellement aléatoire à chaque interaction utilisateur.

2. **Fenêtre de don BTC au clic**
- Le clic sur la phrase ouvre une fenêtre dédiée avec:
	- le texte de soutien demandé,
	- le QR code `src/photoselect/assets/QR_BTC.jpg`.

### Validation
- Compilation Python validée sur `src/photoselect/app.py`.

---

## Mise à Jour — 20 avril 2026 (mode GPS par scan)

### Travaux réalisés

1. **Choix GPS au démarrage de scan/re-scan**
- Une boîte de dialogue demande le mode de géolocalisation à l'opérateur à chaque lancement:
	- Oui = local,
	- Non = en ligne,
	- Annuler = pas de scan.

2. **Retour automatique sur En ligne après scan**
- Une fois le scan terminé, la sélection Géo repasse automatiquement sur `En ligne`.
- La configuration est sauvegardée dans cet état.

### Validation
- Compilation Python validée sur `src/photoselect/app.py`.

---

## Mise à Jour — 20 avril 2026 (logo dynamique)

### Travaux réalisés

1. **Miniature active dans le slot logo**
- Après initialisation complète, la zone logo est remplacée par une miniature de la photo active.
- Taille strictement identique au logo d'origine.
- Mise en page inchangée (aucun déplacement du plan UI).

2. **Synchronisation visuelle**
- La miniature se met à jour au changement de photo active.
- En absence de photo active, retour automatique au logo par défaut.

3. **Mise à jour documentation systématique**
- README, HANDOFF et ENDOFF mis à jour pour refléter les nouvelles fonctionnalités.

4. **Réindexation sélection avec purge des dossiers obsolètes**
- La réindexation supprime désormais automatiquement les dossiers de sélection inexistants:
	- de la liste du sélecteur,
	- de la base des chemins enregistrés,
	- des tables d'index/audit album.
- La sélection `Dossier sélection` est remise en cohérence si le dossier courant a disparu.

### Validation
- Compilation Python validée sur `src/photoselect/app.py`.

---

## Mise à Jour — 20 avril 2026

### Travaux réalisés

1. **Mode Affichage Sélection**
- Ajout d'un mode radio `Sélection` sur la ligne Affichage.
- En mode Sélection, les visionneuses affichent le contenu du dossier sélectionné dans `Dossier sélection`.
- Si le mode Sélection est actif, un changement du sélecteur `Dossier sélection` met immédiatement à jour l'affichage.
- La navigation clavier en visionneuse (gauche/droite) suit l'ordre des photos du dossier de sélection.

2. **Sélecteurs de dossiers persistants**
- Les champs `Dossier Source` et `Dossier sélection` utilisent désormais des combobox triées alphabétiquement.
- Les chemins choisis via `Parcourir` sont mémorisés dans une base locale dédiée (`_path_registry.sqlite3`).

3. **Planisphère interactif amélioré**
- Rendu précis des pays via GeoJSON local (`assets/world_countries.geojson`).
- Zoom à la molette sur la carte et zoom automatique sur la zone de sélection.
- Réinitialisation de zone via double-clic carte ou clic logo.
- Les points GPS restent visibles après sélection de zone.

4. **Robustesse data / scan**
- Correctif de copie image vers le presse-papiers Windows (visionneuses + tableau).
- Purge durcie pour supprimer aussi la DB legacy `.photoselect.db`.
- Flux `Vider / Re-scanner` sécurisé pour éviter le rechargement involontaire de l'ancienne base.

### Validation
- Compilation Python validée sur `src/photoselect/app.py`.

### Commit de passation
- Voir l'historique git pour le commit de passation de ce lot.

---

## Mise à Jour — 19 avril 2026 (suite)

### Travaux réalisés

1. **Rework zone haute UI**
- Champs source/sortie/recherche compactés.
- Boutons `Parcourir`/`Effacer` uniformisés et rapprochés des champs.
- Boutons d'actions réorganisés en 3 colonnes.
- Logo maintenu en haut de page.

2. **Popup de guidage au lancement**
- Fenêtre d'information affichée en fin d'initialisation, au premier plan.
- Consignes de placement des 3 fenêtres.
- Case `Ne plus afficher` persistée pour les lancements suivants.

3. **Synchronisation visionneuses optimisée**
- Correction des rechargements inutiles lors des bascules photo/mosaïque.
- En mode photo, la mosaïque cachée n'est plus synchronisée au clic tableau.
- Sync mosaïque au premier plan uniquement si la photo sélectionnée n'est pas déjà dans la page courante.

4. **Interaction Maps du tableau**
- Ouverture Maps limitée au clic sur la colonne `Coordonnées GPS`.
- Suppression de l'ouverture Maps depuis `Pays`, `Région`, `Ville`.

5. **Colonnes tableau**
- `Taille` déplacée juste avant `Appareil`.

### Validation
- Compilation Python validée sur `src/photoselect/app.py`.

---

## Mise à Jour — 21 avril 2026 (batch visages + startup)

### Travaux réalisés

1. **Batch de modifications Visages**
- Les actions `renommer` et `supprimer` dans l'onglet Visages passent désormais par un marquage local puis une exécution groupée.
- Ajout d'une barre batch dédiée avec résumé des actions, bouton d'exécution et remise à zéro des marquages.
- Ajout d'un éditeur inline avec suggestions de noms existants (hors `Personne xxxx`).
- La suppression batch retire uniquement la vignette visage et sa référence DB, jamais la photo source.

2. **Scan source et exclusions techniques**
- Les dossiers `Dossier sélection`, `Visages` et `Visages/Doublons` peuvent être imbriqués sous la source.
- Ils sont exclus du scan, du comptage et du nettoyage DB source.

3. **Doublons à la demande uniquement**
- Les traitements lourds de doublons ne partent plus au démarrage, en fin de scan, ni après suppression standard.
- Le recalcul est réservé à l'audit et au scope `Doublons`.
- L'onglet statistiques affiche `Non calcule (audit ou mode Doublons)` tant qu'aucun calcul explicite n'a eu lieu.

4. **GPS de scan simplifié**
- Le scan global utilise maintenant toujours le mode GPS `local`.
- La boîte de dialogue de choix GPS au démarrage du scan a été supprimée.

5. **Performance de démarrage**
- Initialisation DB allégée au boot: pas de nettoyage source ni de démarrage immédiat des services visages dans le chemin critique.
- Chargement startup via snapshot DB en arrière-plan puis remplissage progressif du tableau par paquets sur le thread UI.
- Démarrage différé du worker visages et du preload visages après hydratation startup.
- Chargement GeoJSON de la carte repoussé après l'hydratation startup, avec fallback de rendu immédiat.
- Nettoyage DB source réintroduit sous forme de maintenance différée après ouverture.

### Validation
- Compilation Python validée sur `src/photoselect/app.py`.
- Smoke tests de lancement validés.
- Sur base réelle (~32998 photos), le snapshot startup est chargé avant le log GeoJSON, confirmant que la carte ne bloque plus le démarrage critique.

### Suite recommandée
- Rendre la visionneuse mosaïque lazy au démarrage.
- Déporter aussi le chargement/redimensionnement du logo hors chemin critique.
- Remplacer davantage de statistiques startup par des agrégats SQL ciblés.

---

## Mise à Jour — 21 avril 2026 (chantier Albums virtuels)

### Travaux réalisés

1. **Modèle de données Albums virtuels (SQLite source)**
- Ajout d'un schéma dédié dans `database.py`:
	- `virtual_albums` (métadonnées album),
	- `virtual_album_members` (relation many-to-many avec `photos`).
- Albums gérés par base source active.
- Ordre d'ajout persistant via `sort_order`.
- CRUD complet: création, renommage, suppression d'album, ajout/retrait de photos, lecture des membres.

2. **Nouvelle colonne UI `Albums` entre logo et carte**
- Ajout d'une sidebar `Albums` (largeur 25 caractères) entre logo et planisphère.
- Scroll souris + clavier sans impact vertical sur les autres blocs UI.
- La carte se compacte pour prendre l'espace restant disponible.

3. **Nouveau scope d'affichage `Albums`**
- Ajout d'un radio `Albums` dans la ligne Affichage.
- Clic sur un album dans la sidebar: bascule immédiate du scope et chargement du tableau/visionneuses.
- Chargement de lignes basé sur les `photo_id` réels de la base source (pas de duplication de fichiers).

4. **Ajout à l'album depuis les menus contextuels**
- Entrée `Ajouter à l'album...` ajoutée:
	- dans la visionneuse (clic droit image),
	- dans le menu du tableau,
	- dans le menu orientation de la vignette/logo.
- Dialogue dédié: liste filtrable des albums + création d'un nouvel album si le nom saisi n'existe pas.

5. **Gestion album côté sidebar (style Picasa)**
- Menu contextuel sur la liste Albums:
	- `Renommer l'album (F2)`,
	- `Supprimer l'album`.
- Raccourcis F2/Suppr sur la liste Albums.
- Suppression d'album non destructive: ne touche pas aux fichiers source.

6. **Comportement Suppr en vue Albums**
- En scope `Albums`, la touche Suppr et l'action menu suppriment uniquement l'appartenance album (retrait du membre), sans envoyer le fichier à la corbeille.
- Hors scope `Albums`, le comportement source/corbeille existant est conservé.

### Validation
- Compilation Python validée sur:
	- `src/photoselect/app.py`,
	- `src/photoselect/database.py`.
- Tests fonctionnels ciblés exécutés:
	- instanciation UI avec sidebar Albums,
	- chargement réel d'un album virtuel,
	- bascule de scope par clic sidebar,
	- retrait non destructif en vue Albums,
	- renommage/suppression d'album.

### État de passation
- Le chantier demandé est implémenté et utilisable.
- Les changements principaux sont dans:
	- `src/photoselect/app.py`,
	- `src/photoselect/database.py`,
	- `README.md`.
- `photoselect_config.json` a évolué localement pendant les tests (runtime).

### Point d'entrée recommandé pour le prochain fil
- Ouvrir le chantier "sélection virtuelle" suivant (évolutions UX):
	- réordonnancement manuel intra-album,
	- éventuels raccourcis d'ajout rapide multi-albums,
	- options d'affichage/tri spécifiques à la sidebar Albums.

---

## Mise à jour — 22 avril 2026 (reprise correctif mosaïque visages)

### Résumé exécutable
- Reprise d'un correctif interrompu: en mode `photo`, la mosaïque cachée pouvait rester désynchronisée après sélection d'une ligne en scope `Visages`.

### Correctif appliqué
1. **Marquage "grid dirty" en mode photo / scope visages**
- Fichier: `src/photoselect/app.py`
- Dans `_display_on_viewers(...)`, branche photo:
	- ajout d'un marquage `self._grid_scope_dirty = True` quand `viewer_scope == "faces"`.
	- effet: au prochain basculement en mode `multiple`, la mosaïque est invalidée et reconstruite sur la sélection active.

### Validation
- Contrôle statique: aucun problème détecté sur `src/photoselect/app.py` après modification.

### Point de reprise
1. Valider en UI le scénario:
	- scope Visages,
	- navigation en mode Photo,
	- bascule en mode Mosaïque,
	- vérification du recalage correct sur la sélection courante.

---

## Mise à Jour — 22 avril 2026 (GPS lien + mosaïque page manquante)

### Travaux réalisés

1. **Colonne GPS — visuel lien cliquable (correction)**
- Constat: le tag `gps_link` (foreground bleu) colorait toute la ligne, pas seulement la cellule GPS.
- Correction: suppression du `tag_configure("gps_link", foreground=...)`.
- Remplacement par un préfixe `🔗` dans le texte de la cellule GPS (`_build_tree_values` + mise à jour inline dans `_rename_single`).
- Le curseur `hand2` au survol de la colonne GPS reste en place.
- L'en-tête `Coordonnées GPS 🌐` reste en place.
- Limitation technique documentée: ttk.Treeview ne supporte pas le soulignement par cellule — le préfixe `🔗` est l'équivalent visuel le plus propre disponible.

2. **Mosaïque mode Continu — première page vide (bug signalé, non résolu)**
- Symptôme: en mode Mosaïc + affichage Continu, cliquer sur "Continuer" ne remplit pas la première page, mais les suivantes s'affichent correctement.
- Deuxième symptôme lié: il manque aussi une photo sur la deuxième page.
- Investigation démarrée: lecture de `_display_multiple_grid`, `_list_view_images`, `_navigate_grid_page`, `_get_visible_ordered_paths`.
- **Non résolu dans cette session** — aucune modification de code pour ce bug.

### État
- La correction GPS (🔗 préfixe) est appliquée et validée (pas de nouvelles erreurs).
- Le bug mosaïque première page vide est à traiter dans le prochain fil.

### Point de reprise (mosaïque)
1. Identifier précisément ce que signifie "Continuer" pour l'utilisateur: bouton UI, PageDown, ou clic dans la visionneuse?
2. Vérifier si le bug est lié à `page_size` calculé sur `winfo_width()/height()` avant que la fenêtre ne soit rendue (valeur 1 retournée si la fenêtre n'est pas encore visible).
3. Piste probable: au premier appel de `_display_multiple_grid`, `canvas_w` / `canvas_h` retournent 1 (fenêtre non encore affichée) → `page_size=1` → une seule image chargée → page "vide" visuellement.
4. Solution suggérée: forcer `deiconify()` avant le calcul du layout, ou utiliser les dimensions de l'écran (`winfo_screenwidth()`) comme fallback quand `winfo_width() <= 1`.

---

## Mise à Jour — 22 avril 2026 (mosaïque Continu résolue)

### Résolution

- Le bug ne venait pas d'une taille de fenêtre à `1x1`.
- Le vrai déclencheur était la présence de JPG illisibles dans la tranche de page mosaïque.
- La boucle de rendu sautait ces fichiers sans compléter la page avec les suivants, ce qui laissait des trous visibles.

### Correctif appliqué

- Ajout d'un cache `self._grid_unreadable_paths` côté `FullscreenViewer`.
- Filtrage de ces chemins avant navigation mosaïque.
- Backfill de la page courante avec les images valides suivantes tant que l'écran peut encore être rempli.
- Logs `build_grid` enrichis (`raw_count`, `displayable_count`, `unreadable_cached`).

### Validation

- Compilation ciblée `app.py` : OK.
- Test Python ciblé sur `_display_multiple_grid` : 40 miniatures bien affichées malgré 2 faux JPG injectés dans les 40 premiers éléments.

---

## Mise à Jour — 22 avril 2026 (audit illisibles)

### Résolution

- L'audit intégrité principal sait maintenant détecter les fichiers illisibles directement depuis les chemins stockés en base.
- Pas de rescan disque global ajouté: la localisation repose sur `photos.filepath` en SQLite.
- Une nouvelle réconciliation permet de copier les illisibles dans `_PhotoSelect/Illisibles`, avec option de suppression définitive de l'original après copie.

### Validation

- `python -m py_compile src/photoselect/app.py` : OK.
- Snippet Python ciblé : détection d'un faux JPG dans `unreadable_entries` + construction du chemin de copie attendu dans `_PhotoSelect/Illisibles/B/bad.jpg`.
