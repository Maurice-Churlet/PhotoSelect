# PhotoSelect

Auteur : Maurice Churlet
mchvalidation@gmail.com
Copyright (c) 2026 Maurice

## Licence

Ce projet est sous licence GNU GPL v3.
Le texte complet de la licence est fourni dans le fichier `licence.txt` du dépôt.

En résumé :
- vous pouvez utiliser, modifier et redistribuer le code
- toute redistribution doit rester sous GPL v3
- le code source doit rester accessible

**PhotoSelect** est un outil performant permettant la sélection rapide d'images parmi d'importantes collections photographiques.

## Statut du Logiciel et Clause de Prudence

Ce logiciel est actuellement **en cours de développement**.

- Certaines fonctionnalités peuvent évoluer, changer de comportement ou présenter des anomalies.
- Il est fortement recommandé de travailler sur des **copies** de vos photos et de conserver des sauvegardes régulières.
- Avant toute opération de tri, renommage, export ou suppression, vérifiez vos paramètres et le dossier actif.

### Clause de non-responsabilité

Le logiciel est fourni « en l'état », sans garantie expresse ou implicite de disponibilité, de fiabilité ou d'adéquation à un usage particulier.
Les auteurs, contributeurs et distributeurs ne pourront être tenus responsables des pertes de données, suppressions involontaires, dommages directs ou indirects, ou tout autre préjudice lié à l'utilisation du logiciel.
L'utilisateur reste seul responsable de ses données, de ses sauvegardes et des actions réalisées avec l'application.

## Le But du Projet

L'objectif principal est de rationaliser le fastidieux processus de tri des photos après un shooting, un voyage ou toute autre capture massive :
1. **Extraction à la volée** : Lecture quasi instantanée des métadonnées EXIF pour déterminer la date de prise de vue, l'appareil photo, la taille et les **coordonnées GPS**.
2. **Géocodage Hors-Ligne (Reverse Geocoding)** : Sans avoir besoin du moindre accès réseau, le logiciel traduit les repères par satellites (Latitude/Longitude) en un lieu réel lisible (Pays, Région, Ville).
3. **Sélection ultra-rapide** : L'outil de visionneuse synchronisé en direct avec la base de données permet un tri ergonomique "1 photo = 1 touche".
4. **Renommage Informatif Dynamique** : Les photos conservées sont automatiquement copiées dans votre répertoire cible et renommées de manière descriptive avec leurs données (ex: `240325_France_Ile-de-France_Paris_00.jpg`), éradiquant les noms de fichiers obscurs tels que `DSC_0128.jpg`.

## Fonctionnalités Clés

