# -*- coding: utf-8 -*-
"""
comma_core — logique du moteur de recherche CoMMA (sans interface graphique).

Ce module ne dépend que de la bibliothèque standard de Python (3.8+) et
fonctionne sous Windows, macOS et Linux.

Sources interrogées (toutes vérifiées le 15/11/2026) :

* Catalogue CoMMA, API DTS de TexTile : https://comma.inria.fr/api/
    - /collection/?id=<id>&nav=children&page=<n>   arborescence
      ville -> établissement -> manuscrit (20 membres par page) ;
      chaque manuscrit est une « Resource » dont l'@id est l'URI Biblissima
      (https://data.biblissima.fr/entity/Q...) et dont dublinCore.source
      est l'URL du manifeste IIIF utilisé par CoMMA.
    - /navigation/?resource=<uri>&down=1           liste des pages ; les
      identifiants de pages sont les noms des fichiers ALTO.
* Archives ALTO : https://huggingface.co/datasets/comma-project/alto-dataset
    - /api/datasets/.../tree/main/<dossier>?recursive=true   liste des
      archives (pagination par en-tête Link) ;
    - chaque archive .tar.gz commence par un membre « manifest.txt » dont
      la première ligne est l'URI du manifeste IIIF d'origine et les
      suivantes la liste ordonnée des fichiers ALTO. Une requête HTTP
      partielle (Range: bytes=0-4095) suffit à lire cette première ligne.
* Biblissima (Wikibase) : https://data.biblissima.fr/w/api.php
    - P196 = manifeste IIIF, P195 = cote, P194 = établissement ;
    - recherche inverse : list=search, srnamespace=120,
      srsearch=haswbstatement:P196=<URL du manifeste>.

Règle d'or : une recherche infructueuse n'est jamais une preuve d'absence.
"""

from __future__ import annotations

import getpass
import gzip
import http.client
import json
import os
import re
import socket
import ssl
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from comma_i18n import _
except ImportError:  # module de traduction absent : textes français
    def _(text: str) -> str:
        return text

VERSION = "1.0"

COMMA_API = "https://comma.inria.fr/api"
COMMA_SITE = "https://comma.inria.fr"
COMMA_ROOT_COLLECTION = "mega-corpus"
HF_REPO = "comma-project/alto-dataset"
HF_TREE = "https://huggingface.co/api/datasets/" + HF_REPO + "/tree/main"
HF_RESOLVE = "https://huggingface.co/datasets/" + HF_REPO + "/resolve/main/"
HF_BROWSE = "https://huggingface.co/datasets/" + HF_REPO + "/blob/main/"
BIBLISSIMA_API = "https://data.biblissima.fr/w/api.php"
BIBLISSIMA_ENTITY = "https://data.biblissima.fr/entity/"
BIBLISSIMA_ITEM_PAGE = "https://data.biblissima.fr/w/Item:"

USER_AGENT = "CommaRecherche/%s (outil de recherche CoMMA; Python urllib)" % VERSION

ProgressFn = Optional[Callable[[str], None]]

# --------------------------------------------------------------------------
# Limites et validation des données venues du réseau (traitées comme non fiables)
# --------------------------------------------------------------------------

MAX_JSON_BYTES = 64 << 20          # réponse JSON la plus grosse acceptée
MAX_DOWNLOAD_BYTES = 8 << 30       # plafond absolu d'un téléchargement de taille inconnue
MAX_MEMBER_BYTES = 512 << 20       # plus gros fichier accepté dans une archive
MAX_MEMBERS = 200_000              # nombre de fichiers dans une archive
MAX_EXPANSION = 60                 # taille extraite <= 60 x taille compressée (+ 64 Mo)
MAX_LISTING_PAGES = 1000           # pages de liste Hugging Face par dossier
MAX_LAST_PAGE = 5000               # pages d'une collection DTS
MAX_CRAWL_PAGES = 50_000           # pages lues pendant tout le parcours du catalogue

_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | \
    {"LPT%d" % i for i in range(1, 10)}
_BAD_PATH_CHARS = set('<>:"|?*\\')


def is_web_url(value) -> bool:
    """Vrai seulement pour une adresse http(s) avec un nom d'hôte.

    Indispensable avant webbrowser.open : sous Windows, il appelle os.startfile,
    qui exécuterait un chemin local ou réseau (« \\\\serveur\\x.exe »).
    """
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    if any(c in value for c in "\r\n\t\0"):
        return False
    try:
        parts = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def web_url_or_none(value) -> Optional[str]:
    return value.strip() if is_web_url(value) else None


def is_safe_archive_path(path) -> bool:
    """Chemin d'archive acceptable venu de la liste Hugging Face : relatif, sans « .. »,
    sans lettre de lecteur ni barre oblique inverse, sans caractère interdit sous Windows.
    (Les espaces et les virgules sont permis : 37 archives de Munich en contiennent.)"""
    if not isinstance(path, str) or not path.endswith(".tar.gz") or len(path) > 400:
        return False
    if path.startswith("/"):
        return False
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return False
        if any(ord(c) < 32 or c in _BAD_PATH_CHARS for c in part):
            return False
        if part != part.strip() or part.endswith("."):
            return False
    stem = parts[-1][:-7]
    return bool(stem) and not stem.startswith(".") and stem.split(".")[0].upper() not in _WIN_RESERVED


def safe_local_name(text: str) -> str:
    """Nom de dossier ou de fichier local sûr sur les trois systèmes."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text or "").strip(" .")
    if not name or name.split(".")[0].upper() in _WIN_RESERVED:
        name = "archive_" + name
    return name[:180]


def _text(value) -> str:
    """Champ texte d'une réponse réseau : toujours une chaîne."""
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)

# --------------------------------------------------------------------------
# Emplacements des fichiers
# --------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_DATA_DIR = os.path.join(APP_DIR, "donnees")

CATALOG_FILE = "catalogue_comma.json.gz"
ALTO_INDEX_FILE = "index_alto.json.gz"


def user_cache_dir() -> str:
    """Répertoire de cache propre à la plateforme."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        path = os.path.join(base, "CommaRecherche")
    elif sys.platform == "darwin":
        path = os.path.expanduser("~/Library/Caches/CommaRecherche")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        path = os.path.join(base, "comma-recherche")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:  # profil non inscriptible : dossier temporaire propre à l'utilisateur
        try:
            user = re.sub(r"[^A-Za-z0-9_-]", "_", getpass.getuser() or "user")
        except Exception:
            user = "user"
        path = os.path.join(tempfile.gettempdir(), "comma-recherche-" + user)
        os.makedirs(path, mode=0o700, exist_ok=True)
        if hasattr(os, "getuid"):  # dossier partagé /tmp : il doit être à nous, et à nous seuls
            st = os.lstat(path)
            if os.path.islink(path) or st.st_uid != os.getuid() or (st.st_mode & 0o077):
                path = tempfile.mkdtemp(prefix="comma-recherche-")
    return path


def default_download_dir() -> str:
    home = os.path.expanduser("~")
    for name in ("Downloads", "Téléchargements"):
        cand = os.path.join(home, name)
        if os.path.isdir(cand):
            return os.path.join(cand, "CoMMA_ALTO")
    return os.path.join(home, "CoMMA_ALTO")


def settings_path() -> str:
    return os.path.join(user_cache_dir(), "reglages.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    """Écriture atomique (fichier temporaire puis remplacement) ; jamais d'exception."""
    try:
        path = settings_path()
        with _SAVE_LOCK:
            tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            _replace_with_retry(tmp, path)
    except Exception:
        pass


def _read_json_gz(path: str):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


_SAVE_LOCK = threading.Lock()


