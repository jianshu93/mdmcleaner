#!/usr/bin/env python
""" 
extracting protein-coding as rRNA genes from assemblies, and identifying markergenes.
"""

#TODO: !!! filtering contigs should NOT be based on trust-index by default! That should only be an additional info for evaluating the remaining bin. Instead filtering should be based only on divergent classification and/or the absence onf any coding features 


# note to self:
# cutoff values were determined in different ways:
# for tigrfam and pfam: strict and sensitive values were parsed from the "GA" and "NC" fields, respectively. moderate values were calculated as the average betrween the respective strict and sensitive cutoffs
# for cogs all marker models were aligned seperately against the component merkergene-alignments and against all nonmarker-cog-alignments
# strict: the higher value of the cutoff that yielded 95% of the true positives (component markergene-alignents) and the cutoff that yielded less than 5% false positives (all nonmarker alignments)
# sensitive: the LOWER value of the cutoff that yielded 95% of the true positives (component markergene-alignents) and the cutoff that yielded less than 5% false positives (all nonmarker alignments)
# moderate: the average between strict and sensitive

import subprocess
import sys
import os
from Bio import SeqIO
import misc
from misc import openfile
import re
#import Bio.SearchIO.HmmerIO.hmmer3_domtab.Hmmer3DomtabHmmhitParser #probably better to parse it my self

# ~ basescore = 6 #scores start out at 6
# ~ maxscore = 12 #todo: this can change based on the scoring system. find a way to calculate this automatically, no matter how much the scoring system may change...
# ~ ref_db_contam_cutoff_protein = 85 #todo: use marker-appropriate cutoffs on 4 levels: strong= species-cutoff; medium = genus-cutoff, weak = order-cutoff, fringle = phylum-cutoff
# ~ ref_db_contam_cutoff_rRNA = 96 #todo: use marker-appropriate cutoffs on 4 levels: strong= species-cutoff; medium = genus-cutoff, weak = order-cutoff, fringle = phylum-cutoff
#currently the marker-hmms only encompass universal SINGLE-COPY genes. It would be interesting to include the multicopy-universal genes as well! --> parse the COG-database for this...?

libpath = os.path.dirname(os.path.realpath(__file__))
hmmpath = os.path.realpath(os.path.join(libpath, "../hmms/"))
hmmpath_prok = os.path.realpath(os.path.join(hmmpath, "prok/"))
hmmpath_bact = os.path.realpath(os.path.join(hmmpath, "bact/"))
hmmpath_arch = os.path.realpath(os.path.join(hmmpath, "arch/"))
hmmpathdict={	"prok" : [hmmpath_prok], \
				"bact" : [hmmpath_prok, hmmpath_bact], \
				"arch" : [hmmpath_prok, hmmpath_arch], \
				"all" : [hmmpath_prok, hmmpath_bact, hmmpath_arch] }
cutofftablefile = os.path.join(hmmpath, "cutofftable_combined.tsv")
#each path in hmmpathdict should contain a number of hmm files named e.g. COG.hmm, PFAM.hmm or TIGR.hmm, containing concatenated hmm models for each level/db-type

protmarkerlevel_dict = { 0 : "prok_marker", 1 : "bac_marker", 2 : "arc_marker" }

_rnammerpattern = re.compile("^rRNA_(.+)_\d+-\d+_DIR[+-](\s.*)*$")
_barrnappattern = re.compile("^[\d_]{1,3}S_rRNA::(.+):\d+-\d+\([+-]\)(\s.*)*$") #adjusted to also capture "5_8S_rRNA"
_prodigalpattern = re.compile("^(.+)_\d+(\s.*)*")
_trnapattern = re.compile('^trna_([\w\|\.-]+)__aragorn_([\w-]+)__(c?\[\d+,\d+\])')

full_tRNA_species=[	"tRNA-Ala", "tRNA-Arg", "tRNA-Asn", "tRNA-Asp", "tRNA-Cys", "tRNA-Gln", "tRNA-Glu", "tRNA-Gly", "tRNA-His", "tRNA-Ile", \
							"tRNA-Leu", "tRNA-Lys", "tRNA-Met", "tRNA-Phe", "tRNA-Pro", "tRNA-Ser", "tRNA-Thr", "tRNA-Trp", "tRNA-Tyr", "tRNA-Val", "tRNA-SeC"] #expected "full" set of tRNA species expeced for prototoph bacteria. For estimating completeness

def _get_new_contigdict_entry(record): #todo change contiglen and totalprotcount to ints rather than lists!
	return {"contiglen": len(record), "totalprotcount" : 0, "ssu_rRNA" : [], "ssu_rRNA_tax" : None, "lsu_rRNA" : [], "lsu_rRNA_tax":None, "tsu_rRNA" : [], "tRNAs": [],"prok_marker" : [], "prok_marker_tax" :None,  "bac_marker" : [], "arc_marker" : [], "totalprots" : [], "total_prots_tax": None, "toplevel_marker" : None, "toplevel_tax" : None, "toplevel_taxlevel" : None, "toplevel_ident": None, "ambigeous" : False, "consensus_level_diff": 0, "contradict_consensus": None, "contradict_consensus_evidence": 0, "contradictions_interlevel": [], "viral" : None, "refdb_ambig" : False, "refdb_ambig_infotext": None, "refdb_ambig_evidence": None, "tax_score" : None, "trust_index" : None,"tax_note" : None, "non-coding" : False, "filterflag" : None}

def split_fasta_for_parallelruns(infasta, minlength = 0, number_of_fractions = 2, outfilebasename = None):
	"""
	splits large multifastas into several portions, to enable parallel runs of single threaded processes, such as rnammer or prodigal
	requires subdivide_multifas.py
	returns a list of lists of sequence records
	each list of sequence records should then be passed to the stdin of a seperate RNAmmer or prodigal call (not necessary for barrnap, because that already supports multithreading)
	"""
	import random
	from Bio import SeqIO

	sys.stderr.write("\nsubdividing contigs of {} for multiprocessing\n".format(infasta))
	fastafile = openfile(infasta)
	records = SeqIO.parse(fastafile, "fasta")
	contigdict = {}
	#print("whooooooo --{}-- whooooooo".format(number_of_fractions))
	outlist = [[] for x in range(int(number_of_fractions))]
	contigcounter = 0
	index = 0
	direction = 1

	for record in records: #distribute contigs evenly over fractions by iterating up and down the fractions again and again --> ensures even size distribution if possible...
		if len(record) < minlength:
			continue
		contigcounter += 1
		contigdict[record.id] = _get_new_contigdict_entry(record)
		if index > len(outlist)-1:
			direction = -1
			index = len(outlist) -1
		if index < 0:
			direction = 1
			index = 0
		outlist[index].append(record)
		index += direction
		
	outlist = [ x for x in outlist if len(x) > 0 ] #removing any leftover fractions that did not get contigs (in case number of contigs was lower than number of fractions)
	sys.stderr.write("divided {} contigs into {} fractions\n".format(contigcounter, len(outlist)))

	if outfilebasename != None: #IF an outfilenasename is specified --> Do NOT return a list of lists of seqrecords, but instead write fractions fo tempfiles and return list of linemanes instead
		outfilenamelist = []
		for i in range(len(outlist)):
			outfilenamelist.append("{}_temp_fraction_{}.fasta".format(outfilebasename, i))
			with openfile(outfilenamelist[-1], "wt") as outfile:
				print("writing to {}".format(outfile.name))
				SeqIO.write(outlist[i], outfile, "fasta")
		return outfilenamelist, contigdict

	return outlist, contigdict

def runprodigal(infasta, outfilename, prodigal="prodigal", threads = 1): # if input is a list it simply expect it to be a list of seqrecords, for piping via stdin
	"""
	creates a protein fasta file of all CDS identified in the inputfasta via prodigal,
	all other prodigal output is ignored
	prodigal is called using the "-p meta" argument for metagenomes, in the assumption that the input fasta MAY consist of mutliple organisms
	The return value is simply the value of 'outfilename'
	"""
	input_is_empty = False
	prodigal_cmd = [prodigal, "-a", outfilename, "-p", "meta", "-q", "-o", "/dev/null"] # TODO: maybe add option to change translation table ("-g")? Although table 11 should be general enough?
	if type(infasta) == str and os.path.isfile(infasta):
		prodigal_cmd += ["-i", infasta]
		inputarg = None
	elif type(infasta) == list:
		inputarg =  "\n".join([record.format("fasta") for record in infasta])
	else:
		raise IOError("\nERROR: don't recognize query argument\n")
	try:
		prodigal_proc = subprocess.run(prodigal_cmd, input = inputarg, stdout = subprocess.PIPE, stderr = subprocess.PIPE, text = True)  
		prodigal_proc.check_returncode()
	except Exception:
		sys.stderr.write(prodigal_proc.stderr)
		raise Exception("\nERROR: Something went wrong while trying to call prodigal...\n")
	return outfilename

def _get_trnas_single(infasta,  aragorn="aragorn", threads=1):
	# ~ import pdb; pdb.set_trace()
	aragorn_cmd = [aragorn, "-gcbact", "-w"]
	try:
		# ~ print(infasta)
		# ~ print("wtf")
		aragorn_cmd = aragorn_cmd + [ infasta]
		# ~ print("::::::::::::::::::::::::::::")
		# ~ print(aragorn_cmd)
		# ~ print("............................")
		# ~ print(" ".join(aragorn_cmd))
		aragorn_proc = subprocess.run(aragorn_cmd, stdout = subprocess.PIPE, stderr = subprocess.PIPE, text = True)
		aragorn_proc.check_returncode()
		print("aragorn single run finished")
	except Exception:
		sys.stderr.write(aragorn_proc.stderr)
		raise Exception("\nERROR: Something went wrong while trying to call Aragorn...\n")
	outlinelist = aragorn_proc.stdout.split("\n")
	print("will return outlinelist now")
	return  outlinelist

def get_trnas(*subfastas, outdirectory = ".", aragorn = "aragorn", threads = 1):
	"""
	runs aragorn on each provided genomic (sub)fasta, to identify tRNA sequences.
	if multiple subfastas and multiple threads are given, it will run several instances of aragorn in parallel
	returns a dictionary with contignames of contigs carrying tRNAs as keys, and lists of tuples (each consisting of the corresponding tRNA name and contig-coordinates) as values.
	if no contig carries a tRNA gene, it will return an empty dictionary
	"""
	#TODO: create another function "extract_trnas" that can extract the exact trna seqeucne based on the respective coordinates, for blasting against the nucleotide-dbs
	from itertools import chain
	print("starting trna scan")
	tempfilelist = []
	subfastas=list(subfastas)
	for i in range(len(subfastas)):
		if misc.has_gzip_suffix(subfastas[i]):
			tempfilename = os.path.join(outdirectory, "delme_aragorn_tempfile_{}.fa".format(len(tempfilelist)))
			misc.unixzcat(subfastas[i], outfilename=tempfilename)
			subfastas[i] = tempfilename
			tempfilelist.append(tempfilename)
	commandlist = [("getmarkers", "_get_trnas_single", {"infasta" : subfasta}) for subfasta in subfastas]
	outstringlistlist =misc.run_multiple_functions_parallel(commandlist, threads)
	print("should be finished now!")
	sys.stdout.flush()
	sys.stderr.flush()
	outlist = _parse_aragorn_output(list(chain(*outstringlistlist)))
	print("finished parsing trna results")
	sys.stdout.flush()
	sys.stderr.flush()
	for t in tempfilelist:
		os.remove(t)
	return outlist
	
