# Recherche CoMMA — manuscrits, transcriptions et ALTO

Application autonome pour retrouver un manuscrit du corpus **CoMMA** (INRIA) à partir
de sa cote, ouvrir sa transcription sur comma.inria.fr et récupérer automatiquement
ses fichiers **ALTO** sur Hugging Face, avec un niveau de certitude explicite.

## Exécutables (sans Python)

Des exécutables prêts à l'emploi sont joints aux versions publiées (onglet **Releases** du
dépôt) : `CoMMA-Search-windows.zip`, `CoMMA-Search-macos.zip` (Mac Apple Silicon),
`CoMMA-Search-linux.tar.gz`. Décompressez et lancez `CoMMA-Search`.

Ils ne sont pas signés :

* **Windows** : si SmartScreen affiche « Windows a protégé votre ordinateur », cliquez sur
  *Informations complémentaires*, puis *Exécuter quand même*.
* **macOS** : la première fois, clic droit (ou Ctrl-clic) sur `CoMMA-Search.app`, puis *Ouvrir*.

Ils sont construits par le workflow **Binaries** (`.github/workflows/binaries.yml`) avec
PyInstaller (`packaging/comma_search.spec`) ; pour en construire soi-même :
`pip install pyinstaller certifi` puis `pyinstaller --noconfirm --clean packaging/comma_search.spec`.

## Lancement

