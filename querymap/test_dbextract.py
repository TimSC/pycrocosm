"""Stored extract integration tests through the Python/SWIG interface.

Run with: python manage.py test querymap.test_dbextract
Uses MAP_DATABASE, but creates and removes unique table prefixes. Django's
transaction rollback does not cover pgmap's separately committed transactions.
"""
import io
import re
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
    # Only for the lock that keeps two runs of the update command apart
    databases = {"default"}

    def setUp(self):
        self.fetched = [] # Addresses asked of the stand-in for another server
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
            self.assertTrue(admin.CreateMapTables(0, 15, False, error), error.errStr)
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
                [pgmap.OsmWay(w) for w in data.ways],
                [pgmap.OsmRelation(r) for r in data.relations])

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

    def create_way(self, nodes):
        way = pgmap.OsmWay()
        way.objId = -1
        for node in nodes:
            way.refs.append(node.objId)
        return self.upload("create", [way])[1][0]

    def modify_way(self, way, nodes):
        way = pgmap.OsmWay(way)
        way.refs.clear()
        for node in nodes:
            way.refs.append(node.objId)
        return self.upload("modify", [way])[1][0]

    def set_members(self, relation, members):
        relation.members.clear()
        for kind, obj, role in members:
            relation.AddMember(kind, obj.objId, role)

    def create_relation(self, members):
        relation = pgmap.OsmRelation()
        relation.objId = -1
        self.set_members(relation, members)
        return self.upload("create", [relation])[2][0]

    def modify_relation(self, relation, members):
        relation = pgmap.OsmRelation(relation)
        self.set_members(relation, members)
        relation.tags["test"] = "modified"
        return self.upload("modify", [relation])[2][0]

    def updated_ids(self, extract_id):
        """Update, check against a fresh map query and return the object keys."""
        self.update(extract_id)
        self.assert_current(extract_id)
        return set(self.contents(extract_id))

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
            result = t.UpdateExtract(extract_id, name)
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
        created = self.upload("create", nodes)[0]
        way = pgmap.OsmWay()
        way.objId = -1
        way.refs.append(created[0].objId)
        way.refs.append(created[-1].objId)
        way = self.upload("create", [way])[1][0]
        relation = pgmap.OsmRelation()
        relation.objId = -1
        relation.AddMember("way", way.objId, "outer")
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
                    # The header says which edits the extract is current to
                    root = ET.fromstring(b"".join(chunks))
                    self.assertEqual((int(root.get("edit_activity_id")), int(root.get("atomic_edit_id"))),
                                     self.checkpoint(extract_id))
                    self.assertEqual(root.get("generator"), common.xmlAttribs["generator"])
                finally:
                    response.close()

        # They are the extract's own, not the map's latest: a later edit does not change them
        saved_at = self.checkpoint(extract_id)
        self.create_node(0.5)
        response = self.download(reverse("replication:download_extract_by_id", args=[extract_id]))
        root = ET.fromstring(b"".join(response.streaming_content))
        self.assertEqual((int(root.get("edit_activity_id")), int(root.get("atomic_edit_id"))), saved_at)
        self.update(extract_id)
        self.assertGreater(self.checkpoint(extract_id)[0], saved_at[0])
        response = self.download(reverse("replication:download_extract_by_id", args=[extract_id]))
        root = ET.fromstring(b"".join(response.streaming_content))
        self.assertEqual((int(root.get("edit_activity_id")), int(root.get("atomic_edit_id"))),
                         self.checkpoint(extract_id))

        # An extract with no checkpoint is still downloadable, without them
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("UPDATE {} SET edit_activity_id=NULL, atomic_edit_id=NULL WHERE id=%s").format(
                sql.Identifier(self.prefixes[1] + "extracts")), [extract_id])
        response = self.download(reverse("replication:download_extract_by_id", args=[extract_id]))
        self.assertEqual(response.status_code, 200)
        root = ET.fromstring(b"".join(response.streaming_content))
        self.assertIsNone(root.get("edit_activity_id"))
        self.assertIsNone(root.get("atomic_edit_id"))
        self.assertEqual(len(root.findall("node")), 1006) # The 1005 first made and the one added since

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

    def test_way_create_modify_and_delete(self):
        inside = self.create_node(0)
        near = self.create_node(2)
        far = self.create_node(3)
        other = self.create_node(4)
        extract_id = self.save()
        self.assertEqual(set(self.contents(extract_id)), {("node", inside.objId)})

        way = self.create_way([inside, near])
        self.assertEqual(self.updated_ids(extract_id), {
            ("node", inside.objId), ("node", near.objId), ("way", way.objId)})
        # Changing membership swaps the completion node.
        way = self.modify_way(way, [inside, far])
        self.assertEqual(self.updated_ids(extract_id), {
            ("node", inside.objId), ("node", far.objId), ("way", way.objId)})
        self.assertEqual(self.contents(extract_id)[("way", way.objId)][0]["version"], "2")
        # A way with no remaining node in the bbox leaves the extract.
        way = self.modify_way(way, [far, other])
        self.assertEqual(self.updated_ids(extract_id), {("node", inside.objId)})
        way = self.modify_way(way, [other, inside])
        self.assertEqual(self.updated_ids(extract_id), {
            ("node", inside.objId), ("node", other.objId), ("way", way.objId)})
        self.upload("delete", [way])
        self.assertEqual(self.updated_ids(extract_id), {("node", inside.objId)})

    def test_way_and_its_nodes_created_together(self):
        extract_id = self.save()
        nodes = []
        for index, lon in enumerate((0.1, 0.2, 5)):
            node = pgmap.OsmNode()
            node.objId = -index - 1
            node.lon, node.lat = lon, 0
            nodes.append(node)
        way = pgmap.OsmWay()
        way.objId = -1
        for node in nodes:
            way.refs.append(node.objId)
        self.upload("create", nodes + [way])
        ids = self.updated_ids(extract_id)
        self.assertEqual(len(ids), 4)
        self.assertEqual(len([key for key in ids if key[0] == "way"]), 1)

    def test_relation_create_modify_and_delete(self):
        inside = self.create_node(0)
        outside = self.create_node(2)
        distant = self.create_node(3)
        way = self.create_way([outside, distant])
        extract_id = self.save()
        base = {("node", inside.objId)}
        self.assertEqual(set(self.contents(extract_id)), base)

        # Selected through a node member; other members are not completed.
        relation = self.create_relation([("node", inside, "stop"), ("way", way, "route")])
        self.assertEqual(self.updated_ids(extract_id), base | {("relation", relation.objId)})
        attrs, members, _ = self.contents(extract_id)[("relation", relation.objId)]
        self.assertEqual([dict(member[1])["role"] for member in members], ["stop", "route"])
        # No member inside the extract: the relation leaves.
        relation = self.modify_relation(relation, [("way", way, "route")])
        self.assertEqual(self.updated_ids(extract_id), base)
        # Selected through a way that enters the extract.
        way = self.modify_way(way, [outside, inside])
        self.assertEqual(self.updated_ids(extract_id), base | {
            ("node", outside.objId), ("way", way.objId), ("relation", relation.objId)})
        # Selected through a completion node outside the bbox.
        relation = self.modify_relation(relation, [("node", outside, "stop")])
        self.assertEqual(self.updated_ids(extract_id), base | {
            ("node", outside.objId), ("way", way.objId), ("relation", relation.objId)})
        self.assertEqual(self.contents(extract_id)[("relation", relation.objId)][0]["version"], "3")
        # Relations of relations are not followed.
        parent = self.create_relation([("relation", relation, "")])
        self.assertNotIn(("relation", parent.objId), self.updated_ids(extract_id))
        self.upload("delete", [parent])
        self.upload("delete", [relation])
        self.assertEqual(self.updated_ids(extract_id), base | {
            ("node", outside.objId), ("way", way.objId)})

    def test_mixed_edits_in_one_update(self):
        first = self.create_node(0)
        second = self.create_node(0.5)
        outside = self.create_node(2)
        way = self.create_way([first, outside])
        relation = self.create_relation([("way", way, "outer")])
        extract_id = self.save()
        self.assert_current(extract_id)
        before = self.checkpoint(extract_id)

        first = self.modify_node(first, 5)
        way = self.modify_way(way, [second, outside, first])
        relation = self.modify_relation(relation, [("node", first, ""), ("way", way, "outer")])
        extra = self.create_relation([("node", second, "")])
        self.assertEqual(self.updated_ids(extract_id), {
            ("node", first.objId), ("node", second.objId), ("node", outside.objId),
            ("way", way.objId), ("relation", relation.objId), ("relation", extra.objId)})
        after = self.checkpoint(extract_id)
        self.assertGreater(after[0], before[0] + 3)
        self.assertGreater(after[1], before[1])

    def store_static(self, nodes, ways=(), relations=()):
        """Store objects in the static baseline, as an imported planet would be."""
        data = pgmap.OsmData()
        for obj in list(nodes) + list(ways) + list(relations):
            obj.metaData.version = 1
            obj.metaData.changeset = self.changeset_id
            obj.metaData.uid = 1
            obj.metaData.username = "extract-tester"
            obj.metaData.timestamp = int(time.time())
        for node in nodes:
            data.nodes.append(node)
        for way in ways:
            data.ways.append(way)
        for relation in relations:
            data.relations.append(relation)
        maps = [pgmap.mapi64i64() for _ in range(3)]
        error = pgmap.PgMapError()
        t = self.map.GetTransaction("EXCLUSIVE")
        try:
            self.assertTrue(t.StoreObjects(data, *maps, True, error), error.errStr)
            t.Commit()
        except BaseException:
            t.Abort()
            raise

    def test_static_objects_overridden_by_edits(self):
        nodes = []
        for index, lon in enumerate((0, 2, 0.5, 3)):
            node = pgmap.OsmNode()
            node.objId = 5000 + index
            node.lon, node.lat = lon, 0
            nodes.append(node)
        ways = []
        for index, refs in enumerate(((0, 1), (2, 3))):
            way = pgmap.OsmWay()
            way.objId = 6000 + index
            for ref in refs:
                way.refs.append(nodes[ref].objId)
            ways.append(way)
        relation = pgmap.OsmRelation()
        relation.objId = 7000
        self.set_members(relation, [("way", ways[0], "outer"), ("node", nodes[2], "")])
        self.store_static(nodes, ways, [relation])
        everything = ({("node", node.objId) for node in nodes} |
                      {("way", way.objId) for way in ways} | {("relation", 7000)})
        extract_id = self.save()
        self.assert_current(extract_id)
        self.assertEqual(set(self.contents(extract_id)), everything)

        # An unrelated edit must keep unedited static objects in the extract.
        self.create_node(9)
        self.assertEqual(self.updated_ids(extract_id), everything)
        # Moving a static node out removes its otherwise unedited static parents.
        self.modify_node(nodes[0], 4)
        self.assertEqual(self.updated_ids(extract_id), {
            ("node", 5002), ("node", 5003), ("way", 6001), ("relation", 7000)})
        # The stale static membership of an edited way must not select it.
        self.modify_way(ways[1], [nodes[1], nodes[3]])
        self.assertEqual(self.updated_ids(extract_id), {("node", 5002), ("relation", 7000)})
        self.upload("delete", [relation])
        self.assertEqual(self.updated_ids(extract_id), {("node", 5002)})
        self.upload("delete", [nodes[2]])
        self.assertEqual(self.updated_ids(extract_id), set())

    def test_bbox_query_mode(self):
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("INSERT INTO {} (key, value) VALUES ('useBboxInQuery','1')").format(
                sql.Identifier(self.prefixes[1] + "meta")))
        west = self.create_node(-2)
        east = self.create_node(2)
        far = self.create_node(5)
        extract_id = self.save()
        self.assertEqual(self.contents(extract_id), {})
        # The way crosses the bbox without having a node inside it.
        way = self.create_way([west, east])
        relation = self.create_relation([("way", way, "")])
        self.assertEqual(self.updated_ids(extract_id), {
            ("node", west.objId), ("node", east.objId), ("way", way.objId),
            ("relation", relation.objId)})
        way = self.modify_way(way, [east, far])
        self.assertEqual(self.updated_ids(extract_id), set())

    # Moving a node never edits its parent ways, so the update must work out
    # for itself which unchanged ways enter, stay in or leave the extract.

    def way_move_fixture(self, lons):
        """An anchor node inside the bbox plus a way with nodes at these longitudes."""
        anchor = self.create_node(-0.5)
        nodes = [self.create_node(lon) for lon in lons]
        way = self.create_way(nodes)
        extract_id = self.save()
        self.assert_current(extract_id)
        return anchor, nodes, way, extract_id

    def way_keys(self, nodes, way):
        return {("node", node.objId) for node in nodes} | {("way", way.objId)}

    def assert_way_unedited(self, extract_id, way):
        attrs, members, _ = self.contents(extract_id)[("way", way.objId)]
        self.assertEqual(attrs["version"], "1")
        self.assertEqual([int(dict(member[1])["ref"]) for member in members], list(way.refs))

    def test_node_moved_outside_keeps_outside_way_out(self):
        anchor, nodes, way, extract_id = self.way_move_fixture((2, 3))
        original = self.contents(extract_id)
        self.assertEqual(set(original), {("node", anchor.objId)})
        before = self.checkpoint(extract_id)

        # Starts outside the extract and finishes outside it.
        self.modify_node(nodes[0], 4)
        self.assertEqual(self.updated_ids(extract_id), {("node", anchor.objId)})
        self.assertEqual(self.contents(extract_id), original)
        self.assertGreater(self.checkpoint(extract_id)[0], before[0])
        # Passing from one side of the bbox to the other does not select it either.
        self.modify_node(nodes[1], -3)
        self.assertEqual(self.updated_ids(extract_id), {("node", anchor.objId)})
        self.assertEqual(self.contents(extract_id), original)

    def test_node_moved_within_bbox_keeps_parent_way(self):
        anchor, nodes, way, extract_id = self.way_move_fixture((0, 2))
        expected = self.way_keys(nodes, way) | {("node", anchor.objId)}
        self.assertEqual(set(self.contents(extract_id)), expected)

        # The moved node finishes inside the extract, so its parent way stays.
        self.modify_node(nodes[0], 0.5)
        self.assertEqual(self.updated_ids(extract_id), expected)
        attrs = self.contents(extract_id)[("node", nodes[0].objId)][0]
        self.assertEqual((attrs["lon"], attrs["version"]), (0.5, "2"))
        self.assert_way_unedited(extract_id, way)

    def test_completion_node_moved_outside_keeps_parent_way(self):
        anchor, nodes, way, extract_id = self.way_move_fixture((0, 2))
        expected = self.way_keys(nodes, way) | {("node", anchor.objId)}

        # The way's outside node moves but remains needed to complete the way.
        self.modify_node(nodes[1], 6)
        self.assertEqual(self.updated_ids(extract_id), expected)
        attrs = self.contents(extract_id)[("node", nodes[1].objId)][0]
        self.assertEqual((attrs["lon"], attrs["version"]), (6, "2"))
        self.assert_way_unedited(extract_id, way)

    def test_node_moved_out_keeps_way_with_another_node_inside(self):
        anchor, nodes, way, extract_id = self.way_move_fixture((0, 0.5, 2))
        expected = self.way_keys(nodes, way) | {("node", anchor.objId)}

        # Another node still selects the way; the moved node stays as completion.
        self.modify_node(nodes[0], 5)
        self.assertEqual(self.updated_ids(extract_id), expected)
        self.assertEqual(self.contents(extract_id)[("node", nodes[0].objId)][0]["lon"], 5)
        self.assert_way_unedited(extract_id, way)

    def test_node_moved_in_adds_outside_way(self):
        anchor, nodes, way, extract_id = self.way_move_fixture((2, 3))
        self.assertEqual(set(self.contents(extract_id)), {("node", anchor.objId)})

        # The unedited way enters along with its other, still outside, node.
        self.modify_node(nodes[0], 0.5)
        self.assertEqual(self.updated_ids(extract_id),
                         self.way_keys(nodes, way) | {("node", anchor.objId)})
        self.assert_way_unedited(extract_id, way)

    def test_only_inside_node_moved_out_removes_way(self):
        anchor, nodes, way, extract_id = self.way_move_fixture((0, 2))
        self.assertIn(("way", way.objId), self.contents(extract_id))

        # The way and both its nodes leave; nothing is deleted from the map.
        self.modify_node(nodes[0], 5)
        self.assertEqual(self.updated_ids(extract_id), {("node", anchor.objId)})
        self.assertEqual(self.contents(extract_id)[("node", anchor.objId)][0]["version"], "1")

    def test_moved_node_shared_by_inside_and_outside_ways(self):
        anchor = self.create_node(-0.5)
        shared = self.create_node(0)
        inside = self.create_node(0.5)
        outside = self.create_node(3)
        kept = self.create_way([shared, inside])
        dropped = self.create_way([shared, outside])
        extract_id = self.save()
        self.assertEqual(set(self.contents(extract_id)), {
            ("node", anchor.objId), ("node", shared.objId), ("node", inside.objId),
            ("node", outside.objId), ("way", kept.objId), ("way", dropped.objId)})

        # Only the way with another node inside survives the shared node leaving.
        shared = self.modify_node(shared, 4)
        self.assertEqual(self.updated_ids(extract_id), {
            ("node", anchor.objId), ("node", shared.objId), ("node", inside.objId),
            ("way", kept.objId)})
        shared = self.modify_node(shared, 0.2)
        self.assertEqual(self.updated_ids(extract_id), {
            ("node", anchor.objId), ("node", shared.objId), ("node", inside.objId),
            ("node", outside.objId), ("way", kept.objId), ("way", dropped.objId)})

    def test_node_moves_add_and_remove_unedited_relations(self):
        anchor = self.create_node(-0.5)
        first = self.create_node(2)
        second = self.create_node(3)
        lone = self.create_node(4)
        way = self.create_way([first, second])
        by_way = self.create_relation([("way", way, "outer")])
        by_node = self.create_relation([("node", lone, "stop")])
        by_completion = self.create_relation([("node", second, "stop")])
        parent = self.create_relation([("relation", by_way, "")])
        extract_id = self.save()
        base = {("node", anchor.objId)}
        self.assertEqual(set(self.contents(extract_id)), base)

        # A member node moving in selects its relation directly.
        lone = self.modify_node(lone, 0.5)
        self.assertEqual(self.updated_ids(extract_id), base | {
            ("node", lone.objId), ("relation", by_node.objId)})
        # A way entering brings relations of the way and of its completion node,
        # but not a relation that only contains another relation.
        first = self.modify_node(first, 0)
        entered = base | {
            ("node", lone.objId), ("relation", by_node.objId),
            ("node", first.objId), ("node", second.objId), ("way", way.objId),
            ("relation", by_way.objId), ("relation", by_completion.objId)}
        self.assertEqual(self.updated_ids(extract_id), entered)
        self.assertNotIn(("relation", parent.objId), entered)
        for relation in (by_way, by_node, by_completion):
            self.assertEqual(self.contents(extract_id)[("relation", relation.objId)][0]["version"], "1")
        # Moving the nodes back out removes all of them again.
        self.modify_node(first, 2)
        self.modify_node(lone, 4)
        self.assertEqual(self.updated_ids(extract_id), base)

    def test_bbox_query_mode_node_moves(self):
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("INSERT INTO {} (key, value) VALUES ('useBboxInQuery','1')").format(
                sql.Identifier(self.prefixes[1] + "meta")))
        west = self.create_node(-2)
        east = self.create_node(-3)
        way = self.create_way([west, east])
        relation = self.create_relation([("way", way, "")])
        extract_id = self.save()
        self.assertEqual(self.contents(extract_id), {})
        # Stretching the unedited way across the bbox selects it and its relation.
        east = self.modify_node(east, 2)
        self.assertEqual(self.updated_ids(extract_id), {
            ("node", west.objId), ("node", east.objId), ("way", way.objId),
            ("relation", relation.objId)})
        self.modify_node(east, -3)
        self.assertEqual(self.updated_ids(extract_id), set())

    def relation_members(self, extract_id, relation):
        _, members, _ = self.contents(extract_id)[("relation", relation.objId)]
        return [(dict(m[1])["type"], int(dict(m[1])["ref"]), dict(m[1])["role"]) for m in members]

    def test_relation_edited_while_staying_in_extract(self):
        inside = self.create_node(0)
        other = self.create_node(0.5)
        outside = self.create_node(3)
        way = self.create_way([other, outside])
        relation = self.create_relation([("node", inside, "stop")])
        extract_id = self.save()
        expected = {("node", inside.objId), ("node", other.objId), ("node", outside.objId),
                    ("way", way.objId), ("relation", relation.objId)}
        self.assertEqual(set(self.contents(extract_id)), expected)
        self.assertEqual(self.relation_members(extract_id, relation),
                         [("node", inside.objId, "stop")])
        stale = self.contents(extract_id)[("relation", relation.objId)]

        # Members, order, roles and tags change; the relation remains selected.
        relation = self.modify_relation(relation, [
            ("way", way, "route"), ("node", inside, "platform"), ("node", outside, "stop")])
        self.assertEqual(self.contents(extract_id)[("relation", relation.objId)], stale)
        self.assertEqual(self.updated_ids(extract_id), expected)
        attrs, _, tags = self.contents(extract_id)[("relation", relation.objId)]
        self.assertEqual(attrs["version"], "2")
        self.assertIn(("tag", (("k", "test"), ("v", "modified"))), tags)
        self.assertEqual(self.relation_members(extract_id, relation), [
            ("way", way.objId, "route"), ("node", inside.objId, "platform"),
            ("node", outside.objId, "stop")])
        # Stored membership rows must be replaced, not left from the old version.
        with self.db.cursor() as cursor:
            counts = []
            for suffix in ("n", "w", "r"):
                cursor.execute(sql.SQL("SELECT version, index, member FROM {} WHERE extract_id=%s "
                                       "AND id=%s ORDER BY index").format(
                    sql.Identifier(self.prefixes[1] + "extract_relation_mems_" + suffix)),
                    [extract_id, relation.objId])
                counts.append(cursor.fetchall())
        self.assertEqual(counts, [[(2, 1, inside.objId), (2, 2, outside.objId)],
                                  [(2, 0, way.objId)], []])

        # Dropping a member that is not in the extract any other way changes
        # only the relation; a member node moving does not edit the relation.
        relation = self.modify_relation(relation, [("node", inside, "platform")])
        self.modify_node(inside, 0.25)
        self.assertEqual(self.updated_ids(extract_id), expected)
        self.assertEqual(self.contents(extract_id)[("relation", relation.objId)][0]["version"], "3")
        self.assertEqual(self.relation_members(extract_id, relation),
                         [("node", inside.objId, "platform")])

    @staticmethod
    def differences(result):
        return [(d.type, d.objId, d.extractVersion, d.queryVersion) for d in result.differences]

    def compare(self, extract_id=0, name=""):
        result = pgmap.ExtractComparison()
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            t.CompareExtract(extract_id, name, result)
        finally:
            t.Abort()
        return result, self.differences(result)

    def compare_all(self):
        results = pgmap.vectorextractcomparison()
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            t.CompareAllExtracts(results)
        finally:
            t.Abort()
        return [pgmap.ExtractComparison(result) for result in results]

    def test_compare_extract_with_query(self):
        inside = self.create_node(0)
        outside = self.create_node(2)
        way = self.create_way([inside, outside])
        relation = self.create_relation([("way", way, "")])
        extract_id = self.save()
        result, differences = self.compare(extract_id)
        self.assertTrue(result.Matches())
        self.assertEqual(differences, [])
        self.assertEqual(result.extractId, extract_id)
        self.assertEqual(list(result.bbox), self.bbox)
        self.assertEqual(list(result.extractCounts), [2, 1, 1])
        self.assertEqual(list(result.queryCounts), [2, 1, 1])
        self.assertFalse(result.pendingActivity)
        self.assertFalse(result.queryModeDiffers)

        # Edits not yet applied to the extract show up as each kind of difference.
        inside = self.modify_node(inside)
        added = self.create_node(0.5)
        self.upload("delete", [relation])
        result, differences = self.compare(name="snapshot")
        self.assertFalse(result.Matches())
        self.assertTrue(result.pendingActivity)
        self.assertEqual(differences, [("node", inside.objId, 1, 2),
                                       ("node", added.objId, 0, 1),
                                       ("relation", relation.objId, 1, 0)])
        self.assertEqual((result.versionMismatches, result.missingFromExtract, result.notInQuery),
                         (1, 1, 1))
        self.assertEqual(list(result.extractCounts), [2, 1, 1])
        self.assertEqual(list(result.queryCounts), [3, 1, 0])
        self.assertEqual(result.NumDifferences(), 3)

        self.update(extract_id)
        result, differences = self.compare(extract_id)
        self.assertTrue(result.Matches())
        self.assertFalse(result.pendingActivity)
        self.assertEqual(list(result.extractCounts), [3, 1, 0])
        # Comparing must not write to the extract or advance its checkpoint.
        self.assert_current(extract_id)

    def test_compare_detects_damaged_extract(self):
        inside = self.create_node(0)
        outside = self.create_node(2)
        way = self.create_way([inside, outside])
        extract_id = self.save()
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("DELETE FROM {} WHERE extract_id=%s AND id=%s").format(
                sql.Identifier(self.prefixes[1] + "extract_liveways")), [extract_id, way.objId])
            cursor.execute(sql.SQL("UPDATE {} SET version=7 WHERE extract_id=%s AND id=%s").format(
                sql.Identifier(self.prefixes[1] + "extract_livenodes")), [extract_id, outside.objId])
        result, differences = self.compare(extract_id)
        self.assertFalse(result.pendingActivity)
        self.assertEqual(differences, [("node", outside.objId, 7, 1), ("way", way.objId, 0, 1)])

    def test_compare_extract_errors(self):
        with self.assertRaisesRegex(RuntimeError, "Extract not found"):
            self.compare(12345)
        with self.assertRaises(Exception):
            self.compare()

    def test_compare_all_extracts(self):
        self.assertEqual(self.compare_all(), [])
        node = self.create_node(0)
        first = self.save("first")
        second = self.save("second")
        results = self.compare_all()
        self.assertEqual([result.extractId for result in results], [first, second])
        self.assertTrue(all(result.Matches() for result in results))

        # Only the extract that was brought up to date matches afterwards.
        node = self.modify_node(node)
        self.update(second)
        stale, current = self.compare_all()
        self.assertEqual((stale.extractId, current.extractId), (first, second))
        self.assertFalse(stale.Matches())
        self.assertTrue(stale.pendingActivity)
        self.assertEqual(self.differences(stale), [("node", node.objId, 1, 2)])
        self.assertTrue(current.Matches())
        self.assertFalse(current.pendingActivity)
        self.update(first)
        self.assertTrue(all(result.Matches() for result in self.compare_all()))

    def test_admin_lists_extracts(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from replicate.models import DbExtract

        class Staff:
            # Stands in for a logged-in staff user; no Django database needed.
            is_active = is_staff = is_authenticated = True
            is_superuser = is_anonymous = False
            pk = id = 1
            def has_perm(self, perm, obj=None): return False
            def has_module_perms(self, app_label): return False
            def get_username(self): return "staff"
            def get_short_name(self): return "staff"
            def has_usable_password(self): return True

        model_admin = admin.site._registry[DbExtract]
        url = reverse("admin:replicate_dbextract_changelist")

        def page(user=Staff()):
            request = RequestFactory().get(url)
            request.user = user
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                response = model_admin.changelist_view(request)
                return response, response.render().content.decode("utf-8")

        self.assertIn("No extracts are stored", page()[1])
        inside = self.create_node(0)
        outside = self.create_node(2)
        self.create_way([inside, outside])
        first = self.save("first <extract>")
        self.modify_node(inside)
        second = self.save("")
        response, html = page()
        self.assertEqual(response.status_code, 200)
        rows = response.context_data["extracts"]
        self.assertEqual([row["id"] for row in rows], [first, second])
        self.assertEqual([row["name"] for row in rows], ["first <extract>", ""])
        self.assertEqual([row["up_to_date"] for row in rows], [False, True])
        self.assertEqual(rows[0]["bbox"], self.bbox)
        self.assertEqual(rows[0]["query_mode"], "membership")
        # Object counts are left to the detail page.
        self.assertFalse({"nodes", "ways", "relations"} & set(rows[0]))
        self.assertNotIn("Nodes", html)
        detail_url = reverse("admin:replicate_dbextract_detail", args=[first])
        self.assertEqual(detail_url, url + "{}/".format(first))
        self.assertIn('href="{}"'.format(detail_url), html)

        def detail(extract_id, user=Staff()):
            request = RequestFactory().get(detail_url)
            request.user = user
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                response = model_admin.detail_view(request, extract_id)
                return response, response.render().content.decode("utf-8")

        response, page_html = detail(first)
        self.assertEqual(response.status_code, 200)
        shown = response.context_data["extract"]
        self.assertEqual((shown["nodes"], shown["ways"], shown["relations"]), (2, 1, 0))
        self.assertEqual({key: shown[key] for key in rows[0]}, rows[0])
        for element, count in (("extract_nodes", 2), ("extract_ways", 1), ("extract_relations", 0)):
            self.assertIn('<td id="{}">{}</td>'.format(element, count), page_html)
        self.assertIn('<td id="extract_auto_update">disabled</td>', page_html)
        self.assertIn('<td id="extract_update_url">this map</td>', page_html)
        self.assertIn("first &lt;extract&gt;", page_html)
        self.assertIn('href="{}"'.format(reverse("replication:download_extract_gz_by_id", args=[first])), page_html)
        for route in ("download_extract_o5m_gz_by_id", "download_extract_pbf_by_id"):
            self.assertIn('href="{}"'.format(reverse("replication:" + route, args=[first])), page_html)
        # Read-only staff get no update or delete controls.
        self.assertNotIn("/update/", page_html)
        self.assertNotIn("/delete/", page_html)
        from django.http import Http404
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(Http404):
            detail(99999)
        outsider = Staff()
        outsider.is_staff = False
        with self.assertRaises(PermissionDenied):
            detail(first, outsider)
        self.assertEqual(self.checkpoint(first), (rows[0]["edit_activity_id"], rows[0]["atomic_edit_id"]))
        self.assertLess(abs(time.time() - rows[0]["performed_at"].timestamp()), 300)
        self.assertIn("first &lt;extract&gt;", html)
        self.assertIn('href="{}"'.format(reverse("replication:download_extract_gz_by_id", args=[first])), html)
        for route in ("download_extract_o5m_gz_by_id", "download_extract_pbf_by_id"):
            self.assertIn('href="{}"'.format(reverse("replication:" + route, args=[first])), html)
        # Read only for staff; hidden from everyone else.
        request = RequestFactory().get(url)
        request.user = Staff()
        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        request.user.is_staff = False
        self.assertFalse(model_admin.has_module_permission(request))

    def test_admin_creates_extract(self):
        from django.contrib import admin
        from django.contrib.messages.storage.cookie import CookieStorage
        from django.core.exceptions import PermissionDenied
        from django.test import RequestFactory
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

        class Staff(Superuser):
            is_superuser = False
            def has_perm(self, perm, obj=None): return False

        model_admin = admin.site._registry[DbExtract]
        url = reverse("admin:replicate_dbextract_add")

        def add(data=None, user=Superuser()):
            request = RequestFactory().post(url, data) if data is not None else RequestFactory().get(url)
            request.user = user
            request._messages = CookieStorage(request)
            # As the test client does; RequestFactory posts carry no CSRF token.
            request._dont_enforce_csrf_checks = True
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                response = model_admin.add_view(request)
            if response.status_code == 200:
                response.render()
            return request, response

        def stored():
            with self.db.cursor() as cursor:
                cursor.execute(sql.SQL("SELECT id, name FROM {} ORDER BY id").format(
                    sql.Identifier(self.prefixes[1] + "extracts")))
                return cursor.fetchall()

        inside = self.create_node(0)
        outside = self.create_node(2)
        way = self.create_way([inside, outside])
        valid = {"name": " admin made ", "west": "-1", "south": "-1", "east": "1", "north": "1"}

        _, response = add()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="west"', response.content)
        self.assertIn(b"csrfmiddlewaretoken", response.content)

        # Rejected input stores nothing.
        for changes, field in (({"east": "-2"}, "east"), ({"north": "-1"}, "north"),
                               ({"west": "-181"}, "west"), ({"south": "abc"}, "south"),
                               ({"north": ""}, "north")):
            with self.subTest(changes=changes):
                _, response = add(dict(valid, **changes))
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context_data["form"].errors)
        with self.assertRaises(PermissionDenied):
            add(valid, Staff())
        with self.assertRaises(PermissionDenied):
            add(None, Staff())
        # Without a CSRF token the post is refused before anything is stored.
        request = RequestFactory().post(url, valid)
        request.user = Superuser()
        with patch("replicate.extracts.get_pgmap", return_value=self.map):
            self.assertEqual(model_admin.add_view(request).status_code, 403)
        self.assertEqual(stored(), [])

        request, response = add(valid)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("admin:replicate_dbextract_changelist"))
        (extract_id, name), = stored()
        self.assertEqual(name, "admin made")
        self.assertEqual([str(message) for message in request._messages],
                         ["Saved database extract {}.".format(extract_id)])
        self.assert_current(extract_id)
        self.assertEqual(set(self.contents(extract_id)), {
            ("node", inside.objId), ("node", outside.objId), ("way", way.objId)})

        # Names must stay unique; unnamed extracts may repeat.
        _, response = add(valid)
        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context_data["form"].errors)
        self.assertEqual(len(stored()), 1)
        for _ in range(2):
            self.assertEqual(add(dict(valid, name=""))[1].status_code, 302)
        self.assertEqual([row[1] for row in stored()], ["admin made", "", ""])
        # The list offers the add link only to those who may use it.
        for user, offered in ((Superuser(), True), (Staff(), False)):
            request = RequestFactory().get("/")
            request.user = user
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                html = model_admin.changelist_view(request).render().content.decode("utf-8")
            self.assertEqual(url in html, offered)

    def test_admin_updates_and_deletes_extracts(self):
        from django.contrib import admin
        from django.contrib.messages.storage.cookie import CookieStorage
        from django.core.exceptions import PermissionDenied
        from django.http import Http404
        from django.test import RequestFactory
        from replicate.models import DbExtract

        class User:
            is_active = is_staff = is_authenticated = True
            is_superuser = is_anonymous = False
            pk = id = 1
            def __init__(self, *perms): self.perms = {"replicate." + perm for perm in perms}
            def has_perm(self, perm, obj=None): return perm in self.perms
            def has_module_perms(self, app_label): return True
            def get_username(self): return "staff"
            def get_short_name(self): return "staff"
            def has_usable_password(self): return True

        model_admin = admin.site._registry[DbExtract]

        def call(view, extract_id, user, method="post", csrf=True):
            request = getattr(RequestFactory(), method)("/")
            request.user = user
            request._messages = CookieStorage(request)
            request._dont_enforce_csrf_checks = csrf
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                response = view(request, extract_id)
                if response.status_code == 200:
                    response.render()
            return response, [str(message) for message in request._messages]

        def rows(extract_id):
            counts = []
            with self.db.cursor() as cursor:
                for table in ("extracts", "extract_livenodes", "extract_liveways",
                              "extract_liverelations", "extract_way_mems",
                              "extract_relation_mems_n", "extract_relation_mems_w",
                              "extract_relation_mems_r"):
                    column = "id" if table == "extracts" else "extract_id"
                    cursor.execute(sql.SQL("SELECT count(*) FROM {} WHERE {}=%s").format(
                        sql.Identifier(self.prefixes[1] + table), sql.Identifier(column)), [extract_id])
                    counts.append(cursor.fetchone()[0])
            return counts

        listing = reverse("admin:replicate_dbextract_changelist")
        self.assertEqual(reverse("admin:replicate_dbextract_update", args=[5]), listing + "5/update/")
        self.assertEqual(reverse("admin:replicate_dbextract_remove", args=[5]), listing + "5/delete/")
        updater, deleter = User("change_dbextract"), User("delete_dbextract")

        inside = self.create_node(0)
        outside = self.create_node(2)
        way = self.create_way([inside, outside])
        child = self.create_relation([])
        self.create_relation([("node", inside, ""), ("way", way, ""), ("relation", child, "")])
        first = self.save("first")
        second = self.save("second")
        kept = self.contents(second)
        self.assertEqual(rows(first), [1, 2, 1, 1, 2, 1, 1, 1])
        self.modify_node(inside, 0.5)
        stale = self.contents(first)

        # Refused requests change nothing.
        with self.assertRaises(PermissionDenied):
            call(model_admin.update_view, first, deleter)
        with self.assertRaises(PermissionDenied):
            call(model_admin.remove_view, first, updater)
        with self.assertRaises(PermissionDenied):
            call(model_admin.remove_view, first, updater, "get")
        self.assertEqual(call(model_admin.update_view, first, updater, "get")[0].status_code, 405)
        self.assertEqual(call(model_admin.update_view, first, updater, csrf=False)[0].status_code, 403)
        self.assertEqual(call(model_admin.remove_view, first, deleter, csrf=False)[0].status_code, 403)
        self.assertEqual(self.contents(first), stale)
        self.assertEqual(rows(first), [1, 2, 1, 1, 2, 1, 1, 1])

        response, notes = call(model_admin.update_view, first, updater)
        self.assertEqual((response.status_code, response["Location"]), (302, listing))
        self.assertEqual(notes, ["Database extract {}: updated.".format(first)])
        self.assert_current(first)
        self.assertNotEqual(self.contents(first), stale)
        self.assertEqual(self.contents(second), kept)
        response, notes = call(model_admin.update_view, 99999, updater)
        self.assertEqual(response.status_code, 302)
        self.assertIn("Extract not found", notes[0])

        # Deleting asks first, then removes only that extract's rows.
        response, _ = call(model_admin.remove_view, first, deleter, "get")
        self.assertEqual(response.status_code, 200)
        self.assertIn("database extract {}".format(first), response.content.decode("utf-8"))
        self.assertEqual(rows(first), [1, 2, 1, 1, 2, 1, 1, 1])
        with self.assertRaises(Http404):
            call(model_admin.remove_view, 99999, deleter, "get")
        response, notes = call(model_admin.remove_view, first, deleter)
        self.assertEqual((response.status_code, response["Location"]), (302, listing))
        self.assertEqual(notes, ["Deleted database extract {}.".format(first)])
        self.assertEqual(rows(first), [0] * 8)
        self.assertEqual(rows(second), [1, 2, 1, 1, 2, 1, 1, 1])
        self.assertEqual(self.contents(second), kept)
        self.assertEqual(self.contents(), self.contents_after_update(second))
        response, notes = call(model_admin.remove_view, first, deleter)
        self.assertIn("Extract not found", notes[0])

        # The list shows each action only to users allowed to use it.
        for user, update, delete in ((updater, True, False), (deleter, False, True), (User(), False, False)):
            request = RequestFactory().get("/")
            request.user = user
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                html = model_admin.changelist_view(request).render().content.decode("utf-8")
            self.assertEqual(listing + "{}/update/".format(second) in html, update)
            self.assertEqual(listing + "{}/delete/".format(second) in html, delete)

    def contents_after_update(self, extract_id):
        self.update(extract_id)
        return self.contents(extract_id)

    def test_download_gzipped_extract(self):
        import gzip
        nodes = []
        for index in range(1005):
            node = pgmap.OsmNode()
            node.objId = -index - 1
            node.lon, node.lat = 0, 0
            nodes.append(node)
        created = self.upload("create", nodes)[0]
        self.create_way([created[0], created[-1]])
        extract_id = self.save()
        url = reverse("replication:download_extract_gz_by_id", args=[extract_id])
        self.assertTrue(url.endswith("/extract/{}.osm.gz".format(extract_id)))
        response = self.download(url)
        try:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.streaming)
            self.assertEqual(response["Content-Type"], "application/x-gzip")
            self.assertIn('filename="extract-{}.osm.gz"'.format(extract_id), response["Content-Disposition"])
            chunks = list(response.streaming_content)
            self.assertGreater(len(chunks), 1)
            data = b"".join(chunks)
        finally:
            response.close()
        self.assertEqual(data[:2], b"\x1f\x8b")
        self.assertEqual(self.decode_contents(gzip.decompress(data)), self.contents(extract_id))
        root = ET.fromstring(gzip.decompress(data))
        self.assertEqual((int(root.get("edit_activity_id")), int(root.get("atomic_edit_id"))),
                         self.checkpoint(extract_id))
        self.assertEqual(len(self.contents(extract_id)), 1006)

        missing = self.download(reverse("replication:download_extract_gz_by_id", args=[99999]))
        self.assertEqual(missing.status_code, 404)
        # Closing early must release the snapshot transaction's locks.
        response = self.download(url)
        self.assertTrue(next(iter(response.streaming_content)) is not None)
        response.close()
        with self.db.cursor() as cursor:
            cursor.execute("BEGIN")
            try:
                cursor.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE NOWAIT").format(
                    sql.Identifier(self.prefixes[1] + "extract_livenodes")))
            finally:
                cursor.execute("ROLLBACK")

    def test_public_extract_list(self):
        url = reverse("replication:extracts")
        self.assertTrue(url.endswith("/replication/extracts"))

        def page():
            # No login: the anonymous test client must be able to read it.
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                response = Client().get(url)
            return response, response.content.decode("utf-8")

        response, html = page()
        self.assertEqual(response.status_code, 200)
        self.assertIn("No extracts are available", html)
        # The map's edit IDs are shown even with nothing to list: none, before any edit
        self.assertIn('id="server_edit_activity_id">0<', html)
        self.assertIn('id="server_atomic_edit_id">0<', html)
        self.create_node(0)
        first = self.save("first <extract>")
        second = self.save("")
        response, html = page()
        self.assertEqual(response.status_code, 200)
        self.assertIn("first &lt;extract&gt;", html)
        self.assertIn("(unnamed)", html)
        self.assertIn("-1.0, -1.0, 1.0, 1.0", html)
        for extract_id in (first, second):
            link = reverse("replication:download_extract_gz_by_id", args=[extract_id])
            self.assertIn('href="{}"'.format(link), html)
            download = self.download(link)
            self.assertEqual(download.status_code, 200)
            download.close()
        # Nothing for administrators is exposed.
        self.assertNotIn("/admin/", html)
        self.assertEqual(Client().post(url).status_code, 405)

        # The map's current edit IDs are above the list, and each extract's own in it
        def ids_on_page(html):
            server = (int(re.search(r'id="server_edit_activity_id">(\d+)<', html).group(1)),
                      int(re.search(r'id="server_atomic_edit_id">(\d+)<', html).group(1)))
            rows = list(zip(re.findall(r'<td class="edit_activity_id">([^<]*)</td>', html),
                            re.findall(r'<td class="atomic_edit_id">([^<]*)</td>', html)))
            return server, rows
        def latest():
            with self.db.cursor() as cursor:
                cursor.execute(sql.SQL("SELECT COALESCE(max(id),0), COALESCE(max(atomic_edit_id),0) FROM {}").format(
                    sql.Identifier(self.prefixes[1] + "edit_activity")))
                return cursor.fetchone()
        saved_at = latest()
        self.assertGreater(saved_at[0], 0)
        server, rows = ids_on_page(html)
        self.assertEqual(server, saved_at)
        self.assertLess(html.index('id="server_edit_ids"'), html.index("<table"))
        self.assertEqual(rows, [(str(saved_at[0]), str(saved_at[1]))] * 2)
        # After an edit the map moves on; an extract follows when it is updated
        self.create_node(0.5)
        self.update(first)
        response, html = page()
        server, rows = ids_on_page(html)
        self.assertEqual(server, latest())
        self.assertGreater(server[0], saved_at[0])
        self.assertEqual(rows, [(str(server[0]), str(server[1])), (str(saved_at[0]), str(saved_at[1]))])
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("UPDATE {} SET edit_activity_id=NULL, atomic_edit_id=NULL WHERE id=%s").format(
                sql.Identifier(self.prefixes[1] + "extracts")), [second])
        self.assertEqual(ids_on_page(page()[1])[1][1], ("not set", "not set"))

        # The replication page links to the list, and the front page to that.
        with patch("replicate.views.get_pgmap", return_value=self.map):
            index = Client().get(reverse("replication:replicate"))
        self.assertEqual(index.status_code, 200)
        self.assertIn("<a id='extracts' href='extracts'>", index.content.decode("utf-8"))
        self.assertTrue(url.endswith("/replication/extracts"))
        with patch("frontpage.views.get_pgmap", return_value=self.map):
            front = Client().get(reverse("frontpage:index"))
        self.assertEqual(front.status_code, 200)
        self.assertIn('href="{}"'.format(reverse("replication:replicate")), front.content.decode("utf-8"))
        self.assertNotIn('href="{}"'.format(url), front.content.decode("utf-8"))

        # A database failure gives an error page without internal details.
        with patch("replicate.extracts.get_pgmap", side_effect=RuntimeError("secret detail")):
            broken = Client().get(url)
        self.assertEqual(broken.status_code, 500)
        self.assertNotIn("secret detail", broken.content.decode("utf-8"))

    def test_map_query_on_several_connections(self):
        # Prepared statements belong to one connection. Each server thread has
        # its own, so a query must work on every connection, old or new.
        node = self.create_node(0)

        def query(connection):
            output = io.BytesIO()
            encoder = pgmap.PyOsmXmlEncode(output, common.xmlAttribs)
            t = connection.GetTransaction("ACCESS SHARE")
            try:
                mgr = t.GetQueryMgr()
                status = mgr.Start(self.bbox, int(time.time()), encoder)
                while status == 0:
                    status = mgr.Continue()
                del mgr
                self.assertEqual(status, 1)
                # The meta lookup also uses a prepared statement.
                error = pgmap.PgMapError()
                self.assertEqual(t.GetMetaValue("schema_version", error), "15")
            finally:
                t.Abort()
            return set(self.decode_contents(output.getvalue()))

        def connect():
            return pgmap.PgMap(self.connection_string, self.prefixes[0],
                               self.prefixes[1], self.prefixes[1], self.prefixes[2])

        expected = {("node", node.objId)}
        second = connect()
        for connection in (self.map, second, self.map, second):
            self.assertEqual(query(connection), expected)
        # Connections created after earlier ones have closed must work too.
        del second
        for _ in range(3):
            self.assertEqual(query(connect()), expected)

    def test_latest_edit_ids(self):
        def latest():
            t = self.map.GetTransaction("ACCESS SHARE")
            try:
                ids = t.GetLatestEditIds()
                return (ids[0], ids[1]), dict(t.GetLatestEditIdAttribs())
            finally:
                t.Abort()

        def table_ids():
            with self.db.cursor() as cursor:
                cursor.execute(sql.SQL("SELECT COALESCE(max(id),0), COALESCE(max(atomic_edit_id),0) FROM {}").format(
                    sql.Identifier(self.prefixes[1] + "edit_activity")))
                return cursor.fetchone()

        self.assertEqual(latest(), ((0, 0), {"edit_activity_id": "0", "atomic_edit_id": "0"}))
        node = self.create_node(0)
        first = latest()[0]
        self.assertEqual(first, table_ids())
        self.assertGreater(first[0], 0)
        self.assertGreater(first[1], 0)
        # These are the checkpoints a stored extract records.
        self.assertEqual(self.checkpoint(self.save()), first)
        self.modify_node(node)
        ids, attribs = latest()
        self.assertEqual(ids, table_ids())
        self.assertGreater(ids[0], first[0])
        self.assertGreater(ids[1], first[1])
        self.assertEqual(attribs, {"edit_activity_id": str(ids[0]), "atomic_edit_id": str(ids[1])})

        # As root attributes they describe the snapshot the data was read from.
        output = io.BytesIO()
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            encoder = pgmap.PyOsmXmlEncode(output, t.GetLatestEditIdAttribs())
            query = t.GetQueryMgr()
            status = query.Start(self.bbox, int(time.time()), encoder)
            while status == 0:
                status = query.Continue()
            del query
        finally:
            t.Abort()
        root = ET.fromstring(output.getvalue())
        self.assertEqual((int(root.get("edit_activity_id")), int(root.get("atomic_edit_id"))), ids)
        self.assertEqual(root.find("node").get("version"), "2")

    # Importing a file as a stored extract

    def export_document(self, extract_id, encoder_class=None, attribs=None):
        """The stored extract as an XML (or other text format) document."""
        output = io.BytesIO()
        encoder = (encoder_class or pgmap.PyOsmXmlEncode)(
            output, pgmap.mapstringstring(attribs or {}))
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            t.ExportExtract(extract_id, "", encoder)
        finally:
            t.Abort()
        return output.getvalue().decode("utf-8")

    def import_document(self, document, name="imported", bbox=(), edit_ids=(-1, -1),
                        loader=None):
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            importer = t.StartImportExtract(name, list(bbox), *edit_ids)
            (loader or pgmap.LoadFromOsmXml)(document, importer)
            counts = (importer.GetNumNodes(), importer.GetNumWays(), importer.GetNumRelations())
            extract_id = importer.GetId()
            del importer
            t.Commit()
            return extract_id, counts
        except BaseException:
            t.Abort()
            raise

    def stored_rows(self, extract_id):
        """Everything stored for an extract that an import is expected to reproduce."""
        queries = {
            "extracts": "SELECT ST_AsText(bbox), use_bbox_in_query FROM {} WHERE id=%s",
            "extract_livenodes": "SELECT id, changeset, username, uid, timestamp, version, "
                "tags::text, ST_AsText(geom) FROM {} WHERE extract_id=%s ORDER BY id",
            "extract_liveways": "SELECT id, changeset, username, uid, timestamp, version, "
                "tags::text, members::text, ST_AsText(bbox) FROM {} WHERE extract_id=%s ORDER BY id",
            "extract_liverelations": "SELECT id, changeset, username, uid, timestamp, version, "
                "tags::text, members::text, memberroles::text FROM {} WHERE extract_id=%s ORDER BY id",
        }
        for suffix in ("way_mems", "relation_mems_n", "relation_mems_w", "relation_mems_r"):
            queries["extract_" + suffix] = ("SELECT id, version, index, member FROM {} "
                                            "WHERE extract_id=%s ORDER BY id, index")
        rows = {}
        with self.db.cursor() as cursor:
            for table, query in queries.items():
                cursor.execute(sql.SQL(query).format(sql.Identifier(self.prefixes[1] + table)),
                               [extract_id])
                rows[table] = cursor.fetchall()
        return rows

    def extract_ids(self):
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT id FROM {} ORDER BY id").format(
                sql.Identifier(self.prefixes[1] + "extracts")))
            return [row[0] for row in cursor.fetchall()]

    def populated_extract(self):
        """A saved extract holding tagged nodes, a way that leaves the bbox and relations."""
        tagged = pgmap.OsmNode()
        tagged.objId = -1
        tagged.lon, tagged.lat = 0.123456789, -0.5
        tagged.tags["name"] = "Café \"A\" <&>"
        tagged = self.upload("create", [tagged])[0][0]
        inside = self.create_node(0.25, 0.5)
        outside = self.create_node(2.5, 0.75)
        way = self.create_way([inside, outside, tagged])
        child = self.create_relation([("node", tagged, "stop")])
        self.create_relation([("node", inside, ""), ("way", way, "outer"),
                              ("relation", child, "part"), ("node", inside, "again")])
        return self.save("original")

    def test_import_reproduces_exported_extract(self):
        original = self.populated_extract()
        expected = self.stored_rows(original)
        self.assertEqual([len(expected[t]) for t in ("extract_livenodes", "extract_liveways",
                                                     "extract_liverelations")], [3, 1, 2])
        for encoder, loader in ((pgmap.PyOsmXmlEncode, pgmap.LoadFromOsmXml),
                                (pgmap.PyOsmJsonEncode, pgmap.LoadFromOsmJson)):
            with self.subTest(loader=loader.__name__):
                document = self.export_document(original, encoder)
                imported, counts = self.import_document(document, loader=loader)
                self.assertNotEqual(imported, original)
                self.assertEqual(counts, (3, 1, 2))
                self.assertEqual(self.stored_rows(imported), expected)
                self.assertEqual(self.contents(imported), self.contents(original))
                # Exporting the import gives back the document it came from
                self.assertEqual(self.export_document(imported, encoder), document)
                # Nothing in the document said which edits it is current to
                self.assertEqual(self.checkpoint(imported), (None, None))
                self.assertEqual(self.compare(imported)[1], [])

    def test_import_takes_checkpoint_from_document_and_updates(self):
        original = self.populated_extract()
        ids = self.checkpoint(original)
        document = self.export_document(original, attribs={
            "edit_activity_id": str(ids[0]), "atomic_edit_id": str(ids[1])})
        imported, _ = self.import_document(document, name="synced")
        self.assertEqual(self.checkpoint(imported), ids)

        # The import is brought up to date like any other extract
        self.create_node(0.75, 0.25)
        self.assertNotEqual(self.contents(imported), self.contents())
        self.update(imported)
        self.assert_current(imported)

    def test_import_checkpoint_and_bbox_arguments_override_document(self):
        original = self.populated_extract()
        ids = self.checkpoint(original)
        document = self.export_document(original, attribs={
            "edit_activity_id": "999999", "atomic_edit_id": "999999"})
        imported, _ = self.import_document(document, bbox=[-0.5, -0.75, 0.5, 0.75], edit_ids=ids)
        self.assertEqual(self.checkpoint(imported), ids)
        self.assertEqual(self.stored_rows(imported)["extracts"][0][0],
                         "POLYGON((-0.5 -0.75,-0.5 0.75,0.5 0.75,0.5 -0.75,-0.5 -0.75))")
        # Contents are stored as given, not filtered against the new rectangle
        self.assertEqual(self.contents(imported), self.contents(original))

    def test_import_without_checkpoint_cannot_update(self):
        original = self.populated_extract()
        imported, _ = self.import_document(self.export_document(original))
        with self.assertRaisesRegex(RuntimeError, "checkpoint"):
            self.update(imported)
        self.assertEqual(self.contents(imported), self.contents(original))

    def test_import_needs_a_bbox(self):
        original = self.populated_extract()
        document = self.export_document(original)
        root = ET.fromstring(document)
        root.remove(root.find("bounds"))
        unbounded = ET.tostring(root, encoding="unicode")
        with self.assertRaisesRegex(RuntimeError, "bbox"):
            self.import_document(unbounded)
        self.assertEqual(self.extract_ids(), [original])
        imported, counts = self.import_document(unbounded, bbox=self.bbox)
        self.assertEqual(counts, (3, 1, 2))
        self.assertEqual(self.stored_rows(imported), self.stored_rows(original))

    def test_import_rejects_unusable_input(self):
        original = self.populated_extract()
        document = self.export_document(original)
        root = ET.fromstring(document)
        root.append(root.find("node"))
        cases = {
            "duplicate object": (ET.tostring(root, encoding="unicode"), {}),
            "truncated document": (document[:len(document) // 2], {}),
            "bad bbox": (document, {"bbox": [1, 1, -1, -1]}),
            "half a checkpoint": (document, {"edit_ids": (5, -1)}),
            "bad checkpoint in document": (document.replace(
                "<osm ", '<osm edit_activity_id="x" atomic_edit_id="1" ', 1), {}),
        }
        for label, (text, arguments) in cases.items():
            with self.subTest(label):
                with self.assertRaises(Exception):
                    self.import_document(text, **arguments)
                # A failed import leaves no extract behind
                self.assertEqual(self.extract_ids(), [original])

    def test_import_skips_deleted_objects_and_handles_large_batches(self):
        data = pgmap.OsmData()
        way = pgmap.OsmWay()
        way.objId = 7
        way.metaData.version = 3
        for index in range(2500):
            node = pgmap.OsmNode()
            node.objId = index + 1
            node.metaData.version = 1
            node.lon, node.lat = index / 10000.0, 0.5
            data.nodes.append(node)
            way.refs.append(node.objId)
        deleted = pgmap.OsmNode()
        deleted.objId = 5000
        deleted.metaData.version = 2
        deleted.metaData.visible = False
        data.nodes.append(deleted)
        data.ways.append(way)

        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            importer = t.StartImportExtract("bulk", self.bbox)
            data.StreamTo(importer)
            self.assertEqual((importer.GetNumNodes(), importer.GetNumWays()), (2500, 1))
            extract_id = importer.GetId()
            del importer
            t.Commit()
        except BaseException:
            t.Abort()
            raise
        rows = self.stored_rows(extract_id)
        self.assertEqual(len(rows["extract_livenodes"]), 2500)
        self.assertEqual([row[:3] for row in rows["extract_way_mems"][:2]], [(7, 3, 0), (7, 3, 1)])
        self.assertEqual(len(rows["extract_way_mems"]), 2500)
        self.assertEqual(rows["extract_liveways"][0][8], "LINESTRING(0 0.5,0.2499 0.5)")
        # Metadata the stream left out is stored as absent
        self.assertEqual(rows["extract_livenodes"][0][1:5], (None, None, None, None))
        self.assertEqual(self.contents(extract_id)[("way", 7)][1][0], ("nd", (("ref", "1"),)))

    def test_download_extract_in_other_formats(self):
        import gzip
        self.populated_extract()
        nodes = []
        for index in range(1005):
            node = pgmap.OsmNode()
            node.objId = -index - 1
            node.lon, node.lat = 0.001 * (index % 100), 0.1
            nodes.append(node)
        self.upload("create", nodes)
        extract_id = self.save("formats")
        expected = sorted((kind, ident, int(value[0]["version"]))
                          for (kind, ident), value in self.contents(extract_id).items())
        self.assertGreater(len(expected), 1005)

        cases = (("download_extract_o5m_gz_by_id", "o5m.gz", "application/x-gzip", pgmap.LoadFromO5mBytes),
                 ("download_extract_pbf_by_id", "pbf", "application/octet-stream", pgmap.LoadFromPbfBytes))
        for route, extension, content_type, loader in cases:
            with self.subTest(extension):
                url = reverse("replication:" + route, args=[extract_id])
                self.assertTrue(url.endswith("/extract/{}.{}".format(extract_id, extension)))
                response = self.download(url)
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.streaming)
                    self.assertEqual(response["Content-Type"], content_type)
                    self.assertIn('filename="extract-{}.{}"'.format(extract_id, extension),
                                  response["Content-Disposition"])
                    data = b"".join(response.streaming_content)
                finally:
                    response.close()
                if extension.endswith(".gz"):
                    data = gzip.decompress(data)
                decoded = pgmap.OsmData()
                loader(data, decoded)
                found = [("node", n.objId, n.metaData.version) for n in decoded.nodes]
                found += [("way", w.objId, w.metaData.version) for w in decoded.ways]
                found += [("relation", r.objId, r.metaData.version) for r in decoded.relations]
                self.assertEqual(sorted(found), expected)
                self.assertEqual(len(decoded.bounds), 1)
                # o5m carries the extract's edit IDs; PBF has nowhere to put them
                ids = self.checkpoint(extract_id)
                self.assertEqual(dict(decoded.attributes), {} if extension == "pbf" else
                                 {"edit_activity_id": str(ids[0]), "atomic_edit_id": str(ids[1])})
                way = decoded.ways[0]
                self.assertEqual(len(way.refs), 3)
                relation = [r for r in decoded.relations if len(r.members) == 4][0]
                self.assertEqual([m.role for m in relation.members], ["", "outer", "part", "again"])

                self.assertEqual(self.download(reverse("replication:" + route, args=[99999])).status_code, 404)
                # Closing early must release the snapshot transaction's locks.
                early = self.download(url)
                early.close()
                self.update(extract_id)

    def test_extract_pages_link_every_format(self):
        extract_id = self.save("linked")
        with patch("replicate.extracts.get_pgmap", return_value=self.map):
            html = Client().get(reverse("replication:extracts")).content.decode("utf-8")
        for extension in ("osm.gz", "o5m.gz", "pbf"):
            self.assertIn('/extract/{}.{}"'.format(extract_id, extension), html)

    # Automatic update settings of an extract

    def extract_info(self, extract_id):
        info = pgmap.ExtractInfo()
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            self.assertTrue(t.GetExtract(extract_id, info))
        finally:
            t.Abort()
        return info

    def set_auto_update(self, extract_id, name, enabled, url):
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            result = t.SetExtractAutoUpdate(extract_id, name, enabled, url)
            t.Commit()
            return result
        except BaseException:
            t.Abort()
            raise

    def test_auto_update_settings(self):
        self.create_node(0)
        first = self.save("first")
        second = self.save("second")
        # A new extract is not updated automatically, and would be from this map
        for extract_id in (first, second):
            info = self.extract_info(extract_id)
            self.assertEqual((info.autoUpdate, info.updateUrl), (False, ""))

        self.assertEqual(self.set_auto_update(first, "", True, "https://example.org/api/0.6/"), first)
        info = self.extract_info(first)
        self.assertEqual((info.autoUpdate, info.updateUrl), (True, "https://example.org/api/0.6/"))
        other = self.extract_info(second)
        self.assertEqual((other.autoUpdate, other.updateUrl), (False, ""))
        # By name, and each setting independently of the other
        self.assertEqual(self.set_auto_update(0, "second", True, ""), second)
        other = self.extract_info(second)
        self.assertEqual((other.autoUpdate, other.updateUrl), (True, ""))
        self.set_auto_update(first, "", False, "http://localhost:8000/")
        info = self.extract_info(first)
        self.assertEqual((info.autoUpdate, info.updateUrl), (False, "http://localhost:8000/"))

        # The listing carries them too, and updating the extract keeps them
        self.set_auto_update(first, "", True, "https://example.org/x?a=1&b='q'")
        self.create_node(0.5)
        self.update(first)
        listed = pgmap.vectorextractinfo()
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            t.ListExtracts(listed)
            settings = [(e.extractId, e.autoUpdate, e.updateUrl) for e in listed]
        finally:
            t.Abort()
        self.assertEqual(settings, [(first, True, "https://example.org/x?a=1&b='q'"), (second, True, "")])
        from replicate.extracts import get_db_extract
        with patch("replicate.extracts.get_pgmap", return_value=self.map):
            described = get_db_extract(first)
        self.assertEqual((described["auto_update"], described["update_url"]),
                         (True, "https://example.org/x?a=1&b='q'"))

        # Only blank or a web address is accepted, and only for an extract that exists
        for bad in ("example.org", "ftp://example.org/", "javascript:alert(1)", "https://exa mple.org/",
                    "https://example.org/\n", "http://" + "a" * 2000):
            with self.assertRaises(Exception, msg=bad):
                self.set_auto_update(second, "", True, bad)
        other = self.extract_info(second)
        self.assertEqual((other.autoUpdate, other.updateUrl), (True, ""))
        with self.assertRaisesRegex(RuntimeError, "Extract not found"):
            self.set_auto_update(99999, "", True, "")
        with self.assertRaisesRegex(RuntimeError, "Extract not found"):
            self.set_auto_update(0, "missing", True, "")

    def test_auto_update_columns_are_added_to_an_older_schema_14(self):
        self.create_node(0)
        extract_id = self.save("made before the columns")
        extracts = sql.Identifier(self.prefixes[1] + "extracts")
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER TABLE {} DROP COLUMN auto_update, DROP COLUMN update_url").format(extracts))
        with self.assertRaises(Exception):
            self.extract_info(extract_id)

        # Asking for schema 14 again, as the admin tool does, brings the table up to date
        admin = self.map.GetAdmin("")
        error = pgmap.PgMapError()
        try:
            self.assertTrue(admin.CreateMapTables(0, 15, False, error), error.errStr)
            admin.Commit()
        except BaseException:
            admin.Abort()
            raise
        finally:
            del admin
        info = self.extract_info(extract_id)
        self.assertEqual((info.name, info.autoUpdate, info.updateUrl), ("made before the columns", False, ""))
        self.assertEqual(len(self.contents(extract_id)), 1)
        # Doing so once more changes nothing
        self.set_auto_update(extract_id, "", True, "https://example.org/")
        admin = self.map.GetAdmin("")
        try:
            self.assertTrue(admin.CreateMapTables(0, 15, False, error), error.errStr)
            admin.Commit()
        finally:
            del admin
        info = self.extract_info(extract_id)
        self.assertEqual((info.autoUpdate, info.updateUrl), (True, "https://example.org/"))

    # The manage.py command that updates extracts

    # Another server's web API, for extracts that update from one. The other
    # server is this application, called through Django's test client against
    # the test map, so the extract is fed by the API alone.
    REMOTE = "http://remote.test/"

    def fetch(self, url):
        """Stands in for reading a web address."""
        from urllib.parse import urlsplit
        from replicate.remoteupdate import RemoteUpdateError
        if not url.startswith(self.REMOTE):
            raise RemoteUpdateError("{} could not be read: no such server".format(url))
        parts = urlsplit(url)
        path = parts.path + ("?" + parts.query if parts.query else "")
        self.fetched.append(path)
        with patch("replicate.views.get_pgmap", return_value=self.map), \
             patch("elements.views.get_pgmap", return_value=self.map), \
             patch("multifetch.views.get_pgmap", return_value=self.map):
            response = Client().get(path)
        if response.status_code != 200:
            raise RemoteUpdateError("{} answered {}".format(url, response.status_code))
        return response.content

    def update_command(self, *arguments):
        from django.core.management import call_command
        out = io.StringIO()
        self.fetched = []
        with patch("replicate.extracts.get_pgmap", return_value=self.map), \
             patch("replicate.remoteupdate.http_fetch", self.fetch):
            call_command("updateextracts", *arguments, stdout=out, no_color=True)
        return out.getvalue().splitlines()

    def test_update_command(self):
        from django.core.management.base import CommandError
        self.assertEqual(self.update_command(), ["No extracts are enabled for automatic updates"])
        self.assertEqual(self.update_command("--all"), ["No extracts to update"])

        self.create_node(0)
        automatic = self.save("automatic")
        manual = self.save("manual")
        remote = self.save("")
        self.set_auto_update(automatic, "", True, "")
        self.set_auto_update(remote, "", True, self.REMOTE)
        self.assertEqual(self.update_command(), [
            "Extract {} (automatic): already up to date".format(automatic),
            "Extract {}: already up to date with {}".format(remote, self.REMOTE),
            "0 updated, 2 already up to date, 0 failed"])
        # The other server was only asked where it has got to
        self.assertEqual(self.fetched, ["/replication/edit_ids"])

        # After an edit, the extracts enabled for it are brought up to date:
        # one from this map, the other from the server it names
        added = self.create_node(0.5)
        key = ("node", added.objId)
        lines = self.update_command()
        self.assertRegex(lines[0], r"^Extract {} \(automatic\): updated in \d+\.\ds$".format(automatic))
        self.assertRegex(lines[1], r"^Extract {}: updated from {} \(1 activity row\) in \d+\.\ds$".format(
            remote, re.escape(self.REMOTE)))
        self.assertEqual(lines[2], "2 updated, 0 already up to date, 0 failed")
        self.assertIn(key, self.contents(automatic))
        self.assertNotIn(key, self.contents(manual))
        self.assertIn(key, self.contents(remote))
        self.assert_current(automatic)
        self.assert_current(remote)
        self.assertIn("/replication/edit_ids", self.fetched)
        self.assertTrue(any(path.startswith("/replication/edit_activities?first_id=") for path in self.fetched))

        # Chosen by ID, an extract is updated whatever its setting
        lines = self.update_command("--id", str(manual))
        self.assertRegex(lines[0], r"^Extract {} \(manual\): updated in".format(manual))
        self.assertEqual(lines[1], "1 updated, 0 already up to date, 0 failed")
        self.assertIn(key, self.contents(manual))

        # --all takes every extract
        another = self.create_node(0.25)
        lines = self.update_command("--all")
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[3], "3 updated, 0 already up to date, 0 failed")
        for extract_id in (automatic, manual, remote):
            self.assertIn(("node", another.objId), self.contents(extract_id))
        self.assertEqual(self.update_command("--id", str(automatic), "--id", str(manual))[-1],
                         "0 updated, 2 already up to date, 0 failed")

        for arguments, message in ((("--id", "99999"), "No extract with ID 99999"),
                                   (("--all", "--id", str(manual)), "not both")):
            with self.assertRaisesRegex(CommandError, message):
                self.update_command(*arguments)

    def test_update_command_carries_on_after_a_failure(self):
        from django.core.management.base import CommandError
        self.create_node(0)
        broken = self.save("broken")
        unreachable = self.save("unreachable")
        working = self.save("working")
        for extract_id in (broken, working):
            self.set_auto_update(extract_id, "", True, "")
        self.set_auto_update(unreachable, "", True, "http://elsewhere.test/")
        # Without a checkpoint an extract cannot be updated
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("UPDATE {} SET edit_activity_id=NULL, atomic_edit_id=NULL WHERE id=%s").format(
                sql.Identifier(self.prefixes[1] + "extracts")), [broken])
        added = self.create_node(0.5)

        with self.assertRaisesRegex(CommandError, "2 extracts could not be updated") as caught:
            self.update_command()
        with patch("replicate.extracts.get_pgmap", return_value=self.map), \
             patch("replicate.remoteupdate.http_fetch", self.fetch):
            out = io.StringIO()
            from django.core.management import call_command
            with self.assertRaises(CommandError):
                call_command("updateextracts", stdout=out, no_color=True)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "Extract {} (broken): failed, Extract has no established "
                                   "synchronization checkpoint".format(broken))
        self.assertEqual(lines[1], "Extract {} (unreachable): failed, http://elsewhere.test/replication/edit_ids "
                                   "could not be read: no such server".format(unreachable))
        # The first run brought the working extract up to date; nothing was left to do in the second
        self.assertEqual(lines[2], "Extract {} (working): already up to date".format(working))
        self.assertEqual(lines[3], "0 updated, 1 already up to date, 2 failed")
        self.assertIn(("node", added.objId), self.contents(working))
        self.assertNotIn(("node", added.objId), self.contents(unreachable))

    def test_admin_sets_automatic_updates(self):
        from django.contrib import admin
        from django.contrib.messages.storage.cookie import CookieStorage
        from django.core.exceptions import PermissionDenied
        from django.test import RequestFactory
        from replicate.models import DbExtract

        class User:
            is_active = is_staff = is_authenticated = True
            is_superuser = is_anonymous = False
            pk = id = 1
            def __init__(self, *perms): self.perms = {"replicate." + perm for perm in perms}
            def has_perm(self, perm, obj=None): return perm in self.perms
            def has_module_perms(self, app_label): return True
            def get_username(self): return "staff"
            def get_short_name(self): return "staff"
            def has_usable_password(self): return True

        model_admin = admin.site._registry[DbExtract]
        self.create_node(0)
        extract_id = self.save("settable")
        other = self.save("other")
        editor, viewer = User("change_dbextract"), User()

        def post(data, user=editor, target=extract_id):
            request = RequestFactory().post("/", data)
            request.user = user
            request._messages = CookieStorage(request)
            request._dont_enforce_csrf_checks = True
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                response = model_admin.automatic_view(request, target)
            return response, [str(message) for message in request._messages]

        def detail(user=editor):
            request = RequestFactory().get("/")
            request.user = user
            with patch("replicate.extracts.get_pgmap", return_value=self.map):
                return model_admin.detail_view(request, extract_id).render().content.decode("utf-8")

        def stored(target=extract_id):
            info = self.extract_info(target)
            return info.autoUpdate, info.updateUrl

        # The page offers the settings to those who may change extracts
        html = detail()
        self.assertIn('id="extract_automatic_form"', html)
        self.assertIn(reverse("admin:replicate_dbextract_automatic", args=[extract_id]), html)
        self.assertNotIn(" checked", html)
        self.assertNotIn('id="extract_automatic_form"', detail(viewer))

        response, notes = post({"auto_update": "on", "update_url": ""})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("admin:replicate_dbextract_detail", args=[extract_id]))
        self.assertEqual(notes, ["Automatic updates of database extract {} are now enabled.".format(extract_id)])
        self.assertEqual(stored(), (True, ""))
        self.assertEqual(stored(other), (False, ""))
        html = detail()
        self.assertIn('name="auto_update" checked', html)
        self.assertIn('<td id="extract_auto_update">enabled</td>', html)
        # The update command now takes this extract
        self.assertIn("Extract {} (settable): already up to date".format(extract_id), self.update_command())

        response, notes = post({"auto_update": "on", "update_url": "  https://example.org/api/  "})
        self.assertEqual(stored(), (True, "https://example.org/api/"))
        self.assertIn('value="https://example.org/api/"', detail())
        # An unticked box is not sent at all, and turns the setting off
        response, notes = post({"update_url": ""})
        self.assertEqual(notes, ["Automatic updates of database extract {} are now disabled.".format(extract_id)])
        self.assertEqual(stored(), (False, ""))

        # What pgmap refuses is reported, and nothing changes
        response, notes = post({"auto_update": "on", "update_url": "ftp://example.org/"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(notes, ["Could not change extract {}: Update URL must be blank or begin with "
                                 "http:// or https://".format(extract_id)])
        self.assertEqual(stored(), (False, ""))
        response, notes = post({"auto_update": "on"}, target=99999)
        self.assertEqual(notes, ["Could not change extract 99999: Extract not found"])

        with self.assertRaises(PermissionDenied):
            post({"auto_update": "on"}, user=viewer)
        self.assertEqual(stored(), (False, ""))
        request = RequestFactory().get("/")
        request.user = editor
        self.assertEqual(model_admin.automatic_view(request, extract_id).status_code, 405)

    # Changing an extract from supplied objects, without reading the map

    def test_patch_applies_objects_by_the_extract_rule(self):
        def obj(cls, ident, version=1, **fields):
            item = cls()
            item.objId = ident
            item.metaData.version = version
            for name, value in fields.items():
                setattr(item, name, value)
            return item

        def node(ident, lon, version=1):
            return obj(pgmap.OsmNode, ident, version, lon=lon, lat=0.0)

        def way(ident, refs, version=1):
            item = obj(pgmap.OsmWay, ident, version)
            for ref in refs:
                item.refs.append(ref)
            return item

        def relation(ident, members, version=1):
            item = obj(pgmap.OsmRelation, ident, version)
            for kind, ref in members:
                item.AddMember(kind, ref, "")
            return item

        def patch_with(objects=(), removed=(), checkpoint=None):
            t = self.map.GetTransaction("ACCESS SHARE")
            try:
                patch = t.StartPatchExtract(extract_id)
                for item in objects:
                    {pgmap.OsmNode: patch.StoreNode, pgmap.OsmWay: patch.StoreWay,
                     pgmap.OsmRelation: patch.StoreRelation}[type(item)](item)
                for kind, ident, version in removed:
                    patch.RemoveObject(kind, ident, version)
                patch.Apply()
                if checkpoint:
                    patch.SetCheckpoint(*checkpoint)
                result = (dict((kind, list(patch.GetArrivedIds(kind))) for kind in ("node", "way", "relation")),
                          list(patch.GetMissingNodeIds()))
                del patch
                t.Commit()
                return result
            except BaseException:
                t.Abort()
                raise

        def held():
            return dict((key, int(value[0]["version"])) for key, value in self.contents(extract_id).items())

        extract_id = self.save("patched") # An empty extract of the rectangle -1..1

        # A node inside stays; one outside, and a way and relation with nothing inside, do not
        arrived, missing = patch_with([node(1, 0.5), node(2, 3), node(3, 4), way(10, [2, 3]),
                                       relation(20, [("way", 10)]), relation(21, [("node", 1)])])
        self.assertEqual(held(), {("node", 1): 1, ("relation", 21): 1})
        self.assertEqual((arrived, missing), ({"node": [1], "way": [], "relation": [21]}, []))

        # A way reaching in brings its other nodes, and whatever holds it or them
        arrived, missing = patch_with([node(2, 3), node(4, 5), way(11, [1, 2, 4]), relation(20, [("way", 11)]),
                                       relation(22, [("node", 4)]), relation(23, [("node", 99)])])
        self.assertEqual(held(), {("node", 1): 1, ("node", 2): 1, ("node", 4): 1, ("way", 11): 1,
                                  ("relation", 20): 1, ("relation", 21): 1, ("relation", 22): 1})
        self.assertEqual(arrived, {"node": [2, 4], "way": [11], "relation": [20, 22]})
        # A node the way uses but nobody sent is reported, and filled in later
        arrived, missing = patch_with([way(11, [1, 2, 4, 5], version=2)])
        self.assertEqual((arrived["node"], missing), ([], [5]))
        arrived, missing = patch_with([node(5, 6)])
        self.assertEqual((arrived["node"], missing, held()[("way", 11)]), ([5], [], 2))

        # A higher version replaces, the same version too, a lower one is ignored
        patch_with([node(4, 7, version=3)])
        patch_with([node(4, 8, version=2), way(11, [1], version=1)])
        self.assertEqual((held()[("node", 4)], held()[("way", 11)]), (3, 2))
        self.assertIn('lon="7', self.export_document(extract_id))
        # Sent together, the highest version of each is the one applied
        patch_with([node(2, 3, version=5), node(2, 3, version=4)], removed=[("node", 5, 3), ("node", 5, 9)])
        self.assertEqual(held()[("node", 2)], 5)
        self.assertNotIn(("node", 5), held())
        patch_with([node(5, 6, version=9)])

        # Deletions: of a relation, then of the node that kept the way, which takes the rest with it
        patch_with(removed=[("relation", 22, 2), ("relation", 21, 1), ("relation", 99, 1)])
        self.assertNotIn(("relation", 22), held())
        self.assertNotIn(("relation", 21), held())
        patch_with([node(1, 2.5, version=2)], removed=[("node", 4, 2)])
        self.assertEqual(held(), {})

        # The checkpoint is set when asked, and the extract no longer counts as a bbox-mode one
        self.assertNotEqual(self.checkpoint(extract_id), (41, 17))
        patch_with([node(1, 0.5, version=3)], checkpoint=(41, 17))
        self.assertEqual(self.checkpoint(extract_id), (41, 17))
        self.assertEqual(held(), {("node", 1): 3})

        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            with self.assertRaisesRegex(RuntimeError, "Extract not found"):
                t.StartPatchExtract(99999)
        finally:
            t.Abort()

    # What an edit activity record says about the relations around an edit

    def activity_records(self):
        """The published records, as (action, IDs of the relatedparents relations or None)."""
        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?first_id=1"))
        records = []
        for row in root.findall("editactivity"):
            holder = row.find("relatedparents")
            records.append((row.get("action"), None if holder.get("null") == "true" else
                            sorted(int(r.get("id")) for r in holder.findall("relation"))))
        return records

    def test_activity_records_relations_of_related_nodes(self):
        first = self.create_node(0)
        second = self.create_node(0.1)
        third = self.create_node(0.2)
        holds_second = self.create_relation([("node", second, "")])
        way = self.create_way([first, second])
        # Creating the way: its nodes are related, and the relation holding one of them is recorded
        self.assertEqual(self.activity_records()[-1], ("create", [holds_second.objId]))
        # A relation that is itself edited, or an affected parent, is recorded as that, not again here
        holds_way = self.create_relation([("way", way, ""), ("node", third, "")])
        self.assertEqual(self.activity_records()[-1], ("create", []))
        first = self.modify_node(first, 0.05)
        # Moving a node: its way is an affected parent and the way's other node is related
        self.assertEqual(self.activity_records()[-1], ("modify", [holds_second.objId]))
        self.modify_way(way, [first, second, third])
        self.assertEqual(self.activity_records()[-1], ("modify", [holds_second.objId]))
        self.upload("delete", [holds_second])
        self.assertEqual(self.activity_records()[-1], ("delete", []))
        # Each is the relation as it was then: the record keeps its version
        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?first_id=1"))
        kept = [r for row in root.findall("editactivity") for r in row.find("relatedparents")]
        self.assertEqual(set((r.tag, r.get("version"), r.find("member").get("ref")) for r in kept),
                         {("relation", "1", str(second.objId))})

    def test_table_from_before_the_added_columns_says_what_to_do(self):
        # A map at schema 14 from before a column was added must be brought up
        # to date; until then an edit is refused, with the reason, and not half recorded
        self.create_node(0)
        table = sql.Identifier(self.prefixes[1] + "edit_activity")
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(table))
            before = cursor.fetchone()[0]
            cursor.execute(sql.SQL("ALTER TABLE {} DROP COLUMN relation_reach_bbox").format(table))
        with self.assertRaisesRegex(AssertionError, "ask the admin tool for the latest schema version again"):
            self.create_node(0.1)
        from replicate.remoteupdate import RemoteUpdateError
        with self.assertRaises(RemoteUpdateError):
            self.fetch(self.REMOTE + "replication/edit_activities")
        # Asking for schema 14 again puts it right
        admin = self.map.GetAdmin("")
        error = pgmap.PgMapError()
        try:
            self.assertTrue(admin.CreateMapTables(0, 15, False, error), error.errStr)
            admin.Commit()
        except BaseException:
            admin.Abort()
            raise
        finally:
            del admin
        self.create_node(0.2)
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(table))
            self.assertEqual(cursor.fetchone()[0], before + 1)
            # The column the table began with, which nothing filled in, has gone
            cursor.execute("SELECT count(*) FROM information_schema.columns WHERE table_name=%s AND column_name='bbox'",
                           (self.prefixes[1] + "edit_activity",))
            self.assertEqual(cursor.fetchone()[0], 0)

    # The box each existing way or relation had before an edit

    def existing_boxes(self):
        """For each published record: the action, the existing objects, and their existing_bbox entries."""
        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?first_id=1"))
        records = []
        for row in root.findall("editactivity"):
            existing = [(e.tag, int(e.get("id"))) for e in row.find("existing")]
            holder = row.find("existing_bbox")
            if holder.get("null") == "true":
                boxes = None
            else:
                boxes = [(b.get("type"), int(b.get("id")), None if b.get("null") == "true" else
                          tuple(float(b.get(k)) for k in ("minlon", "minlat", "maxlon", "maxlat")))
                         for b in holder.findall("bbox")]
            records.append((row.get("action"), existing, boxes))
        return records

    def test_activity_records_existing_bboxes(self):
        a = self.create_node(0.1, 0.2)
        b = self.create_node(0.3, 0.5)
        c = self.create_node(0.6, 0.1)
        way = self.create_way([a, b])
        relation = self.create_relation([("way", way, ""), ("node", c, "")])
        # Nothing existed before a creation
        self.assertEqual(self.existing_boxes()[-1], ("create", [], []))

        # A way: the box around its nodes as they were before the edit
        way = self.modify_way(way, [a, b, c])
        self.assertEqual(self.existing_boxes()[-1],
                         ("modify", [("way", way.objId)], [("way", way.objId, (0.1, 0.2, 0.3, 0.5))]))
        # and after that edit it is the larger one
        way = self.modify_way(way, [a, b])
        self.assertEqual(self.existing_boxes()[-1][2], [("way", way.objId, (0.1, 0.1, 0.6, 0.5))])

        # A node's entry is the position it had
        a = self.modify_node(a, 0.15)
        self.assertEqual(self.existing_boxes()[-1],
                         ("modify", [("node", a.objId)], [("node", a.objId, (0.1, 0.2, 0.1, 0.2))]))

        # A relation, and several objects edited together: one entry each, in the recorded order
        relation = self.modify_relation(relation, [("way", way, "outer")])
        action, existing, boxes = self.existing_boxes()[-1]
        self.assertEqual((action, existing), ("modify", [("relation", relation.objId)]))
        self.assertEqual([entry[:2] for entry in boxes], existing)
        self.assertEqual(len(boxes[0][2]), 4)
        self.upload("delete", [relation])
        b2 = pgmap.OsmNode(b)
        b2.lat = 0.9
        way2 = pgmap.OsmWay(way)
        self.upload("modify", [b2, way2])
        action, existing, boxes = self.existing_boxes()[-1]
        self.assertEqual(sorted(existing), sorted([("node", b.objId), ("way", way.objId)]))
        self.assertEqual(len(boxes), len(existing))
        self.assertEqual(dict((entry[:2], entry[2]) for entry in boxes),
                         {("node", b.objId): (0.3, 0.5, 0.3, 0.5), ("way", way.objId): (0.15, 0.2, 0.3, 0.5)})
        # Every record has as many entries as existing objects
        for action, existing, boxes in self.existing_boxes():
            self.assertEqual(len(boxes), len(existing), action)

        # Stored as geometry: a collection with one member for each existing
        # object, a point for the node, which the database can search by area
        table = sql.Identifier(self.prefixes[1] + "edit_activity")
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT GeometryType(existing_bbox), ST_SRID(existing_bbox), "
                "ST_NumGeometries(existing_bbox), ST_AsText(existing_bbox) FROM {} ORDER BY id DESC LIMIT 1").format(table))
            kind, srid, count, text = cursor.fetchone()
            self.assertEqual((kind, srid, count), ("GEOMETRYCOLLECTION", 4326, 2))
            self.assertIn("POINT(0.3 0.5)", text)
            self.assertIn("POLYGON((0.15 0.2,0.15 0.5,0.3 0.5,0.3 0.2,0.15 0.2))", text)
            # A creation, with nothing existing, has an empty collection, not NULL
            cursor.execute(sql.SQL("SELECT ST_AsText(existing_bbox) FROM {} WHERE action='create' LIMIT 1").format(table))
            self.assertEqual(cursor.fetchone(), ("GEOMETRYCOLLECTION EMPTY",))
            # Records whose existing ways or relations reached into an area
            for area, expected in (((0.25, 0.25, 0.35, 0.35), True), ((5, 5, 6, 6), False)):
                cursor.execute(sql.SQL("SELECT count(*) FROM {} WHERE existing_bbox && ST_MakeEnvelope(%s,%s,%s,%s,4326)").format(table), area)
                self.assertEqual(cursor.fetchone()[0] > 0, expected)

        from replicate.views import collection_boxes
        self.assertEqual(collection_boxes("GEOMETRYCOLLECTION EMPTY"), [])
        self.assertEqual(collection_boxes("GEOMETRYCOLLECTION(POLYGON EMPTY,POLYGON((1 2,1 4,3 4,3 2,1 2)),"
                                          "POINT(-5.5 6e0),POLYGON EMPTY)"),
                         [None, (1.0, 2.0, 3.0, 4.0), (-5.5, 6.0, -5.5, 6.0), None])
        with self.assertRaises(ValueError):
            collection_boxes("POINT(1 2)")

    # Diffs applied by the admin tool are recorded as edits

    def apply_diff(self, blocks):
        """Apply an osmChange made of (action, object XML) blocks, as the admin tool's "Apply diffs" does."""
        import gzip, os, tempfile
        document = '<?xml version="1.0" encoding="UTF-8"?>\n<osmChange version="0.6">' + "".join(
            "<{0}>{1}</{0}>".format(action, xml) for action, xml in blocks) + "</osmChange>"
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "001.osc.gz")
        with gzip.open(path, "wb") as handle:
            handle.write(document.encode("utf-8"))
        error = pgmap.PgMapError()
        admin = self.map.GetAdmin("EXCLUSIVE")
        try:
            self.assertTrue(admin.ApplyDiffs(folder, 0, error), error.errStr)
            admin.Commit()
        except BaseException:
            admin.Abort()
            raise
        finally:
            del admin
            os.remove(path)
            os.rmdir(folder)

    def test_applied_diffs_are_recorded_as_edit_activity(self):
        a = self.create_node(0.2)
        b = self.create_node(0.4)
        way = self.create_way([a, b])
        far = self.create_node(40.0)
        other = self.create_node(41.0)
        farWay = self.create_way([far, other])
        extract_id = self.save()
        self.assert_current(extract_id)
        before = self.checkpoint(extract_id)

        meta = 'changeset="77" uid="5" user="importer" timestamp="2026-03-01T10:00:00Z"'
        def node(obj_id, version, lon, lat=0):
            return '<node id="{}" version="{}" lon="{}" lat="{}" {}/>'.format(obj_id, version, lon, lat, meta)
        newNode, newRelation = 900001, 900002
        self.apply_diff([
            ("create", node(newNode, 1, 0.5)),
            ("modify", node(a.objId, 2, 0.25)),
            ("modify", '<way id="{}" version="2" {}><nd ref="{}"/><nd ref="{}"/></way>'.format(
                way.objId, meta, a.objId, newNode)),
            ("delete", node(b.objId, 2, 0.4)),
            # A relation far from the rectangle, then its node's way is brought in
            ("create", '<relation id="{}" version="1" {}><member type="node" ref="{}" role="stop"/></relation>'.format(
                newRelation, meta, far.objId)),
            ("modify", node(other.objId, 2, 0.6)),
        ])

        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?first_id={}".format(before[0] + 1)))
        rows = root.findall("editactivity")
        def listed(row, section):
            return [(e.tag, int(e.get("id")), int(e.get("version"))) for e in row.find(section)]
        self.assertEqual([row.get("action") for row in rows], ["create", "modify", "modify", "delete", "create", "modify"])
        # One diff file is one atomic edit, after the uploads before it
        self.assertEqual(len(set(row.get("atomic_edit_id") for row in rows)), 1)
        self.assertGreater(int(rows[0].get("atomic_edit_id")), before[1])
        self.assertEqual([int(row.get("block_index")) for row in rows], list(range(6)))
        self.assertEqual((rows[0].get("changeset"), rows[0].get("timestamp")), ("77", "2026-03-01T10:00:00Z"))
        # The lists follow the rules of an upload
        self.assertEqual((listed(rows[0], "existing"), listed(rows[0], "updated")), ([], [("node", newNode, 1)]))
        self.assertEqual(listed(rows[1], "existing"), [("node", a.objId, 1)])
        self.assertEqual(listed(rows[1], "updated"), [("node", a.objId, 2)])
        self.assertEqual(listed(rows[1], "affectedparents"), [("way", way.objId, 1)])
        self.assertEqual(listed(rows[1], "related"), [("node", b.objId, 1)])
        self.assertEqual(listed(rows[2], "existing"), [("way", way.objId, 1)])
        self.assertEqual(listed(rows[2], "updated"), [("way", way.objId, 2)])
        self.assertEqual(sorted(listed(rows[2], "related")), sorted([("node", a.objId, 2), ("node", newNode, 1)]))
        self.assertEqual(listed(rows[3], "existing"), [("node", b.objId, 1)])
        self.assertEqual(listed(rows[4], "related"), [("node", far.objId, 1)])
        self.assertEqual(listed(rows[5], "affectedparents"), [("way", farWay.objId, 1)])
        self.assertEqual(listed(rows[5], "related"), [("node", far.objId, 1)])
        self.assertEqual(listed(rows[5], "relatedparents"), [("relation", newRelation, 1)])
        # and so do the boxes
        def boxes(row, section):
            return [None if e.get("null") == "true" else tuple(float(e.get(k)) for k in ("minlon", "minlat", "maxlon", "maxlat"))
                    for e in row.find(section).findall("bbox")]
        self.assertEqual(boxes(rows[1], "existing_bbox"), [(0.2, 0.0, 0.2, 0.0)])
        self.assertEqual(boxes(rows[1], "updated_bbox"), [(0.25, 0.0, 0.25, 0.0)])
        self.assertEqual(boxes(rows[1], "affectedparents_before_bbox"), [(0.2, 0.0, 0.4, 0.0)])
        self.assertEqual(boxes(rows[1], "affectedparents_after_bbox"), [(0.25, 0.0, 0.4, 0.0)])
        self.assertEqual(boxes(rows[2], "updated_bbox"), [(0.25, 0.0, 0.5, 0.0)])
        self.assertEqual(boxes(rows[3], "updated_bbox"), [None])
        self.assertEqual(boxes(rows[5], "affectedparents_after_bbox"), [(0.6, 0.0, 40.0, 0.0)])
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT ST_XMin(relation_reach_bbox), ST_XMax(relation_reach_bbox) "
                "FROM {} WHERE id=%s").format(sql.Identifier(self.prefixes[1] + "edit_activity")),
                (int(rows[4].get("id")),))
            self.assertEqual(cursor.fetchone(), (40.0, 41.0))

        # The stored extract no longer counts as up to date, and updating it
        # from the records gives what the map now has in the rectangle
        self.update(extract_id)
        self.assert_current(extract_id)
        self.assertEqual(set(self.contents(extract_id)), {
            ("node", a.objId), ("node", newNode), ("way", way.objId),
            ("node", other.objId), ("node", far.objId), ("way", farWay.objId), ("relation", newRelation)})
        self.assertEqual(self.compare(extract_id)[1], [])

        # A second diff is a second atomic edit
        self.apply_diff([("modify", node(newNode, 2, 0.55)), ("delete", node(other.objId, 3, 0.6))])
        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?limit=2"))
        later = root.findall("editactivity")
        self.assertEqual([row.get("action") for row in later], ["modify", "delete"])
        self.assertEqual(int(later[0].get("atomic_edit_id")), int(rows[0].get("atomic_edit_id")) + 1)
        self.assertEqual([int(row.get("block_index")) for row in later], [0, 1])

    def test_store_handles_many_objects_and_repeats_in_one_block(self):
        # More objects than one batch of the store holds, in one upload
        count = 1100
        nodes = []
        for index in range(count):
            node = pgmap.OsmNode()
            node.objId = -index - 1
            node.lon, node.lat = 0.0001 * index, 0.5
            node.tags["n"] = "it's {}".format(index)
            nodes.append(node)
        created = self.upload("create", nodes)[0]
        self.assertEqual(len(set(n.objId for n in created)), count)
        way = self.create_way(created[:600])
        relation = self.create_relation([("way", way, "a'b"), ("node", created[0], "")])
        moved = []
        for node in created:
            copy = pgmap.OsmNode(node)
            copy.lat = 0.6
            moved.append(copy)
        moved = self.upload("modify", moved)[0]
        self.upload("delete", [pgmap.OsmNode(n) for n in moved[700:]])
        found = self.contents()
        self.assertEqual(sum(1 for key in found if key[0] == "node"), 700)
        self.assertEqual(found[("node", created[5].objId)][0]["version"], "2")
        self.assertEqual(float(found[("node", created[5].objId)][0]["lat"]), 0.6)
        self.assertEqual(len(found[("way", way.objId)][1]), 600)
        self.assertIn(("relation", relation.objId), found)
        table = lambda name: sql.Identifier(self.prefixes[1] + name)
        with self.db.cursor() as cursor:
            # Every first version is in the history, the deleted versions too, and nothing is live twice
            cursor.execute(sql.SQL("SELECT version, visible, count(*) FROM {} GROUP BY 1, 2 ORDER BY 1, 2").format(table("oldnodes")))
            self.assertEqual(cursor.fetchall(), [(1, True, count), (2, True, 400), (3, False, 400)])
            cursor.execute(sql.SQL("SELECT count(*), count(DISTINCT id) FROM {}").format(table("livenodes")))
            self.assertEqual(cursor.fetchone(), (700, 700))
            cursor.execute(sql.SQL("SELECT count(*), min(index), max(index) FROM {} WHERE id=%s").format(table("way_mems")), (way.objId,))
            self.assertEqual(cursor.fetchone(), (600, 0, 599))
            cursor.execute(sql.SQL("SELECT tags->>'n' FROM {} WHERE id=%s").format(table("livenodes")), (created[7].objId,))
            self.assertEqual(cursor.fetchone(), ("it's 7",))

        # A diff can hold several versions of one object in a single block:
        # each is stored in turn, the last one ending up live
        meta = 'changeset="77" uid="5" user="importer" timestamp="2026-03-01T10:00:00Z"'
        target = created[3].objId
        self.apply_diff([("modify", "".join('<node id="{}" version="{}" lon="{}" lat="0.7" {}/>'.format(
            target, version, 0.001 * version, meta) for version in (3, 4, 5)))])
        self.assertEqual(self.contents()[("node", target)][0]["version"], "5")
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT version FROM {} WHERE id=%s ORDER BY version").format(table("oldnodes")), (target,))
            self.assertEqual([row[0] for row in cursor.fetchall()], [1, 2, 3, 4])
            cursor.execute(sql.SQL("SELECT version, ST_X(geom) FROM {} WHERE id=%s").format(table("livenodes")), (target,))
            self.assertEqual(cursor.fetchall(), [(5, 0.005)])

    # Importing a map file straight into the static tables

    def test_import_map_file_directly(self):
        import gzip, os, tempfile
        data = pgmap.OsmData()
        awkward = "it's \"quoted\", back\\slash, tab\there, new\nline, caf\u00e9 \u6771\u4eac"
        def meta(obj, obj_id, version, visible=True, user="mapper"):
            obj.objId = obj_id
            obj.metaData.version = version
            obj.metaData.visible = visible
            obj.metaData.timestamp = 1700000000 + obj_id
            obj.metaData.changeset = 40 + obj_id
            if user:
                obj.metaData.uid = 7
                obj.metaData.username = user
            return obj
        for obj_id, lon, lat in ((101, 0.1, 0.2), (102, 0.3, 0.4), (103, -0.25, 0.123456789)):
            node = meta(pgmap.OsmNode(), obj_id, 2)
            node.lon, node.lat = lon, lat
            data.nodes.append(node)
        data.nodes[0].tags["name"] = awkward
        anonymous = meta(pgmap.OsmNode(), 104, 1, user="")
        anonymous.metaData.changeset = 0
        anonymous.metaData.timestamp = 0
        anonymous.lon, anonymous.lat = 0.5, 0.5
        data.nodes.append(anonymous)
        deleted = meta(pgmap.OsmNode(), 105, 3, visible=False, user="o'brien, \"x\"")
        deleted.lon, deleted.lat = 0.6, 0.6
        data.nodes.append(deleted)
        way = meta(pgmap.OsmWay(), 201, 4)
        for ref in (101, 102, 103, 101):
            way.refs.append(ref)
        way.tags["highway"] = "path"
        way.tags[awkward] = awkward
        data.ways.append(way)
        inner = meta(pgmap.OsmRelation(), 301, 1)
        inner.AddMember("node", 104, "")
        data.relations.append(inner)
        relation = meta(pgmap.OsmRelation(), 302, 5)
        relation.AddMember("way", 201, "outer")
        relation.AddMember("node", 101, awkward)
        relation.AddMember("relation", 301, "sub")
        relation.AddMember("node", 102, "")
        relation.tags["type"] = "site"
        data.relations.append(relation)

        def run_import(path):
            error = pgmap.PgMapError()
            admin = self.map.GetAdmin("")
            try:
                ok = admin.ImportMapFile(0, path, error)
                admin.Commit() if ok else admin.Abort()
            except BaseException:
                admin.Abort()
                raise
            finally:
                del admin
            return ok, error.errStr

        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "import.o5m.gz")
        try:
            buffer = io.BytesIO()
            data.StreamTo(pgmap.PyO5mEncode(buffer))
            with gzip.open(path, "wb") as handle:
                handle.write(buffer.getvalue())
            self.assertEqual(run_import(path), (True, ""))
            # A file that is not there, or of a kind that is not known, is refused with the reason
            ok, message = run_import(os.path.join(folder, "missing.o5m"))
            self.assertFalse(ok)
            self.assertNotEqual(message, "")
            ok, message = run_import(os.path.join(folder, "import.txt"))
            self.assertFalse(ok)
        finally:
            os.remove(path)
            os.rmdir(folder)

        # The map now has what the file had
        found = self.contents()
        self.assertEqual(sorted(found), [("node", 101), ("node", 102), ("node", 103), ("node", 104),
                                         ("relation", 301), ("relation", 302), ("way", 201)])
        attrs, children = found[("node", 101)][0], found[("node", 101)][1]
        self.assertEqual((attrs["version"], attrs["user"], attrs["uid"], attrs["changeset"]), ("2", "mapper", "7", "141"))
        self.assertEqual((float(attrs["lon"]), float(attrs["lat"])), (0.1, 0.2))
        # To the seven decimal places an o5m file keeps
        self.assertEqual(float(found[("node", 103)][0]["lat"]), 0.1234568)
        self.assertNotIn("user", found[("node", 104)][0])
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            stored = pgmap.OsmData()
            t.GetObjectsById("node", [101], stored)
            t.GetObjectsById("way", [201], stored)
            t.GetObjectsById("relation", [302], stored)
            self.assertEqual(stored.nodes[0].tags["name"], awkward)
            self.assertEqual(list(stored.ways[0].refs), [101, 102, 103, 101])
            self.assertEqual(dict(stored.ways[0].tags), {"highway": "path", awkward: awkward})
            self.assertEqual([(m.TypeName(), m.ref, m.role) for m in stored.relations[0].members],
                             [("way", 201, "outer"), ("node", 101, awkward), ("relation", 301, "sub"), ("node", 102, "")])
        finally:
            t.Abort()

        # In the static tables, as the csv route leaves them
        table = lambda name: sql.Identifier(self.prefixes[0] + name)
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT id, version, visible, username FROM {}").format(table("oldnodes")))
            self.assertEqual(cursor.fetchall(), [(105, 3, False, "o'brien, \"x\"")])
            cursor.execute(sql.SQL("SELECT username, uid, changeset, timestamp, ST_SRID(geom) FROM {} WHERE id=104").format(table("livenodes")))
            self.assertEqual(cursor.fetchall(), [(None, None, None, None, 4326)])
            for name, expected in (("nodeids", [101, 102, 103, 104, 105]), ("wayids", [201]), ("relationids", [301, 302])):
                cursor.execute(sql.SQL("SELECT id FROM {} ORDER BY id").format(table(name)))
                self.assertEqual([row[0] for row in cursor.fetchall()], expected, name)
            cursor.execute(sql.SQL("SELECT id, version, index, member FROM {} ORDER BY index").format(table("way_mems")))
            self.assertEqual(cursor.fetchall(), [(201, 4, 0, 101), (201, 4, 1, 102), (201, 4, 2, 103), (201, 4, 3, 101)])
            for name, expected in (("relation_mems_n", [(301, 1, 0, 104), (302, 5, 1, 101), (302, 5, 3, 102)]),
                                   ("relation_mems_w", [(302, 5, 0, 201)]), ("relation_mems_r", [(302, 5, 2, 301)])):
                cursor.execute(sql.SQL("SELECT id, version, index, member FROM {} ORDER BY id, index").format(table(name)))
                self.assertEqual(cursor.fetchall(), expected, name)
            cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(self.prefixes[1] + "livenodes")))
            self.assertEqual(cursor.fetchone()[0], 0)

    # Schema version 15: member tables of live versions only, with a key

    def set_schema(self, version):
        admin = self.map.GetAdmin("")
        error = pgmap.PgMapError()
        try:
            self.assertTrue(admin.CreateMapTables(0, version, False, error), error.errStr)
            admin.Commit()
        except BaseException:
            admin.Abort()
            raise
        finally:
            del admin

    def member_rows(self, suffix, prefix=None):
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT id, version, index, member FROM {} ORDER BY id, version, index, member").format(
                sql.Identifier((prefix or self.prefixes[1]) + suffix)))
            return cursor.fetchall()

    def test_member_tables_hold_live_versions_only(self):
        a = self.create_node(0.1)
        b = self.create_node(0.2)
        c = self.create_node(0.3)
        way = self.create_way([a, b])
        relation = self.create_relation([("way", way, ""), ("node", a, ""), ("node", c, "")])
        self.assertEqual(self.member_rows("way_mems"), [(way.objId, 1, 0, a.objId), (way.objId, 1, 1, b.objId)])
        self.assertEqual(self.member_rows("relation_mems_n"),
                         [(relation.objId, 1, 1, a.objId), (relation.objId, 1, 2, c.objId)])
        self.assertEqual(self.member_rows("relation_mems_w"), [(relation.objId, 1, 0, way.objId)])

        # Moving a node rewrites the way holding it at the same version: its rows stay as they are
        a = self.modify_node(a, 0.15)
        self.assertEqual(self.member_rows("way_mems"), [(way.objId, 1, 0, a.objId), (way.objId, 1, 1, b.objId)])
        # A new version replaces the rows of the old one
        way = self.modify_way(way, [b, c, a])
        self.assertEqual(self.member_rows("way_mems"),
                         [(way.objId, 2, 0, b.objId), (way.objId, 2, 1, c.objId), (way.objId, 2, 2, a.objId)])
        relation = self.modify_relation(relation, [("node", b, "")])
        self.assertEqual(self.member_rows("relation_mems_n"), [(relation.objId, 2, 0, b.objId)])
        self.assertEqual(self.member_rows("relation_mems_w"), [])
        # and a deleted object has none, while its history keeps its members
        self.upload("delete", [relation])
        self.assertEqual(self.member_rows("relation_mems_n"), [])
        self.upload("delete", [way])
        self.assertEqual(self.member_rows("way_mems"), [])
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT version, visible, jsonb_array_length(members) FROM {} WHERE id=%s ORDER BY version").format(
                sql.Identifier(self.prefixes[1] + "oldways")), (way.objId,))
            self.assertEqual(cursor.fetchall(), [(1, True, 2), (2, True, 3), (3, False, 3)])
            # The same row cannot be there twice
            cursor.execute(sql.SQL("INSERT INTO {} VALUES (9, 1, 0, 5)").format(sql.Identifier(self.prefixes[1] + "way_mems")))
            with self.assertRaises(psycopg2.IntegrityError):
                cursor.execute(sql.SQL("INSERT INTO {} VALUES (9, 1, 0, 5)").format(sql.Identifier(self.prefixes[1] + "way_mems")))
            cursor.execute(sql.SQL("DELETE FROM {} WHERE id=9").format(sql.Identifier(self.prefixes[1] + "way_mems")))
            # The views join their two halves without comparing rows
            for kind in ("nodes", "ways", "relations"):
                cursor.execute("SELECT pg_get_viewdef(%s::regclass)", (self.prefixes[1] + "visible" + kind,))
                self.assertIn("UNION ALL", cursor.fetchone()[0], kind)

    def test_schema_15_rebuilds_member_tables(self):
        # As a map at version 14 is after some editing: rows of versions that
        # are no longer live, rows of deleted objects, and rows added twice
        self.set_schema(14)
        with self.db.cursor() as cursor:
            cursor.execute("SELECT pg_get_viewdef(%s::regclass)", (self.prefixes[1] + "visibleways",))
            self.assertNotIn("UNION ALL", cursor.fetchone()[0])
        a = self.create_node(0.1)
        b = self.create_node(0.2)
        c = self.create_node(0.3)
        way = self.create_way([a, b])
        gone = self.create_way([b, c])
        inner = self.create_relation([("node", a, "")])
        relation = self.create_relation([("way", way, ""), ("node", c, ""), ("relation", inner, "")])
        way = self.modify_way(way, [a, b, c])
        self.upload("delete", [gone])
        self.assertEqual(len(self.member_rows("way_mems")), 2 + 3 + 2 + 2)
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("INSERT INTO {0} SELECT * FROM {0}").format(sql.Identifier(self.prefixes[1] + "way_mems")))
            cursor.execute(sql.SQL("CREATE INDEX {} ON {} (member)").format(
                sql.Identifier(self.prefixes[1] + "way_mems_mids"), sql.Identifier(self.prefixes[1] + "way_mems")))
        before = self.contents()

        self.set_schema(15)
        self.assertEqual(self.member_rows("way_mems"),
                         [(way.objId, 2, 0, a.objId), (way.objId, 2, 1, b.objId), (way.objId, 2, 2, c.objId)])
        self.assertEqual(self.member_rows("relation_mems_n"),
                         sorted([(inner.objId, 1, 0, a.objId), (relation.objId, 1, 1, c.objId)]))
        self.assertEqual(self.member_rows("relation_mems_w"), [(relation.objId, 1, 0, way.objId)])
        self.assertEqual(self.member_rows("relation_mems_r"), [(relation.objId, 1, 2, inner.objId)])
        with self.db.cursor() as cursor:
            cursor.execute("SELECT indexdef FROM pg_indexes WHERE tablename=%s ORDER BY indexname", (self.prefixes[1] + "way_mems",))
            definitions = [row[0] for row in cursor.fetchall()]
            self.assertTrue(any("UNIQUE" in d and "(id, version, index)" in d for d in definitions), definitions)
            # The index of members it had is put back
            self.assertTrue(any(d.rstrip().endswith("(member)") for d in definitions), definitions)
            cursor.execute(sql.SQL("SELECT value FROM {} WHERE key='schema_version'").format(sql.Identifier(self.prefixes[1] + "meta")))
            self.assertEqual(cursor.fetchone(), ("15",))
        # The map reads the same, and goes on working
        self.assertEqual(self.contents(), before)
        way = self.modify_way(way, [c, a])
        self.assertEqual(self.member_rows("way_mems"), [(way.objId, 3, 0, c.objId), (way.objId, 3, 1, a.objId)])
        extract_id = self.save()
        self.modify_node(a, 0.12)
        self.update(extract_id)
        self.assert_current(extract_id)

        # Back at 14 the key and the newer views go, and the map still works
        self.set_schema(14)
        with self.db.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_indexes WHERE tablename=%s AND indexdef LIKE '%%UNIQUE%%'", (self.prefixes[1] + "way_mems",))
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT pg_get_viewdef(%s::regclass)", (self.prefixes[1] + "visibleways",))
            self.assertNotIn("UNION ALL", cursor.fetchone()[0])
        self.modify_way(way, [a, c])
        self.assertIn(("way", way.objId), self.contents())
        self.set_schema(15)
        self.assertEqual(len(self.member_rows("way_mems")), 2)

    # Searching the records by area, and the index for changeset counts

    def filtered_ids(self, bbox):
        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?first_id=1&bbox=" + bbox))
        return [int(row.get("id")) for row in root.findall("editactivity")]

    def test_edit_activity_extent_and_bbox_filter(self):
        def latest():
            root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?limit=1"))
            return int(root.find("editactivity").get("id"))
        a = self.create_node(0.1, 0.2)
        createA = latest()
        b = self.create_node(0.3, 0.5)
        way = self.create_way([a, b])
        createWay = latest()
        c = self.create_node(5.0, 5.0)
        createC = latest()
        relation = self.create_relation([("way", way, ""), ("node", c, "")])
        createRelation = latest()

        # An area inside the way's box that holds none of its nodes: the way
        # and the relation reach into it, the nodes on their own do not
        inside = "0.19,0.3,0.21,0.4"
        self.assertEqual(self.filtered_ids(inside), [createWay, createRelation])
        self.assertEqual(self.filtered_ids("4.9,4.9,5.1,5.1"), [createC, createRelation])
        self.assertEqual(self.filtered_ids("0.05,0.15,0.15,0.25"), [createA, createWay, createRelation])

        # Moving a node concerns wherever the way and relation holding it reach,
        # before or after, though the record's only updated object is the node
        a = self.modify_node(a, 0.05)
        moveA = latest()
        self.assertEqual(self.filtered_ids(inside), [createWay, createRelation, moveA])
        self.assertEqual(self.filtered_ids("0.04,0.19,0.06,0.21"), [moveA])
        self.assertEqual(self.filtered_ids("4.9,4.9,5.1,5.1"), [createC, createRelation, moveA])
        # A node leaving an area is found there by where it was
        c = self.modify_node(c, 8.0)
        moveC = latest()
        self.assertEqual(self.filtered_ids("4.9,4.9,5.1,5.1"), [createC, createRelation, moveA, moveC])
        self.assertEqual(self.filtered_ids("7.9,4.9,8.1,5.1"), [moveC])
        # A deleted relation is found by where it was
        self.upload("delete", [relation])
        deleteRelation = latest()
        self.assertEqual(self.filtered_ids("7.9,4.9,8.1,5.1"), [moveC, deleteRelation])
        self.assertEqual(self.filtered_ids("50,50,51,51"), [])

        table = sql.Identifier(self.prefixes[1] + "edit_activity")
        with self.db.cursor() as cursor:
            # Each of the geometry columns can be searched through an index
            cursor.execute("SELECT indexdef FROM pg_indexes WHERE tablename=%s",
                           (self.prefixes[1] + "edit_activity",))
            definitions = [row[0] for row in cursor.fetchall()]
            for column in ("existing_bbox", "updated_bbox", "affectedparents_before_bbox",
                           "affectedparents_after_bbox", "relation_reach_bbox"):
                self.assertTrue(any("USING gist ({})".format(column) in d for d in definitions), column)
            self.assertFalse(any("USING gist (bbox)" in d for d in definitions))
            # The reach is set only where a relation matters beyond its own box, which is nowhere here
            cursor.execute(sql.SQL("SELECT count(*) FROM {} WHERE relation_reach_bbox IS NOT NULL").format(table))
            self.assertEqual(cursor.fetchone()[0], 0)
            # A record that does not say where its objects are could concern anywhere
            cursor.execute(sql.SQL("UPDATE {} SET updated_bbox=NULL WHERE id=%s").format(table), (createA,))
        self.assertEqual(self.filtered_ids("50,50,51,51"), [createA])

        # On a map that does not store boxes a relation's reach is not known,
        # so an edit to one does not say where it is, and is always sent
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("UPDATE {} SET bbox=NULL").format(
                sql.Identifier(self.prefixes[1] + "liveways")))
        other = self.create_relation([("way", way, "")])
        createOther = latest()
        self.modify_relation(other, [("way", way, "x")])
        self.assertEqual(self.filtered_ids("50,50,51,51"), [createA, createOther, latest()])

    def test_relation_edit_is_found_through_a_way_sharing_its_member_node(self):
        # A way from far away into an area, and a relation holding only the
        # way's far node. An extract of the area has the way, so the far node,
        # so the relation, though the relation is nowhere near.
        near = self.create_node(0.5)
        far = self.create_node(40.0, 10.0)
        way = self.create_way([near, far])
        def latest():
            root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?limit=1"))
            return int(root.find("editactivity").get("id"))
        area = "0.4,-0.1,0.6,0.1"
        found = self.filtered_ids(area)
        relation = self.create_relation([("node", far, "stop")])
        created = latest()
        relation = self.modify_relation(relation, [("node", far, "halt")])
        modified = latest()
        self.upload("delete", [relation])
        deleted = latest()
        self.assertEqual(self.filtered_ids(area), found + [created, modified, deleted])
        # The relation's own box is the far node; the way's is what reaches the area
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT ST_XMin(relation_reach_bbox), ST_YMin(relation_reach_bbox), "
                "ST_XMax(relation_reach_bbox), ST_YMax(relation_reach_bbox), GeometryType(relation_reach_bbox), "
                "ST_SRID(relation_reach_bbox), ST_AsText(existing_bbox) FROM {} WHERE id=%s").format(
                sql.Identifier(self.prefixes[1] + "edit_activity")), (modified,))
            self.assertEqual(cursor.fetchone(), (0.5, 0.0, 40.0, 10.0, "POLYGON", 4326,
                "GEOMETRYCOLLECTION(POLYGON((40 10,40 10,40 10,40 10,40 10)))"))
        # A relation whose member node is in no way matters only where it is
        lone = self.create_node(60.0, 20.0)
        self.create_relation([("node", lone, "")])
        self.assertEqual(self.filtered_ids(area), found + [created, modified, deleted])
        self.assertEqual(self.filtered_ids("59,19,61,21"), [latest() - 1, latest()])

    def test_edit_activity_has_changeset_index(self):
        with self.db.cursor() as cursor:
            cursor.execute("SELECT indexdef FROM pg_indexes WHERE tablename=%s",
                           (self.prefixes[1] + "edit_activity",))
            definitions = [row[0] for row in cursor.fetchall()]
        self.assertTrue(any(d.rstrip().endswith("(changeset)") for d in definitions), definitions)

    def test_published_objects_are_shared_between_records_and_complete(self):
        a = self.create_node(0.1, 0.2)
        b = self.create_node(0.3, 0.5)
        way = self.create_way([a, b])
        a2 = self.modify_node(a, 0.15)
        way2 = self.modify_way(way, [b, a2])
        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?first_id=1"))
        rows = root.findall("editactivity")[-5:]
        def listed(row, section):
            return [(e.tag, int(e.get("id")), int(e.get("version"))) for e in row.find(section)]
        self.assertEqual([row.get("action") for row in rows], ["create", "create", "create", "modify", "modify"])
        self.assertEqual(listed(rows[2], "updated"), [("way", way.objId, 1)])
        self.assertEqual(sorted(listed(rows[2], "related")), sorted([("node", a.objId, 1), ("node", b.objId, 1)]))
        # The node move: both versions of the node, the unchanged way above it, its other node
        self.assertEqual(listed(rows[3], "existing"), [("node", a.objId, 1)])
        self.assertEqual(listed(rows[3], "updated"), [("node", a.objId, 2)])
        self.assertEqual(listed(rows[3], "affectedparents"), [("way", way.objId, 1)])
        self.assertEqual(listed(rows[3], "related"), [("node", b.objId, 1)])
        self.assertEqual(listed(rows[4], "existing"), [("way", way.objId, 1)])
        self.assertEqual(listed(rows[4], "updated"), [("way", way.objId, 2)])
        self.assertEqual(sorted(listed(rows[4], "related")), sorted([("node", a.objId, 2), ("node", b.objId, 1)]))
        # The objects are whole: position, tags and members as the map has them
        moved = rows[3].find("updated")[0]
        self.assertEqual((float(moved.get("lon")), float(moved.get("lat"))), (0.15, 0.2))
        self.assertEqual([(tag.get("k"), tag.get("v")) for tag in moved.findall("tag")], [("test", "modified")])
        self.assertEqual([int(nd.get("ref")) for nd in rows[4].find("updated")[0].findall("nd")],
                         [b.objId, a.objId])
        self.assertEqual(rows[3].get("changeset"), str(self.changeset_id))
        # One record on its own is published the same way
        single = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activity/" + rows[3].get("id")))
        self.assertEqual(ET.tostring(single.find("editactivity")), ET.tostring(rows[3]))

    # The boxes of the updated objects and of the affected parents

    def published_boxes(self, section, objects):
        """The entries of one box section of the latest published record, having
        checked there is one for each object of the list it goes with, in order."""
        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_activities?first_id=1"))
        row = root.findall("editactivity")[-1]
        listed = [(e.tag, int(e.get("id"))) for e in row.find(objects)]
        holder = row.find(section)
        if holder.get("null") == "true":
            return None
        boxes = [(b.get("type"), int(b.get("id")), None if b.get("null") == "true" else
                  tuple(float(b.get(k)) for k in ("minlon", "minlat", "maxlon", "maxlat")))
                 for b in holder.findall("bbox")]
        self.assertEqual([entry[:2] for entry in boxes], listed, section)
        return boxes

    def test_activity_records_updated_and_affected_parent_bboxes(self):
        a = self.create_node(0.1, 0.2)
        b = self.create_node(0.3, 0.5)
        c = self.create_node(0.6, 0.1)
        # A created node has its position; nothing is a parent of it
        self.assertEqual(self.published_boxes("updated_bbox", "updated"), [("node", c.objId, (0.6, 0.1, 0.6, 0.1))])
        self.assertEqual(self.published_boxes("affectedparents_before_bbox", "affectedparents"), [])
        self.assertEqual(self.published_boxes("affectedparents_after_bbox", "affectedparents"), [])

        # A created way has the box around its nodes
        way = self.create_way([a, b])
        self.assertEqual(self.published_boxes("updated_bbox", "updated"),
                         [("way", way.objId, (0.1, 0.2, 0.3, 0.5))])
        relation = self.create_relation([("way", way, ""), ("node", c, "")])
        self.assertEqual(self.published_boxes("updated_bbox", "updated"),
                         [("relation", relation.objId, (0.1, 0.1, 0.6, 0.5))])

        # Moving a node leaves the way and relation holding it at the same
        # version, but with a different extent: both sides are recorded
        a = self.modify_node(a, 0.05)
        self.assertEqual(self.published_boxes("existing_bbox", "existing"), [("node", a.objId, (0.1, 0.2, 0.1, 0.2))])
        self.assertEqual(self.published_boxes("updated_bbox", "updated"), [("node", a.objId, (0.05, 0.2, 0.05, 0.2))])
        before = self.published_boxes("affectedparents_before_bbox", "affectedparents")
        after = self.published_boxes("affectedparents_after_bbox", "affectedparents")
        self.assertEqual(sorted(entry[:2] for entry in before),
                         [("relation", relation.objId), ("way", way.objId)])
        self.assertEqual(dict((entry[:2], entry[2]) for entry in before),
                         {("way", way.objId): (0.1, 0.2, 0.3, 0.5), ("relation", relation.objId): (0.1, 0.1, 0.6, 0.5)})
        self.assertEqual(dict((entry[:2], entry[2]) for entry in after),
                         {("way", way.objId): (0.05, 0.2, 0.3, 0.5), ("relation", relation.objId): (0.05, 0.1, 0.6, 0.5)})

        # A modified way: its new box, with the old one among the existing
        way = self.modify_way(way, [a, b, c])
        self.assertEqual(self.published_boxes("existing_bbox", "existing"),
                         [("way", way.objId, (0.05, 0.2, 0.3, 0.5))])
        self.assertEqual(self.published_boxes("updated_bbox", "updated"),
                         [("way", way.objId, (0.05, 0.1, 0.6, 0.5))])

        # Stored as geometry the database can search by area, aligned with the lists
        table = sql.Identifier(self.prefixes[1] + "edit_activity")
        columns = ("existing_bbox", "updated_bbox", "affectedparents_before_bbox", "affectedparents_after_bbox")
        with self.db.cursor() as cursor:
            cursor.execute("SELECT column_name, udt_name FROM information_schema.columns WHERE table_name=%s",
                           (self.prefixes[1] + "edit_activity",))
            kinds = dict(cursor.fetchall())
            for column in columns:
                self.assertEqual(kinds.get(column), "geometry", column)
            # The columns these replace are gone
            for column in ("sync_before", "sync_after", "bbox_before", "bbox_after"):
                self.assertNotIn(column, kinds)
            cursor.execute(sql.SQL("SELECT GeometryType(updated_bbox), ST_SRID(updated_bbox), ST_AsText(updated_bbox), "
                "jsonb_array_length(affectedparents), ST_NumGeometries(affectedparents_before_bbox), "
                "ST_NumGeometries(affectedparents_after_bbox) FROM {} ORDER BY id DESC LIMIT 1").format(table))
            kind, srid, text, parents, parentsBefore, parentsAfter = cursor.fetchone()
            self.assertEqual((kind, srid), ("GEOMETRYCOLLECTION", 4326))
            self.assertEqual(text, "GEOMETRYCOLLECTION(POLYGON((0.05 0.1,0.05 0.5,0.6 0.5,0.6 0.1,0.05 0.1)))")
            # The relation holding the way is its affected parent
            self.assertEqual((parents, parentsBefore, parentsAfter), (1, 1, 1))
            # The node move is found by where the way reached afterwards, not before
            area = (0.04, 0.3, 0.07, 0.4)
            for column, expected in (("affectedparents_after_bbox", True), ("affectedparents_before_bbox", False)):
                cursor.execute(sql.SQL("SELECT count(*) FROM {} WHERE action='modify' AND nodes=1 AND {} && "
                    "ST_MakeEnvelope(%s,%s,%s,%s,4326)").format(table, sql.Identifier(column)), area)
                self.assertEqual(cursor.fetchone()[0] > 0, expected, column)

        # A deleted object has no box afterwards, though it had one before
        self.upload("delete", [relation])
        self.assertEqual(self.published_boxes("updated_bbox", "updated"), [("relation", relation.objId, None)])
        self.assertEqual(len(self.published_boxes("existing_bbox", "existing")[0][2]), 4)

    def test_updated_and_parent_bboxes_without_stored_boxes(self):
        a = self.create_node(0.1, 0.2)
        b = self.create_node(0.3, 0.5)
        way = self.create_way([a, b])
        # A map that does not store boxes: a way's is worked out from its nodes
        clear = sql.SQL("UPDATE {} SET bbox=NULL").format(sql.Identifier(self.prefixes[1] + "liveways"))
        with self.db.cursor() as cursor:
            cursor.execute(clear)
        a = self.modify_node(a, 0.05)
        self.assertEqual(self.published_boxes("affectedparents_before_bbox", "affectedparents"),
                         [("way", way.objId, (0.1, 0.2, 0.3, 0.5))])
        self.assertEqual(self.published_boxes("affectedparents_after_bbox", "affectedparents"),
                         [("way", way.objId, (0.05, 0.2, 0.3, 0.5))])

    def test_existing_bbox_without_stored_boxes(self):
        a = self.create_node(0.1, 0.2)
        b = self.create_node(0.3, 0.5)
        way = self.create_way([a, b])
        relation = self.create_relation([("way", way, "")])
        # A map that does not store boxes: a way's is worked out from its nodes, a relation's is not known
        with self.db.cursor() as cursor:
            for kind in ("ways", "relations"):
                cursor.execute(sql.SQL("UPDATE {} SET bbox=NULL").format(
                    sql.Identifier(self.prefixes[1] + "live" + kind)))
        way = self.modify_way(way, [b, a])
        self.assertEqual(self.existing_boxes()[-1][2], [("way", way.objId, (0.1, 0.2, 0.3, 0.5))])
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("UPDATE {} SET bbox=NULL").format(
                sql.Identifier(self.prefixes[1] + "liverelations")))
        relation = self.modify_relation(relation, [("way", way, "x")])
        # ...so the collection is not recorded, which a search takes as anywhere
        self.assertEqual(self.existing_boxes()[-1][2], None)
