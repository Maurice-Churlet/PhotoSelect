python -m photoselect
& C:\Dev\PhotoSelect\.venv\Scripts\Activate.ps1


Parfait. Version ultra-simple en 3 étapes, sans usine à gaz.

Active la signature automatique des commits (avec ta clé SSH existante)
Dans PowerShell, lance :
git config --global gpg.format ssh
git config --global user.signingkey "$env:USERPROFILE.ssh\id_ed25519.pub"
git config --global commit.gpgsign true

Si ton fichier de clé a un autre nom, remplace id_ed25519.pub.

Ajoute cette clé dans GitHub comme clé de signature
Va dans :
GitHub > Settings > SSH and GPG keys > New SSH key
Type : Signing Key
Colle le contenu de ton fichier .pub.

Vérifie que ça marche
Fais un commit normal puis :

git log --show-signature -1

Sur GitHub, ton commit apparaîtra avec le badge Verified.

Option minimale recommandée :
Commence juste par signer les commits. C’est déjà une preuve d’origine forte et très simple.
Si tu veux, je te donne ensuite la version ultra-simple pour signer aussi les releases.


-----------------------------------------------
Je m’en charge directement: je vais détecter ta clé SSH publique, configurer Git pour signer automatiquement les commits avec cette clé, puis vérifier la configuration.

C’est configuré et actif.

Configuration appliquée:

gpg.format = ssh
user.signingkey = C:\Users\mchur\.ssh\id_ed25519.pub
commit.gpgsign = true
Prochaine étape côté GitHub:

Ouvre Settings > SSH and GPG keys > New SSH key
Type: Signing Key
Colle le contenu de id_ed25519.pub
Ensuite, ton prochain commit devrait apparaître Verified sur GitHub.