def _write_json_gz(path: str, data) -> None:
    """Écriture atomique : fichier temporaire unique puis remplacement.

    Sous Windows, os.replace échoue si un autre programme (antivirus, indexation)
    tient le fichier ouvert : on réessaie quelques fois avant d'abandonner.
    """
    with _SAVE_LOCK:
        tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            for attempt in range(6):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.5 * (attempt + 1))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _load_newest(filename: str):
    """Charge la version la plus récente (cache utilisateur ou données livrées)."""
    best = None
    folders = [BUNDLED_DATA_DIR]
    try:
        folders.insert(0, user_cache_dir())
    except OSError:
        pass
    for folder in folders:
        path = os.path.join(folder, filename)
        if not os.path.exists(path):
            continue
        try:
            data = _read_json_gz(path)
        except Exception:  # fichier abîmé (coupure pendant l'écriture…) : ignoré
            continue
        if not isinstance(data, dict):
            continue
        if best is None or str(data.get("generated", "")) > str(best.get("generated", "")):
            best = data
    return best


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    """Certificats du système (Windows, Linux) ; certifi sous macOS, où le Python de
    python.org n'en a pas, ou si le système n'en fournit aucun."""
    if sys.platform != "darwin":
        paths = ssl.get_default_verify_paths()
        if sys.platform.startswith("win") or (paths.cafile and os.path.exists(paths.cafile)) or \
                (paths.capath and os.path.isdir(paths.capath)):
            return ssl.create_default_context()
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL = _ssl_context()


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse toute redirection qui ne mène pas à une adresse https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise urllib.error.HTTPError(newurl, code, "redirect to a non-https URL refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener() -> urllib.request.OpenerDirector:
    """Ouvreur réduit : https seulement (pas de file://, ftp://, data:, ni http en clair)."""
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(), urllib.request.UnknownHandler(),
                    urllib.request.HTTPDefaultErrorHandler(), _HttpsOnlyRedirect(),
                    urllib.request.HTTPErrorProcessor(), urllib.request.HTTPSHandler(context=_SSL)):
        opener.add_handler(handler)
    return opener


_OPENER = _build_opener()


class HttpError(Exception):
    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


class Interrupted(RuntimeError):
    """Arrêt demandé par l'utilisateur (fermeture de la fenêtre…)."""


def _permanent_network_error(exc: BaseException) -> bool:
    """Erreurs qu'une nouvelle tentative ne corrigera pas (certificat, nom d'hôte inconnu)."""
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ssl.SSLCertVerificationError, socket.gaierror))


def http_get(url: str, headers: Optional[dict] = None, timeout: float = 60,
             retries: int = 4, want_headers: bool = False, max_bytes: Optional[int] = None,
             stop: Optional[threading.Event] = None):
    """GET avec reprises sur les erreurs passagères (429, 5xx, réseau, réponse tronquée).

    max_bytes borne la lecture : si un serveur ignore l'en-tête Range et renvoie
    toute une archive de plusieurs centaines de Mo, on n'en lit que le début.
    Sans max_bytes, une réponse de plus de MAX_JSON_BYTES est refusée.
    """
    if not url.startswith("https://"):
        raise HttpError(_("Adresse refusée (https seulement) : %s") % url[:200])
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    last = None
    for attempt in range(retries + 1):
        if stop is not None and stop.is_set():
            raise Interrupted(_("Mise à jour interrompue"))
        wait_s = min(30, 1.5 * 2 ** attempt)
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with _OPENER.open(req, timeout=timeout) as resp:
                if max_bytes:
                    body = resp.read(max_bytes)
                else:
                    body = resp.read(MAX_JSON_BYTES + 1)
                    if len(body) > MAX_JSON_BYTES:
                        raise HttpError(_("Réponse trop volumineuse pour %s") % url)
                if want_headers:
                    return body, resp.status, dict(resp.headers)
                return body
        except urllib.error.HTTPError as exc:
            last = HttpError(_("HTTP %s pour %s") % (exc.code, url), exc.code)
            if exc.code not in (408, 425, 429, 500, 502, 503, 504):
                raise last
            try:  # le serveur indique combien de temps attendre
                wait_s = max(wait_s, min(60, float(exc.headers.get("Retry-After") or 0)))
            except (TypeError, ValueError, AttributeError):
                pass
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                http.client.HTTPException) as exc:
            last = HttpError(_("Erreur réseau pour %s : %s") % (url, exc))
            if _permanent_network_error(exc):
                raise last
        if attempt < retries:
            end = time.time() + wait_s
            while time.time() < end:  # attente interrompable
                if stop is not None and stop.is_set():
                    raise Interrupted(_("Mise à jour interrompue"))
                time.sleep(0.2)
    raise last  # type: ignore[misc]


def http_json(url: str, **kw):
    body = http_get(url, **kw)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:  # page d'erreur HTML, réponse vide ou tronquée
        raise HttpError(_("Réponse illisible (JSON attendu) pour %s") % url)


# --------------------------------------------------------------------------
# Normalisation des textes, des cotes et des manifestes IIIF
# --------------------------------------------------------------------------

_LIGATURES = {"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae", "ß": "ss", "ſ": "s"}


