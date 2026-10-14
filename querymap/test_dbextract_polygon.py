"""The stored extract tests again, with a boundary that is not a rectangle.

The boundary is the tests' usual rectangle with its corners cut off, so
everything the tests place in the rectangle is still inside it, and every
extract is still checked against a fresh map query of the same polygon.
The tests of their own here look at the corners.
"""
import io
import re
import time

from psycopg2 import sql

import pgmap
import querymap.test_dbextract as local
import querymap.test_dbextract_remote as remote
from pycrocosm import common
from replicate.extracts import describe_db_extract

OCTAGON = "POLYGON((-1 -0.8,-0.8 -1,0.8 -1,1 -0.8,1 0.8,0.8 1,-0.8 1,-1 0.8,-1 -0.8))"

class PolygonBoundary:
    """Saves extracts with the octagon as their boundary, and queries the map within it."""
    boundary = OCTAGON

    def save(self, name="snapshot"):
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            extract_id = t.SaveExtractWkt(self.boundary, name)
            t.Commit()
            return extract_id
        except BaseException:
            t.Abort()
            raise

    def contents(self, extract_id=None, name=""):
        if extract_id is not None:
            return super().contents(extract_id, name)
        output = io.BytesIO()
        encoder = pgmap.PyOsmXmlEncode(output, common.xmlAttribs)
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            query = t.GetQueryMgr()
            self.assertGreaterEqual(query.Start(self.boundary, int(time.time()), encoder), 0)
            while query.Continue() == 0:
                pass
            del query
            t.Commit()
        except BaseException:
            t.Abort()
            raise
        return self.decode_contents(output.getvalue())

    def info(self, extract_id):
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            info = pgmap.ExtractInfo()
            self.assertTrue(t.GetExtract(extract_id, info))
            return describe_db_extract(info)
        finally:
            t.Abort()

    def export_document(self, extract_id, encoder_class=None, attribs=None):
        # As a download writes it: with the boundary in the header
        attribs = dict(attribs or {})
        attribs.setdefault("boundary", self.info(extract_id)["boundary"])
        return super().export_document(extract_id, encoder_class, attribs)

    def test_import_needs_a_bbox(self):
        # The boundary in the header is enough without the bounds
        original = self.populated_extract()
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.export_document(original))
        root.remove(root.find("bounds"))
        imported, counts = self.import_document(ET.tostring(root, encoding="unicode"))
        self.assertEqual(counts, (3, 1, 2))
        self.assertEqual(self.stored_rows(imported), self.stored_rows(original))
        # and one the caller gives replaces it
        imported, _ = self.import_document(ET.tostring(root, encoding="unicode"), bbox=self.bbox)
        self.assertTrue(self.info(imported)["rectangular"])