- **Zone logo dynamique** : Après initialisation complète, le slot logo est remplacé par une miniature de la photo active (même taille, sans déplacement de mise en page).
- **Colonne Albums virtuelle** : Une liste `Albums` scrollable est affichée entre le logo et la carte, sans pousser verticalement le reste de l'interface.
- **Info répertoire au survol** : Survoler une photo dans les 2 visionneuses (et la miniature affichée à la place du logo) affiche son répertoire dans la zone des compteurs.
- **Colonne `Répertoire` dans le tableau** : Affiche le dossier parent de chaque photo pour faciliter le tri inter-dossiers.
- **Message centré cliquable** : Dans la barre de conversation, un texte aléatoire issu de `_resources/coffee.md` est affiché avec le préfixe `Clic ici ...`.
- La phrase `Clic ici ...` se renouvelle aléatoirement à chaque interaction utilisateur (clic, clavier, navigation).
- **Tableau de Bord épuré** : Interface simplifiée sans titres redondants pour maximiser l'espace utile.
- **Recherche instantanée** : Un champ de recherche filtre le tableau en temps réel par nom de collection (ex: tapez `Paris` ou `2407` pour retrouver vos photos).
- **Recherche ciblée par colonne** : Le champ accepte aussi `pays=...`, `région=...`/`region=...` et `ville=...` pour filtrer uniquement la colonne demandée (ex: `pays=th`, `region=faro`, `ville=lyon`).
- **Filtre GPS dédié** : Sous la liste des pays, un champ `GPS` accepte les formats `Lat-17.65946 Lon-149.417145` ou `-17.6594, 149.4171`, avec un champ `Zone` (1 à 9 degrés) pour filtrer le tableau et cadrer la carte autour du point.
- **Sélecteurs de répertoires persistants** : Les champs Source et Dossier sélection sont des sélecteurs (liste triée alphabétiquement), alimentés automatiquement par `Parcourir`.
- **Visionneuse plein écran synchronisée** qui s'aligne automatiquement avec les résultats du tableau, compatible multi-écrans avec DPI hétérogènes.
- **Scan interruptible avec reprise** : Le scan peut être interrompu et repris là où il s'est arrêté. Des statistiques en temps réel (vitesse, temps restant) sont affichées.
- **Audit d'intégrité & Réconciliation** : Un outil puissant pour synchroniser votre base de données avec l'état réel des fichiers sur le disque (suppression des entrées orphelines, mise à jour des métadonnées).
- **Normalisation des lieux** : Harmonise les noms de pays/villes pour une collection cohérente.
- **Suppression sécurisée** : Les fichiers supprimés sont envoyés dans la corbeille Windows (jamais de suppression définitive).
- **Renommage des originaux** : Un bouton permet de renommer physiquement tous les fichiers source avec leur nom de collection généré.
- **Albums virtuels** : Des albums par base source permettent de regrouper les photos sans dupliquer les fichiers, avec appartenance multiple possible.
- **Classification thématique IA** : Un moteur basé sur ImageNet (MobileNetV2) analyse vos photos pour créer automatiquement des albums thématiques (Animaux, Insectes, Fleurs, Églises, Temples).
- **Menus contextuels (Clic droit)** : Copie de la photo dans le presse-papier Windows, copie des données d'une ligne du tableau, édition rapide ou **Ajouter à l'album...**.
- **Diaporama contextuel (tableau)** : Depuis le clic droit du tableau, l'action **Diaporama** démarre sur la photo sélectionnée.
- **Raccourci diaporama sur le tableau** : Avec une ligne sélectionnée et le focus sur le tableau, **`D`** / **`d`** lance le diaporama depuis cette photo en conservant l'ordre visible du tableau.
- **Diaporama basé sur la vue active** : Le diaporama suit l'ordre réellement visible (filtres + tri du tableau), plus uniquement le dossier courant.
- **Synchronisation en cours de diaporama** : La ligne du tableau suit la photo affichée et se recentre automatiquement.
- **Contrôle rapide du diaporama** : Un clic utilisateur stoppe immédiatement un diaporama actif pour reprendre la main sans friction.
- **Vitesse de diaporama persistante** : Le délai (secondes/photo) est conservé d'une session à l'autre.
- **Édition individuelle (F2)** : Modification du nom de collection, du pays, de la région et de la ville d'une photo avec renommage physique du fichier.
- **Multi-sélection et suppression par lots** : Sélectionnez plusieurs photos (Ctrl+Clic / Shift+Clic) et supprimez-les d'un coup avec confirmation.
- **Aide intégrée (F1)** : Affichage du README formaté (titres, gras, tableaux, code) directement dans l'application.
- **Fenêtre soutien BTC** : Un clic sur le message `Clic ici ...` ouvre une fenêtre avec texte de soutien et QR code BTC (`assets/QR_BTC.jpg`).
- **Fenêtre d'information au démarrage** : Une fenêtre de guidage explique le positionnement des 3 fenêtres (2 visionneuses + tableau de bord), avec option **Ne plus afficher** mémorisée pour les prochains lancements.
- **Base de données robuste & persistante** : Si l'utilisateur quitte le logiciel, il peut revenir le lendemain, rouvrir le dossier et tout son avancement (photos copiées ou non) sera intact (stockage technique dans le dossier visé : `.photoselect.db`).
- **Mémorisation Visuelle** : Taille, position et état (plein écran / fenêtré) de chaque fenêtre sont automatiquement sauvegardés d'une ouverture à l'autre, même sur des configurations multi-écrans.

