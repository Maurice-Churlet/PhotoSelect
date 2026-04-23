python -m photoselect
& C:\Dev\PhotoSelect\.venv\Scripts\Activate.ps1

Génération d'un exécutable
(.venv) ps C:\Dev\PhotoSelect> pyinstaller PhotoSelect.spec

Pour faire un test et voir le badge "Verified" apparaître, vous pouvez faire un petit commit "à vide" (sans modifier de fichier) et le pousser sur GitHub.

Tapez ces deux commandes dans votre terminal :

powershell
git commit --allow-empty -m "Test de signature"
git push
Ce qui va se passer :
git commit : Votre ordinateur va utiliser votre clé SSH pour signer le message.
git push : Le message est envoyé à GitHub.
Vérification : Allez rafraîchir la page de votre dépôt sur GitHub. Vous devriez voir apparaître le commit "Test de signature" avec le fameux bouton vert Verified à côté !

git log --show-signature -1

git push -f origin main

Signer le commit localement :
git commit --amend --no-edit -S

Mettre à jour GitHub :
git push -f origin main

Si tu veux voir la signature dans ton terminal, tu peux exécuter ces deux commandes :
Créer le fichier des signataires autorisés :
echo "mchvalidation@gmail.com $(cat C:\Users\mchur\.ssh\id_ed25519.pub)" > C:\Users\mchur\.ssh\allowed_signers

Dire à Git d'utiliser ce fichier :
git config --global gpg.ssh.allowedSignersFile "C:/Users/mchur/.ssh/allowed_signers"

git log --show-signature -1

Pour faire un commit signé (ce que tu appelles "crypté") avec tes modifications sur le contrôle d'écran, voici les commandes à taper dans ton terminal PowerShell :

powershell
# 1. Ajouter tous les fichiers modifiés (app.py, README.md, etc.)
git add .
# 2. Créer le commit signé avec ton message
git commit -S -m "Contrôle d'écran"
# 3. Envoyer sur GitHub
git push
Pourquoi ces commandes ?

-S : C'est cette option qui dit à Git d'utiliser ta clé SSH pour signer le commit.
-m "..." : C'est le message qui apparaîtra dans ton historique.
Une fois que c'est fait, tu verras le badge Verified sur GitHub pour ce nouveau commit !