def strip_accents(text: str) -> str:
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def norm_text(text: str) -> str:
    """Minuscules, sans accents, ponctuation remplacée par des espaces."""
    text = strip_accents(text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def canon_alnum(token: str) -> str:
    """Supprime les zéros initiaux de chaque groupe de chiffres : a002 -> a2, 0816 -> 816."""
    return re.sub(r"\d+", lambda m: str(int(m.group(0))), token)


def _has_digit(tok: str) -> bool:
    return any(c.isdigit() for c in tok)


def shelf_keys(text: str) -> set:
    """Clés « cote » d'un intitulé : jetons contenant un chiffre, normalisés,
    plus les formes fusionnées « lettre isolée + nombre » (A 002 -> a2) et
    « nombre + lettre isolée » (6400 G -> 6400g)."""
    toks = norm_text(text).split()
    keys = set()
    for i, tok in enumerate(toks):
        if _has_digit(tok):
            keys.add(canon_alnum(tok))
            if i > 0 and len(toks[i - 1]) == 1 and toks[i - 1].isalpha():
                keys.add(canon_alnum(toks[i - 1] + tok))
            if i + 1 < len(toks) and len(toks[i + 1]) == 1 and toks[i + 1].isalpha():
                keys.add(canon_alnum(tok + toks[i + 1]))
    return keys


def iiif_key(url: Optional[str]) -> Optional[str]:
    """Clé canonique d'un manifeste IIIF.

    * ARK : « ark:/NAAN/nom » (gallica.bnf.fr/iiif/ark:/12148/x/manifest.json et
      openapi.bnf.fr/iiif/presentation/v3/ark:/12148/x/manifest.json donnent la
      même clé, ce qui a été vérifié sur BnF Latin 16400) ;
    * sinon : hôte + chemin en minuscules, sans protocole ni « /manifest(.json) ».
    """
    if not url:
        return None
    url = url.strip()
    m = re.search(r"ark:/(\d+)/([A-Za-z0-9]+)", url)
    if m:
        return "ark:/%s/%s" % (m.group(1), m.group(2).lower())
    u = re.sub(r"^https?://", "", url, flags=re.I)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    u = re.sub(r"/manifest(\.json)?$", "", u, flags=re.I)
    u = re.sub(r"\.json$", "", u, flags=re.I)
    return u.lower()


def qid_of(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(Q\d+)\b", text)
    return m.group(1) if m else None


def comma_doc_url(uri: str, page: Optional[str] = None) -> str:
    """URL de l'interface CoMMA (route Vue « document » de TexTile).

    L'interface encode l'URI avec encodeURIComponent puis le routeur l'encode
    une seconde fois : https%253A%252F%252Fdata.biblissima.fr%252Fentity%252FQ...
    """
    enc = urllib.parse.quote(urllib.parse.quote(uri, safe=""), safe="")
    url = "%s/doc/%s" % (COMMA_SITE, enc)
    if page:
        url += "/p/" + urllib.parse.quote(page, safe="")
    return url


# --------------------------------------------------------------------------
# Analyse de la requête
# --------------------------------------------------------------------------

STOPWORDS = {
    "de", "du", "des", "la", "le", "les", "l", "d", "et", "en", "a", "au", "aux",
    "ms", "mss", "manuscrit", "manuscrits", "cod", "codex", "n", "no", "num",
    "numero", "cote", "ville", "the", "of",
}

# abréviations -> mots acceptés dans l'intitulé (un seul suffit)
ABBREVIATIONS: Dict[str, Tuple[str, ...]] = {
    "bm": ("municipale", "mediatheque", "bibliotheque", "bibliotheques"),
    "bmvr": ("municipale", "mediatheque", "bibliotheque"),
    "bibl": ("bibliotheque",),
    "bib": ("bibliotheque",),
    "med": ("mediatheque",),
    "bnf": ("nationale",),
    "bn": ("nationale",),
    "bnu": ("universitaire",),
    "bu": ("universitaire", "universite"),
    "bis": ("interuniversitaire", "sorbonne"),
    "am": ("municipales", "municipale"),
    "ad": ("departementales", "departementale"),
    "an": ("nationales",),
    "arch": ("archives",),
    "lat": ("latin",),
    "latin": ("latin", "lat"),
    "fr": ("francais", "fr"),
    "fran": ("francais",),
    "franc": ("francais",),
    "francais": ("francais", "fr"),
    "nal": ("nal",),
    "naf": ("naf",),
    "nouv": ("nouvelles", "nouv", "nal", "naf"),
    "acq": ("acquisitions", "nal", "naf"),
    "ste": ("sainte",),
    "st": ("saint",),
    "gr": ("grec",),
    "ital": ("italien",),
    "esp": ("espagnol",),
    "all": ("allemand",),
    "res": ("reserve", "res"),
    "smith": ("smith",),
}


@dataclass
class ParsedQuery:
    raw: str
    numbers: List[str]            # clés de cote exigées (toutes)
    words: List[Tuple[str, ...]]  # groupes de mots (au moins un mot par groupe)
    qid: Optional[str] = None
    iiif: Optional[str] = None    # clé IIIF canonique si la requête est un manifeste/ARK
    archive: Optional[str] = None  # nom d'archive ALTO saisi tel quel


def parse_query(raw: str) -> ParsedQuery:
    raw = (raw or "").strip()
    q = ParsedQuery(raw=raw, numbers=[], words=[])
    if not raw:
        return q
    # identifiants directs
    if re.search(r"\.tar\.gz\s*$", raw) or re.match(r"^(batch_\d+|old-batch)", raw):
        q.archive = raw.split("/")[-1].strip()
        return q
    dec = urllib.parse.unquote(urllib.parse.unquote(raw))
    if re.search(r"ark:/\d+/", dec) or ("manifest" in dec and dec.startswith("http")):
        q.iiif = iiif_key(dec)
        return q
    if re.fullmatch(r"[Qq]\d+", raw) or "biblissima" in dec or "comma.inria.fr/doc" in dec:
        qq = qid_of(dec.upper() if re.fullmatch(r"[Qq]\d+", raw) else dec)
        if qq:
            q.qid = qq
            return q

    toks = norm_text(raw).split()
    used = [False] * len(toks)
    for i, tok in enumerate(toks):
        if not _has_digit(tok) or used[i]:
            continue
        key = tok
        if i > 0 and not used[i - 1] and len(toks[i - 1]) == 1 and toks[i - 1].isalpha():
            key = toks[i - 1] + tok
            used[i - 1] = True
        elif i + 1 < len(toks) and len(toks[i + 1]) == 1 and toks[i + 1].isalpha() \
                and toks[i + 1] not in ("a", "d", "l"):
            key = tok + toks[i + 1]
            used[i + 1] = True
        used[i] = True
        q.numbers.append(canon_alnum(key))
    for i, tok in enumerate(toks):
        if used[i] or tok in STOPWORDS:
            continue
        if tok in ABBREVIATIONS:
            q.words.append(ABBREVIATIONS[tok])
        elif len(tok) >= 2:
            q.words.append((tok,))
    return q


# --------------------------------------------------------------------------
# Catalogue CoMMA
# --------------------------------------------------------------------------

@dataclass
class Manuscript:
    uri: str                     # https://data.biblissima.fr/entity/Q...
    title: str                   # intitulé CoMMA
    iiif: Optional[str]          # manifeste IIIF (dublinCore.source)
    created: str = ""
    language: str = ""
    extent: str = ""
    city: str = ""
    institution: str = ""
    # champs calculés
    words: List[str] = field(default_factory=list, repr=False)
    keys: set = field(default_factory=set, repr=False)
    key_iiif: Optional[str] = None

    @property
    def qid(self) -> Optional[str]:
        return qid_of(self.uri) if "biblissima" in self.uri else None

    @property
    def shelfmark(self) -> str:
        """Cote seule (intitulé sans l'établissement)."""
        t = self.title
        if self.institution and t.startswith(self.institution):
            rest = t[len(self.institution):].lstrip(" ,.")
            if rest:
                return rest
        return t.rsplit(",", 1)[-1].strip() if "," in t else t

    @property
    def normalized_reference(self) -> str:
        """Ville, établissement, cote — séparés par des virgules."""
        t = self.title
        city = self.city
        if city and t.startswith(city + ". "):
            t = city + ", " + t[len(city) + 2:]
        return re.sub(r"\s+", " ", t).strip()

    def comma_url(self, page: Optional[str] = None) -> str:
        return comma_doc_url(self.uri, page)

    def prepare(self) -> None:
        self.words = norm_text(self.title).split()
        self.keys = shelf_keys(self.title)
        self.key_iiif = iiif_key(self.iiif)

    def to_json(self) -> dict:
        return {"uri": self.uri, "title": self.title, "iiif": self.iiif,
                "created": self.created, "language": self.language,
                "extent": self.extent, "city": self.city,
                "institution": self.institution}

    @classmethod
    def from_json(cls, d: dict) -> "Manuscript":
        uri = d.get("uri") if isinstance(d, dict) else None
        if not isinstance(uri, str) or not uri:
            raise ValueError("fiche sans identifiant")
        m = cls(uri=uri, title=_text(d.get("title")), iiif=web_url_or_none(d.get("iiif")),
                created=_text(d.get("created")), language=_text(d.get("language")),
                extent=_text(d.get("extent")), city=_text(d.get("city")),
                institution=_text(d.get("institution")))
        m.prepare()
        return m


def _first(v) -> str:
    if isinstance(v, list):
        v = [x for x in v if x]
        return ", ".join(str(x) for x in v)
    return _text(v)


def _resource_to_manuscript(member: dict, parents: Sequence[str]) -> Manuscript:
    dc = member.get("dublinCore") if isinstance(member.get("dublinCore"), dict) else {}
    src = dc.get("source")
    if isinstance(src, list):
        src = next((s for s in src if isinstance(s, str) and s), None)
    city = _text(parents[0]) if parents else ""
    institution = _text(parents[-1]) if len(parents) >= 2 else ""
    m = Manuscript(uri=_text(member.get("@id")), title=_text(member.get("title")),
                   iiif=web_url_or_none(src), created=_first(dc.get("created")),
                   language=_first(dc.get("language")),
                   extent=_first(dc.get("extent")), city=city,
                   institution=institution)
    m.prepare()
    return m


def comma_collection_page(cid: str, page: int = 1, stop: Optional[threading.Event] = None) -> dict:
    url = "%s/collection/?id=%s&nav=children&page=%d" % (
        COMMA_API, urllib.parse.quote(cid, safe=""), page)
    return http_json(url, stop=stop)


def comma_resource(uri: str, stop: Optional[threading.Event] = None) -> Optional[Manuscript]:
    """Interroge directement le serveur DTS pour une ressource (Q...)."""
    url = "%s/collection/?id=%s" % (COMMA_API, urllib.parse.quote(uri, safe=""))
    try:
        d = http_json(url, retries=2, stop=stop)
    except HttpError as exc:
        if exc.code == 404:
            return None
        raise
    if not isinstance(d, dict) or d.get("@type") != "Resource":
        return None
    return _resource_to_manuscript(d, [])


def comma_pages(uri: str, stop: Optional[threading.Event] = None) -> List[str]:
    """Identifiants ordonnés des pages d'une ressource (API DTS /navigation)."""
    url = "%s/navigation/?resource=%s&down=1" % (COMMA_API, urllib.parse.quote(uri, safe=""))
    d = http_json(url, retries=2, stop=stop)
    members = d.get("member") if isinstance(d, dict) else None
    return [m["identifier"] for m in (members or [])
            if isinstance(m, dict) and isinstance(m.get("identifier"), str) and m["identifier"]]


def comma_first_page(uri: str, stop: Optional[threading.Event] = None) -> Optional[str]:
    pages = comma_pages(uri, stop=stop)
    return pages[0] if pages else None


def _last_page(view: Optional[dict]) -> int:
    if not isinstance(view, dict):
        return 1
    m = re.search(r"[?&]page=(\d{1,9})", _text(view.get("last"))[:2048])
    return max(1, min(int(m.group(1)), MAX_LAST_PAGE)) if m else 1


def crawl_comma_catalog(progress: ProgressFn = None, workers: int = 6,
                        stop: Optional[threading.Event] = None) -> dict:
    """Parcourt toute l'arborescence DTS de CoMMA (≈ 2 300 requêtes)."""
    results: Dict[str, Manuscript] = {}
    lock = threading.Lock()
    counters = {"pages": 0}
    abort = threading.Event()   # arrêt demandé ou erreur : les tâches en attente s'arrêtent vite
    visited = {COMMA_ROOT_COLLECTION}  # une collection rattachée à deux parents n'est lue qu'une fois

    def handle_members(members, parents):
        subs = []
        for mem in members:
            if not isinstance(mem, dict):
                continue
            if mem.get("@type") == "Resource":
                ms = _resource_to_manuscript(mem, parents)
                with lock:
                    results[ms.uri] = ms
            elif mem.get("@type") == "Collection" and isinstance(mem.get("@id"), str) and mem["@id"]:
                with lock:
                    if mem["@id"] in visited:
                        continue
                    visited.add(mem["@id"])
                subs.append((mem["@id"], list(parents) + [mem.get("title") or mem["@id"]]))
        return subs

    def fetch_page(cid, page, parents):
        if abort.is_set():
            return [], []
        d = comma_collection_page(cid, page, stop=abort)
        with lock:
            counters["pages"] += 1
            if counters["pages"] > MAX_CRAWL_PAGES:  # serveur qui inventerait des pages sans fin
                raise HttpError(_("Parcours du catalogue anormalement long : arrêté"))
        if not isinstance(d, dict):
            return [], []
        members = d.get("member") if isinstance(d.get("member"), list) else []
        subs = handle_members(members, parents)
        extra = []
        if page == 1:
            last = _last_page(d.get("view"))
            extra = [(cid, p, parents) for p in range(2, last + 1)]
        return subs, extra

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_page, COMMA_ROOT_COLLECTION, 1, [])}
        last_report = 0
        while futures:
            done, futures = wait(futures, return_when=FIRST_COMPLETED, timeout=1.0)
            if stop is not None and stop.is_set():
                abort.set()
            for fut in done:
                try:
                    subs, extra = fut.result()
                except BaseException:
                    abort.set()
                    for f in futures:
                        f.cancel()
                    raise
                if abort.is_set():
                    continue
                for cid, parents in subs:
                    futures.add(ex.submit(fetch_page, cid, 1, parents))
                for cid, p, parents in extra:
                    futures.add(ex.submit(fetch_page, cid, p, parents))
            if progress and counters["pages"] - last_report >= 50:
                last_report = counters["pages"]
                progress(_("Catalogue CoMMA : %d pages lues, %d manuscrits")
                     % (counters["pages"], len(results)))
    if abort.is_set():
        raise Interrupted(_("Mise à jour interrompue"))
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": COMMA_API, "count": len(results),
            "items": [m.to_json() for m in results.values()]}