## Ergonomie & Touches de Navigation

Antisèche des commandes utiles, regroupées par contexte.

### Global

| Raccourci / Action | Effet |
| --- | --- |
| **`F1`** | Ouvre l'aide intégrée. |
| **`P`** / **`p`** | Bascule le tableau de bord au premier / arrière plan. |
| **La `Croix` X** | Quitte l'application en sauvegardant la géométrie. |

### Tableau

| Raccourci / Action | Effet |
| --- | --- |
| **`Clic-Gauche`** | Sélectionne la ligne et affiche l'image. |
| **`Double-Clic`** | Renomme la photo sélectionnée. |
| **`Clic-Droit`** | Ouvre le menu contextuel de la ligne. |
| **Menu contextuel -> Déplacer vers un dossier...** | Déplace toutes les photos sélectionnées vers un autre répertoire. |
| **Menu contextuel -> Coller géolocalisation (sélection)...** | Colle GPS/Pays/Région/Ville sur une ou plusieurs photos (au choix). |
| **`Ctrl+Clic`** / **`Shift+Clic`** | Multi-sélection de photos dans le tableau. |
| **`Clic-Gauche`** sur **Coordonnées GPS** | Ouvre Google Maps si les coordonnées existent. |
| **`Flèches`** | Déplace la sélection. |
| **`Page Up`** / **`Page Down`** | Déplace la sélection par page. |
| **`D`** / **`d`** | Lance le diaporama depuis la sélection. |
| **`Espace`** | Exporte la photo courante. |
| **`Delete`** / **`Suppr`** | Supprime la sélection ou la retire de l'album actif. |
| **`F2`** | Renomme / édite la photo sélectionnée. |

### Visionneuse

| Raccourci / Action | Effet |
| --- | --- |
| **`Flèches`** | Image précédente / suivante. |
| **`Page Up`** / **`Page Down`** | Navigation par page. |
| **`Molette Souris`** | Défile les images. |
| **`Espace`** | Exporte la photo courante. |
| **`Delete`** / **`Suppr`** | Supprime la photo courante ou la retire de l'album actif. |
| **`F2`** | Renomme / édite la photo courante. |
| **`Ctrl+R`** | Rotation 90° à droite. |
| **`Ctrl+L`** | Rotation 90° à gauche. |
| **`Ctrl+U`** | Normalise l'orientation EXIF. |
| **`F12`** | Bascule plein écran / fenêtré. |
| **`Double-Clic`** | Bascule plein écran / fenêtré. |
| **`Esc`** | Quitte le plein écran. |
| **`Ctrl+1`** / **`Ctrl+2`** | Déplace les visionneuses sur l'écran 1 ou 2. |
| **`Clic-Droit`** | Ouvre le menu contextuel. |

### Diaporama

| Raccourci / Action | Effet |
| --- | --- |
| **`D`** / **`d`** | Démarre le diaporama. |
| **`Clic`** | Arrête immédiatement le diaporama en cours. |
| **`1` à `9`** | Définit la vitesse en secondes par photo. |
| **`+`** / **`-`** | Accélère ou ralentit le diaporama. |

### Onglet Visages

| Raccourci / Action | Effet |
| --- | --- |
| **`Clic`** sur une vignette | Ouvre la photo d'origine en grand. |
| **`Ctrl+Clic`** sur une vignette | Marque le visage comme **correct**. |
| **`V`** | Marque le visage sélectionné comme **correct**. |
| **`F`** | Marque le visage sélectionné comme **faux / pas lui**. |
| **`Clic-Droit`** sur une vignette | Ouvre le menu contextuel du visage. |
| **`Molette Souris`** | Fait défiler l'onglet Visages. |

