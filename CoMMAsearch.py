# -*- coding: utf-8 -*-
"""
CoMMAsearch.py — moteur de recherche CoMMA et récupération des ALTO.
CoMMA search engine and automatic ALTO retrieval.

Utilisation / Usage
-------------------
* Spyder : ouvrir ce fichier et appuyer sur F5 / open this file and press F5.
* Terminal : python CoMMAsearch.py
* Recherche pré-remplie / prefilled search :
      python CoMMAsearch.py --recherche "Troyes 816"
* Sans interface / no GUI :
      python CoMMAsearch.py --cli "Troyes, BM, 816"
  (--telecharger : télécharger et extraire l'archive confirmée / download and
  extract the confirmed archive)
* Langue / language : --lang fr | --lang en (sinon : réglage enregistré, puis
  langue du système / otherwise: saved setting, then system language).

Aucune bibliothèque à installer : Python 3.8+ avec Tkinter. Windows, macOS, Linux.
No library to install: Python 3.8+ with Tkinter. Windows, macOS, Linux.

Fichiers / files : ce script, comma_core.py, comma_i18n.py et le dossier
« donnees » doivent rester ensemble / must stay together.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import comma_core as cc  # noqa: E402
import comma_i18n as i18n  # noqa: E402
from comma_i18n import _  # noqa: E402

# Fenêtres fermées dont des fils secondaires tournent encore : on les garde en vie
# ici pour que leurs objets Tk soient libérés par le fil principal (sinon Tkinter
# peut faire planter tout le processus, et donc le noyau Spyder).
_ALIVE = []
_CRASH_FH = None


def error_log_path():
    return os.path.join(cc.user_cache_dir(), "journal_erreurs.txt")


def log_error(text):
    """Ajoute une erreur au journal (fichier), sans jamais lever d'exception."""
    import time as _time
    try:
        path = error_log_path()
        try:  # sous Windows, impossible tant que faulthandler tient le fichier : on continue d'ajouter
            if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
                os.replace(path, path + ".ancien")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n[%s] v%s, Python %s, %s\n%s\n" % (_time.strftime("%Y-%m-%d %H:%M:%S"), cc.VERSION,
                                                     sys.version.split()[0], sys.platform, text))
    except Exception:
        pass


def enable_crash_trace():
    """faulthandler : en cas de plantage brutal (Tcl, mémoire…), la pile Python est écrite
    dans journal_erreurs.txt — utile quand la fenêtre disparaît sans message."""
    global _CRASH_FH
    try:
        import faulthandler
        if _CRASH_FH is None:
            _CRASH_FH = open(error_log_path(), "a", encoding="utf-8")
        faulthandler.enable(file=_CRASH_FH, all_threads=True)
    except Exception:
        pass


def _purge_alive():
    """Libère (dans le fil principal) les anciennes fenêtres dont les fils sont terminés."""
    _ALIVE[:] = [app for app in _ALIVE if any(t.is_alive() for t in getattr(app, "threads", []))]


EXAMPLES = ["Troyes, BM, 816", "Troyes 0816", "Paris, BnF, Latin 16400", "BnF Latin 16400",
            "Châteaudun A2", "Châteaudun, Archives municipales, A 002", "Troyes, BM, 1759"]


def human_size(n):
    if not n:
        return ""
    units = ("o", "Ko", "Mo", "Go") if i18n.get_language() == "fr" else ("B", "KB", "MB", "GB")
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return ("%d %s" % (n, unit)) if unit == units[0] else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return ""


def init_language(argv=None):
    """--lang, sinon réglage enregistré, sinon langue du système."""
    argv = argv or []
    lang = None
    if "--lang" in argv:
        i = argv.index("--lang")
        if i + 1 < len(argv):
            lang = argv[i + 1]
    lang = lang or cc.load_settings().get("language") or i18n.detect_language()
    i18n.set_language(lang)
    return i18n.get_language()


# ==========================================================================
# Mode texte (sans Tkinter, ou option --cli)
# ==========================================================================

def run_cli(query, live=True, download_dir=None):
    catalog = cc.Catalog.load_default()
    index = cc.AltoIndex.load_default()
    if not len(catalog):
        print(_("Catalogue absent : construction (quelques minutes)…"))
        data = cc.crawl_comma_catalog(progress=print)
        catalog.save(data)
    print(_("Catalogue : %d manuscrits (%s) ; index ALTO : %d archives, %d manifest.txt lus (%s)")
          % (len(catalog), catalog.generated, len(index), index.scanned_count, index.generated))
    hits, q, note = catalog.search(query)
    print(_("\nRequête : %r -> cotes %s, mots %s (%s)") % (query, q.numbers, q.words, note))
    if q.archive:
        for a in index.find_by_name(q.archive):
            mss, notes = cc.archive_to_manuscripts(a, catalog, index)
            print(_("Archive %s") % a.path)
            for n in notes:
                print("   ", n)
            hits += [(m, 1.0) for m in mss]
    if not hits:
        print(_("Aucun résultat dans le catalogue local."))
        return []
    for i, (m, s) in enumerate(hits[:15], 1):
        print("%2d. [%3d %%] %s  (%s)" % (i, s * 100, m.title, m.qid))
    out = []
    for m, s in hits[:3]:
        res = cc.resolve_alto(m, catalog, index, live=live)
        if live:
            cc.check_comma_page(res)
        out.append(res)
        print("\n=== %s" % m.normalized_reference)
        print(_("  Biblissima     : %s") % ((cc.BIBLISSIMA_ENTITY + m.qid) if m.qid else "—"))
        print(_("  Transcription  : %s") % m.comma_url(res.first_page))
        print("                   %s" % res.comma_note)
        print(_("  Manifeste IIIF : %s") % m.iiif)
        for mt in res.matches:
            print("  ALTO [%s, %d %%] %s (%s)" % (cc.status_label(mt.status), mt.certainty, mt.archive.path,
                                                human_size(mt.archive.size)))
            print("        " + mt.detail.replace("\n", "\n        "))
        for n in res.notes:
            print(_("  NB : ") + n)
        if download_dir and res.confirmed:
            rep = cc.download_and_extract(res.confirmed[0].archive, download_dir, progress=print,
                                          provenance={"cote": m.normalized_reference,
                                                      "biblissima": m.uri, "iiif": m.iiif})
            print(_("  Extraction : %(presents)d / %(annonces)d fichiers dans %(dossier)s") % rep)
    return out


