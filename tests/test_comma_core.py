# -*- coding: utf-8 -*-
"""Tests hors ligne : python -m unittest discover -s tests"""
import io
import os
import sys
import tarfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comma_core as cc  # noqa: E402


def ms(qid, title, iiif, city, inst):
    m = cc.Manuscript(uri=cc.BIBLISSIMA_ENTITY + qid, title=title, iiif=iiif, city=city, institution=inst)
    m.prepare()
    return m


TROYES = "Troyes. Médiathèque du Grand Troyes"
BNF = "Paris. Bibliothèque nationale de France, Département des manuscrits"
SAMPLE = [
    ms("Q412957", TROYES + ", Ms. 816", "https://api.irht.cnrs.fr/ark:/63955/f9a5a7k98nwu/manifest.json", "Troyes", TROYES),
    ms("Q252384", TROYES + ", Ms. 806", "https://gallica.bnf.fr/iiif/ark:/12148/btv1b10520061k/manifest.json", "Troyes", TROYES),
    ms("Q999001", TROYES + ", Ms. 1816", "https://example.org/a/manifest.json", "Troyes", TROYES),
    ms("Q277651", TROYES + ", Ms. 1759", "https://api.irht.cnrs.fr/ark:/63955/f9n82uakhj1w/manifest.json", "Troyes", TROYES),
    ms("Q61665", BNF + ", Latin 16400", "https://gallica.bnf.fr/iiif/ark:/12148/btv1b9077719x/manifest.json", "Paris", BNF),
    ms("Q999002", BNF + ", Latin 16401", "https://gallica.bnf.fr/iiif/ark:/12148/btv1bxxxx/manifest.json", "Paris", BNF),
    ms("Q999003", BNF + ", Latin 6400G", "https://gallica.bnf.fr/iiif/ark:/12148/btv1byyyy/manifest.json", "Paris", BNF),
    ms("Q999004", "Rouen. Bibliothèque municipale, Ms. 816", "https://example.org/r/manifest.json", "Rouen", "Rouen. Bibliothèque municipale"),
    ms("Q419352", "Châteaudun. Archives municipales, Hôtel-Dieu, ms. A2",
       "https://api.irht.cnrs.fr/ark:/63955/fdy27ubqm4j0/manifest.json", "Châteaudun", "Châteaudun. Archives municipales"),
    ms("Q999005", "Châteaudun. Archives municipales, Hôtel-Dieu, ms. A3", "https://example.org/c3/manifest.json",
       "Châteaudun", "Châteaudun. Archives municipales"),
]


def catalog():
    c = cc.Catalog()
    c.load({"generated": "test", "items": [m.to_json() for m in SAMPLE]})
    return c


class TestNormalisation(unittest.TestCase):
    def test_norm_text(self):
        self.assertEqual(cc.norm_text("Châteaudun, Archives — A 002"), "chateaudun archives a 002")

    def test_canon(self):
        self.assertEqual(cc.canon_alnum("0816"), "816")
        self.assertEqual(cc.canon_alnum("a002"), "a2")
        self.assertEqual(cc.canon_alnum("6400g"), "6400g")

    def test_shelf_keys(self):
        self.assertIn("a2", cc.shelf_keys("Hôtel-Dieu, ms. A2"))
        self.assertIn("a2", cc.shelf_keys("Hôtel-Dieu, ms. A 002"))
        self.assertIn("6400g", cc.shelf_keys("Latin 6400 G"))

    def test_iiif_key(self):
        a = cc.iiif_key("https://openapi.bnf.fr/iiif/presentation/v3/ark:/12148/btv1b9077719x/manifest.json")
        b = cc.iiif_key("https://gallica.bnf.fr/iiif/ark:/12148/btv1b9077719x/manifest.json")
        self.assertEqual(a, b)
        self.assertEqual(a, "ark:/12148/btv1b9077719x")
        self.assertEqual(cc.iiif_key("https://www.e-codices.unifr.ch/metadata/iiif/csg-0001/manifest.json"),
                         "www.e-codices.unifr.ch/metadata/iiif/csg-0001")

    def test_page_id_of_alto(self):
        self.assertEqual(cc.page_id_of_alto("d/f12-f-008v-009r.xml"), "f12-f-008v-009r")

    def test_comma_url(self):
        self.assertEqual(
            cc.comma_doc_url("https://data.biblissima.fr/entity/Q419352", "f0-plat-superieur"),
            "https://comma.inria.fr/doc/https%253A%252F%252Fdata.biblissima.fr%252Fentity%252FQ419352/p/f0-plat-superieur")