class Catalog:
    def __init__(self, data: Optional[dict] = None):
        self.generated = ""
        self.items: List[Manuscript] = []
        self.by_qid: Dict[str, Manuscript] = {}
        self.by_iiif: Dict[str, List[Manuscript]] = {}
        if data:
            self.load(data)

    def load(self, data: dict) -> None:
        items = []
        for d in data.get("items", []) or []:
            try:
                items.append(Manuscript.from_json(d))
            except Exception:  # entrée abîmée : ignorée plutôt que tout perdre
                continue
        by_qid: Dict[str, Manuscript] = {}
        by_iiif: Dict[str, List[Manuscript]] = {}
        for m in items:
            if m.qid:
                by_qid[m.qid] = m
            if m.key_iiif:
                by_iiif.setdefault(m.key_iiif, []).append(m)
        # remplacement d'un bloc : une recherche en cours ne voit jamais un index à moitié rempli
        self.items, self.by_qid, self.by_iiif = items, by_qid, by_iiif
        self.generated = str(data.get("generated", ""))

    @classmethod
    def load_default(cls) -> "Catalog":
        return cls(_load_newest(CATALOG_FILE))

    def save(self, data: dict) -> str:
        path = os.path.join(user_cache_dir(), CATALOG_FILE)
        _write_json_gz(path, data)
        self.load(data)
        return path

    def __len__(self):
        return len(self.items)

    # -- recherche ---------------------------------------------------------

    @staticmethod
    def _word_hit(group: Tuple[str, ...], words: List[str]) -> bool:
        for g in group:
            for w in words:
                if w == g or (len(g) >= 3 and w.startswith(g)):
                    return True
        return False

    def search(self, raw: str, limit: int = 300) -> Tuple[List[Tuple[Manuscript, float]], ParsedQuery, str]:
        """Renvoie [(manuscrit, score 0..1)], la requête analysée, une note."""
        q = parse_query(raw)
        if q.qid:
            m = self.by_qid.get(q.qid)
            return ([(m, 1.0)] if m else []), q, _("identifiant Biblissima")
        if q.iiif:
            return [(m, 1.0) for m in self.by_iiif.get(q.iiif, [])], q, _("manifeste IIIF")
        if not q.numbers and not q.words:
            return [], q, ""
        hits = []
        for m in self.items:
            if q.numbers and not all(n in m.keys for n in q.numbers):
                continue
            if q.words:
                matched = sum(1 for g in q.words if self._word_hit(g, m.words))
                score = matched / len(q.words)
            else:
                score = 1.0
            if not q.numbers and score < 1.0:
                continue
            hits.append((m, score))
        note = _("cote exacte") if q.numbers else ""
        if not hits and q.numbers:
            # second passage, plus souple : préfixes numériques (816 -> 816bis…)
            for m in self.items:
                if all(any(k.startswith(n) for k in m.keys) for n in q.numbers):
                    matched = sum(1 for g in q.words if self._word_hit(g, m.words)) if q.words else 0
                    score = (matched / len(q.words)) if q.words else 1.0
                    hits.append((m, score * 0.8))
            note = _("correspondance approchée (aucune cote exacte)")
        if q.words and hits:
            best = max(sc for _m, sc in hits)
            # on garde les meilleurs, mais pas seulement le 1er rang si le score est faible
            floor = best if best >= 0.99 else max(0.0, best - 0.34)
            hits = [h for h in hits if h[1] >= floor - 1e-9]
        hits.sort(key=lambda h: (-h[1], _natural_key(h[0].title)))
        if len(hits) > limit:
            note = (note + " ; " if note else "") + _("%d premiers sur %d — précisez la cote") % (limit, len(hits))
        return hits[:limit], q, note


