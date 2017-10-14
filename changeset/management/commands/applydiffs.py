from django.core.management.base import BaseCommand, CommandError
from changeset.views import upload_update_diff_result
import os
import gzip
import pgmap
from querymap.views import p

def ProcessFile(fi):
	t = p.GetTransaction(b"EXCLUSIVE")

	#Decode XML
	data = pgmap.OsmChange()
	dec = pgmap.OsmChangeXmlDecodeString()
	dec.output = data
	pageSize = 100000
	while True:
		inputXml = fi.read(pageSize)
		if len(inputXml) == 0:
			break
		dec.DecodeSubString(inputXml, len(inputXml), False)
	dec.DecodeSubString("".encode("UTF-8"), 0, True)
	if not dec.parseCompletedOk:
		raise RuntimeError(dec.errString)

	for i in range(data.blocks.size()):
		action = data.actions[i]
		block = data.blocks[i]

		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()

		#Set visiblity flag
		visible = action != "delete"
		for j in range(block.nodes.size()):
			block.nodes[j].metaData.visible = visible
		for j in range(block.ways.size()):
			block.ways[j].metaData.visible = visible
		for j in range(block.relations.size()):
			block.relations[j].metaData.visible = visible

		ok = t.StoreObjects(block, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		if not ok:
			print errStr.errStr
			return False

	t.Commit()
	return True

class Command(BaseCommand):
	help = 'Apply diffs to database'

	def add_arguments(self, parser):
		#parser.add_argument('poll_id', nargs='+', type=int)
		pass

	def handle(self, *args, **options):

		diffFolder = "/home/tim/Desktop/103"
		
		for root, dirs, files in os.walk(diffFolder):

			for fina in files:
				ext = os.path.splitext(fina)[-1]
				if ext != '.gz': continue
				fullFina = os.path.join(root, fina)
				print fullFina
				xml = gzip.open(fullFina)
				ok = ProcessFile(xml)
				if not ok:
					return