def _parse_aragorn_output(outstringlist):
	import re
	print("started parsing")
	sys.stdout.flush()
	sys.stderr.flush()
	trnapattern = " (tRNA-\w+)\s+(c?\[\d+,\d+\])"
	# ~ locationpattern = "c?\[(\d+),(\d+)\]"
	outlist = []
	trnalist = []
	contig = None
	trnaset = set()
	for line in outstringlist:
		if line.startswith(">"):
			# ~ if len(trnalist) > 0:
			#	# ~ outdict[contig] = trnalist
			#	# ~ trnalist = []
			contig = line[1:].split()[0]
			continue
		trnahit = re.search(trnapattern, line)
		if trnahit != None:
			trna = trnahit.group(1)
			location = trnahit.group(2)
			genename = "trna_{}__aragorn_{}__{}".format(contig, trna, location)
			# ~ coordinatematch = re.search(locationpattern, location)
			# ~ if location.startswith("c"):
				# ~ start = int(coordinatematch.group(2))
				# ~ stop = int(coordinatematch.group(1))
			# ~ else:
				# ~ start = int(coordinatematch.group(1))
				# ~ stop = int(coordinatematch.group(2))
			# ~ print(trna, location)
			# ~ print("-----------")
			if trna not in full_tRNA_species:
				sys.stderr.write("\nAttention: non-standard trna: {}\n".format(trna))
				sys.stderr.flush()
			outlist.append(genename)
			trnaset.add(trna)
	print("found {} of {} tRNAs --> {}%".format(len(trnaset), len(full_tRNA_species), len(trnaset)/len(full_tRNA_species)*100)) #todo: change to an actual completeness function that takes into consideration that only about 22% or sequenced bacteria encode SeC-tRNAs
	print(outlist)
	return outlist
	
		
def runbarrnap_single(infasta, barrnap="barrnap", kingdom = "bac", output_directory = ".", threads=1): 
	#tempfastalist, gffoutputs = [], []
	#for kingdom in ["bac", "arc", "euk"]:
	#todo: the following hack is to circumvent the problem of barrnap not handling compressed files. A better way to do this would to just assume files are sent via pipe?
	tempfasta = os.path.join(output_directory, "temp_barrnap_{}.fasta".format(kingdom))
	barrnap_cmd = [barrnap, "--kingdom", kingdom, "--outseq", tempfasta, "--threads", str(threads), "--quiet"] #todo: enable piping via stdin 
	if type(infasta) == str and os.path.isfile(infasta): #todo: this is convoluted. maybe just always read the fasta if provided as file and pass it as fasta-string?
		if infasta.endswith(".gz"):
			inputarg = "\n".join([record.format("fasta") for record in misc.read_fasta(infasta)])
		else:
			barrnap_cmd += [infasta]
			inputarg = None
	elif type(infasta) == list: #allows piping data that was already read in in fasta format
		inputarg =  "\n".join([record.format("fasta") for record in infasta])
	else:
		raise IOError("\nERROR: don't recognize query argument\n")
	assert os.path.isfile(infasta), "Error. can't find input file {}".format(infasta) # since barrnap can do multithreading, do not accept subdivided input_fasta for this
	try:
		print(" ".join(barrnap_cmd))
		barrnap_proc = subprocess.run(barrnap_cmd, input = inputarg, stdout = subprocess.PIPE, stderr = subprocess.PIPE, text = True)
		barrnap_proc.check_returncode()
	except Exception:
		sys.stderr.write(barrnap_proc.stderr)
		raise Exception("\nERROR: Something went wrong while trying to call barrnap...\n")
	gff_output = barrnap_proc.stdout
	#todo: need to parse barrnap results from stdout (gff-output) rather than output-fasta-headers
	return (tempfasta, gff_output) #todo: make sure these results are then collected for each kingdom and run through deduplicate_barrnap_results()

def runbarrnap_all(infasta, outfilebasename, barrnap="barrnap", output_directory = ".", threads=3): #todo: parse resultfolder from basename. or rather basename from resultfolder!
	from Bio import SeqIO
	import misc
	sys.stderr.write("\nscanning for rRNA genes...\n")
	joblist = []
	for kingdom in ["bac", "arc", "euk"]:
		joblist.append(("getmarkers", "runbarrnap_single", {"infasta" : infasta, "barrnap" : barrnap, "kingdom" : kingdom, "output_directory" : output_directory }))
	outputlist = misc.run_multiple_functions_parallel(joblist, threads)
	tempfasta_list = [op[0] for op in outputlist]
	# ~ print(tempfasta_list)
	gff_outputlist = [ op[1] for op in outputlist]
	# ~ print(gff_outputlist)
	final_fastadict, contig_rrnadict = deduplicate_barrnap_results(tempfasta_list, gff_outputlist) #todo: also get a dictionary which which markers are on which contig
	for ff in final_fastadict:
		# ~ print("temporary barrnap file: {}_{}.fasta".format(outfilebasename, ff))
		outfile = openfile("{}_{}.fasta".format(outfilebasename, ff), "wt")
		SeqIO.write(final_fastadict[ff], outfile, "fasta")
		outfile.close()
		final_fastadict[ff] = outfile.name #todo: perhaps, instead of a dict, return fastafilenames as list?
	if type(infasta) == str and os.path.isfile(infasta + ".fai"):
		os.remove(infasta + ".fai") #todo: find a way to do this safely! check if file existed beforehand. only delete it her if it didn't (barrnap creates this file, but does not clean up after itself)
	# ~ print("after_deduplication:")
	# ~ print(final_fastadict)
	# ~ print("----")
	# ~ print(contig_rrnadict)
	# ~ print("??????????????????????")
	return final_fastadict, contig_rrnadict

def deduplicate_barrnap_results(tempfastas, gff_outputs):
	from Bio import SeqIO #todo: check if this is even necessary if SeqIO was already imported globally for this module
	import os
	contig_hit_dict = {}
	seqtype_dict = { "18S_rRNA": "ssu_rRNA", "16S_rRNA": "ssu_rRNA",  "12S_rRNA": "ssu_rRNA", "23S_rRNA": "lsu_rRNA",  "28S_rRNA": "lsu_rRNA", "5S_rRNA" : "tsu_rRNA", "5_8S_rRNA" : "tsu_rRNA"}
	for gff in gff_outputs:
		for line in gff.rstrip().split("\n"):
			if line.startswith("#"):
				continue
			tokens = line.split()
			contig = tokens[0]
			start = int(tokens[3])
			stop = int(tokens[4])
			evalue = float(tokens[5])
			orient = tokens[6]
			rrna = tokens[8].split(";")[0][5:]
			seqid = "{}::{}:{}-{}({})".format(rrna, contig, start, stop, orient)
			altseqid = "{}::{}:{}-{}({})".format(rrna, contig, start-1, stop, orient) ##todo: remove this if barrnap issue is resolved. barrnap currently (v.0.9) gives different start position in fasta header and in gff output. Until i am sure what is the reason, or to make this work when if that is fixed in barrnap, i have to check for both variants
			# ~ if rrna in ["5S_rRNA", "5_8S_rRNA"]:
				# ~ continue #ignoring 5S rRNA for now
			if contig in contig_hit_dict:
				redundant = False
				index = 0
				while index < len(contig_hit_dict[contig]):
					evalueold = contig_hit_dict[contig][index]["evalue"]
					rangenew = set(range(start, stop))
					rangeold = range(contig_hit_dict[contig][index]["coords"][0], contig_hit_dict[contig][index]["coords"][1])
					intersection = rangenew.intersection(rangeold)
					if len(intersection)/min([len(rangenew), len(rangeold)]) > 0.5: #if it intersects by more than 50%, keep only the one with the better evalue
						if evalue <= evalueold:
							contig_hit_dict[contig].pop(index)
							continue
						else:
							redundant = True
					index += 1
				if not redundant:
					contig_hit_dict[contig].append({"seqid" : seqid, "altseqid" : altseqid, "coords" : (start, stop, orient), "evalue" : evalue, "seqtype" : seqtype_dict[rrna] })	#todo: "altseqid" key not needed if barrnap issue is resolved				 
			else:
				contig_hit_dict[contig] = [{"seqid" : seqid, "altseqid" : altseqid, "coords" : (start, stop, orient), "evalue" : evalue, "seqtype" : seqtype_dict[rrna] }] #todo: "altseqid" key not needed if barrnap issue is resolved
	finalseqids = set()
	for contig in contig_hit_dict:
		for seq in contig_hit_dict[contig]:
			finalseqids.add(seq["seqid"])
			finalseqids.add(seq["altseqid"]) #todo: remove this if barrnap issue is resolved
	finalfastadict = {"ssu_rRNA" : [], "lsu_rRNA" : [], "tsu_rRNA" : []} #16S & 18S are "ssu_rRNAs", 23S & 28S are "lsu_rRNAs". 5S is stored for later use if required. The term "tsu" was adopted from rnammer, to distinguish  todo: change this and save 5S also (just to distinguisch actual noncoding contigs from those that have at least 5S rRNA or trRNA)
	#beforecounter = 0
	contig_rrna_dict = {}
	for fasta in tempfastas:
		#todo: add a seqcounter for before and after dedup
		infile = openfile(fasta)
		for record in SeqIO.parse(infile, "fasta"):
			recordtype, contig = parse_barrnap_headers(record.id)
			#recordtype = record.id[0:9] #todo: not the best way to get the marker type (16S_rRNA or 23S_rRNA) from the fasta-headers. but good enough for now...
			#beforecounter += 1
			if record.id in finalseqids: # todo:/note: I realize that if two models (e.g. arc & bac) detect the exact same region with the exact same coordinates, this would lead to a dupicate genesequence in the rRNA-predictions. However, currently it seems this would be without consequences for the further workflow
				if contig not in contig_rrna_dict or record.id not in contig_rrna_dict[contig][seqtype_dict[recordtype]]: #workaround to prevent multiple entries of the same rRNA that was detected with the exact nsame name and coordinates by multiple models (bacterial, archaeal and eukaryotic)
					finalfastadict[seqtype_dict[recordtype]].append(record)
					if contig not in contig_rrna_dict:
						contig_rrna_dict[contig] = {"ssu_rRNA" : [], "lsu_rRNA" : [], "tsu_rRNA" : []}
					contig_rrna_dict[contig][seqtype_dict[recordtype]].append(record.id)
	for fasta in tempfastas: #currently doing this AFTER the previous loop, to make sure the files are only deleted when everything went well (debugging purposes)
		os.remove(fasta)
	print("\nfound {} rna sequences\n".format(sum([ len(finalfastadict[ghj]) for ghj in finalfastadict]))) #todo: delete this line (debugging only)
	return finalfastadict, contig_rrna_dict		#todo: also return a dictionary with contignames as keys and type of marker as values?
					