def _natural_key(text: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", norm_text(text))]


# --------------------------------------------------------------------------
# Archives ALTO (Hugging Face)
# --------------------------------------------------------------------------

VARIANT_SUFFIXES = ("integral", "decor", "partiel", "reliure", "extrait", "selection")


@dataclass
class AltoArchive:
    path: str
    size: Optional[int] = None
    iiif: Optional[str] = None       # 1re ligne de manifest.txt
    first_xml: Optional[str] = None  # 2e ligne de manifest.txt
    scanned: str = ""                # date de lecture de manifest.txt
    error: Optional[str] = None
    tokens: List[str] = field(default_factory=list, repr=False)
    keys: set = field(default_factory=set, repr=False)

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def stem(self) -> str:
        n = self.name
        return n[:-7] if n.endswith(".tar.gz") else n

    @property
    def variant(self) -> str:
        last = self.stem.rsplit("-", 1)[-1]
        return last if last in VARIANT_SUFFIXES else ""

    @property
    def key_iiif(self) -> Optional[str]:
        return iiif_key(self.iiif)

    @property
    def download_url(self) -> str:
        return HF_RESOLVE + urllib.parse.quote(self.path)

    @property
    def browse_url(self) -> str:
        return HF_BROWSE + urllib.parse.quote(self.path)

    def prepare(self) -> None:
        self.tokens = [t for t in self.stem.lower().split("-") if t]
        joined = " ".join(self.tokens)
        self.keys = shelf_keys(joined)

    def to_json(self) -> dict:
        d = {"path": self.path, "size": self.size}
        for k in ("iiif", "first_xml", "scanned", "error"):
            v = getattr(self, k)
            if v:
                d[k] = v
        return d

    @classmethod
    def from_json(cls, d: dict) -> "AltoArchive":
        path = d.get("path") if isinstance(d, dict) else None
        if not is_safe_archive_path(path):
            raise ValueError("chemin d'archive refusé")
        size = d.get("size")
        a = cls(path=path, size=size if isinstance(size, int) and size >= 0 else None,
                iiif=d.get("iiif") if isinstance(d.get("iiif"), str) else None,
                first_xml=d.get("first_xml") if isinstance(d.get("first_xml"), str) else None,
                scanned=_text(d.get("scanned")), error=d.get("error") if isinstance(d.get("error"), str) else None)
        a.prepare()
        return a


def _next_link(headers: dict) -> Optional[str]:
    """Page suivante d'une liste Hugging Face, seulement si elle reste sur la même API."""
    link = _text(headers.get("Link") or headers.get("link"))[:8192]
    for part in link.split(","):
        if 'rel="next"' in part:
            m = re.search(r"<([^<>]{1,2048})>", part)
            if m and m.group(1).startswith(HF_TREE + "/"):
                return m.group(1)
    return None


def _listing_entry(it) -> Optional[dict]:
    if not isinstance(it, dict) or it.get("type") != "file" or not is_safe_archive_path(it.get("path")):
        return None
    size = it.get("size")
    return {"path": it["path"], "size": size if isinstance(size, int) and size >= 0 else None}


def list_hf_archives(progress: ProgressFn = None, stop: Optional[threading.Event] = None) -> List[dict]:
    """Liste toutes les archives .tar.gz (≈ 30 requêtes, quelques secondes).

    Les entrées dont le chemin n'est pas sûr sont ignorées ; la pagination ne suit
    que des liens vers la même API, sans boucle, et avec un nombre de pages borné.
    """
    top = http_json(HF_TREE, stop=stop)
    out = []
    for d in top if isinstance(top, list) else []:
        entry = _listing_entry(d)
        if entry:
            out.append(entry)
        if not isinstance(d, dict) or d.get("type") != "directory":
            continue
        folder = d.get("path")
        if not isinstance(folder, str) or not folder or "/" in folder or folder in (".", "..") or \
                any(ord(c) < 32 or c in _BAD_PATH_CHARS for c in folder):
            continue
        url = "%s/%s?recursive=true&expand=false" % (HF_TREE, urllib.parse.quote(folder))
        seen = set()
        while url and url not in seen and len(seen) < MAX_LISTING_PAGES:
            seen.add(url)
            body, _status, headers = http_get(url, want_headers=True, stop=stop)
            try:
                items = json.loads(body.decode("utf-8"))
            except ValueError:
                raise HttpError(_("Réponse illisible (JSON attendu) pour %s") % url)
            for it in items if isinstance(items, list) else []:
                entry = _listing_entry(it)
                if entry:
                    out.append(entry)
            url = _next_link(headers)
        if progress:
            progress(_("Hugging Face : %s… (%d archives)") % (folder, len(out)))
    return out


def _tar_head(data: bytes, path: str) -> Tuple[bytes, str, int]:
    """Décompresse un début de .tar.gz ; renvoie (octets, nom et taille du 1er membre)."""
    try:
        raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, 16 << 20)  # au plus 16 Mo
    except zlib.error:
        raise HttpError(_("Début d'archive illisible : %s") % path)
    if len(raw) < 512:
        raise HttpError(_("Début d'archive illisible : %s") % path)
    member = raw[:100].split(b"\0")[0].decode("utf-8", "replace")
    try:
        size = int(raw[124:136].split(b"\0")[0].strip() or b"0", 8)
    except ValueError:
        size = 0
    return raw, member, size


def read_archive_manifest_head(path: str, nbytes: int = 4096,
                               stop: Optional[threading.Event] = None) -> Tuple[Optional[str], Optional[str], str]:
    """Lit le début de manifest.txt par une requête HTTP partielle.

    Renvoie (ligne IIIF, premier fichier ALTO, nom du 1er membre de l'archive).
    """
    url = HF_RESOLVE + urllib.parse.quote(path)
    data = http_get(url, headers={"Range": "bytes=0-%d" % (nbytes - 1)}, retries=5, max_bytes=nbytes, stop=stop)
    raw, member, size = _tar_head(data, path)
    if not member.endswith("manifest.txt"):
        return None, None, member
    body = raw[512:512 + size].decode("utf-8", "replace").split("\n")
    first = body[0].strip() if body else None
    second = body[1].strip() if len(body) > 1 and body[1].strip() else None
    return first or None, second, member


def read_archive_manifest_full(path: str, start: int = 65536, limit: int = 1 << 22,
                               stop: Optional[threading.Event] = None) -> List[str]:
    """Lit tout manifest.txt (requêtes partielles de taille croissante)."""
    url = HF_RESOLVE + urllib.parse.quote(path)
    nbytes = start
    while True:
        data = http_get(url, headers={"Range": "bytes=0-%d" % (nbytes - 1)}, retries=4, max_bytes=nbytes,
                        stop=stop)
        raw, member, size = _tar_head(data, path)
        if not member.endswith("manifest.txt"):
            return []
        if len(raw) >= 512 + size or len(data) < nbytes or nbytes >= limit:
            body = raw[512:512 + size].decode("utf-8", "replace")
            return [line.strip() for line in body.split("\n") if line.strip()]
        nbytes *= 4


def page_id_of_alto(line: str) -> str:
    """« dossier/f12-f-008v.xml » -> « f12-f-008v »."""
    base = line.rsplit("/", 1)[-1]
    return base[:-4] if base.lower().endswith(".xml") else base


