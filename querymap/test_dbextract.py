"""Stored extract integration tests through the Python/SWIG interface.

Run with: python manage.py test querymap.test_dbextract
Uses MAP_DATABASE, but creates and removes unique table prefixes. Django's
transaction rollback does not cover pgmap's separately committed transactions.
"""
import io
import time
import uuid
import xml.etree.ElementTree as ET
from unittest.mock import patch

import pgmap
import psycopg2
from psycopg2 import sql
from django.test import SimpleTestCase, Client
from django.urls import reverse

from changeset.views import upload_block
from pycrocosm import common
from pycrocosm.mapdb import make_connection_string


class DbExtractTestCase(SimpleTestCase):
    bbox = [-1.0, -1.0, 1.0, 1.0]

    def setUp(self):
        base = "extract_test_" + uuid.uuid4().hex[:12] + "_"
        self.prefixes = [base + kind + "_" for kind in ("static", "mod", "test")]
        self.connection_string = make_connection_string()
        self.db = psycopg2.connect(self.connection_string)
        self.db.autocommit = True
        self.addCleanup(self.db.close)
        # Seed schema zero: the migration bootstrap reads meta before creating it.
        with self.db.cursor() as cursor:
            for prefix in self.prefixes:
                cursor.execute(sql.SQL("CREATE TABLE {} (key TEXT, value TEXT)").format(
                    sql.Identifier(prefix + "meta")))
                cursor.execute(sql.SQL("INSERT INTO {} VALUES ('schema_version','0')").format(
                    sql.Identifier(prefix + "meta")))
        self.map = pgmap.PgMap(self.connection_string, self.prefixes[0],
                              self.prefixes[1], self.prefixes[1], self.prefixes[2])
        self.addCleanup(self.drop_tables)
        admin = self.map.GetAdmin("")
        error = pgmap.PgMapError()
        try:
            self.assertTrue(admin.CreateMapTables(0, 14, False, error), error.errStr)
            admin.Commit()
        except BaseException:
            admin.Abort()
            raise
        finally:
            del admin
        with self.db.cursor() as cursor:
            for prefix in self.prefixes:
                for kind in ("node", "way", "relation", "changeset", "uid"):
                    cursor.execute(sql.SQL("INSERT INTO {} (id,maxid) VALUES (%s,1)").format(
                        sql.Identifier(prefix + "nextids")), [kind])
        t = self.map.GetTransaction("EXCLUSIVE")
        try:
            changeset = pgmap.PgChangeset()
            changeset.uid = 1
            changeset.username = "extract-tester"
            changeset.is_open = True
            changeset.open_timestamp = int(time.time())
            self.changeset_id = t.CreateChangeset(changeset, error)
            self.assertGreater(self.changeset_id, 0, error.errStr)
            self.assertTrue(t.UpdateUsername(1, "extract-tester", error), error.errStr)
            t.Commit()
        except BaseException:
            t.Abort()
            raise

    def drop_tables(self):
        admin = self.map.GetAdmin("")
        error = pgmap.PgMapError()
        try:
            self.assertTrue(admin.DropMapTables(0, error), error.errStr)
            admin.Commit()
        except BaseException:
            admin.Abort()
            raise

    def upload(self, action, objects):
        """Use the production upload path, including activity and bbox tracking."""
        data = pgmap.OsmData()
        for obj in objects:
            obj.metaData.changeset = self.changeset_id
            if isinstance(obj, pgmap.OsmNode):
                data.nodes.append(obj)
            elif isinstance(obj, pgmap.OsmWay):
                data.ways.append(obj)
            else:
                data.relations.append(obj)
        maps = [pgmap.mapi64i64() for _ in range(3)]
        t = self.map.GetTransaction("EXCLUSIVE")
        try:
            result = upload_block(action, data, self.changeset_id, t,
                                  ET.Element("diffResult"), 1, "extract-tester",
                                  int(time.time()), *maps)
            self.assertIs(result, True, getattr(result, "content", result))
            t.Commit()
        except BaseException:
            t.Abort()
            raise
        # Return copies: vector elements must not outlive their SWIG owner.
        return ([pgmap.OsmNode(n) for n in data.nodes],
                [pgmap.OsmWay(w) for w in data.ways])

    def create_node(self, lon, lat=0):
        node = pgmap.OsmNode()
        node.objId = -1
        node.lon, node.lat = lon, lat
        node.tags["test"] = "original"
        return self.upload("create", [node])[0][0]

    def modify_node(self, node, lon=None):
        node = pgmap.OsmNode(node)
        if lon is not None:
            node.lon = lon
        node.tags["test"] = "modified"
        return self.upload("modify", [node])[0][0]

    def save(self, name="snapshot"):
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            extract_id = t.SaveExtract(self.bbox, name)
            t.Commit()
            return extract_id
        except BaseException:
            t.Abort()
            raise

    def update(self, extract_id=0, name=""):
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            result = t.UpdateExtractNodes(extract_id, name)
            t.Commit()
            return result
        except BaseException:
            t.Abort()
            raise

    def contents(self, extract_id=None, name=""):
        output = io.BytesIO()
        encoder = pgmap.PyOsmXmlEncode(output, common.xmlAttribs)
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            if extract_id is None:
                query = t.GetQueryMgr()
                self.assertGreaterEqual(query.Start(self.bbox, int(time.time()), encoder), 0)
                while True:
                    status = query.Continue()
                    self.assertGreaterEqual(status, 0)
                    if status == 1:
                        break
                del query
            else:
                t.ExportExtract(extract_id, name, encoder)
            t.Commit()
        except BaseException:
            t.Abort()
            raise
        return self.decode_contents(output.getvalue())

    @staticmethod
    def decode_contents(xml):
        # Compare objects, not document headers or serializer ordering. Preserve
        # member order and roles, and compare all metadata, tags and coordinates.
        result = {}
        for element in ET.fromstring(xml):
            if element.tag not in ("node", "way", "relation"):
                continue
            attrs = dict(element.attrib)
            for coordinate in ("lat", "lon"):
                if coordinate in attrs:
                    attrs[coordinate] = float(attrs[coordinate])
            children = [(child.tag, tuple(sorted(child.attrib.items()))) for child in element]
            members = [child for child in children if child[0] != "tag"]
            tags = sorted(child for child in children if child[0] == "tag")
            result[(element.tag, int(element.attrib["id"]))] = (attrs, members, tags)
        return result

    def download(self, url):
        with patch("replicate.views.get_pgmap", return_value=self.map):
            return Client().get(url)

    def test_download_extract_by_id_and_name_streams_all_batches(self):
        nodes = []
        for index in range(1005):
            node = pgmap.OsmNode()
            node.objId = -index - 1
            node.lon, node.lat = 0, 0
            nodes.append(node)
        created, _ = self.upload("create", nodes)
        way = pgmap.OsmWay()
        way.objId = -1
        way.refs.append(created[0].objId)
        way.refs.append(created[-1].objId)
        way = self.upload("create", [way])[1][0]
        relation = pgmap.OsmRelation()
        relation.objId = -1
        relation.refTypeStrs.append("way")
        relation.refIds.append(way.objId)
        relation.refRoles.append("outer")
        self.upload("create", [relation])
        extract_id = self.save("download snapshot")
        expected = self.contents(extract_id)
        self.assertEqual(len(expected), 1007)
        self.assert_current(extract_id)
        for url in (reverse("replication:download_extract_by_id", args=[extract_id]),
                    reverse("replication:download_extract_by_name") + "?name=download%20snapshot"):
            with self.subTest(url=url):
                response = self.download(url)
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.streaming)
                    self.assertEqual(response["Content-Type"], "application/xml")
                    self.assertIn("extract-{}.osm".format(extract_id), response["Content-Disposition"])
                    chunks = [chunk for chunk in response.streaming_content if chunk]
                    self.assertGreater(len(chunks), 1)
                    self.assertEqual(self.decode_contents(b"".join(chunks)), expected)
                finally:
                    response.close()

    def test_download_extract_errors(self):
        by_name = reverse("replication:download_extract_by_name")
        self.save("duplicate")
        self.save("duplicate")
        cases = [(by_name, 400), (by_name + "?name=missing", 404),
                 (by_name + "?name=duplicate", 400),
                 (reverse("replication:download_extract_by_id", args=[99999]), 404),
                 (reverse("replication:download_extract_by_id", args=[0]), 400),
                 (reverse("replication:download_extract_by_id", args=[1]) + "?name=duplicate", 400)]
        for url, status in cases:
            with self.subTest(url=url):
                response = self.download(url)
                self.assertEqual(response.status_code, status)
                response.close()
        self.assertEqual(Client().post(by_name).status_code, 405)

    def test_closing_download_early_releases_locks(self):
        self.create_node(0)
        extract_id = self.save()
        response = self.download(reverse("replication:download_extract_by_id", args=[extract_id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(next(iter(response.streaming_content)))
        response.close()
        # A separate connection must be able to acquire a conflicting lock.
        # NOWAIT prevents a leaked streaming transaction from hanging the test.
        with self.db.cursor() as cursor:
            cursor.execute("BEGIN")
            try:
                cursor.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE NOWAIT").format(
                    sql.Identifier(self.prefixes[1] + "extract_livenodes")))
            finally:
                cursor.execute("ROLLBACK")

    def checkpoint(self, extract_id):
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT edit_activity_id, atomic_edit_id FROM {} WHERE id=%s").format(
                sql.Identifier(self.prefixes[1] + "extracts")), [extract_id])
            return cursor.fetchone()

    def assert_current(self, extract_id):
        self.assertEqual(self.contents(extract_id), self.contents())
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT COALESCE(max(id),0), COALESCE(max(atomic_edit_id),0) FROM {}").format(
                sql.Identifier(self.prefixes[1] + "edit_activity")))
            self.assertEqual(self.checkpoint(extract_id), cursor.fetchone())

    def check_node_edit(self, action, inside):
        # Keep an unrelated node in every snapshot so outside edits must preserve
        # real contents, rather than merely leave an empty extract empty.
        anchor = self.create_node(-0.5)
        lon = 0.25 if inside else 2
        node = None if action == "create" else self.create_node(lon)
        extract_id = self.save()
        self.assert_current(extract_id)
        original = self.contents(extract_id)
        before = self.checkpoint(extract_id)

        if action == "create":
            node = self.create_node(lon)
        elif action == "modify":
            # Move within the same region as well as changing tags/version.
            node = self.modify_node(node, 0.5 if inside else 3)
        else:
            self.upload("delete", [node])

        self.assertEqual(self.contents(extract_id), original)
        self.update(extract_id)
        self.assert_current(extract_id)
        updated = self.contents(extract_id)
        self.assertEqual(updated[("node", anchor.objId)], original[("node", anchor.objId)])
        expected_ids = {("node", anchor.objId)}
        if inside and action != "delete":
            expected_ids.add(("node", node.objId))
        self.assertEqual(set(updated), expected_ids)
        if inside and action == "modify":
            attrs, _, tags = updated[("node", node.objId)]
            self.assertEqual(attrs["version"], "2")
            self.assertEqual(attrs["lon"], 0.5)
            self.assertIn(("tag", (("k", "test"), ("v", "modified"))), tags)
        if not inside:
            self.assertEqual(updated, original)
        after = self.checkpoint(extract_id)
        self.assertGreater(after[0], before[0])
        self.assertGreater(after[1], before[1])

    def test_create_node_inside_bbox(self):
        self.check_node_edit("create", inside=True)

    def test_create_node_outside_bbox(self):
        self.check_node_edit("create", inside=False)

    def test_modify_node_inside_bbox(self):
        self.check_node_edit("modify", inside=True)

    def test_modify_node_outside_bbox(self):
        self.check_node_edit("modify", inside=False)

    def test_delete_node_inside_bbox(self):
        self.check_node_edit("delete", inside=True)

    def test_delete_node_outside_bbox(self):
        self.check_node_edit("delete", inside=False)

    def test_node_create_modify_delete_and_bbox_crossings(self):
        kept = self.create_node(0)
        deleted = self.create_node(0.2)
        leaving = self.create_node(0.4)
        entering = self.create_node(2)
        extract_id = self.save()
        self.assert_current(extract_id)
        before = self.checkpoint(extract_id)
        old_contents = self.contents(extract_id)

        kept = self.modify_node(kept)
        self.upload("delete", [deleted])
        leaving = self.modify_node(leaving, 3)
        entering = self.modify_node(entering, 0.6)
        created = self.create_node(-0.5)
        self.assertEqual(self.contents(extract_id), old_contents)
        self.assertEqual(self.update(name="snapshot"), extract_id)
        self.assert_current(extract_id)
        objects = self.contents(extract_id)
        self.assertEqual(set(objects), {("node", n.objId) for n in (kept, entering, created)})
        self.assertEqual(objects[("node", kept.objId)][0]["version"], "2")
        after = self.checkpoint(extract_id)
        self.assertGreater(after[0], before[0])
        self.assertGreater(after[1], before[1])
        self.update(extract_id)
        self.assertEqual(self.checkpoint(extract_id), after)
        self.assert_current(extract_id)

    def test_node_moves_change_way_membership_and_completion_nodes(self):
        inside = self.create_node(0)
        outside = self.create_node(2)
        way = pgmap.OsmWay()
        way.objId = -1
        way.refs.append(inside.objId)
        way.refs.append(outside.objId)
        way = self.upload("create", [way])[1][0]
        extract_id = self.save()
        self.assert_current(extract_id)
        self.assertIn(("node", outside.objId), self.contents(extract_id))

        outside = self.modify_node(outside, 3)
        self.update(extract_id)
        self.assert_current(extract_id)
        self.assertEqual(self.contents(extract_id)[("node", outside.objId)][0]["lon"], 3)
        inside = self.modify_node(inside, 4)
        self.update(extract_id)
        self.assert_current(extract_id)
        self.assertEqual(self.contents(extract_id), {})
        outside = self.modify_node(outside, 0.5)
        self.update(extract_id)
        self.assert_current(extract_id)
        self.assertEqual(set(self.contents(extract_id)), {
            ("node", inside.objId), ("node", outside.objId), ("way", way.objId)})

    def test_updating_one_extract_does_not_change_another(self):
        node = self.create_node(0)
        first = self.save("first")
        second = self.save("second")
        original = self.contents(second)
        checkpoint = self.checkpoint(second)
        self.modify_node(node)
        self.update(first)
        self.assert_current(first)
        self.assertEqual(self.contents(second), original)
        self.assertEqual(self.checkpoint(second), checkpoint)
        self.update(second)
        self.assert_current(second)
        self.assertEqual(self.contents(0, "second"), self.contents(first))