def parse_barrnap_headers(header):
	tokens = header.lstrip(">").split(":") #todo: add some kind of test to verify that this is actually barrnap-result-fasta-header
	recordtype = tokens[0]
	contig = tokens[2]
	return recordtype, contig

def runrnammer(infasta, outfilename, threads = 1): #todo: allow piping via stdin
	pass #todo: implement this (not a priority since rnammer is painful to install for most users)

def hmmersearch(hmmsearch, model, query, outfilename, score_cutoff = None, eval_cutoff = None, threads = 1):# todo: strict parameters = gathering threshold (GA), sensitive parameters = noise cutoff (NC)
	"""
	runs hmmsearch
	score and/or evalue cutoffs can be specified seperately.
	if neither score-, nor eval_cutoff are supplied, it will try to obtain the cutoff values from the "GA" field of the model ("Gathering Threshold"; if available).
	alternatively the score_cutoff can be non-explicetly set either as "strict" or "sensitive". In this case the evalue_cutoff will ignored and the following cutoffs will be used from the hmm file:
		- strict: GA (=Gathering threshold)
		- sensitive: NC (Noise Cutoff)
	note that for self-built hmms without "GA" and "NC" keys, cutoffs will need to be specified explicitely.
	"""
	eval_cutoff_arg, score_cutoff_arg = [], []
	if (eval_cutoff == None and score_cutoff == None) or score_cutoff == "strict":
		score_cutoff = ["--cut_tc"] # use trusted cutoff of hmm model (if available). consider only using gathering threshold (GA) uinstead
	elif score_cutoff == "sensitive":
		score_cutoff = ["--cut_nc"] # use noise cutoff of hmm model (if available).	
	elif score_cutoff == "moderate":
		score_cutoff = ["--cut_ga"] # use gathering cutoff of hmm model (if available)
	else:	
		if eval_cutoff != None:
			eval_cutoff_arg = ["-E", eval_cutoff]
		if score_cutoff != None:
			score_cutoff_arg = ["-T", score_cutoff]
	hmmsearch_cmd = [hmmsearch, "--noali", "--cpu", str(threads), "--domtblout", outfilename] + eval_cutoff_arg + score_cutoff_arg + [model]
	#print("\nquery = {}\n".format(query))
	if type(query) == str: #TODO: This assumes "if query is a string, it must be a filename." That is obviously BS! implement a check that tests if string is a fasta-record! #note to to self: for now i will assume fasta via stdin if query is a list of seqrecords 
		hmmsearch_cmd.append(query)
		inputarg = None
	elif type(query) == list:	#otherwise, if it is a list of seqrecords it must be something to pipe via stdin
		inputarg = "\n".join([record.format("fasta") for record in query])
	else:
		raise IOError("\nERROR: don't recognize query argument\n")
	try:
		hmmsearch_proc = subprocess.run(hmmsearch_cmd, input = inputarg, stdout = subprocess.PIPE, stderr = subprocess.PIPE, text = True)
		hmmsearch_proc.check_returncode()
	except Exception:# Todo: define/choose more detailed exception categories
		sys.stderr.write(hmmsearch_proc.stderr)
		raise Exception("\nERROR: something went wrong while trying to call hmmsearch...\n")
	return outfilename

### NOTE TO SELF: perform hmmsearch always with "sensitive" cutoff, and only PARSE hits with higher cutoffs --> enables reanalyses with different cutoffs without redoing hmmsearch!

def get_cutoff_dict(cutofffilename = cutofftablefile): #todo lookupfile with cutoffs for ALL used models. TODO: better: parse this from model.hmm files (require GA, TC and NC fields in all used models!)
	"""
	reads cutoff values from cutoff_file into a dictonary
	each model is represented as a seperate line with 4 columns:
		- first column = model name
		- second column = strict cutoff
		- third column = moderate cutoff
		- fourth column = sensitive cutoff"
	""" #also todo: make sure this is loaded only once for multiple input fastas (not reloaded again and again for each input)
	cutofffile = openfile(cutofffilename)
	cutoff_dict = {}
	for line in cutofffile:
		if line.startswith("#"):
			continue
		tokens = line.split()
		model = tokens[0].split(".")[0]
		strict = float(tokens[1])
		moderate = float(tokens[2])
		sensitive = float(tokens[3])
		cutoff_dict[model] = {"strict" : strict, "moderate" : moderate, "sensitive" : sensitive}
	return cutoff_dict
	
def parse_hmmer(hmmerfile, cutoff_dict = cutofftablefile, cmode = "moderate", prev_results = None):
	"""
	parses hmmer result file, using cutoff-thresholds passed as a dictionary "cutoff_dict", as returned by "get_cutoff_dict()"
	returns a dictionary containing protein-identifiers as keys and respective marker-designations as values for each hmm hit that passed cutoff criteria
	cutoff_dict should be a dictinary with the "strict", "moderate" and "sensitive" cutoff-values for each marker-model, but CAN also be a filename from which to parse that dict (default = parse from default file)
	prev_results may be a previous hit_dictionary that shlud be updated with hits form the present one
	"""
	assert cmode in  ["strict", "moderate", "sensitive"], "\nError: dont recognize mode \"{}\"! mode must be one of [\"strict\", \"moderate\", \"sensitive\"]\n"
	if type(cutoff_dict) != dict: #alternative for parsing cutoff_dict will be read from a file (better to pass it as dict, though)
		#TODO: add logger message that cutoff dict is being read from file
		cutoff_dict = get_cutoff_dict(cutoff_dict)
	infile = openfile(hmmerfile)
	if prev_results == None:
		markerdict = {}
	elif type(prev_results) == dict:
		markerdict = prev_results
	else:
		raise RuntimeError("\nArgument 'prev_results' should be either None or of type dict\n")
	for line in infile:
		if line.startswith("#"):
			continue
		tokens = line.split()
		prot = tokens[0]
		marker = tokens[4].split(".")[0]
		fscore = float(tokens[7])
		#dscore = float(tokens[13]) #not sure if i will use this
		#print(" found '{}' (which is marker '{}') with score = {}.  cutoff is {}".format(prot, marker, fscore, cutoff_dict[marker][cmode]))
		if fscore < cutoff_dict[marker][cmode]:
			#print("    --> score not goud enough")
			continue 
		if prot not in markerdict:
			#print("            --> {} is being stored".format(marker))
			markerdict[prot] = { "marker" : marker, "fscore" : fscore } #may need to add dscore here
			#print(markerdict)
	return markerdict 

def get_markerprotnames(proteinfastafile, cutoff_dict = cutofftablefile, hmmsearch = "hmmsearch", outdir = ".", cmode = "moderate", level = "prok", threads = "1"): #todo: turn list of markerdicts into dict of markerdits
	"""
	runs hmmersearch and and parse_hmmer on designated proteinfasta using models for designated level. Requires a cutoff_dict as returned by "get_gutoff_dict()"
	cutoff_dict should be a dictinary with the "strict", "moderate" and "sensitive" cutoff-values for each marker-model, but CAN also be a filename from which to parse that dict (default = parse from default file)
	returns a nested dictionary containing protein-identifiers as main keys and subdictionaries with respective marker-designations (key = "marker") and score values (key = "fscore")  as values for each hmm hit that passed cutoff criteria
	"""
	#print("\nget_markerprotnames()  --> proteinfastafile = {}\n".format(proteinfastafile))
	assert level in ["prok", "bact", "arch", "all"], "\nError: dont recognize level \"{}\"! mode must be one of [\"prok\", \"bact\", \"arch\", \"all\"]\n"
	assert cmode in  ["strict", "moderate", "sensitive"], "\nError: dont recognize mode \"{}\"! mode must be one of [\"strict\", \"moderate\", \"sensitive\"]\n" 
	if type(cutoff_dict) != dict: #alternative for parsing cutoff_dict will be read from a file (better to pass it as dict, though)
		#TODO: add logger message that cutoff dict is being read from file
		cutoff_dict = get_cutoff_dict(cutoff_dict)
	list_of_markerdicts = []
	print("getting markerdicts")
	for hmmpath in hmmpathdict[level]:
		hmmfiles = [ os.path.join(hmmpath, hmmfile) for hmmfile in os.listdir(hmmpath) if hmmfile.endswith(".hmm") ]
		markerdict = {}
		for hmmfile in hmmfiles:
			outfile = os.path.join(outdir, os.path.basename(hmmfile) + ".domtblout")
			if os.path.exists(outfile):
				sys.stderr.write("\nHmmer-resultfile '{}' already exists. --> skipping this HMM-search!\n".format(outfile))
			else:
				sys.stderr.write("\nsearching {} ...".format(hmmfile))
				outfile = hmmersearch(hmmsearch, hmmfile, proteinfastafile, outfile, "sensitive", None, threads)
			markerdict = parse_hmmer(outfile, cutoff_dict, cmode, markerdict)
			#print(hmmfile)
			#print(len(markerdict))
			#print(markerdict)
			#print("--------------------------")
		list_of_markerdicts.append(markerdict)
	return deduplicate_markerprots(list_of_markerdicts) #list_of_markerdicts will be in this order: [prok[, bact[, arch]]]

def deduplicate_markerprots(list_of_markerdicts): # For proteins with hits to different models, just keep the hit with the highest score. This function is a highly convoluted way to do this, but it is late and my brain is tired
	#todo: turn list of markerdicts into dict of markerdicts or an own class
	print("before deduplicating: {}".format(", ".join([str(len(x)) for x in list_of_markerdicts])))
	keys = set([ key for md in list_of_markerdicts for key in md ])
	for key in keys:
		a, b = 0, 1
		while a < len(list_of_markerdicts) and b < len(list_of_markerdicts):
			if key in list_of_markerdicts[a]:		
				while b < len(list_of_markerdicts):
					# ~ print("checking if '{}' is in markerdict {} and {}".format(key, a, b))
					if key in list_of_markerdicts[b]:
						# ~ print("   ---> it IS!")
						if list_of_markerdicts[a][key]["fscore"] >= list_of_markerdicts[b][key]["fscore"]:
							# ~ print("        deleting this key in {}".format(b))
							list_of_markerdicts[b].pop(key)
						else:
							# ~ print("        deleting this key in {}".format(a))
							list_of_markerdicts[a].pop(key)
							a += 1
					b += 1
					# ~ print("------------------")
			a += 1
			b += 1
	print("after deduplicating {}".format(", ".join([str(len(x)) for x in list_of_markerdicts])))
	# ~ import pdb; pdb.set_trace()
	return list_of_markerdicts
			
	

def __get_markerprotseqs(proteinfastafile, markerdict): #todo: implement piping proteinfastafile from stdin
	"""
	returns a list of proteinsequences corresponding to markers found in markerdict
	marker designation and score alue are written to the description of each protein sequence
	"""
	from Bio import SeqIO
	protfastafile = openfile(proteinfastafile)
	protrecords  = SeqIO.parse(protfastafile, "fasta")
	markerlist = []
	for prot in protrecords:
		#print("checking if '{}' in markerdict\n".format(prot.id))
		if prot.id in markerdict:
			prot.description = "marker={};score={};desc={}".format(markerdict[prot.id]["marker"], markerdict[prot.id]["fscore"], prot.description)
			markerlist.append(prot)
	#print(markerdict)
	return markerlist

