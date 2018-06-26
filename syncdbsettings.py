import shutil

if __name__=="__main__":

	#Read config
	configData = open("pgmap/config.cfg").read()
	configLi = configData.split("\n")
	config = {}
	for li in configLi:
		liVals = li.split(":")
		if len(liVals) < 2: continue
		config[liVals[0]] = liVals[1].strip()

	shutil.copyfile("pycrocosm/settings.py", "pycrocosm/settings.py.old")
	out = open("pycrocosm/settings.py", "wt")

	for li in open("pycrocosm/settings.py.old").readlines():
		pos = li.find("#SCRIPT_REPLACE_")
		if pos == -1:
			out.write(li) 
			continue
		li2 = li.rstrip()
		li2 = li2.split("#SCRIPT_REPLACE_")
		li2a = li2[0]
		li2as = li2a.split(":")

		outli = li2as[0] + ":'" + config[li2[1]] + "', #SCRIPT_REPLACE_" + li2[1] + "\n"
		out.write(outli)

	out.close()

