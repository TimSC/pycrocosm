import requests
import datetime
import gzip
import os

if __name__=="__main__":

	startDate = datetime.date(2020,8,1)
	today = datetime.date.today()
	outFolder = "/media/data/map/fsm/diffs"

	cursorDate = startDate
	while cursorDate < today:

		print (cursorDate)
		outFina = "{:04d}-{:02d}-{:02d}.osc.gz".format(cursorDate.year, cursorDate.month, cursorDate.day)
		outFinaFull = os.path.join(outFolder, outFina)
		cursorNext = cursorDate + datetime.timedelta(1)

		if os.path.exists(outFinaFull): 
			cursorDate = cursorNext
			continue #Already downloaded

		#Request diff
		url = "https://api.fosm.org/replication/diff?start={}-{}-{}&end={}-{}-{}".format(cursorDate.year, cursorDate.month, cursorDate.day, 
			cursorNext.year, cursorNext.month, cursorNext.day)
		print (url)

		r = requests.get(url)
		if r.status_code == 200:

			l = len(r.content)
			if l == 0: continue #That isn't valid

			outFi = gzip.open(outFinaFull, 'wb')
			outFi.write(r.content)
			outFi.close()

		cursorDate = cursorNext