class AltoIndex:
    def __init__(self, data: Optional[dict] = None):
        self.generated = ""
        self.listed = ""
        self.archives: Dict[str, AltoArchive] = {}
        self.by_iiif: Dict[str, List[AltoArchive]] = {}
        self.lock = threading.Lock()
        if data:
            self.load(data)

    def load(self, data: dict) -> None:
        self.generated = data.get("generated", "")
        self.listed = data.get("listed", self.generated)
        archives = {}
        for d in data.get("archives", []) or []:
            try:
                a = AltoArchive.from_json(d)
            except Exception:
                continue
            archives[a.path] = a
        self.archives = archives
        self._rebuild()

    def all(self) -> List[AltoArchive]:
        """Instantané de la liste : sûr même si un autre fil remplace le dictionnaire."""
        return list(self.archives.values())

    def _rebuild(self) -> None:
        by = {}
        names: Dict[str, int] = {}
        for a in self.all():
            k = a.key_iiif
            if k:
                by.setdefault(k, []).append(a)
            names[a.name] = names.get(a.name, 0) + 1
        self.by_iiif = by
        self.name_counts = names

    def local_name(self, a: "AltoArchive") -> str:
        """Nom du dossier d'extraction : le nom de l'archive, suivi du lot quand
        plusieurs archives portent le même nom (3 925 archives au 15/11/2026)."""
        if getattr(self, "name_counts", {}).get(a.name, 1) > 1:
            return safe_local_name("%s__%s" % (a.stem, a.path.split("/")[0]))
        return safe_local_name(a.stem)

    @classmethod
    def load_default(cls) -> "AltoIndex":
        return cls(_load_newest(ALTO_INDEX_FILE))

    def to_json(self) -> dict:
        return {"generated": self.generated or time.strftime("%Y-%m-%dT%H:%M:%S"),
                "listed": self.listed, "repo": HF_REPO,
                "count": len(self.archives),
                "archives": [a.to_json() for a in sorted(self.all(), key=lambda x: x.path)]}

    def save(self) -> str:
        path = os.path.join(user_cache_dir(), ALTO_INDEX_FILE)
        with self.lock:
            data = self.to_json()
        _write_json_gz(path, data)
        return path

    def __len__(self):
        return len(self.archives)

    @property
    def scanned_count(self) -> int:
        return sum(1 for a in self.all() if a.scanned and not a.error)

    def unscanned(self) -> List[AltoArchive]:
        return [a for a in self.all() if not a.scanned or a.error]

    def merge_listing(self, listing: List[dict]) -> Tuple[int, int]:
        """Intègre une nouvelle liste Hugging Face. Renvoie (ajouts, disparitions)."""
        added = 0
        with self.lock:
            # copie modifiée puis remplacement d'un bloc : les autres fils qui
            # parcourent l'ancien dictionnaire ne sont pas perturbés
            archives = dict(self.archives)
            listing = [d for d in listing if isinstance(d, dict) and is_safe_archive_path(d.get("path"))]
            new_paths = {d["path"] for d in listing}
            for d in listing:
                a = archives.get(d["path"])
                if a is None:
                    a = AltoArchive(path=d["path"], size=d.get("size"))
                    a.prepare()
                    archives[a.path] = a
                    added += 1
                elif d.get("size") and a.size != d.get("size"):
                    a.size = d.get("size")  # archive remplacée : à relire
                    a.iiif = a.first_xml = None
                    a.scanned = ""
            removed = [p for p in archives if p not in new_paths]
            for p in removed:
                del archives[p]
            self.archives = archives
            self.listed = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._rebuild()
        return added, len(removed)

    def scan(self, archive: AltoArchive, rebuild: bool = True,
             stop: Optional[threading.Event] = None) -> AltoArchive:
        try:
            first, second, member = read_archive_manifest_head(archive.path, stop=stop)
            with self.lock:
                old = archive.key_iiif
                archive.iiif = first
                archive.first_xml = second
                archive.error = None if first else _("pas de manifest.txt en tête (%s)") % member
                archive.scanned = time.strftime("%Y-%m-%d")
                if rebuild and old != archive.key_iiif:
                    self._rebuild()
        except Interrupted:
            pass  # arrêt demandé : l'archive reste « non lue », sans erreur enregistrée
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                archive.error = str(exc)
        return archive

    def scan_many(self, archives: Iterable[AltoArchive], progress: ProgressFn = None,
                  workers: int = 6, stop: Optional[threading.Event] = None) -> int:
        archives = list(archives)
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(self._scan_unless_stopped, a, stop) for a in archives]
            for _fut in as_completed(futs):
                done += 1
                if progress and (done % 25 == 0 or done == len(archives)):
                    progress(_("Lecture des manifest.txt : %d / %d") % (done, len(archives)))
        with self.lock:
            self._rebuild()
        return done

    def _scan_unless_stopped(self, a, stop):
        if stop is not None and stop.is_set():
            return a
        return self.scan(a, rebuild=False, stop=stop)

    def find_by_name(self, name: str) -> List[AltoArchive]:
        name = name.strip().lower()
        return [a for a in self.all()
                if a.name.lower() == name or a.stem.lower() == name or a.path.lower() == name]

    # -- correspondance par le nom (secours) -------------------------------

    def name_candidates(self, ms: Manuscript, limit: int = 12) -> List[Tuple[AltoArchive, float]]:
        """Archives dont le NOM évoque la cote du manuscrit (sans preuve)."""
        if not ms.keys:
            return []
        shelf = shelf_keys(ms.shelfmark) or ms.keys
        city_words = [w for w in norm_text(ms.city).split() if w not in STOPWORDS] if ms.city else []
        context_words = [w for w in norm_text(ms.title).split()
                         if not _has_digit(w) and w not in STOPWORDS and len(w) >= 3]
        out = []
        for a in self.all():
            if not shelf <= a.keys:
                continue
            toks = set(a.tokens)
            city_ok = all(w in toks for w in city_words) if city_words else False
            bnf_ok = (a.stem.startswith("bn-f-") and "nationale" in ms.words)
            if not (city_ok or bnf_ok):
                continue
            ctx = sum(1 for w in context_words if w in toks) / max(1, len(context_words))
            extra = len(a.keys - shelf)  # nombres en trop dans le nom : pénalité
            score = 0.6 + 0.3 * ctx - 0.1 * min(extra, 3)
            if a.variant == "integral":
                score += 0.05
            out.append((a, max(0.05, min(score, 0.95))))
        out.sort(key=lambda x: -x[1])
        return out[:limit]


# --------------------------------------------------------------------------
# Biblissima
# --------------------------------------------------------------------------

def biblissima_by_iiif(manifest_url: str, stop: Optional[threading.Event] = None) -> List[str]:
    """Recherche inverse : manifeste IIIF -> Q... (propriété P196)."""
    if not is_web_url(manifest_url):
        return []
    urls = [manifest_url]
    m = re.search(r"ark:/12148/([A-Za-z0-9]+)", manifest_url or "")
    if m:  # Biblissima enregistre l'URL Gallica, l'archive l'URL openapi v3
        urls.append("https://gallica.bnf.fr/iiif/ark:/12148/%s/manifest.json" % m.group(1))
    found: List[str] = []
    for u in dict.fromkeys(urls):
        params = urllib.parse.urlencode({
            "action": "query", "list": "search", "srnamespace": "120",
            "srsearch": "haswbstatement:P196=%s" % u, "format": "json", "srlimit": "10"})
        d = http_json(BIBLISSIMA_API + "?" + params, retries=2, stop=stop)
        query = d.get("query") if isinstance(d, dict) else None
        hits = query.get("search") if isinstance(query, dict) else None
        for s in hits if isinstance(hits, list) else []:
            q = qid_of(_text(s.get("title")) if isinstance(s, dict) else "")
            if q and q not in found:
                found.append(q)
        if found:
            break
    return found


def biblissima_entity(qid: str) -> dict:
    """Libellé, cote (P195) et manifestes IIIF (P196) d'un item Biblissima."""
    params = urllib.parse.urlencode({"action": "wbgetentities", "ids": qid,
                                     "props": "labels|claims", "languages": "fr|en",
                                     "format": "json"})
    d = http_json(BIBLISSIMA_API + "?" + params, retries=2)
    e = d.get("entities", {}).get(qid, {})
    if "missing" in e:
        return {}

    def vals(pid):
        out = []
        for c in e.get("claims", {}).get(pid, []):
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(v, dict):
                v = v.get("id") or v.get("text")
            if v:
                out.append(v)
        return out

    labels = e.get("labels", {})
    label = (labels.get("fr") or labels.get("en") or {}).get("value", "")
    return {"qid": qid, "label": label, "shelfmark": vals("P195"),
            "iiif": vals("P196"), "record": vals("P197")}


# --------------------------------------------------------------------------
# Correspondance CoMMA <-> ALTO
# --------------------------------------------------------------------------

# codes internes (stables) ; libellés et explications traduits par status_label / status_help
CONFIRMED = "confirmed"
CONFIRMED_LIVE = "confirmed_live"
OTHER_DIGITIZATION = "other_digitization"
PROBABLE = "probable"
NOT_FOUND = "not_found"


def status_label(code: str) -> str:
    return {
        CONFIRMED: _("CONFIRMÉE"),
        CONFIRMED_LIVE: _("CONFIRMÉE (vérifiée à l'instant)"),
        OTHER_DIGITIZATION: _("AUTRE NUMÉRISATION"),
        PROBABLE: _("PROBABLE (non vérifiée)"),
        NOT_FOUND: _("AUCUNE TROUVÉE"),
    }.get(code, code)


def status_help(code: str) -> str:
    return {
        CONFIRMED: _("La 1re ligne du manifest.txt de l'archive est le manifeste IIIF de CoMMA "
                     "(lecture enregistrée dans l'index)."),
        CONFIRMED_LIVE: _("La 1re ligne du manifest.txt, relue à l'instant sur Hugging Face, "
                          "est le manifeste IIIF de CoMMA."),
        OTHER_DIGITIZATION: _("Le nom de l'archive correspond à la cote, mais son manifest.txt cite "
                              "un autre manifeste IIIF : ce n'est PAS la transcription affichée par CoMMA."),
        PROBABLE: _("Le nom de l'archive correspond à la cote, mais son manifest.txt n'a pas pu être lu."),
        NOT_FOUND: _("Aucune archive trouvée. Ce n'est jamais une preuve d'absence."),
    }.get(code, "")


@dataclass
class AltoMatch:
    archive: AltoArchive
    status: str
    certainty: int          # 0-100
    detail: str = ""
    other_comma: Optional[Manuscript] = None  # manuscrit CoMMA de l'autre numérisation
    alto_count: Optional[int] = None


@dataclass
class Resolution:
    manuscript: Manuscript
    matches: List[AltoMatch]
    first_page: Optional[str] = None
    pages: List[str] = field(default_factory=list)
    comma_checked: bool = False
    comma_note: str = ""
    biblissima: Optional[dict] = None
    notes: List[str] = field(default_factory=list)

    @property
    def confirmed(self) -> List[AltoMatch]:
        return [m for m in self.matches if m.status in (CONFIRMED, CONFIRMED_LIVE)]