class TestQuery(unittest.TestCase):
    def test_parse(self):
        q = cc.parse_query("Châteaudun, Archives municipales, A 002")
        self.assertEqual(q.numbers, ["a2"])
        q = cc.parse_query("Troyes 0816")
        self.assertEqual(q.numbers, ["816"])
        self.assertEqual(q.words, [("troyes",)])
        self.assertEqual(cc.parse_query("Q412957").qid, "Q412957")
        self.assertEqual(cc.parse_query("https://data.biblissima.fr/entity/Q419352").qid, "Q419352")
        self.assertEqual(cc.parse_query(
            "https://comma.inria.fr/doc/https%253A%252F%252Fdata.biblissima.fr%252Fentity%252FQ412957/p/x").qid, "Q412957")
        self.assertEqual(cc.parse_query("https://api.irht.cnrs.fr/ark:/63955/f9n82uakhj1w/manifest.json").iiif,
                         "ark:/63955/f9n82uakhj1w")
        self.assertEqual(cc.parse_query("batch_018/france-troyes-fonds-principal-0816-integral.tar.gz").archive,
                         "france-troyes-fonds-principal-0816-integral.tar.gz")

    def first(self, query):
        hits, _q, _n = catalog().search(query)
        return [m.qid for m, _ in hits]

    def test_user_examples(self):
        for query, expected in [("Troyes, BM, 816", "Q412957"), ("Troyes 0816", "Q412957"),
                                ("Paris, BnF, Latin 16400", "Q61665"), ("BnF Latin 16400", "Q61665"),
                                ("Châteaudun A2", "Q419352"),
                                ("Châteaudun, Archives municipales, A 002", "Q419352"),
                                ("Troyes, BM, 1759", "Q277651")]:
            with self.subTest(query=query):
                self.assertEqual(self.first(query)[0], expected)

    def test_exact_numbers(self):
        self.assertNotIn("Q999001", self.first("Troyes 816"))   # 1816 n'est pas 816
        self.assertNotIn("Q999002", self.first("Latin 16400"))  # 16401 non plus
        self.assertNotIn("Q999004", self.first("Troyes 816"))   # Rouen écarté par la ville
        self.assertEqual(self.first("Latin 6400 G"), ["Q999003"])

    def test_number_only(self):
        self.assertEqual(set(self.first("816")), {"Q412957", "Q999004"})

    def test_ids(self):
        self.assertEqual(self.first("Q61665"), ["Q61665"])
        self.assertEqual(self.first("https://openapi.bnf.fr/iiif/presentation/v3/ark:/12148/btv1b9077719x/manifest.json"),
                         ["Q61665"])

    def test_reference(self):
        self.assertEqual(SAMPLE[0].normalized_reference, "Troyes, Médiathèque du Grand Troyes, Ms. 816")
        self.assertEqual(SAMPLE[0].shelfmark, "Ms. 816")
        self.assertEqual(SAMPLE[8].shelfmark, "Hôtel-Dieu, ms. A2")