def get_markerprots(proteinfastafile, cutoff_dict = cutofftablefile, cmode = "moderate", level = "prok", outfile_basename = "markerprots", threads = 1): #todo: turn list of markerdicts into dict of markerdits
	"""
	writes fasta sequences of detected markergenes in fasta format to outfile, with marker-designation and hmm score value in description
	'cmode' refers to "cutoff_mode" and can be one of ["strict", "moderate", or "sensitive"]. Sets the score cutoff_values to use for selecting hits. For each marker-designation and cutoff-mode 
	cutoff_dict should be a dictinary with the "strict", "moderate" and "sensitive" cutoff-values for each marker-model, but CAN also be a filename from which to parse that dict (default = parse from default file)
	return value is simply the name of the outfile
	"""
	from Bio import SeqIO
	levelorder = ["prok", "bact", "arch"]
	outdir = os.path.dirname(outfile_basename)
	if type(cutoff_dict) != dict: #alternative for parsing cutoff_dict will be read from a file (better to pass it as dict, though)
		#TODO: add logger message that cutoff dict is being read from file
		cutoff_dict = get_cutoff_dict(cutoff_dict)
	list_of_markerdicts = get_markerprotnames(proteinfastafile, cutoff_dict, hmmsearch = "hmmsearch", outdir = outdir, cmode = "moderate", level = level, threads = threads)
	outfilelist = []
	for i in range(len(list_of_markerdicts)):
		markerseqs = __get_markerprotseqs(proteinfastafile, list_of_markerdicts[i])
		outfilename = "{}_{}.faa".format(outfile_basename, levelorder[i])
		outfile = openfile(outfilename, "wt")
		SeqIO.write(markerseqs, outfile, "fasta")
		outfile.close()
		outfilelist.append(outfilename)
	return outfilelist

	
def write_markerdict(markerdict, outfilename):# todo: improve markerdict #todo: confusingly names multiple unrelated dicts "markerdict" sort this out!!
	"""
	writes the marker dictionary, obtained by get_markerprotnames(), to an overview file in tab-seperated text-table format
	return value is simply the name of the outfile
	"""
	outfile = openfile(outfilename, "wt")
	for m in markerdict:
		outline = "{}\t{}\n".format(m, "\t".join([ str(markerdict[m][v]) for v in markerdict[m].keys() ]))
		outfile.write(outline)
	return outfilename

def combine_multiple_fastas(infastalist, outfilename = None, delete_original = True, contigdict = None, return_markerdict = False): #pass contigdict in order to ba able to capture totalproteincounts per contig. currently only works for prodigal_output# todo: find a more flexible solution!
	"""
	different steps in getmarkers may subdivide input into fractions for better multiprocessing, and subsequently produce multiple output files
	This function is meant to combine such fastas to either a single output file (outfilename) or a list of seqrecords (if outfilename==None)
	Will delete the original fraction-fastas unless delete_original is set to False
	optionally returns a "markerdict", however this is only meant for use within the mdm-cleaner pipeline and only for protein-records.
	Raises an Error if the inputfastas do not contain any records
	"""
	#todo: create an alternative version that writes to the outfile on the fly, for parsing huge assemblies
	#todo: check if contigdict is needed in this form at all
	# ~ print("HEEEEEELLLOOOOO!!!")
	import re
	from Bio import SeqIO
	markerdict = {}
	recordcount = 0
	pattern = re.compile("_\d+$")
	outrecordlist=[]
	if outfilename != None:
		outfile = openfile(outfilename, "wt")
	for f in infastalist:
		# ~ print(f)
		infile=openfile(f)
		if outfilename != None:
			for record in SeqIO.parse(infile, "fasta"):
				# ~ print(record.id)
				recordcount += 1
				markerdict[record.id] = {"stype": "total", "tax": None } #all proteins are by default set to type "total" at first. will be ssigned to markes after hmm-analyses later. possible markertypes=["total", "prok", "bac", "arc", "lsu", "ssu"] 
				SeqIO.write([record], outfile, "fasta")
				if contigdict:
					contigname = re.sub(pattern, "", record.id)
					#print(contigdict[contigname].keys())
					contigdict[contigname]["totalprots"].append(record.id) #todo: if i understand python scopes correctly, te dictionary should be changed globally, even if not explicitely returned... check this!				
					# ~ print(record.id)
					# ~ print(contigdict[contigname]["totalprots"])
					contigdict[contigname]["totalprotcount"]+= 1
					#print(contigdict[contigname]["totalprots"])
		else:
			outrecordlist.extend(list(SeqIO.parse(infile, "fasta")))
			# ~ print("NO OUTFILE")
			#print(outrecordlist)
			for record in outrecordlist: #todo: this is redundant. Simplify!
				# ~ print(record.id)
				recordcount += 1 
				markerdict[record.id] = {"stype": "total", "tax": None } #todo: duplicae command. may be error prone. streamline this
				contigname = re.sub(pattern, "", record.id)
				contigdict[contigname]["totalprots"] += [record.id] #todo: if i understand python scopes correctly, te dictionary should be changed globally, even if not explicitely returned... check this!
				contigdict[contigname]["totalprotcount"] += 1
		infile.close()
	if outfilename != None:
		outfile.close()
		output = outfilename
	else:
		output = outrecordlist
	if delete_original:
		for f in infastalist:
			os.remove(f)
	print("recordcount = {}".format(recordcount))
	if recordcount == 0:
		raise Exception("\nERROR: no records in input\n")
	if return_markerdict:
		return output, markerdict
	return output


def seqid2contig(seqid):
	for pattern in [ _trnapattern, _barrnappattern, _rnammerpattern, _prodigalpattern ]:
		pmatch = re.search(pattern, seqid)
		if pmatch != None:
			# ~ print("used this pattern: '{}' for this seqid: '{}'".format(pattern, seqid))
			# ~ print("--> matching string subgroup 1: '{}'".format(pmatch.group(1)))
			return pmatch.group(1)
		# ~ else:
			# ~ print("this pattern yielded no hit: {}".format(pattern))
			
def prodigalprot2contig(protid): #todo: probably obsolete. replace with above?
	pattern = re.compile("_\d+$")
	contigname = re.sub(pattern, "", protid)
	return contigname

def parse_protmarkerdict(protmarkerdict, contigdict, protmarkerlevel, markerdict = None): #todo make this a hidden object-function of bindata objects. check if contigdict actually needed
	#import re #todo: already imported globally. make sure this works even when calling externally. Then delete this line if not required
	#pattern = re.compile("_\d+$")
	print("parsing protmarkerdict!")
	marker = protmarkerlevel_dict[protmarkerlevel]
	for protid in protmarkerdict:
		contigname = prodigalprot2contig(protid)
		markername = protmarkerdict[protid]["marker"]
		contigdict[contigname][marker].append( protid)
		if markerdict != None:  #todo: if i understand python scopes correctly, te dictionary should be changed globally, even if not explicitely returned... check this!				
			# ~ print(protid)
			#print("\n{} is a marker of type '{}' with name '{}'\n".format(protid, marker,markername))
			# ~ import pdb; pdb.set_trace()
			markerdict[protid]["stype"] = "{} {}".format(marker, markername) #stored as space seperated string with "<marker type> <marker_hmm>". should be split later to get only markertype# TODO: in case someone insists on using spaces in contignames/proteinIDS, maybe change delimintor to tab (\t)?
	return contigdict

def add_rrnamarker_to_contigdict_and_markerdict(rrnamarkerdict, contigdict, markerdict): #todo make this a hidden object-function of bindata objects. check if contigdict actually needed
	for contig in rrnamarkerdict:
		#print(contigdict[contig])
		#print("-"*50)
		#print(rrnamarkerdict[contig])
		contigdict[contig].update(rrnamarkerdict[contig])
		for rRNA_type in rrnamarkerdict[contig]:
			for rRNA_instance in rrnamarkerdict[contig][rRNA_type]:
				markerdict[rRNA_instance]={"stype" : rRNA_type, "tax" : None}
	return contigdict, markerdict

