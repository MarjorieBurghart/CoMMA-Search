# -*- coding: utf-8 -*-
"""
comma_i18n — langue de l'interface (français / English).

Les textes sont écrits en français dans le code et passés à _() ; en anglais,
_() renvoie la traduction de la table EN (ou le texte français s'il en manque
une : le test tests/test_comma_core.py vérifie qu'aucune ne manque).
"""

from __future__ import annotations

import locale
import os
import sys

LANGUAGES = (("fr", "Français"), ("en", "English"))
_current = "fr"


def set_language(code: str) -> None:
    global _current
    code = (code or "").lower()[:2]
    _current = code if code in dict(LANGUAGES) else "fr"


def get_language() -> str:
    return _current


def detect_language() -> str:
    """Langue du système : « fr » si le système est en français, sinon « en »."""
    candidates = []
    if sys.platform.startswith("win"):
        try:
            import ctypes
            lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            if lid & 0x3FF == 0x0C:  # LANG_FRENCH
                return "fr"
            return "en"
        except Exception:
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        if os.environ.get(var):
            candidates.append(os.environ[var])
    try:
        candidates.append(locale.getlocale()[0] or "")
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            import subprocess
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                                 text=True, timeout=2).stdout
            candidates.insert(0, out.replace("(", "").replace('"', "").strip())
        except Exception:
            pass
    for c in candidates:
        c = c.strip().lower()
        if c.startswith("fr") or c.startswith("french"):
            return "fr"
        if c and not c.startswith(("c", "posix")):
            return "en"
    return "fr"


def _(text: str) -> str:
    if _current == "en":
        return EN.get(text, text)
    return text