# ==========================================================================
# Interface graphique
# ==========================================================================

def run_gui(initial_query=""):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    import gc
    _purge_alive()
    gc.collect()  # libère ici, dans le fil principal, les fenêtres des lancements précédents
    enable_crash_trace()
    if sys.platform.startswith("win"):
        try:  # netteté sous Windows (écrans haute résolution) ; sans effet si déjà réglé (Spyder)
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:
            pass

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = min(1360, max(800, sw - 40)), min(820, max(560, sh - 90))
            self.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
            self.minsize(min(980, w), min(600, h))
            self.settings = cc.load_settings()
            self.download_dir = self.settings.get("download_dir") or cc.default_download_dir()
            self.catalog = cc.Catalog()
            self.index = cc.AltoIndex()
            self.msgs = queue.Queue()
            self.stop = threading.Event()
            self.busy = 0
            self.current = None        # Manuscript affiché
            self.resolution = None     # Resolution affichée
            self.res_cache = {}
            self.hits = []
            self.threads = []          # fils secondaires lancés par bg()
            self.loaded = False        # index chargés ?
            self.search_gen = 0        # numéro de la dernière recherche (résultats périmés ignorés)
            self.closing = False
            self.downloading = set()   # archives en cours de téléchargement
            self._select_job = None
            self.query_var = tk.StringVar(master=self, value=initial_query)
            self.lang_var = tk.StringVar(master=self, value=i18n.get_language())
            self._build()
            self.protocol("WM_DELETE_WINDOW", self.on_close)
            self.after(100, self._pump)
            self.bg(self._load_data, self._loaded, _("Chargement des index…"))

        # ---------------------------------------------------------- outils
        def log(self, text):
            self.msgs.put(("log", text))

        def bg(self, fn, done=None, label=None):
            """Exécute fn() dans un fil secondaire, puis done(résultat) dans l'interface.

            Le fil ne touche jamais à Tk : il ne communique que par la file self.msgs.
            """
            self.busy += 1
            self._set_busy(label)
            msgs = self.msgs

            def runner():
                result, err = None, None
                try:
                    result = fn()
                except cc.Interrupted as exc:  # arrêt demandé (fermeture) : pas une erreur
                    err = exc
                except BaseException as exc:  # noqa: BLE001 — même SystemExit : le compteur doit redescendre
                    err = exc
                    tb = traceback.format_exc()
                    log_error(tb)
                    msgs.put(("log", _("ERREUR : %s\n%s") % (exc, traceback.format_exc(limit=3))))
                finally:
                    msgs.put(("done", (done, result, err)))
            t = threading.Thread(target=runner, daemon=True)
            self.threads = [x for x in self.threads if x.is_alive()] + [t]
            t.start()

        def _pump(self):
            """Traite les messages des fils secondaires ; se reprogramme quoi qu'il arrive."""
            try:
                for _i in range(200):  # borne : l'interface reste réactive même sous un flot de messages
                    try:
                        kind, payload = self.msgs.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        if kind == "log":
                            self._append_log(payload)
                            self.status_var.set(str(payload).splitlines()[0][:160] if payload else "")
                        elif kind == "done":
                            done, result, err = payload
                            self.busy = max(0, self.busy - 1)
                            self._set_busy(None)
                            if self.busy == 0:
                                self.status_var.set(_("Prêt."))
                            if done is not None and not self.closing:
                                done(result, err)
                    except Exception as exc:  # noqa: BLE001
                        log_error(traceback.format_exc())
                        try:
                            self._append_log(_("ERREUR interface : %s") % exc)
                        except Exception:
                            pass
            finally:
                if not self.stop.is_set():
                    try:
                        self.after(120, self._pump)
                    except Exception:
                        pass

        def _set_busy(self, label):
            if self.busy > 0:
                self.progress.start(12)
                if label:
                    self.status_var.set(label)
            else:
                self.progress.stop()

        def _append_log(self, text):
            self.log_text.configure(state="normal")
            self.log_text.insert("end", str(text) + "\n")
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 3000:  # le journal ne grossit pas indéfiniment
                self.log_text.delete("1.0", "%d.0" % (lines - 2000))
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        def report_callback_exception(self, exc, val, tb):
            """Exception dans un rappel Tk : journalisée au lieu d'être perdue dans la console."""
            text = "".join(traceback.format_exception(exc, val, tb))
            log_error(text)
            try:
                self._append_log(_("ERREUR interface : %s") % val)
            except Exception:
                if sys.stderr is not None:  # absent dans un exécutable fenêtré (Windows)
                    sys.stderr.write(text)

        def _mb(self, kind, *args, **kw):
            """Boîte de dialogue toujours rattachée à cette fenêtre (pas à une autre racine Tk de Spyder)."""
            kw.setdefault("parent", self)
            return getattr(messagebox, kind)(*args, **kw)

        def open_path(self, path):
            err = cc.open_path(path)
            if err:
                self._mb("showwarning", _("Ouverture impossible"), "%s\n\n%s" % (path, err))

        def copy(self, text):
            if not text:
                return
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_var.set(_("Copié : %s") % text[:120])

        def open_url(self, url):
            if url:
                webbrowser.open(url)

        # -------------------------------------------------------- interface
        def _build(self):
            self.title(_("Recherche CoMMA — manuscrits, transcriptions et ALTO (v%s)") % cc.VERSION)
            style = ttk.Style(self)
            if "clam" in style.theme_names() and sys.platform.startswith("linux"):
                style.theme_use("clam")
            style.configure("Title.TLabel", font=("TkDefaultFont", 11, "bold"))
            style.configure("Small.TButton", padding=(4, 1))

            top = ttk.Frame(self, padding=(10, 8, 10, 2))
            top.pack(fill="x")
            ttk.Label(top, text=_("Cote, référence, Q Biblissima, manifeste IIIF ou nom d'archive :")).pack(anchor="w")
            row = ttk.Frame(top)
            row.pack(fill="x", pady=(2, 0))
            self.entry = ttk.Combobox(row, textvariable=self.query_var, values=EXAMPLES,
                                      font=("TkDefaultFont", 12))
            self.entry.pack(side="left", fill="x", expand=True)
            self.entry.bind("<Return>", lambda e: self.do_search())
            self.entry.bind("<<ComboboxSelected>>", lambda e: self.do_search())
            ttk.Button(row, text=_("Rechercher"), command=self.do_search).pack(side="left", padx=6)
            mb = ttk.Menubutton(row, text="Index…")
            menu = tk.Menu(mb, tearoff=False)
            menu.add_command(label=_("Mettre à jour le catalogue CoMMA (quelques minutes)"),
                             command=self.update_catalog)
            menu.add_command(label=_("Mettre à jour la liste des archives ALTO (+ lire les nouvelles)"),
                             command=self.update_alto_listing)
            menu.add_command(label=_("Lire tous les manifest.txt non lus"), command=self.scan_unscanned)
            menu.add_separator()
            menu.add_command(label=_("Ouvrir le dossier du cache"), command=lambda: self.open_path(cc.user_cache_dir()))
            menu.add_command(label=_("Ouvrir le journal des erreurs"), command=self.open_error_log)
            menu.add_command(label=_("À propos des niveaux de certitude"), command=self.show_help)
            mb["menu"] = menu
            mb.pack(side="left")
            lb = ttk.Menubutton(row, text="Langue / Language")
            lmenu = tk.Menu(lb, tearoff=False)
            for code, name in i18n.LANGUAGES:
                lmenu.add_radiobutton(label=name, value=code, variable=self.lang_var,
                                      command=lambda c=code: self.set_language(c))
            lb["menu"] = lmenu
            lb.pack(side="left", padx=(6, 0))
            self.info_var = tk.StringVar(master=self, value=_("Chargement…"))
            ttk.Label(top, textvariable=self.info_var, foreground="#555").pack(anchor="w", pady=(3, 0))

            self._bottom = ttk.Frame(self, padding=(10, 0, 10, 6))
            self._bottom.pack(side="bottom", fill="x")
            paned = ttk.PanedWindow(self, orient="horizontal")
            paned.pack(fill="both", expand=True, padx=10, pady=6)
            def set_sash():
                try:
                    if paned.winfo_exists():
                        paned.sashpos(0, min(470, max(300, self.winfo_width() // 3)))
                except Exception:
                    pass
            self.after(300, set_sash)

            # -- résultats
            left = ttk.Frame(paned)
            paned.add(left, weight=2)
            self.results_label = ttk.Label(left, text=_("Résultats"), style="Title.TLabel",
                                           wraplength=450, justify="left")
            self.results_label.pack(anchor="w")
            cols = ("score", "cote", "qid", "date")
            self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
            for c, t, w, st in (("score", _("Pert."), 50, False), ("cote", _("Manuscrit (intitulé CoMMA)"), 300, True),
                                ("qid", "Biblissima", 75, False), ("date", "Date", 80, False)):
                self.tree.heading(c, text=t)
                self.tree.column(c, width=w, stretch=st, anchor="w")
            ysb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=ysb.set)
            self.tree.pack(side="left", fill="both", expand=True)
            ysb.pack(side="left", fill="y")
            self.tree.bind("<<TreeviewSelect>>", self.on_select)

            # -- détail
            right = ttk.Frame(paned, padding=(8, 0, 0, 0))
            paned.add(right, weight=3)
            self.detail_title = ttk.Label(right, text=_("Sélectionnez un manuscrit"), style="Title.TLabel",
                                          wraplength=700, justify="left")
            self.detail_title.pack(anchor="w")
            grid = ttk.Frame(right)
            grid.pack(fill="x", pady=(4, 6))
            grid.columnconfigure(1, weight=1)
            self.fields = {}
            copy_, open_ = (_("Copier"), "copy"), (_("Ouvrir ▶"), "open")
            spec = [
                ("ref", _("1. Cote normalisée"), [copy_]),
                ("qid", "2. Biblissima", [copy_, open_]),
                ("comma", _("3. Transcription CoMMA"), [copy_, open_]),
                ("iiif", _("4. Manifeste IIIF"), [copy_, open_]),
                ("meta", _("Date · langue · volume"), []),
            ]
            for r, (key, label, buttons) in enumerate(spec):
                ttk.Label(grid, text=label).grid(row=r * 2, column=0, sticky="nw", padx=(0, 6), pady=2)
                var = tk.StringVar(master=self)
                ent = ttk.Entry(grid, textvariable=var, state="readonly")
                ent.grid(row=r * 2, column=1, sticky="ew", pady=2)
                bf = ttk.Frame(grid)
                bf.grid(row=r * 2, column=2, sticky="w")
                for text, action in buttons:
                    cmd = (lambda v=var: self.copy(v.get())) if action == "copy" else \
                          (lambda k=key: self.open_field(k))
                    ttk.Button(bf, text=text, command=cmd, style="Small.TButton").pack(side="left", padx=1)
                note = tk.StringVar(master=self)
                ttk.Label(grid, textvariable=note, foreground="#666", wraplength=700,
                          justify="left").grid(row=r * 2 + 1, column=1, columnspan=2, sticky="w")
                self.fields[key] = (var, note)

            alto_label = ttk.Label(right, text=_("5–7. Archives ALTO sur Hugging Face"), style="Title.TLabel")
            alto_label.pack(anchor="w")
            af = ttk.Frame(right)  # placée après les boutons : c'est elle qui rétrécit
            acols = ("statut", "cert", "archive", "taille")
            self.atree = ttk.Treeview(af, columns=acols, show="headings", height=3, selectmode="extended")
            for c, t, w, st in (("statut", _("Correspondance"), 250, False), ("cert", _("Certitude"), 70, False),
                                ("archive", _("Archive (dépôt comma-project/alto-dataset)"), 380, True),
                                ("taille", _("Taille"), 70, False)):
                self.atree.heading(c, text=t)
                self.atree.column(c, width=w, stretch=st, anchor="w")
            self.atree.tag_configure("ok", background="#dff3df")
            self.atree.tag_configure("other", background="#fff4d6")
            self.atree.tag_configure("prob", background="#fde9d9")
            asb = ttk.Scrollbar(af, orient="vertical", command=self.atree.yview)
            self.atree.configure(yscrollcommand=asb.set)
            self.atree.pack(side="left", fill="both", expand=True)
            asb.pack(side="left", fill="y")
            self.atree.bind("<<TreeviewSelect>>", self.on_archive_select)

            self.adetail = tk.Text(right, height=5, wrap="word", relief="flat", background=self.cget("background"),
                                   font=("TkDefaultFont", 9))
            self.adetail.pack(fill="x", pady=(4, 2))
            self.adetail.configure(state="disabled")

            bar = ttk.Frame(right)
            bar.pack(fill="x", pady=(2, 0))
            ttk.Button(bar, text=_("⬇ Télécharger et extraire la sélection"),
                       command=self.download_selected).pack(side="left")
            ttk.Button(bar, text=_("⬇ Toutes les confirmées"), command=self.download_confirmed).pack(side="left", padx=4)
            ttk.Button(bar, text=_("Voir sur Hugging Face ▶"), command=self.browse_selected).pack(side="left")
            ttk.Button(bar, text=_("Copier l'URL"), command=self.copy_archive_url).pack(side="left", padx=4)
            bar2 = ttk.Frame(right)
            bar2.pack(fill="x", pady=(4, 0))
            ttk.Button(bar2, text=_("Recherche approfondie"), command=self.deep_search).pack(side="right")
            ttk.Label(bar2, text=_("Destination :")).pack(side="left")
            self.dest_var = tk.StringVar(master=self, value=self.download_dir)
            ttk.Entry(bar2, textvariable=self.dest_var, state="readonly").pack(side="left", fill="x", expand=True, padx=4)
            ttk.Button(bar2, text=_("Changer…"), command=self.choose_dest).pack(side="left")
            ttk.Button(bar2, text=_("Ouvrir ▶"), command=self.open_dest).pack(side="left", padx=(4, 12))
            af.pack(fill="both", expand=True, after=alto_label)

            # -- journal et barre d'état (créés plus haut, placés en bas)
            bottom = self._bottom
            self.log_text = tk.Text(bottom, height=4, wrap="word", state="disabled",
                                    font=("TkFixedFont", 9))
            self.log_text.pack(fill="x")
            st = ttk.Frame(bottom)
            st.pack(fill="x", pady=(4, 0))
            self.status_var = tk.StringVar(master=self, value="")
            ttk.Label(st, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
            self.progress = ttk.Progressbar(st, mode="indeterminate", length=160)
            self.progress.pack(side="right")
            self.entry.focus_set()

        def _ready(self, quiet=False):
            """Les actions sur les index attendent la fin du chargement (sinon elles écraseraient le cache)."""
            if not self.loaded:
                if not quiet:
                    self._mb("showinfo", "Index", _("Chargement des index…"))
                return False
            return True

        def open_error_log(self):
            path = error_log_path()
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                self._mb("showinfo", _("Journal des erreurs"), _("Aucune erreur enregistrée.\n\n%s") % path)
                return
            self.open_path(path)

        def set_language(self, code):
            """Change la langue et reconstruit l'interface sans perdre la recherche ni le journal."""
            if code == i18n.get_language():
                return
            i18n.set_language(code)
            self.lang_var.set(i18n.get_language())
            self.settings["language"] = i18n.get_language()
            cc.save_settings(self.settings)
            log = self.log_text.get("1.0", "end-1c").rstrip("\n")
            for w in self.winfo_children():
                w.destroy()
            self.res_cache.clear()      # les messages déjà produits étaient dans l'autre langue
            self.resolution = None
            self._build()
            if log:
                self._append_log(log)
            if self.busy > 0:
                self._set_busy(None)
            if len(self.catalog):
                self._refresh_info()
                if self.query_var.get().strip():
                    self.do_search()

        # ---------------------------------------------------------- données
        def _load_data(self):
            cat = cc.Catalog.load_default()
            idx = cc.AltoIndex.load_default()
            return cat, idx

        def _loaded(self, result, err):
            self.loaded = True
            if err or not result:
                self._mb("showerror", _("Chargement des index…"), str(err))
                return
            self.catalog, self.index = result
            self._refresh_info()
            if not len(self.catalog):
                if self._mb("askyesno", _("Catalogue absent"),
                                       _("Le catalogue local de CoMMA est absent.\n"
                                         "Le construire maintenant ? (environ 5 minutes, une seule fois)")):
                    self.update_catalog()
            elif self.query_var.get().strip():
                self.do_search()
            # liste des archives : rafraîchie automatiquement si elle date de plus de 7 jours
            if self._age_days(self.index.listed) > 7:
                self.update_alto_listing(quiet=True)

        @staticmethod
        def _age_days(stamp):
            import datetime
            try:
                t = datetime.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
                return (datetime.datetime.now() - t).days
            except Exception:
                return 9999

        def _refresh_info(self):
            self.info_var.set(
                _("Catalogue CoMMA : %d manuscrits (au %s)   •   Archives ALTO : %d, dont %d manifest.txt lus "
                  "(liste au %s)")
                % (len(self.catalog), self.catalog.generated[:10] or "—", len(self.index),
                   self.index.scanned_count, (self.index.listed or "—")[:10]))

        def update_catalog(self):
            if not self._ready():
                return
            def job():
                data = cc.crawl_comma_catalog(progress=self.log, stop=self.stop)
                self.catalog.save(data)
                return data["count"]

            def done(n, err):
                if not err:
                    self.log(_("Catalogue CoMMA mis à jour : %d manuscrits.") % n)
                    self._refresh_info()
            self.bg(job, done, _("Mise à jour du catalogue CoMMA…"))

        def update_alto_listing(self, quiet=False):
            if not self._ready(quiet):
                return
            def job():
                listing = cc.list_hf_archives(progress=self.log, stop=self.stop)
                added, removed = self.index.merge_listing(listing)
                self.log(_("Liste Hugging Face : %d archives (%d nouvelles, %d disparues).")
                         % (len(listing), added, removed))
                todo = self.index.unscanned()
                if todo:
                    self.log(_("Lecture de %d manifest.txt non encore lus…") % len(todo))
                    self.index.scan_many(todo, progress=self.log, stop=self.stop)
                self.index.save()
                return added

            self.bg(job, lambda r, e: self._refresh_info(), None if quiet else _("Mise à jour des archives ALTO…"))

        def scan_unscanned(self):
            if not self._ready():
                return
            todo = self.index.unscanned()
            if not todo:
                self._mb("showinfo", _("Index ALTO"), _("Tous les manifest.txt connus ont été lus."))
                return

            def job():
                self.index.scan_many(todo, progress=self.log, stop=self.stop)
                self.index.save()
            self.bg(job, lambda r, e: self._refresh_info(), _("Lecture de %d manifest.txt…") % len(todo))

        # --------------------------------------------------------- recherche
        def do_search(self):
            raw = self.query_var.get().strip()
            if not raw:
                return
            self.search_gen += 1
            if not self.loaded:  # la recherche sera relancée à la fin du chargement
                self.results_label.configure(text=_("Chargement des index…"))
                return
            hits, q, note = self.catalog.search(raw)
            self.hits = list(hits)
            self._fill_results(note, q)
            if q.archive:
                self._search_archive(q.archive)
            elif not hits:
                self._search_server(raw, q)

        def _fill_results(self, note, q=None, origin=None):
            origin = origin or _("catalogue local")
            self.tree.delete(*self.tree.get_children())
            for i, (m, s) in enumerate(self.hits):
                self.tree.insert("", "end", iid=str(i),
                                 values=("%d %%" % round(s * 100), m.title, m.qid or "", m.created))
            parsed = ""
            if q is not None and (q.numbers or q.words):
                parsed = _(" — cote(s) : %s ; mots : %s") % (
                    ", ".join(q.numbers) or "—", ", ".join("/".join(g) for g in q.words) or "—")
            self.results_label.configure(text=_("Résultats : %d (%s%s)%s") % (
                len(self.hits), origin, (", " + note) if note else "", parsed))
            if self.hits:
                self.tree.selection_set("0")
                self.tree.focus("0")

        def _search_server(self, raw, q):
            """Rien en local : on interroge le serveur CoMMA (ressource directe ou recherche)."""
            gen = self.search_gen

            def job():
                out = []
                if q.qid:
                    m = cc.comma_resource(cc.BIBLISSIMA_ENTITY + q.qid)
                    if m:
                        out.append((m, 1.0))
                    return out
                import re
                import urllib.parse
                d = cc.http_json("%s/manuscripts/?%s" % (cc.COMMA_API, urllib.parse.urlencode({"q": raw, "size": 50})))
                for it in d.get("items", []):
                    title = re.sub(r"<[^>]+>", "", it.get("title") or "")
                    mu = it.get("manifest_url")
                    mu = mu[0] if isinstance(mu, list) and mu else mu
                    m = cc.Manuscript(uri=it.get("collection", ""), title=title, iiif=mu,
                                      created=cc._first(it.get("created")), language=cc._first(it.get("language")))
                    m.prepare()
                    if q.numbers and not all(n in m.keys for n in q.numbers):
                        continue
                    out.append((m, 0.5))
                return out

            def done(res, err):
                if gen != self.search_gen:  # une recherche plus récente a été lancée
                    return
                if err is not None:
                    self.results_label.configure(
                        text=_("Rien dans le catalogue local, et le serveur CoMMA n'a pas pu être interrogé : %s") % err)
                    return
                if res:
                    self.hits = res
                    self._fill_results(_("le catalogue local n'avait rien"), q, origin=_("serveur CoMMA"))
                else:
                    self.results_label.configure(
                        text=_("Aucun résultat (ni dans le catalogue local du %s, ni sur le serveur CoMMA). "
                               "Vérifiez la cote ou mettez le catalogue à jour.") % self.catalog.generated[:10])
            self.bg(job, done, _("Recherche sur le serveur CoMMA…"))

        def _search_archive(self, name):
            archives = self.index.find_by_name(name)
            if not archives:
                self.results_label.configure(text=_("Archive inconnue de l'index : %s") % name)
                return
            gen = self.search_gen

            def job():
                found, notes = [], []
                for a in archives:
                    mss, n = cc.archive_to_manuscripts(a, self.catalog, self.index)
                    notes += ["%s : %s" % (a.name, x) for x in n]
                    found += [m for m in mss if m not in found]
                return found, notes

            def done(res, err):
                if not res or gen != self.search_gen:
                    return
                found, notes = res
                for n in notes:
                    self.log(n)
                self.hits = [(m, 1.0) for m in found]
                self._fill_results(_("chaîne archive → manifest.txt → IIIF → CoMMA/Biblissima"),
                                   origin=_("archive"))
            self.bg(job, done, _("Lecture du manifest.txt de l'archive…"))

        # ------------------------------------------------------------ détail
        def on_select(self, _evt=None):
            sel = self.tree.selection()
            if not sel:
                return
            try:
                m, _s = self.hits[int(sel[0])]
            except (IndexError, ValueError):
                return
            self.current = m
            self._show_manuscript(m)
            cached = self.res_cache.get(m.uri)
            if cached:
                self._show_resolution(cached)
                return
            # petite attente : parcourir la liste au clavier ne lance pas une vérification par ligne
            if getattr(self, "_select_job", None):
                try:
                    self.after_cancel(self._select_job)
                except Exception:
                    pass
            self._select_job = self.after(300, lambda: self._resolve(m))

        def _resolve(self, m):
            self._select_job = None
            if self.current is not m or self.closing:
                return
            catalog, index, lang = self.catalog, self.index, i18n.get_language()
            log, stop = self.log, self.stop

            def job():  # ne capture pas la fenêtre : seulement des données, la file et l'arrêt
                if stop.is_set():
                    return None
                res = cc.resolve_alto(m, catalog, index, live=True, progress=log)
                cc.check_comma_page(res)
                return res

            def done(res, err):
                if lang != i18n.get_language():  # langue changée entre-temps : relancé ailleurs
                    return
                if err is not None:
                    if self.current is m:
                        self._set_adetail(_("Recherche des archives ALTO impossible : %s") % err)
                    return
                if res is None:
                    return
                if res.comma_checked:  # un échec réseau n'est pas mis en cache : nouvel essai au prochain clic
                    self.res_cache[m.uri] = res
                if self.current is m:
                    self._show_resolution(res)
            self.bg(job, done, _("Recherche des archives ALTO…"))

        def _set(self, key, value, note=""):
            var, nvar = self.fields[key]
            var.set(value or "")
            nvar.set(note or "")

        def _show_manuscript(self, m):
            self.resolution = None  # évite de télécharger les archives du manuscrit précédent
            self.detail_title.configure(text=m.title)
            self._set("ref", m.normalized_reference, _("Intitulé CoMMA : %s") % m.title)
            if m.qid:
                self._set("qid", m.qid, _("%s — identifiant de la ressource CoMMA (API DTS)")
                          % (cc.BIBLISSIMA_ENTITY + m.qid))
            else:
                self._set("qid", "", _("Pas d'identifiant Biblissima pour cette ressource : %s") % m.uri)
            self._set("comma", m.comma_url(), _("Vérification en cours…"))
            self._set("iiif", m.iiif or "", _("Source déclarée par CoMMA (dublinCore.source)"))
            meta = " · ".join(x for x in (m.created, m.language, m.extent) if x)
            self._set("meta", meta)
            self.atree.delete(*self.atree.get_children())
            self._set_adetail(_("Recherche des archives ALTO…"))

        def _show_resolution(self, res):
            m = res.manuscript
            self.resolution = res
            self._set("comma", m.comma_url(res.first_page),
                      ("✔ " if res.comma_checked else "⚠ ") + res.comma_note)
            self.atree.delete(*self.atree.get_children())
            for i, mt in enumerate(res.matches):
                tag = "ok" if mt.status in (cc.CONFIRMED, cc.CONFIRMED_LIVE) else (
                    "other" if mt.status == cc.OTHER_DIGITIZATION else "prob")
                self.atree.insert("", "end", iid=str(i), tags=(tag,),
                                  values=(cc.status_label(mt.status), "%d %%" % mt.certainty, mt.archive.path,
                                          human_size(mt.archive.size)))
            if res.matches:
                self.atree.selection_set("0")
            else:
                self._set_adetail("\n".join(res.notes) + _("\n\n→ « Recherche approfondie » relit les manifest.txt "
                                                          "des archives non lues et met à jour la liste Hugging Face."))
            if res.notes and res.matches:
                self.log(_("NB : ") + " ".join(res.notes))

        def _set_adetail(self, text):
            self.adetail.configure(state="normal")
            self.adetail.delete("1.0", "end")
            self.adetail.insert("1.0", text)
            self.adetail.configure(state="disabled")

        def on_archive_select(self, _evt=None):
            mt = self._selected_matches()
            if mt:
                a = mt[0].archive
                self._set_adetail(_("%s — certitude %d %%\n%s\nmanifest.txt : %s\nURL : %s")
                                  % (cc.status_label(mt[0].status), mt[0].certainty, mt[0].detail,
                                     a.iiif or _("non lu"), a.download_url))

        def _selected_matches(self):
            if not self.resolution:
                return []
            out = []
            for i in self.atree.selection():
                try:
                    out.append(self.resolution.matches[int(i)])
                except (IndexError, ValueError):
                    pass
            return out

        def open_field(self, key):
            var, _unused = self.fields[key]
            v = var.get()
            if key == "qid" and v:
                v = cc.BIBLISSIMA_ITEM_PAGE + v
            self.open_url(v)

        # ------------------------------------------------------- ALTO : actions
        def browse_selected(self):
            for mt in self._selected_matches():
                self.open_url(mt.archive.browse_url)

        def copy_archive_url(self):
            urls = [mt.archive.download_url for mt in self._selected_matches()]
            self.copy("\n".join(urls))

        def choose_dest(self):
            d = filedialog.askdirectory(
                parent=self,
                initialdir=self.download_dir if os.path.isdir(self.download_dir) else os.path.expanduser("~"),
                title=_("Dossier de destination des ALTO"))
            if d:
                self.download_dir = d
                self.dest_var.set(d)
                self.settings["download_dir"] = d
                cc.save_settings(self.settings)

        def open_dest(self):
            try:
                os.makedirs(self.download_dir, exist_ok=True)
            except OSError as exc:
                self._mb("showwarning", _("Ouverture impossible"), "%s\n\n%s" % (self.download_dir, exc))
                return
            self.open_path(self.download_dir)

        def download_selected(self):
            sel = self._selected_matches()
            if not sel:
                self._mb("showinfo", "ALTO", _("Sélectionnez une archive dans la liste."))
                return
            risky = [mt for mt in sel if mt.status not in (cc.CONFIRMED, cc.CONFIRMED_LIVE)]
            if risky and not self._mb("askyesno", 
                    _("Correspondance non confirmée"),
                    _("%d archive(s) sélectionnée(s) ne sont PAS confirmées comme la transcription "
                      "affichée par CoMMA :\n\n%s\n\nTélécharger quand même ?")
                    % (len(risky), "\n".join("• %s (%s)" % (m.archive.name, cc.status_label(m.status))
                                              for m in risky))):
                return
            self._download([mt.archive for mt in sel])

        def download_confirmed(self):
            if not self.resolution or not self.resolution.confirmed:
                self._mb("showinfo", "ALTO", _("Aucune archive confirmée pour ce manuscrit (ou vérification en cours)."))
                return
            self._download([mt.archive for mt in self.resolution.confirmed])

        def _download(self, archives):
            resolution = self.resolution
            if resolution is None:
                return
            busy = [a.name for a in archives if a.path in self.downloading]
            if busy:
                self._mb("showinfo", _("Téléchargement"), _("Déjà en cours :\n%s") % "\n".join(busy))
                return
            for a in archives:
                self.downloading.add(a.path)
            m = resolution.manuscript
            dest = self.download_dir
            prov = {"cote": m.normalized_reference, "intitule_comma": m.title, "biblissima": m.uri,
                    "manifeste_iiif_comma": m.iiif, "transcription_comma": m.comma_url(resolution.first_page)}

            def job():
                reports = []
                for a in archives:
                    match = next((mt for mt in resolution.matches if mt.archive is a), None)
                    p = dict(prov, correspondance=cc.status_label(match.status) if match else "?",
                             certitude=match.certainty if match else None)
                    reports.append(cc.download_and_extract(a, dest, progress=self.log, provenance=p, stop=self.stop))
                return reports

            def done(reports, err):
                for a in archives:
                    self.downloading.discard(a.path)
                if err or not reports:
                    self._mb("showerror", _("Téléchargement"), _("Échec : %s") % err)
                    return
                lines = []
                for r in reports:
                    ok = "✔" if not r["manquants"] else "⚠"
                    lines.append(_("%s %s\n   %d / %d fichiers ALTO annoncés par manifest.txt\n   %s")
                                 % (ok, os.path.basename(r["dossier"]), r["presents"], r["annonces"], r["dossier"]))
                    self.log(_("Extrait : %s (%d/%d fichiers)") % (r["dossier"], r["presents"], r["annonces"]))
                if self._mb("askyesno", _("ALTO extraits"), "\n\n".join(lines) + _("\n\nOuvrir le dossier ?")):
                    self.open_path(reports[0]["dossier"])
            self.bg(job, done, _("Téléchargement…"))

        def deep_search(self):
            if not self._ready():
                return
            m = self.current
            if m is None:
                return

            def job():
                listing = cc.list_hf_archives(progress=self.log, stop=self.stop)
                added, removed = self.index.merge_listing(listing)
                self.log(_("Liste Hugging Face actualisée : %d archives (%d nouvelles).") % (len(listing), added))
                todo = self.index.unscanned()
                if todo:
                    self.index.scan_many(todo, progress=self.log, stop=self.stop)
                # relecture des archives de la même ville (noms inattendus)
                city = cc.norm_text(m.city).replace(" ", "-") if m.city else ""
                group = [a for a in self.index.all() if city and ("-%s-" % city) in ("-" + a.stem + "-")]
                if group:
                    self.log(_("Relecture de %d archives de « %s »…") % (len(group), m.city))
                    self.index.scan_many(group, progress=self.log, stop=self.stop)
                self.index.save()
                res = cc.resolve_alto(m, self.catalog, self.index, live=False)
                cc.check_comma_page(res)
                try:
                    q = cc.biblissima_by_iiif(m.iiif) if m.iiif else []
                    res.notes.append(_("Biblissima (P196) pour ce manifeste : %s") % (", ".join(q) or _("aucun item")))
                except Exception as exc:  # noqa: BLE001
                    res.notes.append(_("Biblissima injoignable : %s") % exc)
                return res

            def done(res, err):
                if res is None:
                    return
                self.res_cache[m.uri] = res
                self._refresh_info()
                if self.current is m:
                    self._show_resolution(res)
                for n in res.notes:
                    self.log(_("NB : ") + n)
            self.bg(job, done, _("Recherche approfondie…"))

        def show_help(self):
            codes = (cc.CONFIRMED_LIVE, cc.CONFIRMED, cc.PROBABLE, cc.OTHER_DIGITIZATION, cc.NOT_FOUND)
            self._mb("showinfo", _("Niveaux de certitude"), "\n\n".join(
                "%s : %s" % (cc.status_label(c), cc.status_help(c)) for c in codes))

        def on_close(self):
            """Fermeture : arrêt demandé aux fils, courte attente, puis destruction de la fenêtre."""
            if self.closing:
                return
            self.closing = True
            self.stop.set()
            self.status_var.set(_("Fermeture…"))
            self._close_wait(0)

        def _close_wait(self, waited):
            alive = [t for t in self.threads if t.is_alive()]
            if alive and waited < 8000:  # les fils vérifient l'arrêt entre deux requêtes
                self.after(200, lambda: self._close_wait(waited + 200))
                return
            try:
                if len(self.index) and not alive:
                    self.index.save()  # conserve les manifest.txt lus pendant la session
            except Exception:
                log_error(traceback.format_exc())
            if alive:
                _ALIVE.append(self)  # libéré plus tard par le fil principal (_purge_alive)
            try:
                self.progress.stop()  # sinon Tcl relance l'animation sur une fenêtre détruite
            except Exception:
                pass
            self.quit()      # sort de mainloop même si une autre racine Tk existe (Spyder, matplotlib)
            self.destroy()

    app = None
    try:
        app = App()
        app.mainloop()
    finally:
        # Nettoyage DANS LE FIL PRINCIPAL : sinon le ramasse-miettes pourrait libérer
        # l'interpréteur Tk depuis un fil secondaire, ce qui tue le processus (Tcl_AsyncDelete).
        if app is not None:
            app.closing = True
            app.stop.set()
            import time as _time
            deadline = _time.time() + 5.0
            for t in list(app.threads):
                t.join(timeout=max(0.0, deadline - _time.time()))
            try:
                if app.winfo_exists():
                    app.progress.stop()
                    app.destroy()
            except Exception:
                pass
            app.msgs = queue.Queue()  # les rappels en attente référencent la fenêtre
            if any(t.is_alive() for t in app.threads) and app not in _ALIVE:
                _ALIVE.append(app)
        app = None
        gc.collect()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    init_language(argv)
    # Spyder/IPython passent parfois leurs propres arguments : on ne garde que les nôtres
    if "--cli" in argv:
        i = argv.index("--cli")
        rest = argv[i + 1:]
        words, skip = [], False
        for a in rest:
            if skip:
                skip = False
                continue
            if a == "--lang":
                skip = True
                continue
            if not a.startswith("--"):
                words.append(a)
        query = " ".join(words) or input(_("Cote : "))
        dl = cc.default_download_dir() if "--telecharger" in argv else None
        run_cli(query, live="--hors-ligne" not in argv, download_dir=dl)
        return
    initial = ""
    if "--recherche" in argv:
        i = argv.index("--recherche")
        initial = " ".join(argv[i + 1:i + 2])
    try:
        import tkinter  # noqa: F401
        run_gui(initial)
    except Exception as exc:  # pas d'affichage graphique disponible
        if "display" in str(exc).lower() or isinstance(exc, ImportError):
            print(_("Interface graphique indisponible (%s) : mode texte.") % exc)
            while True:
                q = input(_("\nCote (vide pour quitter) : ")).strip()
                if not q:
                    break
                run_cli(q)
        else:
            raise


if __name__ == "__main__":
    main()