### Éditeur De Nom / Groupe Visage

| Raccourci / Action | Effet |
| --- | --- |
| **`Entrée`** dans **Renommage rapide** | Applique le renommage / marquage. |
| **`Esc`** dans **Renommage rapide** | Annule l'édition rapide en cours. |
| **`Double-Clic`** sur une suggestion | Applique la suggestion sélectionnée. |
| **`Entrée`** sur une suggestion | Applique la suggestion sélectionnée. |
| **`Entrée`** dans le champ **Nom** d'un groupe visage | Renomme la personne affichée. |
| **`Entrée pavé numérique`** dans le champ **Nom** d'un groupe visage | Renomme aussi la personne affichée. |

### Barre Latérale Albums / Visages

| Raccourci / Action | Effet |
| --- | --- |
| **`Clic`** sur un album ou visage | Charge sa sélection. |
| **`Clic-Droit`** | Ouvre le menu contextuel de la liste. |
| **`Delete`** / **`Suppr`** | Supprime l'album sélectionné en mode albums. |
| **`F2`** | Renomme l'album sélectionné en mode albums. |
| **`Molette Souris`** | Fait défiler la liste latérale. |

### Diaporama: statut visible

- Pendant un diaporama, la barre d'état affiche explicitement la vitesse en cours au format: `Mode diaporama : Xs par photo`.

### Comportement du tri tableau

- Après un tri sur une colonne, la sélection est automatiquement repositionnée sur la première ligne visible.
- Le tableau se repositionne aussi sur cette première ligne.

## Version Windows

Une version exécutable autonome (`PhotoSelect.exe`) est disponible. Elle ne nécessite aucune installation de Python ni de dépendances : il suffit de lancer le fichier `.exe` directement.

## Notes de Comportement (Mosaïque)

- En mode **Photo**, un clic sur une ligne du tableau met à jour la visionneuse photo uniquement.
- Après un clic dans le tableau, le focus clavier reste sur le tableau pour permettre les raccourcis immédiats (`D` / `d`, flèches, etc.).
- La mosaïque cachée n'est pas resynchronisée en arrière-plan.
- Lors du passage en mode **Multiple**, la mosaïque se resynchronise uniquement si la photo sélectionnée n'est pas déjà présente dans la page mosaïque actuellement affichée.

## Mode Affichage Sélection

- La ligne **Affichage** propose désormais `Par dossier`, `Continu`, `Sélection`, `Albums` et `Doublons`.
- En mode **Sélection**, les visionneuses utilisent le contenu du dossier choisi dans **Dossier sélection**.
- En mode **Albums**, la sidebar `Albums` pilote directement le tableau et les visionneuses sur l'album virtuel sélectionné.
- Les albums virtuels sont stockés dans la base source active, avec ordre d'ajout persistant.
- Depuis les menus contextuels image/tableau, **Ajouter à l'album...** ouvre une liste filtrable des albums existants et peut créer un nouvel album si besoin.
- Dans la colonne `Albums`, **F2** renomme l'album actif et **Suppr** supprime l'album virtuel sans toucher aux fichiers.
- En mode **Albums**, la touche **Suppr** retire la photo de l'album courant au lieu de l'envoyer à la corbeille.
- En mode **Doublons**, les visionneuses et le tableau affichent uniquement les photos détectées en doublon de signature (`taille + titre + date`).
- Si `Sélection` est coché, changer le sélecteur **Dossier sélection** rafraîchit immédiatement l'affichage.
- La navigation (flèches) suit alors l'ordre des photos de la sélection.
- Le bouton **Réindexer sélection** force une resynchronisation immédiate de l'index de sélection.
- Lors de **Réindexer sélection**, les dossiers de sélection inexistants sont supprimés automatiquement de la liste (sélecteur) et des bases locales associées.
- Le bouton **Audit intégrité** adapte son comportement au scope actif (`Par dossier`, `Continu`, `Sélection`).
- L'audit inclut aussi le comptage des doublons de signature (`taille + titre + date`).
- L'onglet **Statistiques** affiche des métriques dédiées en mode Sélection (et l'état de santé via le dernier audit: illisibles/manquantes).