class bindata(object): #meant for gathering all contig/protein/marker info
	def __init__(self, contigfile, threads = 1, outbasedir = "mdmcleaner_results", mincontiglength = 0, cutofftable = cutofftablefile): #todo: enable init with additional precalculated infos
		import re
		self.barrnap_pattern = re.compile("^\d{1,2}S_rRNA::(.+):\d+-\d+\([+-]\)")
		self.rnammer_pattern = re.compile("^rRNA_(.+)_\d+-\d+_DIR[+-]")
		self.binfastafile = contigfile
		bin_tempname = os.path.basename(contigfile)
		self.trnadict = {}
		for suffix in [".gz", ".fa", ".fasta", ".fna", ".fas", ".fsa"]:
			if bin_tempname.endswith(suffix):
				bin_tempname = bin_tempname[:-len(suffix)]
		self.outbasedir = outbasedir		
		self.bin_tempname = bin_tempname
		self.bin_resultfolder = os.path.join(self.outbasedir, self.bin_tempname)
		self.pickle_progressfile = os.path.join(self.bin_resultfolder, "bindata_progress.pickle") #todo: change to better system
		self.trna_jsonfile = os.path.join(self.bin_resultfolder, "bindata_progress.json.gz") #todo: REALLY start implementing a better system!
		self.trnafastafile = os.path.join(self.bin_resultfolder, self.bin_tempname + "_tRNAs.fasta.gz")
		for d in [self.outbasedir, self.bin_resultfolder]:
			if not os.path.exists(d):
				print("creating {}".format(d))
				os.mkdir(d)
		self.taxondict = None
		self.majortaxdict = None 
		self.consensustax = None
		self.totalprotsfile = os.path.join(self.bin_resultfolder, self.bin_tempname + "_totalprots.faa")
		self._get_all_markers(threads, mincontiglength, cutofftable)
	

		
	    #todo: simplify all those dicts
	    # todo the contigdict is probably not necessary in that form...
	    # todo: one function mapping protein-ids to contigs (just based on prodigal-nomenclature) --> DONE
	    #	todo: a new dict mapping gene/protein-ids to markers ["ssu", "lsu", "prok", "bact", "arch", "total"] --> DONE
	    # todo: inititate all dicts/variables set in _get_all_markers as None here, so that an overview remains possible
	     
	def _get_all_markers(self, threads, mincontiglength, cutofftable, from_json = True): #todo: split into a.) get totalprots b.) get_markerprots c.) get rRNA genes! #todo: delete the "from_json argument or set default to False
		#todo: make a more elegant checkpoint system. This convoluted stuff here may only be temporary because of shortage of time 
		if os.path.exists(self.totalprotsfile):
			sys.stderr.write("\n{} already exists. --> skipping ORF-calling!\n".format(self.totalprotsfile))
			self._prep_onlycontigs(mincontiglength, threads)
		else:
			self._prep_contigsANDtotalprots(mincontiglength, threads)
		self.protmarkerdictlist = get_markerprotnames(self.totalprotsfile, cutofftable, hmmsearch = "hmmsearch", outdir = self.bin_resultfolder, cmode = "moderate", level = "all", threads = threads) #todo: delete hmm_intermediate_results
		#todo: protmarkerdictlists probably not needed in that form. just save a general markerdict and a contigdict
		# ~ print("i am here now")
		for pml in range(len(self.protmarkerdictlist)): #todo: contigdict is maybe not needed in this form. choose simpler dicts ?
			# ~ print("   pml = {}".format(pml))
			self.contigdict = parse_protmarkerdict(self.protmarkerdictlist[pml], self.contigdict, pml, self.markerdict)
		if from_json and os.path.exists(self.pickle_progressfile): #todo: for debugging. hacy solution to preserve LCA from previous runs. can be done better (complete progress_dict like during db-downlad). this here is only temporary!
			print("loading bindata from pickle")
			markerprogress_dict = misc.from_pickle(self.pickle_progressfile)
			self.rRNA_fasta_dict = markerprogress_dict["rRNA_fasta_dict"]
			self.rrnamarkerdict = markerprogress_dict["rrnamarkerdict"]
			self.contigdict = markerprogress_dict["contigdict"]
			self.markerdict = markerprogress_dict["markerdict"]
			 
			# ~ import pdb; pdb.set_trace()
		else:
			self.rRNA_fasta_dict, self.rrnamarkerdict = runbarrnap_all(infasta=self.binfastafile, outfilebasename=os.path.join(self.bin_resultfolder, self.bin_tempname + "_rRNA"), barrnap="barrnap", output_directory = self.bin_resultfolder, threads=threads) #todo add option for rnammer (using the subdivided fastafiles)?
			self.contigdict, self.markerdict = add_rrnamarker_to_contigdict_and_markerdict(self.rrnamarkerdict, self.contigdict, self.markerdict) #todo: contigdict is probably not needed in this form. choose simpler dicts?
		if from_json and os.path.exists(self.trna_jsonfile):
			trna_list = misc.from_json(self.trna_jsonfile)
		else:
			trna_list = get_trnas(self.binfastafile) #todo: aragorn does not accept input from stdin (WHY!?) --> multithreading a bit more complicated. find a solution for mutiprocessing later, that does not break current workflow! --> probably switch to trnascanSE after all?
			trna_records = self.get_trna_sequences_from_contigs(trna_list)
			SeqIO.write(trna_records, openfile(self.trnafastafile, "wt"), "fasta")
			misc.to_json(trna_list, self.trna_jsonfile)
		for trna in trna_list:
			contig = self.marker2contig(trna)
			self.trnadict[trna] = contig
			self.markerdict[trna] = {"stype": "trna", "tax": None }
			self.contigdict[contig]["tRNAs"].append(trna) 
			
		print("created self.contigdict: {}".format(len(self.contigdict))) #todo: delete this line
		#todo: create progressdict like in db-setup (pickling won't work with this kid of object)

	def _save_current_status(self): #todo: for debugging. find better solution later. had to use pickle rather than json, because json does not recognize named_tuples
		markerprogress_dict = {"rRNA_fasta_dict": self.rRNA_fasta_dict, "rrnamarkerdict": self.rrnamarkerdict, "contigdict": self.contigdict, "markerdict": self. markerdict}
		misc.to_pickle(markerprogress_dict, self.pickle_progressfile)
	
	def _prep_contigsANDtotalprots(self, mincontiglength, threads):
		subfastas, self.contigdict = split_fasta_for_parallelruns(self.binfastafile, minlength = mincontiglength, number_of_fractions = threads)
		commandlist = [("getmarkers", "runprodigal", {"infasta" : subfastas[i], "outfilename" : os.path.join(self.bin_resultfolder, "tempfile_{}_prodigal_{}.faa".format(self.bin_tempname, i)) }) for i in range(len(subfastas))]
		tempprotfiles = misc.run_multiple_functions_parallel(commandlist, threads)
		# ~ tempdict = get_trnas(subfastas, threads=threads) #todo: aragorn does not accept input from stdin. find a solution for mutiprocessing later!
		# ~ self.trnadict = { trna[0]: contig for trna in tempdict[contig] for contig in tempdict} 
		# ~ for contig in self.contigdict:
			# ~ self.contigdict[contig]["tRNAs"] = tempdict[contig] 
		self.totalprotsfile, self.markerdict = combine_multiple_fastas(tempprotfiles, outfilename = self.totalprotsfile, delete_original = True, contigdict = self.contigdict,return_markerdict = True)
		print("created self.contigdict: {}".format(len(self.contigdict)))  #todo: delete this line
	
	def _prep_protmarker(self):
		self.protmarkerdictlist = get_markerprotnames(self.totalprotfile, cutofftable, hmmsearch = "hmmsearch", outdir = self.bin_resultfolder, cmode = "moderate", level = "all", threads = "4") #todo: delete hmm_intermediate_results
		for pml in range(len(self.protmarkerdictlist)):
			self.contigdict = parse_protmarkerdict(self.protmarkerdictlist[pml], self.contigdict, pml)	

	def _prep_rRNAmarker(self):
		self.rRNA_fasta_dict, self.rrnamarkerdict = runbarrnap_all(infasta=self.binfastafile, outfilebasename=os.path.join(self.bin_resultfolder, self.bin_tempname + "_rRNA"), barrnap="barrnap", output_directory = self.bin_resultfolder, threads=threads) #todo add option for rnammer (using the subdivided fastafiles)? #todo: parse resultfolder from basename. or rather basename from resultfolder!
		self.contigdict = add_rrnamarker_to_contigdict(self.rrnamarkerdict, self.contigdict)
				
	def _prep_onlycontigs(self, mincontiglength, threads):
		#todo: add trna-scan
		infile = openfile(self.binfastafile)
		self.contigdict = {}
		for record in SeqIO.parse(infile, "fasta"):
			if len(record) >= mincontiglength:
				self.contigdict[record.id] = _get_new_contigdict_entry(record)
		_, self.markerdict = combine_multiple_fastas([self.totalprotsfile], outfilename = None, delete_original = False, contigdict = self.contigdict, return_markerdict = True)
		print("created self.contigdict: {}".format(len(self.contigdict)))
		
	def get_contig_records(self, contiglist=None):
		"""
		returns the bin contig-/scaffold-records in fasta format as a list.
		per default all contigs are returned, but alternatively a list of specific contigs to be returned can be passed as argument
		"""
		if contiglist==None:
			contigset = set([contig for contig in self.contigdict]) #per default return all contigs	
		else:
			contigset = set(contiglist)
		outlist = []
		for record in SeqIO.parse(openfile(self.binfastafile), "fasta"):
			if record.id in contigset:
				if self.contigdict[record.id]["toplevel_tax"] != None:
					taxid = self.contigdict[record.id]["toplevel_tax"][-1].taxid
				else:
					taxid = None
				# ~ print(self.contigdict[record.id])
				# ~ print(list(self.contigdict[record.id].keys()))
				note = self.contigdict[record.id]["tax_note"]
				record.description = "taxid={}; note=\"{}\"".format(taxid, note)
				outlist.append(record) 
		return outlist
	
	def marker2contig(self, seqid):
		contigname = seqid2contig(seqid)
		assert contigname in self.contigdict, "seqid \"{}\" should correspond to a contig \"{}\", but no such contig in bindata!".format(seqid, contigname)
		return contigname

	
	def prot2contig(self, protid): #todo probably obsolete because of more genral funcion above
		contigname = prodigalprot2contig(protid)
		assert contigname in self.contigdict, "Protein id \"{}\" should correspond to a contig \"{}\", but no such contig in bindata!".format(protid, contigname)
		return contigname
	
	# ~ def pickleyourself(self):
		# ~ pass
		
	# ~ def unpickleyourself(self):
		# ~ pass	
		
	# ~ def get_contig2prot_dict(self): #todo: check if actually needed usful in any case... seems uneccessary as long as proteins can be assigned to contigs based on prodigal naming scheme. But MAY be useful in the futire, if planned to allow including ready made (e.g. Prokka) annotations?
		# ~ pass #todo: make this
	
	def get_prot2contig_dict(self): #todo: check if actually needed usful in any case... seems uneccessary as long as proteins can be assigned to contigs based on prodigal naming scheme. But MAY be useful in the futire, if planned to allow including ready made (e.g. Prokka) annotations?
		prot2contigdict = {}
		for contig in self.contigdict:
			for protein in self.contigdict[contig]["totalprots"]:
				prot2contigdict[protein] = contig
		return prot2contigdict
	
	# ~ def get_prot2marker_dict(self):
		# ~ pass #todo: make this

	# ~ def add_lca2markerdict(self, blastdata,db, threads=1): #attempt to enable multithreading here. does not work, because starmap needs to pickle shared objects, and db is not pickable! todo: find a way to make db pickable!
		# ~ import time
		# ~ import lca
		# ~ start=time.time()
		# ~ counter = 0
		# ~ lca_jobs = [ ("lca", "strict_lca", {"taxdb" : db, "seqid" : gene, "blasthitlist" : hittuples}) for gene, hittuples in blastdata.get_best_hits_per_gene() ]
		# ~ lca_results = misc.run_multiple_functions_parallel(lca_jobs, threads)
		# ~ for l in lca_results:
			# ~ self.markerdict[l.seqid]["tax"] = l
		# ~ stop = time.time()
		# ~ print("total lca time was : {}".format(stop -start))
		# ~ sys.stdout.flush()

	def add_lca2markerdict(self, blastdata, db): #todo: add multithreading!!!
		import time
		import lca
		start=time.time()
		counter = 0
		for gene, hittuples in blastdata.get_best_hits_per_gene():
			counter += 1
			if counter % 100 == 0:
				sys.stderr.write("\r\tclassified {} records so far".format(counter))
			self.markerdict[gene]["tax"] = lca.strict_lca(db, gene, hittuples)			
		sys.stderr.write("\r\tfinished classifying {} records!\t\t\n".format(counter))
		stop = time.time()
		print("total lca time was : {}".format(stop -start))
		sys.stdout.flush()

	# ~ def get_lcadict(self, blastdata, db):
		# ~ import time
		# ~ import lca		
		# ~ for contig, hittuples in blastdata.get_best_hits_per_contig():
			# ~ pass #whatt??
		
	
	def verify_arcNbac_marker(self, db):
		"""
		removes "bacterial" markers that are not bacterial and "archaeal" markers that are not archaeal from the list of specific markers
		"""
		for contig in self.contigdict:
			wrongmarkerlist = []
			for bacmarker in self.contigdict[contig]["bac_marker"]:
				if self.markerdict[bacmarker]["tax"] == None or db.isnot_bacteria(self.markerdict[bacmarker]["tax"].taxid):
					wrongmarkerlist.append(bacmarker)
			if len(wrongmarkerlist) > 0:
				print("removing the following 'bacterial' markers from contig {} : {}".format(contig, ", ".join(wrongmarkerlist)))
			self.contigdict[contig]["bac_marker"] = [m for m in self.contigdict[contig]["bac_marker"] if m not in wrongmarkerlist ]
			wrongmarkerlist = []
			for arcmarker in self.contigdict[contig]["arc_marker"]:
				if self.markerdict[arcmarker]["tax"] == None or db.isnot_archaea(self.markerdict[arcmarker]["tax"].taxid):
					wrongmarkerlist.append(arcmarker)
			if len(wrongmarkerlist) > 0:
				print("removing the following 'archaeal' markers from contig {} : {}".format(contig, ", ".join(wrongmarkerlist)))
			self.contigdict[contig]["arc_marker"] = [m for m in self.contigdict[contig]["arc_marker"] if m not in wrongmarkerlist ]	
	
	def get_topleveltax(self, db):
		def levels_difference(querytax, majortax): #querytax and majortax can be either taxtuplelists or majortaxdicts, doesnt matter
			if topleveltax != None:
				if querytax == None:
					return len(topleveltax)
				if len(topleveltax) > len(querytax):
					return len(topleveltax) - len(querytax)
			return 0 
		
		def contradicting_tax(tax_entryA, taxentryB): #todo: convoluted. establish a common datastructure for taxons! 
			if None in [tax_entryA, taxentryB]:
				return False
			taxa = tax_entryA[0]
			taxb = taxentryB[0]
			checklevel=min([len(taxa), len(taxb)]) - 1
			return taxa[checklevel] != taxb[checklevel]
					
		import lca
		print("determining major taxon")
		markerranking = [ "ssu_rRNA_tax", "lsu_rRNA_tax", "prok_marker_tax", "total_prots_tax" ]
		#taxlevels = ["root", "domain", "phylum", "class", "order", "family", "genus", "species"] # todo: change to lca.taxlevels
		self.taxondict = { tl: {} for tl in lca.taxlevels }
		#todo: taxlevels shoule be keys. values should be subdicts tuples of taxas as keys showing the lineage to each taxlevel (e.g.: ("bacteria", "proteobacteria", "alphaproteobacteria")
		for contig in self.contigdict:
			topleveltax = None
			contiglen = self.contigdict[contig]["contiglen"]
			for m in markerranking:
				if self.contigdict[contig][m] != None:
					if topleveltax == None:
						topleveltax = self.contigdict[contig][m]
						self.contigdict[contig]["toplevel_tax"] = topleveltax
						self.contigdict[contig]["toplevel_marker"] = m
						self.contigdict[contig]["toplevel_ident"] = topleveltax[-1].average_ident
						self.contigdict[contig]["toplevel_taxlevel"] = lca.taxlevels[len(topleveltax)-1]
					else:
						contradiction, contradiction_evidence = lca.contradicting_taxasstuples(self.contigdict[contig][m], topleveltax, return_idents = True)
						if contradiction != None:
							self.contigdict[contig]["contradictions_interlevel"].append(contradiction_evidence[0])
			if topleveltax != None: #mark viral contigs, in case theys should be considered specially later (cases were value remains at default "None" are not classified, therfore not sure if viral or not)
				if db.is_viral(topleveltax[0].taxid):
					self.contigdict[contig]["viral"] = True
				else:
					self.contigdict[contig]["viral"] = False		
					
			if topleveltax  != None:
				for x in range(len(topleveltax)):
					taxlevel = lca.taxlevels[x]
					taxtuple = tuple(mt.taxid for mt in topleveltax[:x+1])
					if taxtuple not in self.taxondict[taxlevel]:
						self.taxondict[taxlevel][taxtuple] = {"contiglengths": [contiglen], "sumofcontiglengths" : contiglen}
					else:
						self.taxondict[taxlevel][taxtuple]["contiglengths"].append(contiglen)
						self.taxondict[taxlevel][taxtuple]["sumofcontiglengths"]+=contiglen
		
		#now sort the taxcountdict keys by sumofcontiglenghts
		self.majortaxdict = {tl:None for tl in lca.taxlevels}
		# ~ import pdb; pdb.set_trace()
		last_tax_entry = None
		for tl in lca.taxlevels:
				tempsorted = sorted([(t, self.taxondict[tl][t]["sumofcontiglengths"]) for t in self.taxondict[tl]], key = lambda x : x[1])
				# ~ import pdb; pdb.set_trace()
				while len(tempsorted) > 0:
					if contradicting_tax(last_tax_entry, tempsorted[-1]):
						tempsorted.pop(-1)
					else:
						self.majortaxdict[tl] = tempsorted[-1]
						last_tax_entry = tempsorted[-1]
						break
		
		if last_tax_entry != None:
			self.consensus_tax = db.taxid2taxpath(last_tax_entry[0][-1])
		
		for contig in self.contigdict:
			contradiction, contradiction_evidence = lca.contradict_taxasstuple_majortaxdict(self.contigdict[contig]["toplevel_tax"], self.majortaxdict, return_idents = True) #check each contigs if contradicts majortax
			if contradiction:
				self.contigdict[contig]["contradict_consensus"] = contradiction
				self.contigdict[contig]["contradict_consensus_evidence"] = contradiction_evidence 
			else: #if it DOEN'T contradict, check up to how many levels actually match end report the difference
				self.contigdict[contig]["consensus_level_diff"] = levels_difference(self.contigdict[contig]["toplevel_tax"], self.majortaxdict)
		#return taxondict, majortaxdict 

	def get_consensus_taxstringlist(self):
		if self.consensus_tax != None:
			return [ taxtuple[0] for taxtuple in self.consensus_tax ]


	def calc_contig_trust_score(self, contig, db):#todo: virals are not ignored here, but just at the filtering step. will get low trust regardless
		"""
		the taxpaths of the bin_consensus_tax and each contigs highest_ranking_tax are compared. 
		
			starting from a total_bonus_penalty of 0, for each matching rank beginning from domain (i.e. excluding "root"), a bonus is added according to the corresponding rankindex in "level_boni" (up to a maximum total of 15.875 for full identities up to species level)
				--> example1: if two taxpaths match up to genus level, then the total_bonus_penalty becomes: 0.125 (domain) + 0.25 (phylum) + 0.5 (class) + 1.0 (order) + 2.0 (family) + 4.0 (genus) = 7.875
				--> example2: if two taxpaths only match up to phylum level, then the total_bonus_penalty becomes: 0.125 (domain) + 0.25 (phylum) = 0.375
			if the taxpaths do not contradict each other BUT are of unequal length, the a tenth of the corresponding level_penalty is substracted for each level that is annotated only in one taxpath
				--> example1: if the consensus_taxpath is annotated only to genus level, but the contig-taxpath is annotated to species level, the species-level penalty divided by 10 is substracted from the total_bonus_penalty: 7.875 (total_match_bonus) - [0.125 (rank_penalty for species-level)/10) = 7.84375
				--> example2: if the consensus is annotated to genus level, but the contig only to phylum level, the sum of level-penalties for each "missing" rank divided by 10 is substracted: 0.375 (total_match_bonus) - [0.125 (species) - 0.25 (genus) - 0.5 (family) - 1(order) -2(class)]/4 = 0.375 - 3.875/4 = 0.375 - 0.3875 = -0,0125
			beginning from the first mismatching rank after root (so domain and up), a penalty is substracted for each following rank annoated in the longer of the two taxpaths, according to the corresponding rankindex in "level_penalties"(up to a maximum total of 15.875 for mismatches at domain-level if one of the taxpaths is annotated to species level)
				--> example3: if at least one of the taxpaths is annotated to genus level, but the paths differ at phylum level, then the total_bonus_penalty becomes: 0.125 (match at domain-level) - 4 (mismatch at phylum) -2 (mismatch at class) -1 (mismatch at order) -0.5 (mismatch at family) -0.25 (mismatch at genus) = 0.125 -  7.875 = -7.75
				--> example4: if one taxpath is annotated to species level, but the other contradicts it at domain level, then the total_bonus_penalty becomes: 0 (no match-bonus) - 8 (mismatch at domain) - 4 (mismatch at phylum) -2 (mismatch at class) -1 (mismatch at order) -0.5 (mismatch at family) -0.25 (mismatch at genus) =0 - 15.875 = -15.875
			the total_bonus_penalty is then multiplied by a factor depending on the marker_level used and the average blast_identities that yielded the contig LCA-annotation
					example1: if the contig-annotation is based on 16S-rRNA gene sequences with ~92% blast-identity the total_bonus_penalty becomes: 7.84375 * 1 * 0.92 = 7.21625
					example2: if the contig-annotation is based on total_protein-level with ~40% blast-identities, then the total_bonus_penalty becomes: -0,0125 * 0.7 * 0.4 = -0.0035
			the contig_trust_index is then derived by adding the total_bonus_penalty to a base_level_score of 15.875 (the sum of possible boni) and normalizing to a range between 0-10 (by dividing by the maximum possible score, i.e. 2x the base_level_score, multiplying by 10 and rounding to the next full integer)
					example1: the contig_trust becomes: (15.875 + 7.21625)/(2x15.875)*10 = 23.09125/31.75*10 = 7,272834646 = 7
					example2: the contig_trust becomes: (15.875 -0.0035)//(2x15.875)*10 = 15.8715/31.75*10 = 4.998897638 = 5
		these idices should serve as an indicator which contigs represent a high to medium risk for contamination, requiring validation-analyses if they were to be kept within the bin. 
			trust values ranging from 6-10 represent increasing confidence, that the contig in question is correctly assigned to the correct taxon/bin and likely not a contamination
			trust values of 5 indicate contigs for which neither contamination status nor correct assignment could be indicated. Such contigs should optimally be verified if possible
			trust values below represent contigs which are likely misassigned. Such contigs should require some kind of justification or verification in order to retain them in the bin, and should be excluded otherwise...  
		contigs that show reference-db ambiguities of the category "ref-db-contamination" typically retain a trust-score of 5, since despite the contamination affecting the database, additional validations are required to determine if it also affecte the bin itself (i.e. despite the contig representing a contamination in the reference-database, is it perhaps nonetheless correctly assigned in THIS bin?)  
		EXCEPTION: if a contig contains no detectable coding features, it is assumed eukaryotic
		"""
		import lca
		levels = ["domain", "phylum", "class", "order", "family", "genus", "species"]
		level_penalties = [8.0,4.0,2.0,1.0,0.5,0.25,0.125] #penalties for domain, phylum, class, order, family, genus & species differences, respectively (root is ignored. viral contigs get same penalty as eukaryotic contigs)
		level_boni = reversed(level_boni)
		base_level_score = sum(level_boni)
		max_level_score = base_level_score*2
		# ~ level_score_dict = { group[0]:{"penalty": group[1], "bonus":group[2]} for group in zip(lca.taxlevels[1:], level_scores, reversed(level_scores)) }#differing at domain level results in maximum penalty while agreeing and domain elvel results in minimum bonus
		
		markerlevel_factors = {	'ssu_rRNA_tax' : 1, \
												'lsu_rRNA_tax' : 0.9, \
												'prok_marker_tax' : 0.8, \
												'total_prots_tax' : 0.7, \
												None : 0 }	
		
		
		consensus_taxlevel = list(self.majortaxdict.keys())[-1] #TODO: these should be instance attributes
		consensus_taxid = self.majortaxdict[consensus_taxlevel][0][-1] #TODO: these should be instance attributes
		consensus_taxpath = db.taxid2taxpath(consensus_taxid)[1:] #root is skipped
		
		contig_toptaxasstuplelist = self.contigdict[contig]["toplevel_tax"]
		if contig_toptaxasstuplelist == None:
			if sum([len(contigdict[x]) for x in [ "ssu_rRNA", "lsu_rRNA", "tsu_rRNA", "tRNAs", "prok_marker", "bac_marker", "arc_marker", "totalprots"]]) == 0:
				self.contigdict[contig]["tax_note"] += "ATTENTION: contig contains no detectable coding features --> automatically assumed eukaryotic!"
				self.contigdict[contig]["info_flag"] = "non-coding"
				return 3
			else:
				self.contigdict[contig]["tax_note"] += "unclassified"
				self.contigdict[contig]["info_flag"] = "unclassified"
				return 5
		contig_toptaxid =  contig_toptaxasstuplelist[-1].taxid
		contig_toptaxpath = db.taxid2taxpath(contig_toptaxid)[1:] #root is skipped
		contig_toptaxmarkerlevel = self.contigdict[contig]["toplevel_marker"]
		contig_toptax_taxrank = self.contigdict[contig]["toplevel_taxlevel"]
		contig_toptaxident = self.contigdict[contig]["toplevel_ident"]
		
		contig_toplevelcontradiction_taxrank = self.contigdict[contig]["contradict_consensus"]
		contig_toplevelcontradiction_evidence = self.contigdict[contig]["contradict_consensus"]
		
		total_bonus_penalty = 0
		max_levelindex = max([len(contig_toptaxpath), len(consensus_taxpath)])
		min_levelindex = min([len(contig_toptaxpath), len(consensus_taxpath)])
		if contig_toplevelcontradiction_taxrank:
			self.contigdict[contig]["tax_note"] += "ATTENTION: contig-classification '{}' ({}% blast-ident) contradicts majority-classification '{}' at rank '{}' on '{}'-level".format(contig_toptaxid, contig_toptaxident, consensus_taxid, contig_toplevelcontradiction_taxrank, contig_toptaxmarkerlevel)
			contradiction_levelindex = levels.index(contig_toplevelcontradiction_taxrank)
			if db.isviral(contig_toptaxid):
				self.contigdict[contig]["info_flag"] = "viral"
			else:
				self.contigdict[contig]["info_flag"] = "mismatch_{}".format(contradiction_levelindex)
			total_bonus_penalty -= sum(level_penalties[contradiction_levelindex:max_levelindex])
		else:
			self.contigdict[contig]["tax_note"] += "contig-classification '{}'({}% blast-ident) matches majority-classification '{}' upto rank {} on '{}'-level".format(contig_toptaxid, contig_toptaxident, consensus_taxid, levels[min_levelindex], contig_toptaxmarkerlevel)
			total_bonus_penalty += sum(level_boni[:min_levelindex])
			total_bonus_penalty -= sum(level_penalties[min_levelindex:max_levelindex])/10
			self.contigdict[contig]["tax_note"] 
		bonus_factor = markerlevel_factors[contig_toptaxmarkerlevel] * (contig_toptaxident/100)
		total_bonus_penalty *= bonus_factor
		contig_trust_score = round(((base_level_score + total_bonus_penalty) / max_level_score)*10)
		return contig_trust_score
			
		
	def check_and_set_filterflags(self, contig, filter_rankcutoff = "family", filter_viral = True, filter_unclassified = False): #args are unpacked only to enforce keyword aguments for protblasta and nucblasts (to prevent accidentally passing them in the wrong order) 
		import re
		import lca
		if self.contigdict[contig]["info_flag"] == "non-coding":
			return "delete" #non-coding contigs are automatically assumed aéukaryotic contamination
		if re.match("mismatch_[2-7]", self.contigdict[contig]["info_flag"]) and self.contigdict[contig]["toplevel_marker"] in ["ssu_rRNA_tax", "lsu_rRNA_tax", "tsu_rRNA_tax"]:
			mismatch_rankindex = re.match("mismatch_([2-7])").group(1)
			rankcutoff_index = lca.taxlevels[1:].index(filter_rankcutoff)
			if mismatch_rankindex >= rankcutoff_index:
				filterflag = "delete"
			for protmarker in ["prok_marker_tax", "total_prots_tax"]:
				if self.contigdict[contig][protmarker] != None and (lca.contradicting_taxasstuples(self.contigdict[contig][protmarker], self.contigdict[contig]["toplevel_tax"]) and not lca.contradict_taxasstuple_majortaxdict(self.contigdict[contig][protmarker], self.majortaxdict)):
					self.contigdict[contig]["info_flag"]["refdb_ambig"]
					self.contigdict[contig]["refdb_ambig"] = "gtdb/silva database ambiguity"
					self.contigdict[contig]["tax_note"] +=" but not on {}-level --> ambigeous gtdb-taxon"
					filterflag = "evaluate_high"
					break #only check the highest ranking protein-annotation...
		elif self.contigdict[contig]["refdb_ambig"] and "fringe case" in self.contigdict[contig]["refdb_ambig"] and not "potential refDB-contamination" in self.contigdict[contig]["refdb_ambig"]:
			if self.contigdict[contig]["contradict_consensus"] == None:
				filterflag = "keep"
			else:
				filterflag = "evaluate_low"
		elif self.contigdict[contig]["refdb_ambig"] and "potential refDB-contamination [high indication" in self.contigdict[contig]:
			filterflag = "evaluate_high"
		elif self.contigdict[contig]["refdb_ambig"] and "potential refDB-contamination" in self.contigdict[contig]:
			filterflag = "evaluate_low"
					
					
		pass # should be divided into: include, evaluate_low_suspicion, evaluate_high_suspicion, delete							
		#todo: DONE if infoflag in mismatch_2-7 and markertype in ["ssu_RNA_tax", "lsu_rRNA_tax"] --> check also protein_annotations, put into  evaluate_low(or high?)_suspicion if those match majority_tax
		#			DONE elif infoflag in [unclassified, viral, mismatch_3-7] --> check user_input (delete or include)
		# 		if infoflag = refdb_contam_fringe --> check if not contradicting (delete or low_suspicion)
		#			if infoflag = gtdb_silva --> check if not contradicting, and also check protein_markers (high_suspicion, low_suspicion)
		
	def evaluate_and_flag_all_contigs(self, *args, db, protblasts, nucblasts, filter_rankcutoff = "family", filter_viral = True, filter_unclassified = False): #args are unpacked only to enforce keyword aguments for protblasta and nucblasts (to prevent accidentally passing them in the wrong order) 
		ref_db_ambiguity_overview = {}
		for contig in self.contigdict:
			self.contigdict[contig]["trust_index"] = calc_contig_trust_score(contig, db)
			if self.contigdict[contig]["refdb_ambig"]:
				if self.contigdict[contig]["toplevel_marker"] in ["ssu_rRNA_tax", "lsu_rRNA_tax", "tsu_rRNA_tax"]:
					blastobj = nucblasts
				elif self.contigdict[contig]["toplevel_marker"] in ["prok_marker_tax", "total_prots_tax"]:
					blastobj = protblasts
				ambiguityinfo = self._check_contig_refdb_ambiguity(contig, blastobj, db)
				ref_db_ambiguity_overview[contig] = ambiguityinfo
				self.contigdict[contig]["refdb_ambig"] = ambiguityinfo["amb_type"]
				self.contigdict[contig]["refdb_ambig_infotext"] = ambiguityinfo["amb_infotext"]
				self.contigdict[contig]["refdb_ambig_evidence"] = ambiguityinfo["amb_evidence"]
			self.contigdict[contig]["filterflag"] = self.check_and_set_filterflags(contig, filter_rankcutoff = filter_rankcutoff, filter_viral = filter_viral, filter_unclassified = filter_unclassified) 

	def _check_contig_refdb_ambiguity(self, contig, blastobj, db):
		import lca
		# ~ print("check_contig_refdb_ambiguity")
		markerlevel = self.contigdict[contig]["toplevel_marker"]
		cutoffs = [ x[markerlevel] for x in [ lca.species_identity_cutoffs, lca.genus_identity_cutoffs, lca.order_identity_cutoffs ] ]
		markername_dict = {	"ssu_rRNA_tax" : self.contigdict[contig]["ssu_rRNA"],\
											"lsu_rRNA_tax" : self.contigdict[contig]["lsu_rRNA"],\
											"prok_marker_tax" : self.contigdict[contig]["prok_marker"] + self.contigdict[contig]["arc_marker"] + self.contigdict[contig]["bac_marker"],\
											"total_prots_tax": self.contigdict[contig]["totalprots"] }
		markernames = markername_dict[markerlevel]
		ambiguity_info = blastobj.get_contradicting_tophits(markernames, db, cutoffs, markerlevel) 
		return ambiguity_info		
		
	def print_contigdict(self, filename = None): #todo: delete this!
		if filename:
			outfile = openfile(filename, "wt")
		headerline = "contig\tcontiglen\tprotcount\trRNAcount\tmarkerprotcount\ttoplevel_tax\ttopmarker\ttoptaxevidence\ttoptaxambigeous\tcontradict_consensus\tcontradict_consensus_evidence\tcontradictions_interlevel_evidence\tviral\ttrustindex\n"
		if filename:
			outfile.write(headerline)
		else:
			sys.stdout.write(headerline)
		for contig in self.contigdict:
			contiglen = self.contigdict[contig]["contiglen"]
			totalprotcount = self.contigdict[contig]["totalprotcount"]
			rRNAcount = len(self.contigdict[contig]["ssu_rRNA"]) + len(self.contigdict[contig]["lsu_rRNA"])
			markerprotcount = len(self.contigdict[contig]["prok_marker"]) + len(self.contigdict[contig]["bac_marker"]) + len(self.contigdict[contig]["arc_marker"]) 
			if self.contigdict[contig]["toplevel_tax"] != None:
				toptaxid = self.contigdict[contig]["toplevel_tax"][-1].taxid
				toptaxevidence = self.contigdict[contig]["toplevel_tax"][-1].average_ident #todo: the name of that field should change when i use uniform taxassignment named-tuples
				toptaxambigeous = self.contigdict[contig]["toplevel_tax"][-1].ambigeous#todo: the name of that field should change when i use uniform taxassignment named-tuples
			else:
				toptaxid = None
				toptaxevidence = None
				toptaxambigeous = None
			topmarker = self.contigdict[contig]["toplevel_marker"]
			contradict_consensus = self.contigdict[contig]["contradict_consensus"]
			contradict_consensus_evidence = self.contigdict[contig]["contradict_consensus_evidence"]
			contradictions_interlevel = self.contigdict[contig]["contradictions_interlevel"]
			viral = self.contigdict[contig]["viral"]
			trustindex = self.contigdict[contig]["trust_index"]
			line = "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(contig, contiglen, totalprotcount,rRNAcount,markerprotcount,toptaxid,topmarker,toptaxevidence, toptaxambigeous, contradict_consensus, contradict_consensus_evidence,contradictions_interlevel,viral, trustindex)	
			if filename:
				outfile.write(line)
			else:
				sys.stdout.write(line)
	
	def get_unclass_contignames(self):
		return [ contig for contig in self.contigdict if self.contigdict[contig]["toplevel_tax"] == None ]
	
	def get_unclass_contigs(self):
		return self.get_contig_records(self.get_unclass_contignames())

	def get_unclass_contigs_with_auxrna(self): #todo: does not help 
		return self.get_contig_records([ contig for contig in self.get_unclass_contignames() if max(len(self.contigdict[contig]["tsu_rRNA"]), len(self.contigdict[contig]["tRNAs"])) > 0 ])
	
	def get_auxrnagenes_from_unclass_contigs(self):
		contignames = [ contig for contig in self.get_unclass_contignames() if max(len(self.contigdict[contig]["tsu_rRNA"]), len(self.contigdict[contig]["tRNAs"])) > 0 ]
	
	#todo: write all trnas to a trna-fasta (5s is already written to fasta)
	#here get a list of all such genes on remaining unclassified contigs
	#read in the trna and 5S fastas
	#return only those genes of interest
	
	# ~ def trust_index_from_tax_score(self, tax_score):
		# ~ return round((max([0, tax_score])/maxscore) *10) #0 untrusted, 1-3 highly suspicious, 4-5 unknown, 6-10: trusted
		
	
	def get_contignames_with_trustscore(self, trustscore):
		return [ contig for contig in self.contigdict if self.contigdict[contig]["trust_index"] == trustscore ]
	
	def get_trusted_contignames(self, trustcutoff=6):
		return [ contig for contig in self.contigdict if self.contigdict[contig]["trust_index"] >= trustcutoff ]
	
	def get_trusted_contigs(self, trustcutoff=6):
		return self.get_contig_records(self.get_trusted_contignames())
	
	def get_untrusted_contignames(self, trustcutoff=4):
		return [ contig for contig in self.contigdict if self.contigdict[contig]["trust_index"] <= trustcutoff ]

	def get_untrusted_contigs(self, trustcutoff=4):
		return self.get_contig_records(self.get_untrusted_contignames(trustcutoff))

	def get_fraction_contamination(self, trustcutoff=4):
		sum([ self.contigdict[contig]["contiglen"] for contig in self.get_untrusted_contignames(trustcutoff)]) / self.get_total_size()
		
	def get_total_size(self):
		return sum([ self.contigdict[contig]["contiglen"] for contig in self.contigdict ])
	
	def get_refdbcontamination_contignames(self):
		return [ contig for contig in self.contigdict if "ref-db contamination" in self.contigdict[contig]["tax_note"] ]
		
	def get_fraction_refdbcontamination(self):
		return sum([ self.contigdict[contig]["contiglen"] for contig in self.get_refdbcontamination_contignames() ]) / self.get_total_size()

	def get_nocoding_contignames(self):
		return [ contig for contig in self.contigdict if " lack of coding regions" in self.contigdict[contig]["tax_note"] ]

	def get_fraction_nocoding(self):
		return sum([ self.contigdict[contig]["contiglen"] for contig in self.get_nocoding_contignames() ]) / self.get_total_size()

	def get_trna_coordinates(self, trna_name):
		import re
		locationpattern = "c?\[(\d+),(\d+)\]"
		locationstring = re.match(_trnapattern, trna_name).group(3)
		coordinatematch = re.match(locationpattern, locationstring)
		if locationstring.startswith("c"):
			direction = -1
			start = int(coordinatematch.group(2))
			stop = int(coordinatematch.group(1))
		else:
			direction = 1
			start = int(coordinatematch.group(1))
			stop = int(coordinatematch.group(2))
		return start, stop, direction
	
	def get_trna_sequences_from_contigs(self, trna_namelist):
		print("getting trna sequences")
		trna_namelist = sorted(trna_namelist)
		# ~ print(trna_namelist)
		contignamelist = list(set([self.marker2contig(trna_name) for trna_name in trna_namelist]))
		contigrecords = { record.id : record for record in self.get_contig_records(contignamelist) }
		outrecords = []
		# ~ print("loopin")
		for trna in trna_namelist:
			# ~ print(trna)
			sys.stdout.flush()
			sys.stderr.flush()
			contig = self.marker2contig(trna)
			start, stop, direction = self.get_trna_coordinates(trna)
			newrecord = contigrecords[contig][start:stop:direction]
			newrecord.id = trna
			newrecord.name = trna
			newrecord.description = trna
			outrecords.append(newrecord)
		return outrecords
		
	# ~ def make_kronachart(self):
		# ~ def make_krona_inputtable(contigdict):
			# ~ outlines = []
			# ~ for contig in contigdict:
				
				
		# ~ pass
	
	def clean_yourself(self, trustcutoff=4, ignore_viral=True):	
		for  contig in self.get_untrusted_contignames(trustcutoff=4): #todo: actually have to make sure that all corresponding marker entries in markerdict, that belong to those removed conitgs are also removed. But skipping for now (time issues)
			self.contigdict.pop(contig, None)