EN = {
    # --- comma_core : recherche, réseau, index
    "Mise à jour interrompue": "Update interrupted",
    "cote exacte": "exact shelfmark",
    "correspondance approchée (aucune cote exacte)": "approximate match (no exact shelfmark)",
    "identifiant Biblissima": "Biblissima identifier",
    "manifeste IIIF": "IIIF manifest",
    "%d premiers sur %d — précisez la cote": "first %d of %d — refine the shelfmark",
    "HTTP %s pour %s": "HTTP %s for %s",
    "Erreur réseau pour %s : %s": "Network error for %s: %s",
    "Catalogue CoMMA : %d pages lues, %d manuscrits": "CoMMA catalogue: %d pages read, %d manuscripts",
    "Hugging Face : %s… (%d archives)": "Hugging Face: %s… (%d archives)",
    "Début d'archive illisible : %s": "Unreadable archive header: %s",
    "Réponse illisible (JSON attendu) pour %s": "Unreadable response (JSON expected) for %s",
    "pas de manifest.txt en tête (%s)": "no manifest.txt at the start (%s)",
    "Lecture des manifest.txt : %d / %d": "Reading manifest.txt files: %d / %d",

    # --- comma_core : statuts
    "CONFIRMÉE": "CONFIRMED",
    "CONFIRMÉE (vérifiée à l'instant)": "CONFIRMED (just verified)",
    "AUTRE NUMÉRISATION": "OTHER DIGITIZATION",
    "PROBABLE (non vérifiée)": "PROBABLE (unverified)",
    "AUCUNE TROUVÉE": "NONE FOUND",
    "La 1re ligne du manifest.txt de l'archive est le manifeste IIIF de CoMMA "
    "(lecture enregistrée dans l'index).":
        "The first line of the archive's manifest.txt is CoMMA's IIIF manifest "
        "(reading recorded in the index).",
    "La 1re ligne du manifest.txt, relue à l'instant sur Hugging Face, est le manifeste IIIF de CoMMA.":
        "The first line of manifest.txt, just re-read on Hugging Face, is CoMMA's IIIF manifest.",
    "Le nom de l'archive correspond à la cote, mais son manifest.txt cite un autre manifeste IIIF : "
    "ce n'est PAS la transcription affichée par CoMMA.":
        "The archive name matches the shelfmark, but its manifest.txt cites another IIIF manifest: "
        "this is NOT the transcription shown by CoMMA.",
    "Le nom de l'archive correspond à la cote, mais son manifest.txt n'a pas pu être lu.":
        "The archive name matches the shelfmark, but its manifest.txt could not be read.",
    "Aucune archive trouvée. Ce n'est jamais une preuve d'absence.":
        "No archive found. This is never proof of absence.",

    # --- comma_core : correspondances
    "Vérification en direct de %s…": "Live check of %s…",
    " (relecture impossible : %s)": " (could not re-read: %s)",
    "L'archive a changé : son manifest.txt cite désormais %s":
        "The archive has changed: its manifest.txt now cites %s",
    "\nPremier fichier ALTO : %s": "\nFirst ALTO file: %s",
    "Lecture de %d manifest.txt candidats…": "Reading %d candidate manifest.txt files…",
    "\nManifeste de l'archive : %s": "\nArchive manifest: %s",
    "\nCe manifeste correspond dans CoMMA à : %s (%s)": "\nIn CoMMA, this manifest belongs to: %s (%s)",
    "Aucune archive dont le manifest.txt cite %s n'a été trouvée parmi les %d archives indexées "
    "(%d lues%s). Ce n'est PAS une preuve d'absence : l'archive peut avoir un nom inattendu, "
    "être postérieure à l'index, ou ne pas être publiée.":
        "No archive whose manifest.txt cites %s was found among the %d indexed archives "
        "(%d read%s). This is NOT proof of absence: the archive may have an unexpected name, "
        "be newer than the index, or not be published.",
    "le manifeste IIIF": "the IIIF manifest",
    ", %d non lues": ", %d unread",
    "Ressource et première page confirmées par l'API DTS de CoMMA (%d pages) ; "
    "l'adresse suit le schéma de l'interface TexTile (/doc/<id>/p/<page>).":
        "Resource and first page confirmed by the CoMMA DTS API (%d pages); "
        "the address follows the TexTile interface pattern (/doc/<id>/p/<page>).",
    "Lien construit, non vérifié (API DTS injoignable : %s)":
        "Link built but not verified (DTS API unreachable: %s)",
    "\nComparaison des pages impossible : %s": "\nPage comparison impossible: %s",
    "\n✔ Les %d pages CoMMA et les %d fichiers ALTO de manifest.txt portent les mêmes noms, "
    "dans le même ordre.":
        "\n✔ The %d CoMMA pages and the %d ALTO files in manifest.txt have the same names, "
        "in the same order.",
    "\n⚠ Pages CoMMA : %d ; fichiers ALTO : %d ; noms communs : %d. Les deux listes ne sont pas "
    "identiques.":
        "\n⚠ CoMMA pages: %d; ALTO files: %d; names in common: %d. The two lists are not identical.",
    "manifest.txt illisible : %s": "manifest.txt unreadable: %s",
    "Manifeste trouvé dans le catalogue CoMMA.": "Manifest found in the CoMMA catalogue.",
    "Biblissima (P196) : %s": "Biblissima (P196): %s",
    "Attention : %s est dans CoMMA avec un AUTRE manifeste (%s).":
        "Warning: %s is in CoMMA with ANOTHER manifest (%s).",
    "Biblissima : aucun item ne déclare ce manifeste (P196).":
        "Biblissima: no item declares this manifest (P196).",
    "Biblissima injoignable : %s": "Biblissima unreachable: %s",

    # --- comma_core : téléchargement
    "Téléchargement interrompu": "Download interrupted",
    "Téléchargement de %s : %.1f Mo%s": "Downloading %s: %.1f MB%s",
    "Téléchargement incomplet (%d/%d octets)": "Incomplete download (%d/%d bytes)",
    "Extraction de %s…": "Extracting %s…",

    # --- mode texte
    "Catalogue absent : construction (quelques minutes)…": "Catalogue missing: building it (a few minutes)…",
    "Catalogue : %d manuscrits (%s) ; index ALTO : %d archives, %d manifest.txt lus (%s)":
        "Catalogue: %d manuscripts (%s); ALTO index: %d archives, %d manifest.txt read (%s)",
    "\nRequête : %r -> cotes %s, mots %s (%s)": "\nQuery: %r -> shelfmarks %s, words %s (%s)",
    "Archive %s": "Archive %s",
    "Aucun résultat dans le catalogue local.": "No result in the local catalogue.",
    "  Biblissima     : %s": "  Biblissima     : %s",
    "  Transcription  : %s": "  Transcription  : %s",
    "  Manifeste IIIF : %s": "  IIIF manifest  : %s",
    "  NB : ": "  Note: ",
    "  Extraction : %(presents)d / %(annonces)d fichiers dans %(dossier)s":
        "  Extraction: %(presents)d / %(annonces)d files in %(dossier)s",
    "Cote : ": "Shelfmark: ",
    "\nCote (vide pour quitter) : ": "\nShelfmark (empty to quit): ",
    "Interface graphique indisponible (%s) : mode texte.": "Graphical interface unavailable (%s): text mode.",

    # --- interface : fenêtre, barre du haut, menus
    "Recherche CoMMA — manuscrits, transcriptions et ALTO (v%s)":
        "CoMMA Search — manuscripts, transcriptions and ALTO (v%s)",
    "Chargement des index…": "Loading indexes…",
    "Chargement…": "Loading…",
    "Prêt.": "Ready.",
    "ERREUR : %s\n%s": "ERROR: %s\n%s",
    "ERREUR interface : %s": "Interface ERROR: %s",
    "Copié : %s": "Copied: %s",
    "Cote, référence, Q Biblissima, manifeste IIIF ou nom d'archive :":
        "Shelfmark, reference, Biblissima Q, IIIF manifest or archive name:",
    "Rechercher": "Search",
    "Mettre à jour le catalogue CoMMA (quelques minutes)": "Update the CoMMA catalogue (a few minutes)",
    "Mettre à jour la liste des archives ALTO (+ lire les nouvelles)":
        "Update the ALTO archive list (+ read new ones)",
    "Lire tous les manifest.txt non lus": "Read all unread manifest.txt files",
    "Ouvrir le dossier du cache": "Open the cache folder",
    "À propos des niveaux de certitude": "About certainty levels",
    "Catalogue CoMMA : %d manuscrits (au %s)   •   Archives ALTO : %d, dont %d manifest.txt lus (liste au %s)":
        "CoMMA catalogue: %d manuscripts (as of %s)   •   ALTO archives: %d, %d manifest.txt read (list as of %s)",

    # --- interface : résultats
    "Résultats": "Results",
    "Pert.": "Rel.",
    "Manuscrit (intitulé CoMMA)": "Manuscript (CoMMA title)",
    "Résultats : %d (%s%s)%s": "Results: %d (%s%s)%s",
    " — cote(s) : %s ; mots : %s": " — shelfmark(s): %s; words: %s",
    "catalogue local": "local catalogue",
    "serveur CoMMA": "CoMMA server",
    "archive": "archive",
    "le catalogue local n'avait rien": "nothing in the local catalogue",
    "Recherche sur le serveur CoMMA…": "Searching the CoMMA server…",
    "Aucun résultat (ni dans le catalogue local du %s, ni sur le serveur CoMMA). "
    "Vérifiez la cote ou mettez le catalogue à jour.":
        "No result (neither in the local catalogue of %s nor on the CoMMA server). "
        "Check the shelfmark or update the catalogue.",
    "Archive inconnue de l'index : %s": "Archive not in the index: %s",
    "chaîne archive → manifest.txt → IIIF → CoMMA/Biblissima": "chain archive → manifest.txt → IIIF → CoMMA/Biblissima",
    "Lecture du manifest.txt de l'archive…": "Reading the archive's manifest.txt…",

    # --- interface : fiche du manuscrit
    "Sélectionnez un manuscrit": "Select a manuscript",
    "Copier": "Copy",
    "Ouvrir ▶": "Open ▶",
    "1. Cote normalisée": "1. Normalized shelfmark",
    "3. Transcription CoMMA": "3. CoMMA transcription",
    "4. Manifeste IIIF": "4. IIIF manifest",
    "Date · langue · volume": "Date · language · size",
    "Intitulé CoMMA : %s": "CoMMA title: %s",
    "%s — identifiant de la ressource CoMMA (API DTS)": "%s — identifier of the CoMMA resource (DTS API)",
    "Pas d'identifiant Biblissima pour cette ressource : %s": "No Biblissima identifier for this resource: %s",
    "Vérification en cours…": "Checking…",
    "Source déclarée par CoMMA (dublinCore.source)": "Source declared by CoMMA (dublinCore.source)",

    # --- interface : archives ALTO
    "5–7. Archives ALTO sur Hugging Face": "5–7. ALTO archives on Hugging Face",
    "Correspondance": "Match",
    "Certitude": "Certainty",
    "Archive (dépôt comma-project/alto-dataset)": "Archive (comma-project/alto-dataset repository)",
    "Taille": "Size",
    "Recherche des archives ALTO…": "Looking for ALTO archives…",
    "\n\n→ « Recherche approfondie » relit les manifest.txt des archives non lues et met à jour "
    "la liste Hugging Face.":
        "\n\n→ “Deep search” reads the manifest.txt of unread archives and updates the Hugging Face list.",
    "NB : ": "Note: ",
    "%s — certitude %d %%\n%s\nmanifest.txt : %s\nURL : %s": "%s — certainty %d %%\n%s\nmanifest.txt: %s\nURL: %s",
    "non lu": "not read",
    "⬇ Télécharger et extraire la sélection": "⬇ Download and extract selection",
    "⬇ Toutes les confirmées": "⬇ All confirmed",
    "Voir sur Hugging Face ▶": "View on Hugging Face ▶",
    "Copier l'URL": "Copy URL",
    "Recherche approfondie": "Deep search",
    "Recherche approfondie…": "Deep search…",
    "Destination :": "Destination:",
    "Changer…": "Change…",
    "Dossier de destination des ALTO": "Destination folder for ALTO files",
    "Sélectionnez une archive dans la liste.": "Select an archive in the list.",
    "Correspondance non confirmée": "Unconfirmed match",
    "%d archive(s) sélectionnée(s) ne sont PAS confirmées comme la transcription affichée par "
    "CoMMA :\n\n%s\n\nTélécharger quand même ?":
        "%d selected archive(s) are NOT confirmed as the transcription shown by CoMMA:\n\n%s\n\n"
        "Download anyway?",
    "Aucune archive confirmée pour ce manuscrit.": "No confirmed archive for this manuscript.",
    "Téléchargement": "Download",
    "Téléchargement…": "Downloading…",
    "Échec : %s": "Failed: %s",
    "%s %s\n   %d / %d fichiers ALTO annoncés par manifest.txt\n   %s":
        "%s %s\n   %d / %d ALTO files listed in manifest.txt\n   %s",
    "Extrait : %s (%d/%d fichiers)": "Extracted: %s (%d/%d files)",
    "ALTO extraits": "ALTO files extracted",
    "\n\nOuvrir le dossier ?": "\n\nOpen the folder?",

    # --- interface : index et recherche approfondie
    "Catalogue absent": "Catalogue missing",
    "Le catalogue local de CoMMA est absent.\nLe construire maintenant ? (environ 5 minutes, une seule fois)":
        "The local CoMMA catalogue is missing.\nBuild it now? (about 5 minutes, only once)",
    "Catalogue CoMMA mis à jour : %d manuscrits.": "CoMMA catalogue updated: %d manuscripts.",
    "Mise à jour du catalogue CoMMA…": "Updating the CoMMA catalogue…",
    "Liste Hugging Face : %d archives (%d nouvelles, %d disparues).":
        "Hugging Face list: %d archives (%d new, %d removed).",
    "Lecture de %d manifest.txt non encore lus…": "Reading %d manifest.txt files not yet read…",
    "Mise à jour des archives ALTO…": "Updating ALTO archives…",
    "Index ALTO": "ALTO index",
    "Tous les manifest.txt connus ont été lus.": "All known manifest.txt files have been read.",
    "Lecture de %d manifest.txt…": "Reading %d manifest.txt files…",
    "Liste Hugging Face actualisée : %d archives (%d nouvelles).": "Hugging Face list refreshed: %d archives (%d new).",
    "Relecture de %d archives de « %s »…": "Re-reading %d archives from “%s”…",
    "Biblissima (P196) pour ce manifeste : %s": "Biblissima (P196) for this manifest: %s",
    "aucun item": "no item",
    "Niveaux de certitude": "Certainty levels",

    # --- robustesse : journal des erreurs, fermeture
    "Ouverture impossible": "Cannot open",
    "Ouvrir le journal des erreurs": "Open the error log",
    "Journal des erreurs": "Error log",
    "Aucune erreur enregistrée.\n\n%s": "No error recorded.\n\n%s",
    "Fermeture…": "Closing…",
    "Rien dans le catalogue local, et le serveur CoMMA n'a pas pu être interrogé : %s":
        "Nothing in the local catalogue, and the CoMMA server could not be queried: %s",
    "Recherche des archives ALTO impossible : %s": "Could not look for ALTO archives: %s",
    "Déjà en cours :\n%s": "Already in progress:\n%s",

    # --- sécurité : données réseau non fiables
    "Adresse refusée (https seulement) : %s": "Address refused (https only): %s",
    "Réponse trop volumineuse pour %s": "Response too large for %s",
    "Parcours du catalogue anormalement long : arrêté": "Catalogue walk abnormally long: stopped",
    "Archive refusée : %s": "Archive refused: %s",
    "trop de fichiers": "too many files",
    "fichier démesuré": "oversized file",
    "taille extraite démesurée": "oversized extracted content",
    "chemin dangereux": "unsafe path",
    "Le dossier %s contient déjà une autre archive (%s)": "The folder %s already contains another archive (%s)",
    "Téléchargement plus gros qu'annoncé, arrêté : %s": "Download larger than announced, stopped: %s",
    "Introuvable : %s": "Not found: %s",
    "Ce n'est pas une adresse web : elle n'est pas ouverte.\n\n%s": "This is not a web address: it is not opened.\n\n%s",
    "Chemins trop longs pour Windows (%d caractères) : choisissez un dossier de destination plus court "
    "(par exemple C:\\ALTO). Détail : %s":
        "Paths too long for Windows (%d characters): choose a shorter destination folder "
        "(for example C:\\ALTO). Details: %s",
    "Aucune archive confirmée pour ce manuscrit (ou vérification en cours).":
        "No confirmed archive for this manuscript (or check still in progress).",
}
