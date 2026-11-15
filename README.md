# CoMMA Search — manuscripts, transcriptions and ALTO

A standalone application to find a manuscript of the **CoMMA** corpus (Inria) from its
shelfmark, open its transcription on comma.inria.fr, and automatically retrieve its
**ALTO** files from Hugging Face, with an explicit certainty level.

(French documentation: `LISEZMOI.md`.)

## Executables (no Python needed)

Ready-to-use executables are attached to each release (**Releases** tab of the repository):
`CoMMA-Search-windows.zip`, `CoMMA-Search-macos.zip` (Apple Silicon Macs),
`CoMMA-Search-linux.tar.gz`. Unpack and run `CoMMA-Search`.

They are not signed:

* **Windows**: if SmartScreen says "Windows protected your PC", click *More info*, then *Run anyway*.
* **macOS**: the first time, right-click (or Ctrl-click) `CoMMA-Search.app`, then *Open*.

They are built by the **Binaries** workflow (`.github/workflows/binaries.yml`) with PyInstaller
(`packaging/comma_search.spec`); to build one yourself: `pip install pyinstaller certifi`, then
`pyinstaller --noconfirm --clean packaging/comma_search.spec`.

## Running it

* **Spyder**: open `COMMA_RECHERCHE_SPYDER.py`, press **F5**.
* **Terminal**: `python COMMA_RECHERCHE_SPYDER.py`
* Prefilled search: `python COMMA_RECHERCHE_SPYDER.py --recherche "Troyes 816"`
* No GUI: `python COMMA_RECHERCHE_SPYDER.py --cli "Troyes, BM, 816"`
  (`--telecharger` downloads and extracts the confirmed archive)
* Language: **Langue / Language** menu (French or English, switches immediately, choice
  remembered); on first launch, the system language; on the command line `--lang en`.

Windows, macOS and Linux. **Nothing to install**: Python ≥ 3.8 with Tkinter (bundled with
Python on Windows and macOS, and with Anaconda/Spyder; on Linux: package `python3-tk`).
Keep together:

```
COMMA_RECHERCHE_SPYDER.py   graphical interface (Tkinter)
comma_core.py               logic: search, matching, download
comma_i18n.py               translations (French / English)
donnees/                    bundled indexes (CoMMA catalogue, ALTO archive index)
tests/                      offline tests: python -m unittest discover -s tests
```

## What you can type

* a shelfmark, freely: `Troyes, BM, 816` · `Troyes 0816` · `Paris, BnF, Latin 16400` ·
  `BnF Latin 16400` · `Châteaudun A2` · `Châteaudun, Archives municipales, A 002` ·
  `BnF fr. 2227` · `Montpellier H 242`…;
* a Biblissima identifier (`Q412957`) or its URL;
* a CoMMA transcription URL (`https://comma.inria.fr/doc/...`);
* a IIIF manifest URL, or a bare ARK;
* an ALTO archive name (`france-troyes-fonds-principal-0816-integral.tar.gz`): the
  application then follows the chain archive → manifest.txt → IIIF → CoMMA / Biblissima.

Numbers are matched **exactly** (816 matches neither 1816 nor 8160; leading zeros are
ignored: 0816 = 816, A 002 = A2); words (city, institution, collection) are used for
ranking; usual French abbreviations are understood (BM, BnF, AM, AD, lat., fr., NAL, NAF…).
If the local catalogue has nothing, the application queries the CoMMA server.

## What is shown for each manuscript

1. **Normalized shelfmark** (City, institution, shelfmark) and the CoMMA title.
2. **Biblissima identifier**: the very identifier of the resource in the CoMMA DTS API.
3. **CoMMA transcription**: the resource and its first page are verified through the DTS
   API (`/navigation`); the address follows the TexTile interface route
   (`/doc/<doubly encoded id>/p/<page>`).
4. **IIIF manifest** declared by CoMMA (`dublinCore.source`).
5. **ALTO archives** from the `comma-project/alto-dataset` repository.
6. **One-click download and extraction**, checked against `manifest.txt` (every listed file
   must be present); a `provenance_comma.json` file is written in the extracted folder.