######################################################
# test functions below (can be deleted)
def _test_markernames():
	sys.stderr.write("\ntesting get_markernames...")
	proteinfastafile = sys.argv[1]
	cutofftable = sys.argv[2]
	sys.stderr.write("\nreading cutofftable")
	cutoffdict = get_cutoff_dict(cutofftable)
	sys.stderr.write("\nsearching markers")
	markerdict = get_markerprotnames(proteinfastafile, cutoffdict, hmmsearch = "hmmsearch", outdir = ".", cmode = "moderate", level = "prok", threads = "4")
	sys.stderr.write("\nwriting results\n")
	write_markerdict(markerdict, "delmetestresults.tsv")
	
def _test_basicmarkers():
	infasta = sys.argv[1]
	tempdir = sys.argv[2]
	if not os.path.exists(tempdir):
		os.mkdir(tempdir) #todo: implement tempfile module if available a base module
	#else:
		#raise Exception("\n'{}' already exists\n".format(tempdir))
	cutofftable = os.path.join(hmmpath, "cutofftable_combined.tsv")
	cutoff_dict = get_cutoff_dict(cutofftable)
	sys.stderr.write("\nrunning prodigal...\n")
	protfasta = runprodigal(infasta, os.path.join(tempdir, "delme_protfasta"), prodigal="prodigal")
	#protfasta = os.path.join(tempdir, "delme_protfasta")
	#todo: create a "runparallel function in misc or here
	level = "all"
	sys.stderr.write("\nextracting markers for level {}\n".format(level))
	outfastalist = get_markerprots(protfasta, cutoff_dict, level = level, outfile_basename = os.path.join(tempdir, "markers".format(level)), threads = 4)
	sys.stderr.write("  --> created files: '{}'".format(", ".join(outfastalist)))
	#todo: implement automatic blasts
	#todo implement actual lca

