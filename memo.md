Mémo Technique : PhotoSelect & Workflow Git
1. Gestion du Projet (Python)
Commandes pour l'exécution et la compilation de l'application.

Environnement et Exécution
Activer l'environnement virtuel :

PowerShell
& C:\Dev\PhotoSelect\.venv\Scripts\Activate.ps1
Lancer l'application (mode module) :

PowerShell
python -m photoselect
Compilation
Générer l'exécutable Windows :

PowerShell
pyinstaller PhotoSelect.spec
2. Configuration de la Signature SSH
À configurer une seule fois pour permettre la validation locale des signatures.

Créer le fichier des signataires autorisés :

PowerShell
echo "mchvalidation@gmail.com $(cat C:\Users\mchur\.ssh\id_ed25519.pub)" > C:\Users\mchur\.ssh\allowed_signers
Déclarer le fichier à Git :

PowerShell
git config --global gpg.ssh.allowedSignersFile "C:/Users/mch1ur/.ssh/allowed_signers"
3. Workflow de Signature Git
Procédure pour obtenir le badge "Verified" sur GitHub.

Créer un commit signé
PowerShell
# 1. Ajouter les modifications
git add .

# 2. Créer le commit signé (-S)
git commit -S -m "Description de la modification"

# 3. Envoyer vers le dépôt
git push
Cas particuliers
Premier push (lier la branche) : git push -u origin main

Test rapide (commit vide) : git commit --allow-empty -m "Test de signature"

Signer un commit déjà fait (amend) : git commit --amend --no-edit -S

Forcer la mise à jour distante : git push -f origin main

4. Vérification et Debug
Outils pour contrôler l'état des signatures.

Vérifier la signature du dernier commit dans le terminal :

PowerShell
git log --show-signature -1
Glossaire Technique
-S : Option de signature. Indique à Git d'utiliser votre clé SSH/GPG pour signer numériquement le commit.

pyinstaller : Outil transformant un script Python en un fichier .exe autonome.

--amend : Permet de modifier le tout dernier commit (pratique pour ajouter une signature oubliée).

allowed_signers : Fichier qui fait le lien entre une adresse email et une clé publique pour valider l'identité de l'auteur.