7. **Certainty** of the CoMMA ↔ ALTO match:

| Status | Certainty | Meaning |
|---|---|---|
| CONFIRMED (just verified) | 100 % | the first line of the archive's `manifest.txt`, re-read live, is CoMMA's IIIF manifest |
| CONFIRMED | 98 % | same proof, read when the index was built (live re-read impossible) |
| PROBABLE (unverified) | ≤ 70 % | the archive name matches the shelfmark, but `manifest.txt` could not be read |
| OTHER DIGITIZATION | ≤ 40 % | the name matches, but `manifest.txt` cites **another** IIIF manifest: not the transcription shown by CoMMA |
| NONE FOUND | — | **never proof of absence** |

**Second, independent check**: the full list of CoMMA pages is compared with the list of
ALTO files in `manifest.txt` (count, names and order) — e.g. Troyes 1713: “818 CoMMA pages
and 818 ALTO files, same names, same order”.

Manifests are compared on a canonical key: for an ARK, `ark:/NAAN/name`. This is essential
for the BnF: archives cite `openapi.bnf.fr/iiif/presentation/v3/ark:/12148/…` while CoMMA
and Biblissima cite `gallica.bnf.fr/iiif/ark:/12148/…`.

## How it works

* **CoMMA catalogue**: the CoMMA website search is fuzzy and fails on shelfmarks, so the
  application crawls the public DTS tree once (root → cities → institutions → manuscripts,
  ≈ 2,300 requests, ≈ 4 min) and caches it. A complete catalogue is bundled (32,763
  manuscripts, the total reported by `/api/manuscripts/count/`).
* **ALTO index**: the archive list comes from the Hugging Face API (nothing downloaded);
  each `.tar.gz` starts with `manifest.txt`, so a partial HTTP request (4 KB instead of
  several MB) reads its first line. The bundled index holds this reading for **all**
  archives; the application refreshes the list weekly and only reads new archives.
* **Biblissima**: used for the reverse chain and for deep search:
  `haswbstatement:P196=<IIIF manifest>` in the *Item* namespace (P196 = IIIF manifest,
  P195 = shelfmark, P194 = institution).

## Verified results (15 Nov 2026)

* 32,524 archives indexed, all read, no error. **23,233 of 32,763 CoMMA manuscripts
  (70.9 %)** have at least one confirmed ALTO archive.
* Troyes 816, Châteaudun A2, BnF Latin 16400, Troyes 1713: confirmed (pages = ALTO files).
  For Troyes 1713, the obviously named `france-troyes-fonds-principal-1713-integral` is
  *another digitization*; CoMMA transcribed the Gallica one.
* **Troyes 1759**: no archive cites its manifest (`…/ark:/63955/f9n82uakhj1w`) and no Troyes
  archive carries that number: its ALTO files are not in the public repository as of that
  date (which does not prove they do not exist elsewhere).
* The `manifest_url` returned by CoMMA's full-text search (`/api/manuscripts/`) can be
  wrong (Troyes 1713); the application relies on the DTS API instead.

## If it crashes

* Errors are written to **`journal_erreurs.txt`** (menu **Index… → Open the error log**),
  including hard crashes: the Python stack is written there even if the window vanishes
  without a message. Send this file for diagnosis.
* On **macOS with Spyder**, Tkinter does not mix well with the kernel's Qt event loop: set
  *Preferences → IPython console → Graphics → Backend* to **Inline**, or run the application
  from a terminal (`python COMMA_RECHERCHE_SPYDER.py`).
* On Windows, if extraction fails because paths are too long, choose a short destination
  folder (for example `C:\ALTO`).

## Files

* Cache (catalogue, index, settings): `%LOCALAPPDATA%\CommaRecherche` (Windows),
  `~/Library/Caches/CommaRecherche` (macOS), `~/.cache/comma-recherche` (Linux).
* Extracted ALTO: by default `~/Downloads/CoMMA_ALTO/<archive name>/` (configurable).

## Data

The indexes in `donnees/` are derived from CoMMA's public metadata (Inria) and from the
`comma-project/alto-dataset` repository, published under the CC BY 4.0 licence.
