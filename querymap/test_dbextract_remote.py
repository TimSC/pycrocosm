"""The stored extract tests again, with every update taken from the web API.

An extract with an update URL is brought up to date from the edit activity
another server publishes, not from this server's map. Here the other server is
this one: its API is called through Django's test client, against the same
test map the extract was saved from. Every test of the local update therefore
also checks the remote one against the same expected contents, and against a
fresh map query.
"""
import xml.etree.ElementTree as ET
from unittest.mock import patch

from django.test import override_settings
from psycopg2 import sql

import pgmap
import querymap.test_dbextract as local
from replicate import remoteupdate
from replicate.extracts import describe_db_extract

class RemoteUpdateTestCase(local.DbExtractTestCase):
    def update(self, extract_id=0, name=""):
        t = self.map.GetTransaction("ACCESS SHARE")
        try:
            infos = pgmap.vectorextractinfo()
            t.ListExtracts(infos)
            chosen = [describe_db_extract(info) for info in infos
                      if (info.extractId == extract_id if extract_id else info.name == name)]
        finally:
            t.Abort()
        if len(chosen) == 0:
            raise RuntimeError("Extract not found")
        if len(chosen) > 1:
            raise RuntimeError("Extract name is ambiguous; select by ID")
        extract = chosen[0]
        extract["update_url"] = self.REMOTE
        try:
            self.applied = remoteupdate.update_extract_from_remote(self.map, extract, self.fetch)
        except remoteupdate.RemoteUpdateError as err:
            # The local update reports its failures as RuntimeError
            raise RuntimeError(str(err))
        return extract["id"]

    def use_stored_bboxes(self):
        """Put the map into the mode where its own queries select ways by their stored boxes."""
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("INSERT INTO {} (key, value) VALUES ('useBboxInQuery','1')").format(
                sql.Identifier(self.prefixes[1] + "meta")))

    # An extract kept up from another server always follows the rule of a map
    # without stored bounding boxes, so the two tests of the other rule are
    # replaced by ones saying so.

    def test_bbox_query_mode(self):
        self.use_stored_bboxes()
        west = self.create_node(-2)
        east = self.create_node(2)
        inside = self.create_node(0.5)
        extract_id = self.save()
        # A way crossing the rectangle with no node in it is not brought in
        self.create_way([west, east])
        reaching = self.create_way([west, inside])
        self.update(extract_id)
        self.assertEqual(set(self.contents(extract_id)),
                         {("node", west.objId), ("node", inside.objId), ("way", reaching.objId)})
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT use_bbox_in_query FROM {} WHERE id=%s").format(
                sql.Identifier(self.prefixes[1] + "extracts")), [extract_id])
            self.assertEqual(cursor.fetchone(), (False,))

    def test_bbox_query_mode_node_moves(self):
        self.use_stored_bboxes()
        west = self.create_node(-2)
        east = self.create_node(-3)
        way = self.create_way([west, east])
        extract_id = self.save()
        # Stretched across the rectangle, the way still has no node in it
        east = self.modify_node(east, 2)
        self.update(extract_id)
        self.assertEqual(self.contents(extract_id), {})
        east = self.modify_node(east, 0.5)
        self.update(extract_id)
        self.assertEqual(set(self.contents(extract_id)),
                         {("node", west.objId), ("node", east.objId), ("way", way.objId)})

    # What is particular to updating over the web API

    def test_other_server_says_where_it_has_got_to(self):
        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_ids"))
        self.assertEqual((root.tag, dict(root.attrib)), ("editids", {"edit_activity_id": "0", "atomic_edit_id": "0"}))
        self.create_node(0)
        extract_id = self.save()
        root = ET.fromstring(self.fetch(self.REMOTE + "replication/edit_ids"))
        self.assertEqual((int(root.get("edit_activity_id")), int(root.get("atomic_edit_id"))),
                         self.checkpoint(extract_id))

        # Nothing new: one question, and no rows applied
        self.fetched = []
        self.update(extract_id)
        self.assertEqual((self.fetched, self.applied), (["/replication/edit_ids"], 0))
        # Three edits: their rows are asked for and applied, and the checkpoint moves to the last
        for lon in (0.1, 0.2, 0.3):
            self.create_node(lon)
        self.fetched = []
        self.update(extract_id)
        self.assertEqual(self.applied, 3)
        self.assertEqual(len(self.fetched), 2)
        self.assert_current(extract_id)

    def test_edits_are_applied_whole_across_requests(self):
        inside = self.create_node(0)
        extract_id = self.save()
        nodes = [self.create_node(0.01 * i) for i in range(1, 8)]
        way = self.create_way(nodes[:3])
        self.modify_node(nodes[1], 5)
        self.modify_way(way, [nodes[0], nodes[4], nodes[6]])
        self.upload("delete", [nodes[5]])
        self.create_relation([("way", way, "outer"), ("node", inside, "")])
        # Two rows asked for at a time
        with patch.object(remoteupdate, "ACTIVITY_WINDOW", 2):
            self.fetched = []
            self.update(extract_id)
        self.assertGreater(sum(1 for path in self.fetched if "edit_activities" in path), 4)
        self.assert_current(extract_id)
        self.assertEqual(self.compare(extract_id)[1], [])

    def way_bringing_in_a_node_with_a_relation(self):
        """Everything starts outside the rectangle: a way, and a relation that
        holds one of the way's nodes. Neither is in the extract. Then the
        way's other node is moved inside, which brings in the way and with it
        the far node, and so the relation. The edit did not touch the relation."""
        far = self.create_node(3)
        mover = self.create_node(4)
        way = self.create_way([far, mover])
        relation = self.create_relation([("node", far, "marker")])
        anchor = self.create_node(-0.5)
        extract_id = self.save()
        self.assertEqual(set(self.contents(extract_id)), {("node", anchor.objId)})
        mover = self.modify_node(mover, 0.5)
        expected = {("node", anchor.objId), ("node", far.objId), ("node", mover.objId),
                    ("way", way.objId), ("relation", relation.objId)}
        return extract_id, far, mover, anchor, expected

    def test_edit_records_carry_all_that_an_update_needs(self):
        extract_id, far, mover, anchor, expected = self.way_bringing_in_a_node_with_a_relation()
        self.fetched = []
        self.update(extract_id)
        self.assertEqual(set(self.contents(extract_id)), expected)
        self.assert_current(extract_id)
        # Only the edit activity was asked for: where it has got to, and the records
        self.assertEqual(len(self.fetched), 2)
        self.assertEqual(self.fetched[0], "/replication/edit_ids")
        self.assertTrue(self.fetched[1].startswith("/replication/edit_activities?"))
        # Moving the node out again takes all of that away
        self.modify_node(mover, 4)
        self.update(extract_id)
        self.assertEqual(set(self.contents(extract_id)), {("node", anchor.objId)})

    def test_records_from_before_related_parents_were_kept(self):
        extract_id, far, mover, anchor, expected = self.way_bringing_in_a_node_with_a_relation()
        # As the records of an older server are: nothing said about the relations
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("UPDATE {} SET related_parents=NULL").format(
                sql.Identifier(self.prefixes[1] + "edit_activity")))
        # Such records cannot be relied on, and the update is refused untouched
        before = (self.contents(extract_id), self.checkpoint(extract_id))
        self.fetched = []
        with self.assertRaisesRegex(RuntimeError, "recorded before edits said which relations hold"):
            self.update(extract_id)
        self.assertEqual((self.contents(extract_id), self.checkpoint(extract_id)), before)
        self.assertEqual([path for path in self.fetched if path.startswith("/api/")], [])

    def test_no_extra_requests_in_any_other_test(self):
        # A varied history: every kind of edit, with objects entering and leaving
        inside = self.create_node(0)
        extract_id = self.save()
        nodes = [self.create_node(0.01 * i) for i in range(1, 6)] + [self.create_node(3), self.create_node(4)]
        way = self.create_way(nodes[:3])
        outer = self.create_way(nodes[4:])
        self.create_relation([("node", nodes[6], ""), ("way", outer, "")])
        self.create_relation([("way", way, "outer"), ("node", inside, "")])
        nodes[1] = self.modify_node(nodes[1], 5)
        way = self.modify_way(way, [nodes[0], nodes[3], nodes[5]])
        nodes[5] = self.modify_node(nodes[5], 0.7)
        self.upload("delete", [nodes[2]])
        self.fetched = []
        self.update(extract_id)
        self.assert_current(extract_id)
        self.assertEqual([path for path in self.fetched if path.startswith("/api/")], [])

    def test_refused_updates(self):
        self.create_node(0)
        extract_id = self.save()
        # A checkpoint beyond what the server has means it is not the server the extract came from
        with self.db.cursor() as cursor:
            cursor.execute(sql.SQL("UPDATE {} SET edit_activity_id=edit_activity_id+50 WHERE id=%s").format(
                sql.Identifier(self.prefixes[1] + "extracts")), [extract_id])
        with self.assertRaisesRegex(RuntimeError, "Source activity precedes extract checkpoint"):
            self.update(extract_id)
        # A server that does not answer, or answers with something else
        extract = {"id": extract_id, "update_url": "http://elsewhere.test/", "edit_activity_id": 1, "atomic_edit_id": 1}
        with self.assertRaisesRegex(remoteupdate.RemoteUpdateError, "could not be read"):
            remoteupdate.update_extract_from_remote(self.map, extract, self.fetch)
        extract["update_url"] = self.REMOTE
        with self.assertRaisesRegex(remoteupdate.RemoteUpdateError, "did not send a document"):
            remoteupdate.update_extract_from_remote(self.map, extract, lambda url: b"<html>not found")
        with self.assertRaisesRegex(remoteupdate.RemoteUpdateError, "did not give the server's edit IDs"):
            remoteupdate.update_extract_from_remote(self.map, extract, lambda url: b"<editids/>")
        extract["edit_activity_id"] = None
        with self.assertRaisesRegex(remoteupdate.RemoteUpdateError, "no established synchronization checkpoint"):
            remoteupdate.update_extract_from_remote(self.map, extract, self.fetch)

    # Asking only for the records that concern the extract's rectangle

    def activity_requests(self):
        return [path for path in self.fetched if path.startswith("/replication/edit_activities")]

    def test_only_records_for_the_rectangle_are_asked_for(self):
        from django.conf import settings
        filtering = getattr(settings, "EXTRACT_UPDATE_USE_BBOX", True)
        inside = self.create_node(0.5)
        extract_id = self.save()
        # Far from the rectangle: nodes, a way, a relation, edits to them
        far = [self.create_node(40 + i) for i in range(3)]
        way = self.create_way(far[:2])
        self.create_relation([("way", way, ""), ("node", far[2], "")])
        self.modify_node(far[0], 45)
        # And one edit inside it
        inside = self.modify_node(inside, 0.6)
        self.fetched = []
        self.update(extract_id)
        requests = self.activity_requests()
        self.assertEqual(all("bbox=" in path for path in requests), filtering)
        self.assertEqual(any("bbox=" in path for path in requests), filtering)
        # Seven edits were made; one concerns the rectangle
        self.assertEqual(self.applied, 1 if filtering else 7)
        self.assert_current(extract_id)

        # Edits that are all elsewhere: nothing to apply, and the extract has
        # still caught up, so the next update has nothing to ask about
        self.modify_node(far[1], 46)
        self.modify_node(far[2], 47)
        self.fetched = []
        self.update(extract_id)
        self.assertEqual(self.applied, 0 if filtering else 2)
        self.assert_current(extract_id)
        self.fetched = []
        self.update(extract_id)
        self.assertEqual((self.fetched, self.applied), (["/replication/edit_ids"], 0))

    def test_same_extract_with_and_without_the_filter(self):
        # Objects entering and leaving the rectangle from far away, parents
        # that reach into it, and edits that have nothing to do with it
        anchor = self.create_node(0.2)
        both = [self.save("filtered"), self.save("unfiltered")]
        near = [self.create_node(0.1 * i) for i in range(1, 5)]
        far = [self.create_node(30 + i) for i in range(5)]
        crossing = self.create_way([near[0], far[0]])
        distant = self.create_way([far[1], far[2]])
        relation = self.create_relation([("way", distant, ""), ("node", far[3], "")])
        self.create_relation([("way", crossing, ""), ("node", anchor, "")])
        far[0] = self.modify_node(far[0], 31.5)       # outside node of a way that is in
        far[1] = self.modify_node(far[1], 0.7)        # brings the distant way and its relation in
        distant = self.modify_way(distant, [far[1], far[2], far[4]])
        far[1] = self.modify_node(far[1], 33)         # and takes them out again
        near[1] = self.modify_node(near[1], 50)       # a node leaving
        crossing = self.modify_way(crossing, [far[0], far[4]])  # a way leaving by losing its inside node
        self.upload("delete", [near[2]])
        self.modify_relation(relation, [("node", near[3], "")]) # a far relation gaining an inside member
        applied = []
        for extract_id, filtering in zip(both, (True, False)):
            with override_settings(EXTRACT_UPDATE_USE_BBOX=filtering):
                self.fetched = []
                self.update(extract_id)
            applied.append(self.applied)
            self.assertEqual(any("bbox=" in path for path in self.activity_requests()), filtering)
            self.assert_current(extract_id)
            self.assertEqual(self.compare(extract_id)[1], [])
        self.assertEqual(self.contents(both[0]), self.contents(both[1]))
        self.assertEqual(self.checkpoint(both[0]), self.checkpoint(both[1]))
        # The filter spared it some of the records
        self.assertLess(applied[0], applied[1])

# Only the tests that bring extracts up to date run again here
local.run_only_what_a_variant_changes(RemoteUpdateTestCase)

@override_settings(EXTRACT_UPDATE_USE_BBOX=False)
class RemoteUpdateWithoutBboxTestCase(RemoteUpdateTestCase):
    """Everything again with the filter by area turned off: the extracts must come out the same."""
