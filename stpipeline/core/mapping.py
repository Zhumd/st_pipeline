#!/usr/bin/env python
""" 
This module contains functions related to sequence alignment and barcode
demultiplexing in the ST pipeline
"""

import logging 
import subprocess
from stpipeline.common.stats import Stats
from stpipeline.common.utils import *
import gc
import uuid

def createIndex(genome,
                threads,
                tmpFolder):
    """
    When running STAR in two pass mode a new index needs to be created
    using the discovered splice variants. This functions generates
    the index and returns the location to the new index.
    :param genome: the path to the fasta file with the original genome
    :param threads: number of threads to be used
    :param tmpFolder: where to place the temporary files and to find the file with variants
    :type genome: str
    :type threads: int
    :type tmpFolder: str
    :returns: the path to the new index
    :raises: RuntimeError
    """
    
    logger = logging.getLogger("STPipeline")
    # Unique identifier to the index folder
    genome_dir = str(uuid.uuid4())
    log_sj = "SJ.out.tab"
    log = "Log.out"
    log_final = "Log.final.out"
    if tmpFolder is not None and os.path.isdir(tmpFolder):
        genome_dir = os.path.join(tmpFolder, genome_dir)
        log_sj = os.path.join(tmpFolder, log_sj)
    
    if not os.path.isfile(log_sj):
        error = "STAR 2 pass, error creating index. Splices file not present\n"
        logger.error(error)
        raise RuntimeError(error)
    
    os.mkdir(genome_dir)
    if not os.path.isdir(genome_dir):
        error = "STAR 2 pass, error creating temp folder\n"
        logger.error(error)
        raise RuntimeError(error)   
    
    args = ['STAR']
    args += ["--runMode", "genomeGenerate",
             "--genomeDir", genome_dir,
             "--genomeFastaFiles", genome,
             "--sjdbOverhang", 100,
             "--runThreadN", threads,
             "--sjdbFileChrStartEnd", log_sj]
    try:
        gc.collect()
        proc = subprocess.Popen([str(i) for i in args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                close_fds=True, shell=False)
        (stdout, errmsg) = proc.communicate()
    except Exception as e:
        error = "Error creating index: STAR execution failed\n"
        logger.error(error)
        logger.error(e)
        raise RuntimeError(error)   
    
    if os.path.isfile(log_sj): os.remove(log_sj)
    if os.path.isfile(log): os.remove(log)
    if os.path.isfile(log_final): os.remove(log_final)
    
    if len(errmsg) > 0:
        error = "STAR 2 pass error creating index\n"
        logger.error(error)
        logger.error(stdout)
        logger.error(errmsg)
        raise RuntimeError(error)
    
    return genome_dir

def alignReads(reverse_reads, 
               ref_map, 
               trimReverse, 
               cores,
               file_name_pattern,
               min_intron_size=20,
               max_intron_size=1000000,
               max_gap_size=1000000,
               use_splice_juntions=True,
               sam_type="BAM",
               disable_multimap=False,
               diable_softclipping=False,
               invTrimReverse=0,
               outputFolder=None):
    """
    This function will perform a sequence alignment using STAR
    mapped and unmapped reads are returned and the set of parameters
    used are described
    :param reverse_reads: file containing reverse reads in fastq format for pair end sequences
    :param ref_map: a path to the genome/transcriptome indexes
    :param trimReverse: the number of bases to trim in the reverse reasd (to not map)
    :param cores: the number of cores to use to speed up the alignment
    :param file_name_patter: indicates how the output files will be named
    :param min_intron_size: min allowed intron size when spanning splice junctions
    :param max_intron size: max allowed intron size when spanning splice junctions
    :param max_gap_size: max allowed gap between pairs
    :param use_splice_junctions: whether to use splice aware alignment or not
    :param sam_type: SAM or BAM 
    :param disable_multimap: if True no multiple alignments will be allowed
    :param diable_softclipping: it True no local alignment allowed
    :param invTrimReverse: number of bases to trim in the 5' of the read2
    :param outputFolder: if set all output files will be placed there
    """

    logger = logging.getLogger("STPipeline")
    
    # STAR has predefined output names from the files
    tmpOutputFile = "Aligned.sortedByCoord.out.bam" if sam_type.lower() == "bam" else "Aligned.out.sam"
    tmpOutputFileDiscarded = "Unmapped.out.mate1"
    outputFile = file_name_pattern + "_mapped." + sam_type.lower()
    outputFileDiscarded = file_name_pattern + "_discarded.fastq"
    log_std = "Log.std.out"
    log = "Log.out"
    log_sj = "SJ.out.tab"
    log_final = "Log.final.out"
    log_progress = "Log.progress.out"
    
    if outputFolder is not None and os.path.isdir(outputFolder):
        tmpOutputFile = os.path.join(outputFolder, tmpOutputFile)
        tmpOutputFileDiscarded = os.path.join(outputFolder, tmpOutputFileDiscarded)
        outputFile = os.path.join(outputFolder, outputFile)
        outputFileDiscarded = os.path.join(outputFolder, outputFileDiscarded)
        log_std = os.path.join(outputFolder, log_std)
        log = os.path.join(outputFolder, log)
        log_sj = os.path.join(outputFolder, log_sj)
        log_final = os.path.join(outputFolder, log_final)
        log_progress = os.path.join(outputFolder, log_progress)
    
    # Options
    # outFilterType(BySJout) this will keep only reads 
    # that contains junctions present in SJ.out.tab
    # outSamOrder(Paired) one mate after the other 
    # outSAMprimaryFlag(OneBestScore) only one alignment with the best score is primary
    # outFilterMultimapNmax = read alignments will be output only if the read maps fewer than this value
    # outFilterMismatchNmax = alignment will be output only if 
    # it has fewer mismatches than this value
    # outFilterMismatchNoverLmax = alignment will be output only if 
    # its ratio of mismatches to *mapped* length is less than this value
    # alignIntronMin minimum intron size: genomic gap is considered intron 
    # if its length>=alignIntronMin, otherwise it is considered Deletion
    # alignIntronMax maximum intron size, if 0, max intron size will be 
    # determined by (2 to the power of winBinNbits)*winAnchorDistNbins
    # alignMatesGapMax maximum gap between two mates, if 0, max intron gap will 
    # be determined by (2 to the power of winBinNbits)*winAnchorDistNbins
    # alignEndsType Local standard local alignment with soft-clipping allowed EndToEnd: 
    # force end-to-end read alignment, do not soft-clip
    # chimSegmentMin if >0 To switch on detection of chimeric (fusion) alignments
    # --outMultimapperOrder Random multimap are written in Random order
    # --outSAMmultNmax Number of multimap that we want to output 
    multi_map_number = 1 if disable_multimap else 10
    alignment_mode = "EndToEnd" if diable_softclipping else "Local"
    sjdb_overhang = 100 if use_splice_juntions else 0
    
    core_flags = ["--runThreadN", str(max(cores, 1))]
    trim_flags = ["--clip5pNbases", trimReverse, 
                  "--clip3pNbases", invTrimReverse]
    io_flags   = ["--outFilterType", "Normal", 
                  "--outSAMtype", sam_type, "SortedByCoordinate", # or Usorted (name)
                  "--alignEndsType", alignment_mode, # default Local (allows soft clipping) #EndToEnd disables soft clipping
                  "--outSAMunmapped", "None", # unmapped reads not included in main output
                  "--outSAMorder", "Paired",    
                  "--outSAMprimaryFlag", "OneBestScore", 
                  "--outFilterMultimapNmax", multi_map_number, # put to 1 to not include multiple mappings (default 10)
                  "--alignSJoverhangMin", 5, # default is 5
                  "--alignSJDBoverhangMin", 3, # default is 3
                  "--sjdbOverhang", sjdb_overhang, # 0 to not use splice junction database
                  "--outFilterMismatchNmax", 10, # large number switches it off (default 10)
                  "--outFilterMismatchNoverLmax", 0.3, # default is 0.3
                  "--alignIntronMin", min_intron_size,
                  "--alignIntronMax", max_intron_size, 
                  "--alignMatesGapMax", max_gap_size,
                  "--winBinNbits", 16,
                  "--winAnchorDistNbins", 9,
                  "--chimSegmentMin", 0,
                  "--readMatesLengthsIn", "NotEqual",
                  "--genomeLoad", "NoSharedMemory"] # Options to use share remove can be given here 

    args = ['STAR']
    args += trim_flags
    args += core_flags
    args += io_flags
    args += ["--genomeDir", ref_map,
             "--readFilesIn", reverse_reads,
             "--outFileNamePrefix", outputFolder + "/"]  # MUST ENSURE AT LEAST ONE SLASH
    args += ["--outReadsUnmapped", "Fastx"]
    
    try:
        gc.collect()
        proc = subprocess.Popen([str(i) for i in args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                close_fds=True, shell=False)
        (stdout, errmsg) = proc.communicate()
    except Exception as e:
        error = "Error mapping: STAR execution failed\n"
        logger.error(error)
        logger.error(e)
        raise RuntimeError(error)
    
    if not fileOk(tmpOutputFile) or len(errmsg) > 0:
        error = "Error mapping with STAR: output/s file/s not present: %s\n" % (tmpOutputFile)
        logger.error(error)
        logger.error(stdout)
        logger.error(errmsg)
        raise RuntimeError(error)

    # Rename output files.
    os.rename(tmpOutputFile, outputFile)
    os.rename(tmpOutputFileDiscarded, outputFileDiscarded)
        
    # Remove temp files from STAR
    if os.path.isfile(log_std): os.remove(log_std)
    if os.path.isfile(log): os.remove(log)
    # Do not remove to use it for computing splice variants
    # if os.path.isfile(log_sj): os.remove(log_sj)
    if os.path.isfile(log_progress): os.remove(log_progress)

    if not os.path.isfile(log_final):
        logger.info("Warning, log output from STAR is not present")
        logger.info(stdout)
        logger.info(errmsg)
    else:
        logger.info("Mapping stats: ")
        logger.info("Mapping stats are computed from all the pair reads present in the raw files")
        uniquely_mapped = 0
        multiple_mapped = 0
        # Parse log file from STAR to get stats
        #TODO find a cleaner way to do this
        with open(log_final, "r") as star_log:
            for line in star_log.readlines():
                if line.find("Uniquely mapped reads %") != -1 \
                or line.find("Uniquely mapped reads number") != -1 \
                or line.find("Number of reads mapped to multiple loci") != -1 \
                or line.find("% of reads mapped to multiple loci") != -1 \
                or line.find("% of reads unmapped: too short") != -1:
                    logger.info(str(line).rstrip())
                # Some duplicated code here; TODO refactor
                if line.find("Uniquely mapped reads number") != -1:
                    uniquely_mapped = int(str(line).rstrip().split()[-1])
                if line.find("Number of reads mapped to multiple loci") != -1:
                    multiple_mapped = int(str(line).rstrip().split()[-1])
            logger.info("Total mapped reads : " + str(uniquely_mapped + multiple_mapped))    
    # Remove log file       
    if os.path.isfile(log_final): os.remove(log_final)
    return outputFile, outputFileDiscarded

def barcodeDemultiplexing(reads, 
                          idFile,
                          qa_stats,
                          mismatches,
                          kmer, 
                          start_positon,
                          over_hang,
                          cores,
                          outputFolder=None, 
                          keep_discarded_files=False):
    """ 
    This functions performs a demultiplexing using Taggd. Input reads will be filtered
    out looking at their barcodes. Only the ones that contain a barcode
    that is matched in the barcodes files will be kept.
    Information about the barcode and the array coordinates will be added
    to the output file. 
    :param reads: a file in SAM/BAM/FASTQ format containing reads with barcodes
    :param idFile: a tab delimited file (BARCODE - X - Y) containing all the barcodes
    :param mismatches: the number of allowed mismatches
    :param kmer: the kmer length
    :param start_positon: the start position of the barcode
    :param over_hang: the number of bases to allow for overhang
    :param outputFolder: if set output files will be placed there
    :param keep_discarded_files: if True files with the non demultiplexed reads will be generated
    """
    
    logger = logging.getLogger("STPipeline")
    
    outputFilePrefix = 'demultiplexed'
    if outputFolder is not None and os.path.isdir(outputFolder): 
        outputFilePrefix = os.path.join(outputFolder, outputFilePrefix)

    # We know the output file from the prefix and suffix
    outputFile = outputFilePrefix + "_matched." + getExtension(reads).lower()

    # Taggd options
    #--metric (subglobal (default) , Levenshtein or Hamming)
    #--slider-increment (space between kmer searches, 0 is default = kmer length)
    #--seed
    #--no-multiprocessing
    #--overhang additional flanking bases around read barcode to allow
    #--estimate-min-edit-distance is set estimate the min edit distance among true barcodes
    #--no-offset-speedup turns off speed up, 
    #  it might yield more hits (exactly as findIndexes)
    #--homopolymer-filter if set excludes erads where barcode 
    #  contains a homolopymer of the given length (0 no filter), default 8
    args = ['taggd_demultiplex.py',
            "--max-edit-distance", mismatches,
            "--k", kmer,
            "--start-position", start_positon,
            "--homopolymer-filter", 8,
            "--subprocesses", cores,
            "--overhang", over_hang]
    
    if not keep_discarded_files:
        args.append("--no-unmatched-output")
        args.append("--no-ambiguous-output")
        args.append("--no-results-output")
        
    args += [idFile, reads, outputFilePrefix]
    gc.collect()
    try:
        proc = subprocess.Popen([str(i) for i in args], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                                close_fds=True, shell=False)
        (stdout, errmsg) = proc.communicate()
    except Exception as e:
        error = "Error demultiplexing: taggd execution failed"
        logger.error(error)
        logger.error(e)
        raise RuntimeError(error)
    
    if not fileOk(outputFile):
        error = "Error demultiplexing: output file is not present %s\n" % (outputFile)
        logger.error(error)
        logger.error(stdout)
        logger.error(errmsg)
        raise RuntimeError(error)
    
    #TODO must be a cleaner way to get the stats from the output file
    procOut = stdout.split("\n")
    logger.info("Barcode Mapping stats:")
    for line in procOut: 
        if line.find("Total reads:") != -1:
            logger.info(str(line))
        if line.find("Total reads written:") != -1:
            # Update the QA stats
            #TODO find a cleaner way to to this
            qa_stats.reads_after_demultiplexing = int(line.split()[-1])
            logger.info(str(line))
        if line.find("Perfect Matches:") != -1:
            logger.info(str(line))
        if line.find("Imperfect Matches") != -1:
            logger.info(str(line))
        if line.find("Ambiguous matches:") != -1:
            logger.info(str(line))
        if line.find("Non-unique ambiguous matches:") != -1:
            logger.info(str(line))
        if line.find("Unmatched:") != -1:
            logger.info(str(line))

    return outputFile