class TestAlto(unittest.TestCase):
    def setUp(self):
        self.cat = catalog()
        self.idx = cc.AltoIndex()
        self.idx.load({"generated": "test", "archives": [
            {"path": "batch_018/france-troyes-fonds-principal-0816-integral.tar.gz", "size": 4801165,
             "iiif": "https://api.irht.cnrs.fr/ark:/63955/f9a5a7k98nwu/manifest.json",
             "first_xml": "france-troyes-fonds-principal-0816-integral/f0-marque-de-service-debut.xml", "scanned": "x"},
            {"path": "batch_019/france-troyes-fonds-principal-0816-decor.tar.gz", "size": 10,
             "iiif": "https://api.irht.cnrs.fr/ark:/63955/fother/manifest.json", "scanned": "x"},
            {"path": "batch_019/france-troyes-fonds-principal-1816-integral.tar.gz", "size": 10,
             "iiif": "https://example.org/a/manifest.json", "scanned": "x"},
            {"path": "batch_014/bn-f-departement-des-manuscrits-latin-16400.tar.gz", "size": 10,
             "iiif": "https://openapi.bnf.fr/iiif/presentation/v3/ark:/12148/btv1b9077719x/manifest.json", "scanned": "x"},
            {"path": "batch_010/france-chateaudun-fonds-principal-hotel-dieu-ms-a2-integral.tar.gz", "size": 10,
             "iiif": "https://api.irht.cnrs.fr/ark:/63955/fdy27ubqm4j0/manifest.json", "scanned": "x"},
        ]})

    def resolve(self, qid):
        return cc.resolve_alto(self.cat.by_qid[qid], self.cat, self.idx, live=False)

    def test_confirmed_by_manifest(self):
        r = self.resolve("Q412957")
        self.assertEqual([m.archive.name for m in r.confirmed], ["france-troyes-fonds-principal-0816-integral.tar.gz"])
        other = [m for m in r.matches if m.status == cc.OTHER_DIGITIZATION]
        self.assertEqual([m.archive.name for m in other], ["france-troyes-fonds-principal-0816-decor.tar.gz"])
        self.assertNotIn("france-troyes-fonds-principal-1816-integral.tar.gz", [m.archive.name for m in r.matches])

    def test_bnf_openapi_vs_gallica(self):
        r = self.resolve("Q61665")
        self.assertEqual(len(r.confirmed), 1)

    def test_chateaudun(self):
        self.assertEqual(len(self.resolve("Q419352").confirmed), 1)

    def test_not_found_is_not_absence(self):
        r = self.resolve("Q277651")
        self.assertFalse(r.confirmed)
        self.assertIn("PAS une preuve d'absence", r.notes[0])

    def test_safe_extract(self):
        import tempfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in (("manifest.txt", b"https://x/ark:/1/abc/manifest.json\nd/f0.xml\n"),
                               ("f0.xml", b"<alto/>"), ("../evil.txt", b"x")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
                names = [m.name for m in cc._safe_members(tar, os.path.join(tmp, "out"))]
            self.assertEqual(names, ["manifest.txt", "f0.xml"])


if __name__ == "__main__":
    unittest.main()


class TestTranslations(unittest.TestCase):
    """Chaque texte passé à _() a une traduction anglaise avec les mêmes paramètres."""

    def test_all_strings_translated(self):
        import ast
        import re
        import comma_i18n
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        missing, bad = [], []
        for name in ("comma_core.py", "CoMMAsearch.py"):
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    fr = node.args[0].value
                    en = comma_i18n.EN.get(fr)
                    if en is None:
                        missing.append(fr)
                        continue
                    pat = r"%(?:\(\w+\))?[sdrf%]|%\.\df"
                    if sorted(re.findall(pat, fr)) != sorted(re.findall(pat, en)):
                        bad.append((fr, en))
        self.assertEqual(missing, [])
        self.assertEqual(bad, [])

    def test_switch(self):
        import comma_i18n
        try:
            comma_i18n.set_language("en")
            self.assertEqual(cc.status_label(cc.CONFIRMED), "CONFIRMED")
            self.assertEqual(comma_i18n._("Rechercher"), "Search")
            comma_i18n.set_language("xx")  # langue inconnue -> français
            self.assertEqual(comma_i18n.get_language(), "fr")
            self.assertEqual(cc.status_label(cc.CONFIRMED), "CONFIRMÉE")
        finally:
            comma_i18n.set_language("fr")


class TestRobustness(unittest.TestCase):
    def test_merge_listing_while_iterating(self):
        idx = cc.AltoIndex()
        idx.load({"archives": [{"path": "b/a%d.tar.gz" % i, "size": 1} for i in range(50)]})
        seen = 0
        for a in idx.all():  # un autre fil remplace la liste pendant le parcours
            if seen == 10:
                idx.merge_listing([{"path": "b/x%d.tar.gz" % i, "size": 1} for i in range(80)])
            seen += 1
        self.assertEqual(seen, 50)
        self.assertEqual(len(idx), 80)

    def test_concurrent_cache_writes(self):
        import tempfile
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.json.gz")
            errors = []

            def writer(n):
                try:
                    cc._write_json_gz(path, {"generated": str(n), "items": list(range(5000))})
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
            threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(cc._read_json_gz(path)["items"]), 5000)
            self.assertEqual([f for f in os.listdir(tmp) if f.endswith(".tmp")], [])

    def test_garbage_archive_header(self):
        with self.assertRaises(cc.HttpError):
            cc._tar_head(b"<html>Service unavailable</html>", "x.tar.gz")

    def test_damaged_entries_are_skipped(self):
        c = cc.Catalog()
        c.load({"items": [{"title": "sans uri"}, SAMPLE[0].to_json(), "n'importe quoi"]})
        self.assertEqual(len(c), 1)
        idx = cc.AltoIndex()
        idx.load({"archives": [{"size": 3}, {"path": "b/ok.tar.gz"}]})
        self.assertEqual(len(idx), 1)

    def test_interrupted_before_request(self):
        import threading
        stop = threading.Event()
        stop.set()
        with self.assertRaises(cc.Interrupted):
            cc.http_get("https://example.invalid/", stop=stop)

    def test_open_path_never_raises(self):
        self.assertIsInstance(cc.open_path(os.path.join("/", "chemin", "inexistant", "x")) or "", str)