class PolygonExtractTestCase(PolygonBoundary, local.DbExtractTestCase):

    def test_admin_saves_an_extract_with_a_boundary(self):
        from unittest.mock import patch
        from django.contrib import admin
        from django.contrib.messages.storage.cookie import CookieStorage
        from django.test import RequestFactory
        from django.urls import reverse
        from replicate.models import DbExtract

        class Superuser:
            is_active = is_staff = is_authenticated = is_superuser = True
            is_anonymous = False
            pk = id = 1
            def has_perm(self, perm, obj=None): return True
            def has_module_perms(self, app_label): return True
            def get_username(self): return "admin"
            def get_short_name(self): return "admin"
            def has_usable_password(self): return True

        model_admin = admin.site._registry[DbExtract]
        url = reverse("admin:replicate_dbextract_add")
        def add(data):
            request = RequestFactory().post(url, data)
            request.user = Superuser()
            request._messages = CookieStorage(request)
            request._dont_enforce_csrf_checks = True
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                response = model_admin.add_view(request)
            if response.status_code == 200:
                response.render()
            return response
        def stored():
            with self.db.cursor() as cursor:
                cursor.execute(sql.SQL("SELECT name, ST_AsText(bbox) FROM {} ORDER BY id").format(
                    sql.Identifier(self.prefixes[1] + "extracts")))
                return cursor.fetchall()

        middle = self.create_node(0.5, 0.5)
        self.create_node(0.95, 0.95)
        # Neither a rectangle nor a boundary, both, part of a rectangle, or a boundary pgmap refuses
        for data, message in (({"name": "x"}, "Give all four sides"),
                              ({"name": "x", "west": "-1", "south": "-1", "east": "1", "north": "1", "boundary": OCTAGON},
                               "either a rectangle or a boundary"),
                              ({"name": "x", "west": "-1"}, "This field is required"),
                              ({"name": "x", "boundary": "POLYGON((170 0,-170 0,-170 1,170 1,170 0))"}, "antimeridian")):
            response = add(data)
            self.assertEqual(response.status_code, 200, data)
            self.assertIn(message, response.content.decode("utf-8"), data)
        self.assertEqual(stored(), [])
        # A boundary alone is enough
        response = add({"name": "shaped", "boundary": "  " + OCTAGON + "\n"})
        self.assertEqual(response.status_code, 302)
        (name, boundary), = stored()
        self.assertEqual(name, "shaped")
        self.assertEqual(boundary, OCTAGON)
        # and the extract holds what is inside it
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            infos = pgmap.vectorextractinfo()
            t.ListExtracts(infos)
            extract_id = infos[0].extractId
        finally:
            t.Abort()
        self.assertEqual(set(self.contents(extract_id)), {("node", middle.objId)})

    def test_saveextract_command(self):
        from io import StringIO
        import os, tempfile
        from unittest.mock import patch
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from replicate import extracts

        middle = self.create_node(0.5, 0.5)
        corner = self.create_node(0.95, 0.95)
        def run(*args):
            out = StringIO()
            with patch.object(extracts, "get_pgmap", return_value=self.map):
                call_command("saveextract", *args, stdout=out)
            return int(re.search(r"extract (\d+)", out.getvalue()).group(1))

        boxed = run("--bbox", "-1,-1,1,1", "--name", "boxed")
        shaped = run("--wkt", OCTAGON, "--name", "shaped")
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "boundary.wkt")
        with open(path, "w") as handle:
            handle.write(OCTAGON + "\n")
        try:
            from_file = run("--wkt-file", path)
        finally:
            os.remove(path)
            os.rmdir(folder)
        self.assertEqual(set(self.contents(boxed)), {("node", middle.objId), ("node", corner.objId)})
        self.assertEqual(set(self.contents(shaped)), {("node", middle.objId)})
        self.assertEqual(set(self.contents(from_file)), {("node", middle.objId)})
        self.assertEqual(self.info(shaped)["name"], "shaped")

        for args, message in ((("--name", "x"), "one of --bbox"),
                              (("--bbox", "0,0,1,1", "--wkt", OCTAGON), "one of --bbox"),
                              (("--bbox", "0,0,1"), "four numbers"),
                              (("--wkt", "POLYGON((170 0,-170 0,-170 1,170 1,170 0))"), "antimeridian"),
                              (("--wkt-file", "/no/such/file"), "Cannot read"),
                              (("--bbox", "0,0,1,1", "--name", "shaped"), "already exists")):
            with self.assertRaisesRegex(CommandError, message):
                run(*args)

    def test_downloads_carry_the_boundary(self):
        self.create_node(0.5, 0.5)
        extract_id = self.save("downloaded")
        import gzip
        import xml.etree.ElementTree as ET
        response = self.download("/replication/extract/{}.osm.gz".format(extract_id))
        root = ET.fromstring(gzip.decompress(b"".join(response.streaming_content)))
        self.assertTrue(root.get("boundary").startswith("POLYGON(("))
        imported, _ = self.import_document(ET.tostring(root, encoding="unicode"), name="again")
        self.assertEqual(self.info(imported)["boundary"], self.info(extract_id)["boundary"])

    def test_only_what_is_inside_the_polygon(self):
        middle = self.create_node(0.5, 0.5)
        corner = self.create_node(0.95, 0.95) # In the rectangle, not in the octagon
        polygon = self.save("octagon")
        t = self.map.GetTransaction("ACCESS SHARE")
        rectangle = t.SaveExtract(self.bbox, "rectangle")
        t.Commit()
        self.assertEqual(set(self.contents(polygon)), {("node", middle.objId)})
        self.assertEqual(set(self.contents(rectangle)), {("node", middle.objId), ("node", corner.objId)})

        # Described as what it is: a polygon, within the rectangle around it
        described = self.info(polygon)
        self.assertEqual((described["rectangular"], described["bbox"]), (False, [-1.0, -1.0, 1.0, 1.0]))
        self.assertTrue(described["boundary"].startswith("POLYGON(("))
        self.assertTrue(self.info(rectangle)["rectangular"])

        # Moving into the polygon and out of it
        corner = self.modify_node(corner, 0.9)
        corner2 = pgmap.OsmNode(corner)
        corner2.lat = 0.0
        corner = self.upload("modify", [corner2])[0][0]
        self.assertEqual(self.updated_ids(polygon), {("node", middle.objId), ("node", corner.objId)})
        middle2 = pgmap.OsmNode(middle)
        middle2.lon, middle2.lat = -0.95, -0.95
        self.upload("modify", [middle2])
        self.assertEqual(self.updated_ids(polygon), {("node", corner.objId)})
        # A way reaching in from a cut corner is in by its node inside
        far = self.create_node(0.97, 0.97)
        way = self.create_way([far, corner])
        self.assertEqual(self.updated_ids(polygon), {("node", corner.objId), ("node", far.objId), ("way", way.objId)})
        self.assertEqual(self.compare(polygon)[1], [])

        # The file written from it has the rectangle around it as its bounds
        output = io.BytesIO()
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            t.ExportExtract(polygon, "", pgmap.PyOsmXmlEncode(output, common.xmlAttribs))
        finally:
            t.Abort()
        import xml.etree.ElementTree as ET
        root = ET.fromstring(output.getvalue())
        bounds = root.find("bounds")
        self.assertEqual([float(bounds.get(k)) for k in ("minlon", "minlat", "maxlon", "maxlat")], [-1.0, -1.0, 1.0, 1.0])

    def test_boundaries_that_are_refused(self):
        refused = {
            "MULTIPOLYGON(((0 0,1 0,1 1,0 0)),((2 2,3 2,3 3,2 2)))": "single polygon",
            "POINT(0 0)": "single polygon",
            "POLYGON((0 0,1 1,1 0,0 1,0 0))": "not a valid polygon",
            "POLYGON((170 0,-170 0,-170 1,170 1,170 0))": "antimeridian",
            "POLYGON((0 0,200 0,200 1,0 1,0 0))": "beyond longitude",
            "POLYGON EMPTY": "empty",
            "not wkt at all": "not WKT",
            "   ": "empty",
        }
        for wkt, reason in refused.items():
            t = self.map.GetTransaction("ACCESS SHARE")
            try:
                with self.assertRaises(Exception) as caught:
                    t.SaveExtractWkt(wkt, "refused")
                self.assertIn(reason, str(caught.exception), wkt)
            finally:
                t.Abort()
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(self.prefixes[1] + "extracts")))
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_imported_extract_with_a_polygon_boundary(self):
        node = self.create_node(0.5, 0.5)
        data = pgmap.OsmData()
        copy = pgmap.OsmNode(node)
        data.nodes.append(copy)
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            importer = t.StartImportExtract("imported", pgmap.vectord(), -1, -1)
            importer.SetBoundaryWkt(OCTAGON)
            data.StreamTo(importer)
            importer.Finish()
            extract_id = importer.GetId()
            del importer
            t.Commit()
        except BaseException:
            t.Abort()
            raise
        described = self.info(extract_id)
        self.assertFalse(described["rectangular"])
        self.assertEqual(set(self.contents(extract_id)), {("node", node.objId)})

class PolygonRemoteUpdateTestCase(PolygonBoundary, remote.RemoteUpdateTestCase):
    """Every update over the web API again, the extract's boundary a polygon."""

# Not run again from this module: these are run where they are defined
del local, remote