def resolve_alto(ms: Manuscript, catalog: Catalog, index: AltoIndex,
                 live: bool = True, progress: ProgressFn = None,
                 stop: Optional[threading.Event] = None) -> Resolution:
    """Cherche les archives ALTO d'un manuscrit et qualifie chaque lien."""
    res = Resolution(manuscript=ms, matches=[])
    key = ms.key_iiif
    seen = set()

    # 1. preuve : manifeste IIIF identique dans manifest.txt
    for a in (index.by_iiif.get(key, []) if key else []):
        status, cert, detail = CONFIRMED, 98, status_help(CONFIRMED)
        if live:
            if progress:
                progress(_("Vérification en direct de %s…") % a.name)
            index.scan(a, stop=stop)
            if a.key_iiif == key:
                status, cert, detail = CONFIRMED_LIVE, 100, status_help(CONFIRMED_LIVE)
            elif a.error:
                detail += _(" (relecture impossible : %s)") % a.error
            else:
                status, cert = OTHER_DIGITIZATION, 20
                detail = _("L'archive a changé : son manifest.txt cite désormais %s") % a.iiif
        if status != OTHER_DIGITIZATION and a.first_xml:
            detail += _("\nPremier fichier ALTO : %s") % a.first_xml
        res.matches.append(AltoMatch(a, status, cert, detail))
        seen.add(a.path)

    # 2. secours : noms d'archives évoquant la cote
    cands = [(a, s) for a, s in index.name_candidates(ms) if a.path not in seen]
    to_scan = [a for a, _sc in cands if not a.scanned or a.error]
    if to_scan and live:
        if progress:
            progress(_("Lecture de %d manifest.txt candidats…") % len(to_scan))
        index.scan_many(to_scan, workers=4, stop=stop)
    for a, score in cands:
        if a.key_iiif and key and a.key_iiif == key:
            st = CONFIRMED_LIVE if a in to_scan else CONFIRMED
            detail = status_help(st)
            if a.first_xml:
                detail += _("\nPremier fichier ALTO : %s") % a.first_xml
            res.matches.append(AltoMatch(a, st, 100 if st == CONFIRMED_LIVE else 98, detail))
            continue
        if a.key_iiif:
            others = catalog.by_iiif.get(a.key_iiif, [])
            other = others[0] if others else None
            detail = status_help(OTHER_DIGITIZATION) + _("\nManifeste de l'archive : %s") % a.iiif
            if other is not None:
                detail += _("\nCe manifeste correspond dans CoMMA à : %s (%s)") % (other.title, other.qid)
            res.matches.append(AltoMatch(a, OTHER_DIGITIZATION, int(score * 40), detail, other))
        else:
            res.matches.append(AltoMatch(a, PROBABLE, int(score * 70),
                                         status_help(PROBABLE) + (" (%s)" % a.error if a.error else "")))

    order = {CONFIRMED_LIVE: 0, CONFIRMED: 1, PROBABLE: 2, OTHER_DIGITIZATION: 3}
    res.matches.sort(key=lambda m: (order.get(m.status, 9), -m.certainty, m.archive.path))

    if not res.confirmed:
        n_un = len(index.unscanned())
        res.notes.append(
            _("Aucune archive dont le manifest.txt cite %s n'a été trouvée parmi les %d archives "
              "indexées (%d lues%s). Ce n'est PAS une preuve d'absence : l'archive peut avoir "
              "un nom inattendu, être postérieure à l'index, ou ne pas être publiée.")
            % (ms.iiif or _("le manifeste IIIF"), len(index), index.scanned_count,
               _(", %d non lues") % n_un if n_un else ""))
    return res


def check_comma_page(res: Resolution, compare_pages: bool = True,
                     stop: Optional[threading.Event] = None) -> None:
    """Vérifie la ressource auprès du serveur DTS et récupère la 1re page."""
    try:
        res.pages = comma_pages(res.manuscript.uri, stop=stop)
        res.first_page = res.pages[0] if res.pages else None
        res.comma_checked = True
        res.comma_note = (_("Ressource et première page confirmées par l'API DTS de CoMMA (%d pages) ; "
                            "l'adresse suit le schéma de l'interface TexTile (/doc/<id>/p/<page>).")
                          % len(res.pages))
    except Exception as exc:  # noqa: BLE001
        res.comma_note = _("Lien construit, non vérifié (API DTS injoignable : %s)") % exc
        return
    if compare_pages:
        for m in res.confirmed:
            compare_page_lists(m, res.pages, stop=stop)


def compare_page_lists(match: "AltoMatch", pages: List[str], stop: Optional[threading.Event] = None) -> None:
    """Second contrôle : pages CoMMA et fichiers listés par manifest.txt (nombre et ordre)."""
    try:
        lines = read_archive_manifest_full(match.archive.path, stop=stop)
    except Exception as exc:  # noqa: BLE001
        match.detail += _("\nComparaison des pages impossible : %s") % exc
        return
    alto = [page_id_of_alto(x) for x in lines[1:]]
    match.alto_count = len(alto)
    if not pages:
        return
    if alto == pages:
        match.detail += (_("\n✔ Les %d pages CoMMA et les %d fichiers ALTO de manifest.txt "
                           "portent les mêmes noms, dans le même ordre.") % (len(pages), len(alto)))
    else:
        common = len(set(alto) & set(pages))
        match.detail += (_("\n⚠ Pages CoMMA : %d ; fichiers ALTO : %d ; noms communs : %d. "
                           "Les deux listes ne sont pas identiques.") % (len(pages), len(alto), common))
        match.certainty = min(match.certainty, 90)


def archive_to_manuscripts(a: AltoArchive, catalog: Catalog, index: AltoIndex,
                           use_biblissima: bool = True,
                           stop: Optional[threading.Event] = None) -> Tuple[List[Manuscript], List[str]]:
    """Chaîne inverse : archive -> manifest.txt -> IIIF -> CoMMA (et Biblissima)."""
    notes = []
    if not a.iiif:
        index.scan(a, stop=stop)
    if not a.iiif:
        return [], [_("manifest.txt illisible : %s") % (a.error or "?")]
    notes.append("manifest.txt : %s" % a.iiif)
    found = list(catalog.by_iiif.get(a.key_iiif, []))
    if found:
        notes.append(_("Manifeste trouvé dans le catalogue CoMMA."))
    if use_biblissima:
        try:
            qids = biblissima_by_iiif(a.iiif, stop=stop)
            if qids:
                notes.append(_("Biblissima (P196) : %s") % ", ".join(qids))
                for q in qids:
                    m = catalog.by_qid.get(q)
                    if m is None:
                        m = comma_resource(BIBLISSIMA_ENTITY + q, stop=stop)
                    if m is not None and m not in found:
                        found.append(m)
                        if m.key_iiif != a.key_iiif:
                            notes.append(_("Attention : %s est dans CoMMA avec un AUTRE manifeste (%s).")
                                         % (q, m.iiif))
            else:
                notes.append(_("Biblissima : aucun item ne déclare ce manifeste (P196)."))
        except Exception as exc:  # noqa: BLE001
            notes.append(_("Biblissima injoignable : %s") % exc)
    return found, notes


# --------------------------------------------------------------------------
# Téléchargement et extraction
# --------------------------------------------------------------------------

class ArchiveError(Exception):
    """Archive refusée (contenu dangereux ou démesuré)."""


def _inside(target: str, root_real: str) -> bool:
    target = os.path.realpath(target)
    return target == root_real or target.startswith(root_real + os.sep)


def _safe_members(tar: tarfile.TarFile, dest: str, compressed_size: int = 0):
    """Membres extractibles sans risque : fichiers et dossiers ordinaires seulement
    (ni liens, ni périphériques, ni fichiers « creux »), noms relatifs restant dans
    dest, tailles bornées (fichier, total, nombre de membres)."""
    dest_real = os.path.realpath(dest)
    budget = MAX_EXPANSION * max(compressed_size, 0) + (64 << 20)
    total = count = 0
    for m in tar:
        count += 1
        if count > MAX_MEMBERS:
            raise ArchiveError(_("Archive refusée : %s") % _("trop de fichiers"))
        if not (m.isfile() or m.isdir()) or m.issparse():
            continue
        name = m.name
        if not name or name.startswith(("/", "\\")) or "\\" in name or ":" in name:
            continue
        base = name.rstrip("/").rsplit("/", 1)[-1]
        if base.split(".")[0].upper() in _WIN_RESERVED or any(ord(c) < 32 for c in name):
            continue
        if not _inside(os.path.join(dest, name), dest_real):
            continue
        if m.isfile():
            if m.size > MAX_MEMBER_BYTES:
                raise ArchiveError(_("Archive refusée : %s") % _("fichier démesuré"))
            total += m.size
            if total > budget:
                raise ArchiveError(_("Archive refusée : %s") % _("taille extraite démesurée"))
            m.mode = 0o644
        else:
            m.mode = 0o755
        yield m