def _test_pipeline():
	import getdb
	infasta = sys.argv[1]
	threads = int(sys.argv[2])
	import misc
	outfilebasename = "testtesttest2"
	progressdump = get_all_markers(infasta, outfilebasename, threads, cutoffdict = cutofftablefile)
	getdb.dict2jsonfile(progressdump, "gelallmarkers.json")
	outfile = openfile("testcontigmarkers.tsv", "wt")
	contigdict = progressdump["contigdict"]
	sys.stderr.write("\nwriting results\n")
	outfile.write("contig\t{}\n".format("\t".join([x for x in contigdict[list(contigdict.keys())[0]]])))
	for contig in contigdict:
		line = "{}\t{}\n".format(contig, "\t".join([";".join([y["seqid"] if type(y) == dict else str(y) for y in contigdict[contig][x] ]) for x in contigdict[contig]])) #todo: in protmarkerdicts change "protid" to "seqid". Add "seqid" and "marker" keys to ssu and lsu entries
		outfile.write(line)

def _test_barrnap():
	from Bio import SeqIO
	infasta = sys.argv[1]
	threads = int(sys.argv[2])
	tempfilelist, gfflist = [], []
	rRNA_fasta = runbarrnap_all(infasta=infasta, outfilename="new_test_barrnap_results_dedup.fasta", barrnap="barrnap", threads=threads)

