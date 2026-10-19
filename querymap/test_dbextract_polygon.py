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
# The octagon and an island in its north-eastern corner, which the octagon
# cuts off: still within the tests' rectangle, and apart from the octagon
ISLAND = "((0.92 0.92,0.97 0.92,0.97 0.97,0.92 0.97,0.92 0.92))"
OCTAGON_AND_ISLAND = "MULTIPOLYGON(({}),{})".format(OCTAGON[len("POLYGON("):-1], ISLAND)

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

    def contents(self, extract_id=None, name="", atomic_edit_id=0):
        if extract_id is not None:
            return super().contents(extract_id, name)
        output = io.BytesIO()
        encoder = pgmap.PyOsmXmlEncode(output, common.xmlAttribs)
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            query = t.GetQueryMgr()
            if atomic_edit_id:
                query.SetAtomicEditId(atomic_edit_id)
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

    def buffered(self, wkt, metres, name="buffered", command=None):
        """Save an extract with its boundary grown, as the replicate app does."""
        from unittest.mock import patch
        from replicate import extracts
        with patch.object(extracts, "get_pgmap", return_value=self.map):
            return extracts.save_db_extract(None, name, wkt, metres)

    def test_buffered_boundary(self):
        # About 560 m east of the octagon's eastern edge
        near = self.create_node(1.005, 0)
        far = self.create_node(1.05, 0)
        inside = self.create_node(0.5, 0)
        plain = self.save("plain")
        self.assertEqual(set(self.contents(plain)), {("node", inside.objId)})
        grown = self.buffered(OCTAGON, 1000)
        self.assertEqual(set(self.contents(grown)), {("node", inside.objId), ("node", near.objId)})
        # The stored boundary is the grown one, about 0.009 degrees further out
        described = self.info(grown)
        self.assertEqual(described["shape"], "polygon")
        self.assertAlmostEqual(described["bbox"][2], 1.009, places=3)
        self.assertAlmostEqual(described["bbox"][1], -1.009, places=3)
        # It is kept up to date like any other
        near = self.modify_node(near, 1.02)
        far = self.modify_node(far, 1.004)
        self.update(grown)
        self.assertEqual(set(self.contents(grown)), {("node", inside.objId), ("node", far.objId)})
        self.assertEqual(self.compare(grown)[1], [])
        # A buffer of nothing leaves the boundary as it was
        self.assertEqual(self.info(self.buffered(OCTAGON, 0, "none"))["boundary"], self.info(plain)["boundary"])

    def test_buffer_joins_pieces_closer_than_twice_it(self):
        # The island is about 3 km from the octagon
        apart = self.buffered(OCTAGON_AND_ISLAND, 1000, "apart")
        self.assertEqual(self.info(apart)["shape"], "multipolygon")
        joined = self.buffered(OCTAGON_AND_ISLAND, 2000, "joined")
        self.assertEqual(self.info(joined)["shape"], "polygon")

    def test_buffers_that_are_refused(self):
        for wkt, metres, reason in ((OCTAGON, -5, "zero or more"),
                                    (OCTAGON, float("nan"), "zero or more"),
                                    ("POLYGON((179 0,179.99 0,179.99 1,179 1,179 0))", 5000, "The boundary with its buffer"),
                                    ("POINT(0 0)", 10, "polygon or a multipolygon")):
            with self.subTest(wkt=wkt, metres=metres):
                with self.assertRaisesRegex(Exception, reason):
                    self.buffered(wkt, metres)

    def test_buffer_from_the_commands_and_admin(self):
        from io import StringIO
        from unittest.mock import patch
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from replicate import extracts
        near = self.create_node(1.005, 0)
        def run(*args):
            out = StringIO()
            with patch.object(extracts, "get_pgmap", return_value=self.map):
                call_command(*args, stdout=out)
            return int(re.search(r"extract (\d+)", out.getvalue()).group(1))
        saved = run("saveextract", "--wkt", OCTAGON, "--buffer", "1000", "--name", "grown")
        self.assertIn(("node", near.objId), set(self.contents(saved)))
        with self.assertRaisesRegex(CommandError, "cannot be used with --bbox"):
            run("saveextract", "--bbox", "-1,-1,1,1", "--buffer", "1000")

        # An imported file takes the grown boundary in place of its own
        import os, tempfile
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, "plain.osm")
            t = self.map.GetTransaction("ACCESS SHARE")
            try:
                t.ExportExtractToFile(saved, "", path, True)
            finally:
                t.Abort()
            imported = run("importextract", "--in", path, "--wkt", OCTAGON, "--buffer", "1000", "--name", "imported")
            self.assertEqual(self.info(imported)["boundary"], self.info(saved)["boundary"])
            with self.assertRaisesRegex(CommandError, "needs one"):
                run("importextract", "--in", path, "--buffer", "1000")
        finally:
            for name in os.listdir(folder):
                os.remove(os.path.join(folder, name))
            os.rmdir(folder)

        # The admin form: a buffer needs a boundary
        from replicate.admin import DbExtractForm
        form = DbExtractForm({"west": "-1", "south": "-1", "east": "1", "north": "1", "buffer": "100"})
        self.assertFalse(form.is_valid())
        self.assertIn("buffer", form.errors)
        form = DbExtractForm({"boundary": OCTAGON, "buffer": "1000"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.wkt(), form.buffer_metres()), (OCTAGON, 1000))
        self.assertFalse(DbExtractForm({"boundary": OCTAGON, "buffer": "-1"}).is_valid())

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
            "POINT(0 0)": "polygon or a multipolygon",
            "LINESTRING(0 0,1 1)": "polygon or a multipolygon",
            # Pieces that overlap
            "MULTIPOLYGON(((0 0,2 0,2 2,0 0)),((1 0,3 0,3 2,1 0)))": "not a valid multipolygon",
            "MULTIPOLYGON(((170 0,175 0,175 1,170 0)),((-175 0,-170 0,-170 1,-175 0)))": "antimeridian",
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