def _safe_rel(rel: str) -> Optional[List[str]]:
    """Ligne de manifest.txt utilisable comme chemin relatif, ou None (jamais de
    chemin absolu, UNC, lettre de lecteur ou « .. » : ils ne sont même pas testés)."""
    if not rel or len(rel) > 512 or "\\" in rel or ":" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") or any(ord(c) < 32 for c in p) for p in parts):
        return None
    return parts


def _replace_with_retry(src: str, dst: str) -> None:
    """os.replace, avec quelques nouvelles tentatives si un antivirus tient le fichier (Windows)."""
    for attempt in range(6):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.5 * (attempt + 1))


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _download_once(a: "AltoArchive", part: str, progress: ProgressFn, stop: Optional[threading.Event]) -> None:
    req = urllib.request.Request(a.download_url, headers={"User-Agent": USER_AGENT})
    try:
        resp_cm = _OPENER.open(req, timeout=120)
    except urllib.error.HTTPError as exc:
        raise HttpError(_("HTTP %s pour %s") % (exc.code, a.download_url), exc.code)
    except urllib.error.URLError as exc:
        raise HttpError(_("Erreur réseau pour %s : %s") % (a.download_url, exc))
    with resp_cm as resp, open(part, "wb") as out:
        try:
            announced = int(resp.headers.get("Content-Length") or 0)
        except ValueError:
            announced = 0
        sizes = [n for n in (announced, a.size or 0) if n > 0]
        total = announced or (a.size or 0)
        ceiling = (max(sizes) + (1 << 20)) if sizes else MAX_DOWNLOAD_BYTES
        got = 0
        last = 0.0
        while True:
            if stop is not None and stop.is_set():
                raise Interrupted(_("Téléchargement interrompu"))
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if got > ceiling:  # plus gros qu'annoncé : on n'écrit pas indéfiniment
                raise HttpError(_("Téléchargement plus gros qu'annoncé, arrêté : %s") % a.name, 413)
            if progress and time.time() - last > 0.3:
                last = time.time()
                pct = " (%d %%)" % (100 * got // total) if total else ""
                progress(_("Téléchargement de %s : %.1f Mo%s") % (a.name, got / 1e6, pct))
    if total and got != total:
        raise HttpError(_("Téléchargement incomplet (%d/%d octets)") % (got, total))


def download_and_extract(a: AltoArchive, dest_root: str, progress: ProgressFn = None,
                         provenance: Optional[dict] = None,
                         stop: Optional[threading.Event] = None,
                         local_name: Optional[str] = None) -> dict:
    """Télécharge l'archive, l'extrait dans dest_root/<nom local>/, contrôle manifest.txt.

    local_name : nom du dossier (AltoIndex.local_name : le nom de l'archive, suivi du
    lot quand plusieurs archives portent le même nom). Le dossier et l'archive restent
    toujours directement dans dest_root, quel que soit le nom venu du réseau.
    """
    if not is_safe_archive_path(a.path):
        raise ArchiveError(_("Archive refusée : %s") % _("chemin dangereux"))
    name = safe_local_name(local_name or a.stem)
    os.makedirs(dest_root, exist_ok=True)
    root_real = os.path.realpath(dest_root)
    folder = os.path.join(dest_root, name)
    tgz = os.path.join(dest_root, name + ".tar.gz")
    for target in (folder, tgz):
        if os.path.dirname(os.path.realpath(target)) != root_real:
            raise ArchiveError(_("Archive refusée : %s") % _("chemin dangereux"))
    previous = os.path.join(folder, "provenance_comma.json")
    if os.path.exists(previous):  # le dossier contient-il déjà une AUTRE archive ?
        try:
            with open(previous, encoding="utf-8") as fh:
                other = json.load(fh).get("archive_hugging_face")
        except Exception:
            other = None
        if other and other != a.path:
            raise ArchiveError(_("Le dossier %s contient déjà une autre archive (%s)") % (folder, other))
    os.makedirs(folder, exist_ok=True)
    # deux téléchargements simultanés (fils ou instances) ne se mélangent pas
    part = "%s.%d.%d.part" % (tgz, os.getpid(), threading.get_ident())

    last_exc = None
    for attempt in range(3):
        try:
            _download_once(a, part, progress, stop)
            last_exc = None
            break
        except Interrupted:           # interruption demandée : pas de reprise
            _remove_quietly(part)
            raise
        except (HttpError, OSError, http.client.HTTPException) as exc:
            last_exc = exc
            _remove_quietly(part)
            if isinstance(exc, HttpError) and exc.code and exc.code < 500 and exc.code != 429:
                break
            time.sleep(2 * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    _replace_with_retry(part, tgz)

    if progress:
        progress(_("Extraction de %s…") % a.name)
    with tarfile.open(tgz, "r:gz") as tar:
        members = list(_safe_members(tar, folder, os.path.getsize(tgz)))
        try:
            try:
                tar.extractall(folder, members=members, filter="data")  # Python ≥ 3.12 (et correctifs)
            except TypeError:
                tar.extractall(folder, members=members)
        except OSError as exc:
            longest = max((len(os.path.join(os.path.abspath(folder), m.name)) for m in members), default=0)
            if sys.platform.startswith("win") and longest >= 250:
                raise OSError(_("Chemins trop longs pour Windows (%d caractères) : choisissez un dossier "
                                "de destination plus court (par exemple C:\\ALTO). Détail : %s") % (longest, exc))
            raise

    # contrôle : fichiers annoncés par manifest.txt (lignes non sûres jamais testées)
    report = {"archive": a.path, "dossier": folder, "manifeste_iiif": None,
              "annonces": 0, "presents": 0, "manquants": []}
    mpath = os.path.join(folder, "manifest.txt")
    if os.path.isfile(mpath):
        with open(mpath, encoding="utf-8", errors="replace") as fh:
            lines = [line.strip() for _i, line in zip(range(MAX_MEMBERS + 1), fh) if line.strip()]
        if lines:
            report["manifeste_iiif"] = lines[0][:2048]
            listed = lines[1:]
            report["annonces"] = len(listed)
            folder_real = os.path.realpath(folder)
            for rel in listed:
                parts = _safe_rel(rel)
                found = False
                if parts:
                    for cand in (os.path.join(folder, *parts), os.path.join(folder, *parts[1:]) if len(parts) > 1 else None):
                        if cand and _inside(cand, folder_real) and os.path.isfile(cand):
                            found = True
                            break
                if found:
                    report["presents"] += 1
                elif len(report["manquants"]) < 1000:
                    report["manquants"].append(rel[:512])
    info = dict(provenance or {})
    # chemin relatif seulement : le chemin absolu contient le nom d'utilisateur
    info.update({"archive_hugging_face": a.path, "url": a.download_url,
                 "telecharge_le": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "controle": dict(report, dossier=name)})
    with open(os.path.join(folder, "provenance_comma.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)
    return report


def child_environment() -> dict:
    """Environnement des programmes lancés (navigateur, gestionnaire de fichiers).

    Dans l'exécutable Linux construit par PyInstaller, LD_LIBRARY_PATH pointe vers les
    bibliothèques embarquées, ce qui peut faire planter les programmes lancés."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def open_path(path: str) -> Optional[str]:
    """Ouvre un fichier ou un dossier avec l'application du système.

    Renvoie None si tout va bien, sinon le message d'erreur (jamais d'exception).
    """
    import subprocess
    try:
        if not isinstance(path, str) or not os.path.exists(path):
            return _("Introuvable : %s") % path
        path = os.path.abspath(path)  # jamais interprété comme une option ou une URL
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "--", path])
        else:
            subprocess.Popen(["xdg-open", path], env=child_environment())
        return None
    except Exception as exc:  # noqa: BLE001 — xdg-open absent, dossier supprimé…
        return str(exc)
