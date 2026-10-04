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
        relation.refTypeStrs.clear()
        relation.refIds.clear()
        relation.refRoles.clear()
        for kind, obj, role in members:
            relation.refTypeStrs.append(kind)
            relation.refIds.append(obj.objId)
            relation.refRoles.append(role)

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
            with patch("replicate.admin.get_pgmap", return_value=self.map):
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
            with patch("replicate.admin.get_pgmap", return_value=self.map):
                response = model_admin.detail_view(request, extract_id)
                return response, response.render().content.decode("utf-8")

        response, page_html = detail(first)
        self.assertEqual(response.status_code, 200)
        shown = response.context_data["extract"]
        self.assertEqual((shown["nodes"], shown["ways"], shown["relations"]), (2, 1, 0))
        self.assertEqual({key: shown[key] for key in rows[0]}, rows[0])
        for element, count in (("extract_nodes", 2), ("extract_ways", 1), ("extract_relations", 0)):
            self.assertIn('<td id="{}">{}</td>'.format(element, count), page_html)
        self.assertIn("first &lt;extract&gt;", page_html)
        self.assertIn('href="{}"'.format(reverse("replication:download_extract_gz_by_id", args=[first])), page_html)
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
            with patch("replicate.admin.get_pgmap", return_value=self.map):
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
        with patch("replicate.admin.get_pgmap", return_value=self.map):
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
            with patch("replicate.admin.get_pgmap", return_value=self.map):
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
            with patch("replicate.admin.get_pgmap", return_value=self.map):
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
        self.assertEqual(notes, ["Updated database extract {}.".format(first)])
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
            with patch("replicate.admin.get_pgmap", return_value=self.map):
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