* **Spyder** : ouvrir `COMMA_RECHERCHE_SPYDER.py`, appuyer sur **F5**.
* **Terminal** : `python COMMA_RECHERCHE_SPYDER.py`
* Recherche pré-remplie : `python COMMA_RECHERCHE_SPYDER.py --recherche "Troyes 816"`
* Sans interface : `python COMMA_RECHERCHE_SPYDER.py --cli "Troyes, BM, 816"`
  (`--telecharger` pour télécharger et extraire l'archive confirmée)
* Langue : menu **Langue / Language** (français ou anglais, changement immédiat, choix
  mémorisé) ; au premier lancement, la langue du système ; en ligne de commande `--lang en`.

Windows, macOS et Linux. **Aucune bibliothèque à installer** : Python ≥ 3.8 avec Tkinter
(fourni avec Python sous Windows et macOS, et avec Anaconda/Spyder ; sous Linux :
paquet `python3-tk`). Garder ensemble :

```
COMMA_RECHERCHE_SPYDER.py   interface graphique (Tkinter)
comma_core.py               logique : recherche, correspondances, téléchargement
comma_i18n.py               traductions (français / anglais)
donnees/                    index livrés (catalogue CoMMA, index des archives ALTO)
tests/                      tests hors ligne : python -m unittest discover -s tests
```

## Ce qu'on peut saisir

* une cote, librement : `Troyes, BM, 816` · `Troyes 0816` · `Paris, BnF, Latin 16400` ·
  `BnF Latin 16400` · `Châteaudun A2` · `Châteaudun, Archives municipales, A 002` ·
  `BnF fr. 2227` · `Montpellier H 242`… ;
* un identifiant Biblissima (`Q412957`) ou son URL ;
* une URL de transcription CoMMA (`https://comma.inria.fr/doc/...`) ;
* une URL de manifeste IIIF, ou un ARK seul ;
* un nom d'archive ALTO (`france-troyes-fonds-principal-0816-integral.tar.gz`) : on remonte
  alors la chaîne archive → manifest.txt → IIIF → CoMMA / Biblissima.

Règles de recherche : les numéros sont **exacts** (816 ne renvoie ni 1816 ni 8160 ; les
zéros initiaux sont ignorés : 0816 = 816, A 002 = A2) ; les mots (ville, établissement,
fonds) servent au classement ; les abréviations usuelles sont comprises (BM, BnF, AM, AD,
lat., fr., NAL, NAF…). Si le catalogue local n'a rien, l'application interroge le serveur
CoMMA.

## Ce que l'application affiche pour chaque manuscrit

1. **Cote normalisée** (Ville, établissement, cote) et l'intitulé CoMMA.
2. **Identifiant Biblissima** : c'est l'identifiant même de la ressource dans l'API DTS de
   CoMMA (`@id` = `https://data.biblissima.fr/entity/Q…`).
3. **Transcription CoMMA** : bouton d'ouverture. La ressource et sa première page sont
   vérifiées par l'API DTS (`/navigation`) ; l'adresse suit la route de l'interface TexTile
   (`/doc/<id doublement encodé>/p/<page>`).
4. **Manifeste IIIF** déclaré par CoMMA (`dublinCore.source`).
5. **Archives ALTO** du dépôt `comma-project/alto-dataset`.
6. **Téléchargement et extraction** en un clic, avec contrôle : chaque fichier annoncé par
   `manifest.txt` doit être présent ; une fiche `provenance_comma.json` est écrite dans le
   dossier extrait.
7. **Degré de certitude** de la correspondance CoMMA ↔ ALTO :

| Statut | Certitude | Signification |
|---|---|---|
| CONFIRMÉE (vérifiée à l'instant) | 100 % | la 1re ligne du `manifest.txt` de l'archive, relue en direct, est le manifeste IIIF de CoMMA |
| CONFIRMÉE | 98 % | même preuve, lue lors de la construction de l'index (relecture impossible à l'instant) |
| PROBABLE (non vérifiée) | ≤ 70 % | le nom de l'archive correspond à la cote, mais `manifest.txt` n'a pas pu être lu |
| AUTRE NUMÉRISATION | ≤ 40 % | le nom correspond à la cote, mais `manifest.txt` cite **un autre** manifeste IIIF (ex. archive « -decor » d'une autre reproduction) : ce n'est pas la transcription affichée par CoMMA |
| AUCUNE TROUVÉE | — | **jamais une preuve d'absence** |

La comparaison des manifestes se fait sur une clé canonique : pour un ARK, `ark:/NAAN/nom`.
C'est indispensable pour la BnF : les archives citent
`openapi.bnf.fr/iiif/presentation/v3/ark:/12148/…` alors que CoMMA et Biblissima citent
`gallica.bnf.fr/iiif/ark:/12148/…`.

**Second contrôle, indépendant** : l'application compare la liste complète des pages CoMMA
(API DTS `/navigation`) avec la liste des fichiers ALTO de `manifest.txt` (nombre, noms et
ordre). Exemple : Troyes 1713 → « 818 pages CoMMA et 818 fichiers ALTO, mêmes noms, même
ordre ». Une différence est signalée et plafonne la certitude à 90 %.

## Comment ça marche

### Catalogue CoMMA (index local)
La recherche du site CoMMA est floue et ne fonctionne pas pour les cotes (« Troyes 816 »
n'y renvoie pas le ms. 816). L'application parcourt donc une fois l'arborescence DTS
publique (`/api/collection/?id=…&nav=children&page=…` : racine → villes → établissements
→ manuscrits, 20 par page, ≈ 2 300 requêtes, ≈ 4 min) et garde le résultat en cache. Un
catalogue complet est livré dans `donnees/` (32 763 manuscrits, le total annoncé par
`/api/manuscripts/count/`). Menu **Index… → Mettre à jour le catalogue CoMMA**.

### Index des archives ALTO
* La liste des archives s'obtient par l'API Hugging Face (`/tree/main/<dossier>?recursive=true`,
  ≈ 30 requêtes, quelques secondes) : rien n'est téléchargé.
* Chaque archive `.tar.gz` commence par `manifest.txt` : une requête HTTP partielle
  (`Range: bytes=0-4095`, soit 4 Ko au lieu de plusieurs Mo) suffit à lire l'URI IIIF de la
  1re ligne.
* L'index livré contient cette lecture pour **toutes** les archives du dépôt. L'application
  rafraîchit la liste toutes les semaines et ne lit que les archives nouvelles ou modifiées.

### Biblissima
Utilisé pour la chaîne inverse (archive → manuscrit) et la recherche approfondie :
`haswbstatement:P196=<manifeste IIIF>` dans l'espace de noms *Item* (P196 = manifeste IIIF,
P195 = cote, P194 = établissement). Exemple vérifié :
`api.irht.cnrs.fr/ark:/63955/fdy27ubqm4j0/manifest.json` → **Q419352** (Châteaudun A2).

### Recherche approfondie
Le bouton actualise la liste Hugging Face, lit les `manifest.txt` encore inconnus, relit
toutes les archives de la même ville (noms inattendus) et interroge Biblissima.

## Résultats vérifiés (15/11/2026)

* Index des archives : **32 524 archives** (dépôt `alto-dataset`, commit `940ed84`, modifié le
  14/09/2026), **toutes** lues (1re ligne de `manifest.txt`), aucune erreur.
* **23 233 manuscrits CoMMA sur 32 763 (70,9 %)** ont au moins une archive ALTO confirmée.
  Les 9 530 autres : 4 599 numérisations BnF/Gallica, 4 430 IRHT, 360 Munich (BSB), 141 Oxford
  (Bodleian) ; leur manifeste IIIF n'apparaît dans aucun `manifest.txt` du dépôt.
* 7 319 archives citent un manifeste absent de CoMMA : surtout d'autres reproductions IRHT
  (`-decor` 3 104, `-partiel` 2 613, `-reliure` 1 051), parfois une autre numérisation intégrale.

| Manuscrit | Q | Archive ALTO | Statut |
|---|---|---|---|
| Troyes, ms. 816 | Q412957 | `batch_018/france-troyes-fonds-principal-0816-integral.tar.gz` | confirmée, 295 pages = 295 ALTO |
| Châteaudun, AM, Hôtel-Dieu, ms. A2 | Q419352 | `batch_010/france-chateaudun-fonds-principal-hotel-dieu-ms-a2-integral.tar.gz` | confirmée, 18 ALTO extraits |
| BnF, Latin 16400 | Q61665 | `batch_014/bn-f-departement-des-manuscrits-latin-16400.tar.gz` | confirmée, 120 pages = 120 ALTO |
| Troyes, ms. 1713 | Q240078 | `batch_000/mediatheque-de-troyes-champagne-metropole-ms-1713.tar.gz` | confirmée, 818 = 818 ; **piège** : `france-troyes-fonds-principal-1713-integral` est une autre numérisation (IRHT) |
| Troyes, ms. 1759 | Q277651 | aucune | voir ci-dessous |

**Troyes, ms. 1759** (manifeste `api.irht.cnrs.fr/ark:/63955/f9n82uakhj1w`, 36 pages dans
CoMMA) : aucune des 32 524 archives ne cite ce manifeste, et aucune archive de Troyes ne
porte ce numéro (la série passe de 1757 à 1760). Le dépôt n'a qu'une branche (`main`), et
`comma-project/comma-other-formats` est vide. Conclusion : **l'archive ALTO n'est pas dans
le dépôt public à cette date**. La transcription existe dans CoMMA, donc les données
existent ailleurs (non publiées ou publiées plus tard) : ce n'est pas une preuve qu'elles
n'existent pas. L'application relira automatiquement la liste des archives chaque semaine.

**Attention au champ `manifest_url` de la recherche plein texte CoMMA** (`/api/manuscripts/`) :
pour Troyes 1713, elle donne `…/f6wws6m3dbem` (2 pages, archive `-partiel`), alors que la
ressource DTS déclare le manifeste Gallica, qui correspond aux 818 pages réellement
transcrites. L'application se fonde donc sur l'API DTS (`dublinCore.source`).

## En cas de plantage

* Les erreurs sont enregistrées dans **`journal_erreurs.txt`** (menu **Index… → Ouvrir le journal
  des erreurs**), y compris les plantages brutaux : la pile Python y est écrite même si la fenêtre
  disparaît sans message. Envoyez ce fichier pour diagnostic.
* Sous **macOS avec Spyder**, Tkinter cohabite mal avec la boucle Qt du noyau : réglez
  *Préférences → Console IPython → Graphiques → Backend* sur **« En ligne » (Inline)**, ou
  lancez l'application dans un terminal (`python COMMA_RECHERCHE_SPYDER.py`).
* Sous Windows, si l'extraction échoue pour des chemins trop longs, choisissez un dossier de
  destination court (par exemple `C:\ALTO`).

## Fichiers et dossiers

* Cache (catalogue, index, réglages) : `%LOCALAPPDATA%\CommaRecherche` (Windows),
  `~/Library/Caches/CommaRecherche` (macOS), `~/.cache/comma-recherche` (Linux).
* ALTO extraits : par défaut `~/Downloads/CoMMA_ALTO/<nom de l'archive>/` (modifiable).

## Limites connues

* L'URL de page CoMMA est construite selon la route de l'interface ; l'application vérifie
  la ressource et la page par l'API, pas l'affichage de la page dans le navigateur.
* Le fichier `counts.json` du backend TexTile n'est pas public et n'est pas utilisé.
* Le dépôt `comma-jsonl` (≈ 27 Go) contient cote, Q et manifeste, mais pas le nom des
  archives ALTO : il n'est pas nécessaire ici.

## Données

Les index de `donnees/` sont dérivés des métadonnées publiques de CoMMA (Inria) et du dépôt
`comma-project/alto-dataset`, publiés sous licence CC BY 4.0.