def _test_pipelineobj():
	import getdb
	infasta = sys.argv[1]
	threads = int(sys.argv[2])
	import misc
	outfilebasename = "testtesttest2"
	testbin = bindata(contigfile=infasta, threads=threads)
	outfile = openfile("testcontigmarkers.tsv", "wt")
	sys.stderr.write("\nwriting results\n")
	outfile.write("contig\t{}\n".format("\t".join([x for x in testbin.contigdict[list(testbin.contigdict.keys())[0]]])))
	for contig in testbin.contigdict:
		line = "{}\t{}\n".format(contig, "\t".join([";".join([y["seqid"] if type(y) == dict else str(y) for y in testbin.contigdict[contig][x] ]) for x in testbin.contigdict[contig]])) #todo: in protmarkerdicts change "protid" to "seqid". Add "seqid" and "marker" keys to ssu and lsu entries
		outfile.write(line)	

def _test_splitfasta2file():
	infasta = sys.argv[1]
	threads = int(sys.argv[2])
	outfilebasename = "huhudelmetest/fractiontest"
	a,b=split_fasta_for_parallelruns(infasta = infasta, number_of_fractions = threads, outfilebasename = outfilebasename)
	print(a)

def main():
	import argparse
	myparser = argparse.ArgumentParser(prog=os.path.basename(sys.argv[0]), description= "extracts markers from input-fastas")
	subparsers = myparser.add_subparsers(dest = "command")
	get_trna_args = subparsers.add_parser("get_trnas", help = "extracts trna sequences using Aragorn")
	get_trna_args.add_argument("infastas", nargs = "+", help = "input fasta(s). May be gzip-compressed")
	get_trna_args.add_argument("--outdir", dest = "outdir", default = ".", help = "Output directory for temporary files, etc. Default = '.'")
	get_trna_args.add_argument("-b", "--binary", default = "binary", help = "aragorn executable (with path if not in PATH). Default= assume aragorn is in PATH")
	get_trna_args.add_argument("-t", "--threads", default = 1, type = int, help = "number of parallel threads. Is only used when multiple input files are passed")
	args = myparser.parse_args()
	
	if args.command == "get_trnas":
		print("here are the results:")
		print(get_trnas(*args.infastas, outdirectory = args.outdir, aragorn = args.binary, threads = args.threads)) # todo: make a dedicated standalone funtion with a mini_bindata-object (inherited from bindata)
	#_test_markernames()
	#_test_basicmarkers()
	#_test_multiprodigal()
	#_test_barrnap()
	#_test_pipeline()
	# ~ _test_pipelineobj()
	#_test_splitfasta2file()
if __name__ == '__main__':
	main()