## Indexation des doublons

- L'index des doublons est (re)construit automatiquement à la fin de chaque scan/re-scan.
- Pour les anciennes bases (sans index doublons), une mise à niveau automatique est lancée au démarrage avec barre de progression.
- Le passage sur le mode **Doublons** prépare désormais les données en arrière-plan (UI non bloquante) avec retour visuel de progression.
- Une fois préparé, le scope **Doublons** réutilise un cache mémoire pour des bascules beaucoup plus rapides.
- La suppression d'une photo met à jour l'index des doublons immédiatement.
- Les fichiers situés dans le dossier défini par **Dossier sélection** sont exclus du mode **Doublons** et du compteur de doublons des statistiques.

## Démarrage optimisé

- L'interface est affichée en priorité, puis l'auto-chargement de la base est lancé juste après le rendu initial.
- La préparation des doublons au démarrage est exécutée en tâche de fond (sans blocage de l'ouverture UI).
- Les vérifications de présence de données au démarrage utilisent des requêtes SQL légères (compte/existence) au lieu de charger toute la base en mémoire.

## Carte (Planisphère)

- La carte affiche des contours de pays précis (GeoJSON local) et non plus des formes approximatives.
- **Molette sur la carte** : zoom avant/arrière centré sur le curseur (avec limites min/max).
- **Clic simple sur la carte** : copie les coordonnées (`lat, lon`) dans le champ GPS, dans le presse-papier Windows, et prépare ces coordonnées pour le collage géolocalisation.
- **Sélection de zone** : filtre le tableau, puis zoome/recentre automatiquement la carte sur la zone.
- **Réinitialisation de zone** : double-clic sur la carte ou clic sur le logo.
- Les points GPS restent affichés après sélection de zone.

## Fiabilité Données

- **Copier la photo** via clic droit (visionneuses + tableau) corrigé et stabilisé pour Windows 64-bit.
- **Purge data** supprime aussi les anciennes bases legacy `.photoselect.db` pour éviter leur réimport involontaire.
- **Vider / Re-scanner** n'importe plus la base legacy: le rescan complet repart d'une base propre.
- **Mode GPS par scan** : au lancement de chaque scan/re-scan, l'opérateur choisit `En local` ou `En ligne`.
- **Retour auto En ligne** : à la fin du scan, la sélection GPS repasse automatiquement sur `En ligne`.
- **Audit intégrité enrichi (GPS sans lieu)** : l'audit détecte les photos avec coordonnées GPS mais sans `Pays/Région/Ville`.
- **Correction offline** : l'audit propose de compléter ces champs depuis la base locale (sans appel en ligne).
- **Option EXIF dédiée** : lors du complément offline, une option séparée permet d'écrire (ou non) les métadonnées EXIF.
- **Collage géolocalisation intelligent** : lors d'un collage GPS, PhotoSelect tente d'abord de compléter `Pays/Région/Ville` via recherche en ligne, puis en local si besoin, sans message d'erreur si les deux échouent.

## Installation & Lancement (Version développeur)

L'application repose sur le langage Python et certaines dépendances de lectures natives et géographiques (comme `Pillow` et `reverse_geocoder`).

1. Mettre en place un environnement virtuel (optionnel mais conseillé).
2. Installer les paquets via `pip install -r requirements.txt`.
3. Lancer l'interface en saisissant la commande :
```bash
python -m photoselect
```