class MultiPolygonBoundary(PolygonBoundary):
    """Saves extracts with the octagon and its island as their boundary."""
    boundary = OCTAGON_AND_ISLAND

    def test_schema_15_rebuilds_member_tables(self):
        # That test goes back to schema 14, which is refused while an extract
        # has a multipolygon (see test_multipolygons_need_schema_15); its
        # extract has the octagon alone
        self.boundary = OCTAGON
        super().test_schema_15_rebuilds_member_tables()

class MultiPolygonExtractTestCase(MultiPolygonBoundary, local.DbExtractTestCase):
    """Every extract test again, the boundary in two pieces."""

    def test_only_what_is_inside_a_piece(self):
        middle = self.create_node(0.5, 0.5)
        island = self.create_node(0.95, 0.95)
        # In the corner, but in neither the octagon nor the island
        corner = self.create_node(0.9, 0.93)
        beside = self.create_node(0.99, 0.99)
        # A way from the island out into the corner is in, with all its nodes
        out_a = self.create_node(0.91, 0.99)
        way = self.create_way([island, out_a])
        # A way in the corner alone is not
        corner_way = self.create_way([corner, beside])
        extract_id = self.save()
        self.assertEqual(set(self.contents(extract_id)), {("node", middle.objId), ("node", island.objId),
                                                          ("node", out_a.objId), ("way", way.objId)})
        self.assert_current(extract_id)
        described = self.info(extract_id)
        self.assertEqual((described["rectangular"], described["shape"], described["bbox"]),
                         (False, "multipolygon", [-1.0, -1.0, 1.0, 1.0]))
        self.assertTrue(described["boundary"].startswith("MULTIPOLYGON"))

        # Moving a node off the island takes the way out; moving one onto it brings in its way
        island = self.modify_node(island, 0.9)
        self.update(extract_id)
        self.assertEqual(set(self.contents(extract_id)), {("node", middle.objId)})
        self.assert_current(extract_id)
        corner = self.modify_node(corner, 0.93)
        self.update(extract_id)
        self.assertEqual(set(self.contents(extract_id)), {("node", middle.objId), ("node", corner.objId),
                                                          ("node", beside.objId), ("way", corner_way.objId)})
        self.assert_current(extract_id)
        self.assertEqual(self.compare(extract_id)[1], [])

    def test_extracts_page_says_multipolygon(self):
        from unittest.mock import patch
        from django.test import Client
        self.create_node(0.5, 0.5)
        self.save("islands")
        with patch("replicate.extracts.get_pgmap", return_value=self.map):
            page = Client().get("/replication/extracts").content.decode("utf-8")
        self.assertIn("multipolygon within -1.0, -1.0, 1.0, 1.0", page)

    def column_type(self):
        with self.db.cursor() as cursor:
            cursor.execute("SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                           "WHERE a.attrelid = %s::regclass AND a.attname = 'bbox'", [self.prefixes[1] + "extracts"])
            return cursor.fetchone()[0]

    def schema_version(self):
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT value FROM {} WHERE key='schema_version'").format(
                sql.Identifier(self.prefixes[1] + "meta")))
            return cursor.fetchone()[0]

    def test_multipolygons_need_schema_15(self):
        self.create_node(0.5, 0.5)
        self.assertEqual(self.column_type(), "geometry(Geometry,4326)")
        # Version 14 holds polygons only
        self.set_schema(14)
        self.assertEqual(self.column_type(), "geometry(Polygon,4326)")
        with self.assertRaises(Exception):
            self.save()
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            polygon = t.SaveExtractWkt(OCTAGON, "polygon")
            t.Commit()
        except BaseException:
            t.Abort()
            raise
        self.set_schema(15)
        self.assertEqual(self.column_type(), "geometry(Geometry,4326)")
        islands = self.save()
        self.assertEqual(self.info(polygon)["shape"], "polygon")
        # The database itself takes nothing but polygons and multipolygons
        with self.db.cursor() as cursor:
            with self.assertRaisesRegex(Exception, "extracts_bbox_shape"):
                cursor.execute(sql.SQL("UPDATE {} SET bbox=ST_GeomFromText('POINT(0 0)', 4326) WHERE id=%s").format(
                    sql.Identifier(self.prefixes[1] + "extracts")), [polygon])

        # Back to 14 is refused while an extract has a multipolygon, and changes nothing
        admin = self.map.GetAdmin("")
        error = pgmap.PgMapError()
        try:
            self.assertFalse(admin.CreateMapTables(0, 14, False, error))
        finally:
            admin.Abort()
            del admin
        self.assertIn("Extracts {} in {}extracts have a multipolygon boundary".format(islands, self.prefixes[1]), error.errStr)
        self.assertEqual((self.schema_version(), self.column_type()), ("15", "geometry(Geometry,4326)"))
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            t.DeleteExtract(islands, "")
            t.Commit()
        except BaseException:
            t.Abort()
            raise
        self.set_schema(14)
        self.assertEqual((self.schema_version(), self.column_type()), ("14", "geometry(Polygon,4326)"))
        self.assertEqual(self.info(polygon)["shape"], "polygon")

    def test_a_map_already_at_15_gains_multipolygons(self):
        # As a map that reached version 15 before boundaries could be multipolygons
        with self.db.cursor() as cursor:
            for prefix in self.prefixes:
                cursor.execute(sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}, ALTER COLUMN bbox TYPE GEOMETRY(Polygon, 4326)").format(
                    sql.Identifier(prefix + "extracts"), sql.Identifier(prefix + "extracts_bbox_shape")))
        self.assertEqual(self.column_type(), "geometry(Polygon,4326)")
        with self.assertRaises(Exception):
            self.save()
        # Setting the version it already has brings it up to date
        self.set_schema(pgmap.MAP_SCHEMA_VERSION)
        self.assertEqual(self.column_type(), "geometry(Geometry,4326)")
        self.create_node(0.95, 0.95)
        extract_id = self.save()
        self.assert_current(extract_id)

class MultiPolygonRemoteUpdateTestCase(MultiPolygonBoundary, remote.RemoteUpdateTestCase):
    """Every update over the web API again, the boundary in two pieces."""

# Only the tests that save, query or export with a boundary, or bring an
# extract up to date from another server, run again here
# (with both, only those that use both: the rest run in one or the other)
local.run_only_what_a_variant_changes(PolygonExtractTestCase)
local.run_only_what_a_variant_changes(MultiPolygonExtractTestCase)
local.run_only_what_a_variant_changes(PolygonRemoteUpdateTestCase,
    covered_by=(PolygonExtractTestCase, remote.RemoteUpdateTestCase))
local.run_only_what_a_variant_changes(MultiPolygonRemoteUpdateTestCase,
    covered_by=(MultiPolygonExtractTestCase, remote.RemoteUpdateTestCase))

# Not run again from this module: these are run where they are defined
del local, remote
