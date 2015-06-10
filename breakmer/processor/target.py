#! /usr/bin/python
# -*- coding: utf-8 -*-

import os
import pysam
import shutil
import breakmer.utils as utils
import breakmer.processor.bam_handler as bam_handler
import breakmer.assembly.assembler as assembly

__author__ = "Ryan Abo"
__copyright__ = "Copyright 2015, Ryan Abo"
__email__ = "ryanabo@gmail.com"
__license__ = "MIT"


def load_kmers(fns, kmers):
    """
    """

    if not fns:
        return kmers

    fns = fns.split(',')
    for fn in fns:
        for line in open(fn, 'rU'):
            line = line.strip()
            mer, count = line.split()
            if mer not in kmers:
                kmers[mer] = 0
            kmers[mer] += int(count)
    return kmers


class Variation:
    """
    """

    def __init__(self):
        self.var_reads = {}
        self.sv_reads = None
        self.cleaned_read_recs = None
        self.kmer_clusters = []
        self.kmers = {}
        self.results = []
        self.svs = {}

    def setup_cleaned_reads(self, type):
        """
        """

        if not self.cleaned_read_recs:
            self.cleaned_read_recs = {}
        self.cleaned_read_recs[type] = None

    def clear_sv_reads(self, type):
        """
        """
        self.var_reads[type].clear_sv_reads()

    def clear_cleaned_reads(self):
        """
        """
        self.cleaned_read_recs = None

    def continue_analysis_check(self, type):
        """
        """

        check = True
        if len(self.cleaned_read_recs[type]) == 0:
            check = False
        return check

    def get_sv_reads(self, type):
        """
        """
        return self.var_reads[type].sv

    def add_result(self, result):
        """
        """
        self.results.append(result)


class TargetManager:
    """TargetManager class handles all the high level information relating to a target.
    The analysis is peformed at the target level, so this class contains all the information
    necessary to perform an independent analysis.
    Attributes:
        params:        ParamManager instance
        logging_name:  Module name for logging file purposes.
        name:          Target name specified in the input bed file.
        chrom:         Chromosome ID as specified in the input bed file.
        start:         Genomic position for the target region (minimum value
                       among all intervals).
        end:           Genomic position for the target region (maximum value among
                       all intervals).
        paths:         Dictionary containing the analysis paths for this target.
        files:         Dicionary containing paths to file names needed for analysis.
        read_len:      Integer of the length of a single read.
        repeat_mask:   A list of tuples, each defining a repeat region location within the target region.
        variation:     Instance of Variation class that stores data for variants identified within
                       the target.
        regionBuffer: Integer for base pairs to add or subtract from the target region end and
                       start locations.
    """

    def __init__(self, name, params):
        self.params = params
        self.loggingName = 'breakmer.processor.target'
        self.name = name
        self.chrom = None
        self.start = None
        self.end = None
        self.paths = {}
        self.files = {}
        self.read_len = 0
        self.repeat_mask = None
        self.variation = Variation()
        self.regionBuffer = 200
        self.setup()

    def setup(self):
        """Setup the TargetManager object with the input params.
        Args:
            None
        Returns:
            None
        """

        intervals = self.params.targets[self.name]
        for chrom, int(start), int(end) in intervals:
            if not self.chrom:
                self.chrom = chrom
            if not self.start:
                self.start = start
            if not self.end:
                self.end = end
            if start < self.start:
                self.start = start
            if end > self.end:
                self.end = end

        # Create the proper paths for the target analysis.
        self.add_path('ref_data', os.path.join(self.params.paths['ref_data'], self.name))
        if not self.params.get_param('preset_ref_data'):
            self.add_path('base', os.path.join(self.params.paths['targets'], self.name))
            self.add_path('data', os.path.join(self.paths['base'], 'data'))
            self.add_path('contigs', os.path.join(self.paths['base'], 'contigs'))
            self.add_path('kmers', os.path.join(self.paths['base'], 'kmers'))
            self.add_path('output', os.path.join(self.params.paths['output'], self.name))

        self.files['target_ref_fn'] = [os.path.join(self.paths['ref_data'], self.name + '_forward_refseq.fa'), os.path.join(self.paths['ref_data'], self.name + '_reverse_refseq.fa')]

        ref_fa_marker_f = open(os.path.join(self.paths['ref_data'], '.reference_fasta'), 'w')
        ref_fa_marker_f.write(self.params.opts['reference_fasta'])
        ref_fa_marker_f.close()

        """DEPRECATED
        if 'alternate_reference_fastas' in self.params.opts:
            alt_ref_fa_marker_f = open(os.path.join(self.paths['ref_data'], '.alternate_reference_fastas'), 'w')
            self.files['target_altref_fn'] = []
            alt_iter = 1
            for altref in self.params.opts['alternate_reference_fastas']:
                self.files['target_altref_fn'].append([os.path.join(self.paths['ref_data'], self.name + '_forward_altrefseq_' + str(alt_iter) + '.fa'), os.path.join(self.paths['ref_data'], self.name + '_reverse_altrefseq_' + str(alt_iter) + '.fa')])
                alt_iter += 1
                alt_ref_fa_marker_f.write(altref + '\n')
            alt_ref_fa_marker_f.close()
        """

        self.files['ref_kmer_dump_fn'] = [os.path.join(self.paths['ref_data'], self.name + '_forward_refseq.fa_dump'), os.path.join(self.paths['ref_data'], self.name + '_reverse_refseq.fa_dump')]

    def add_path(self, key, path):
        utils.log(self.loggingName, 'info', 'Creating %s %s path (%s)' % (self.name, key, path))
        self.paths[key] = path
        if not os.path.exists(self.paths[key]):
            os.makedirs(self.paths[key])

    def set_ref_data(self):
        # Write rmask bed file if needed.
        if not self.params.opts['keep_repeat_regions'] and 'repeat_mask_file' in self.params.opts:
            utils.log(self.loggingName, 'info', 'Extracting repeat mask regions for target gene %s.' % self.name)
            self.repeat_mask = utils.setup_rmask(self.get_values(), self.paths['ref_data'], self.params.opts['repeat_mask_file'])

        # Write reference fasta file if needed.
        for i in range(len(self.files['target_ref_fn'])):
            fn = self.files['target_ref_fn'][i]
            direction = "forward"
            if fn.find("forward") == -1:
                direction = "reverse"
            utils.log(self.loggingName, 'info', 'Extracting refseq sequence and writing %s' % fn)
            utils.extract_refseq_fa(self.get_values(), self.paths['ref_data'], self.params.opts['reference_fasta'], direction, fn)

        """DEPRECATED
        # Write alternate reference files.
        if 'target_altref_fn' in self.files:
            if not utils.create_ref_test_fa(os.path.join(self.paths['ref_data'], self.name + "_forward_refseq.fa"), os.path.join(self.paths['ref_data'], self.name + "_start_end_refseq.fa")):
                return

            altref_fns = []
            alt_iter = 1
            for i in range(len(self.files['target_altref_fn'])):
                for j in range(len(self.files['target_altref_fn'][i])):
                    fn = self.files['target_altref_fn'][i][j]
                    marker_fn = utils.get_marker_fn(fn)
                    if not os.path.isfile(marker_fn):
                        altref_fns.append((self.params.opts['alternate_reference_fastas'][i], fn, alt_iter))
                alt_iter += 1

            if len(altref_fns) > 0:
                utils.create_ref_test_fa(os.path.join(self.paths['ref_data'], self.name + "_forward_refseq.fa"), os.path.join(self.paths['ref_data'], self.name + "_start_end_refseq.fa"))
                for i in range(len(altref_fns)):
                    alt_gene_coords = utils.get_altref_genecoords(self.params.opts['blat'], altref_fns[i][0], os.path.join(self.paths['ref_data'], self.name + "_start_end_refseq.fa"), self.chrom, os.path.join(self.paths['ref_data'], self.name + '_altref_blat_' + str(altref_fns[i][2]) + '.psl'))
                    if not alt_gene_coords[2]:
                        utils.log(self.loggingName, 'info', 'No sequence for target gene in %s, no reference kmers extracted.' % altref_fns[i][0])
                        continue
                    gene_vals = (self.chrom, alt_gene_coords[0][1], alt_gene_coords[1][1], self.name, self.get_target_intervals())
                    fn = altref_fns[i][1]
                    direction = "forward"
                    if fn.find("forward") == -1:
                        direction = "reverse"
                    utils.log(self.loggingName, 'info', 'Extracting alternate refseq sequence and writing %s' % fn)
                    utils.extract_refseq_fa(gene_vals, self.paths['ref_data'], altref_fns[i][0], direction, fn)
        """

    def find_sv_reads(self):
        """Entry function to extract sequence reads from sample or normal bam file.
        It extracts and cleans the sample reads from the target region that may
        be used to build a variant contig.
        Args:
            None
        Returns:
            check: A boolean to determine if the analysis should continue. It is
                   False when there are no reads extracted or left after cleaning
                   and True when there are.
        """

        # Extract reads from tumor sample.
        self.extract_bam_reads('sv')
        if self.params.get_param('normal_bam_file'):
            # Extract reads from normal sample, if input.
            self.extract_bam_reads('norm')
            self.clean_reads('norm')

        check = True
        # Determine if there are any SV reads in the target.
        if not self.clean_reads('sv'):
            # Clean up output directory if nothing to analyze
            self.rm_output_dir()
            check = False
        return check

    def clean_reads(self, type):
        """Trim adapter sequences from the extracted reads, format and organize
        the cleaned reads into new files.
        Cutadapt is run to trim the adapter sequences from the sequence reads to
        remove any 'noise' that bogs down the assembly process or analysis. The
        cleaned reads output from cutadapt are then re-processed to determine
        if the soft-clipped sequences were trimmed off or not to further filter
        out reads. The soft-clipped sequences that remain are stored and a new
        fastq file is written.
        Args:
            type (str): A string indicating a tumor ('sv') or normal ('norm') sample being processed.
        Return:
            check (boolean): A boolean to indicate whether the are any reads left after
                             cleaning is complete.
        """

        cutadapt = self.params.get_param('cutadapt')
        cutadaptConfigFn = self.params.get_param('cutadapt_config_file')
        utils.log(self.loggingName, 'info', 'Cleaning reads using %s with configuration file %s' % (cutadapt, cutadaptConfigFn))
        self.files['%s_cleaned_fq' % type] = os.path.join(self.paths['data'], self.name + '_%s_reads_cleaned.fastq' % type)
        utils.log(self.loggingName, 'info', 'Writing clean reads to %s' % self.files['%s_cleaned_fq' % type])
        output, errors = utils.run_cutadapt(cutadapt, cutadaptConfigFn, self.files['%s_fq' % type], self.files['%s_cleaned_fq' % type], self.loggingName)

        self.variation.setup_cleaned_reads(type)
        self.files['%s_cleaned_fq' % type], self.variation.cleaned_read_recs[type], self.read_len = utils.get_fastq_reads(self.files['%s_cleaned_fq' % type], self.get_sv_reads(type))
        self.clear_sv_reads(type)
        check = self.variation.continue_analysis_check(type)
        utils.log(self.loggingName, 'info', 'Clean reads exist %s' % check)
        return check

    def extract_bam_reads(self, sampleType):
        """
        Args:
            sampleType: A string indicating a tumor ('sv') or normal ('norm') sample being processed.
        Return:
            None
        """

        self.setup_read_extraction_files(type)
        bamType = 'sample'
        if sampleType == 'norm':
            bamType = 'normal'

        utils.log(self.loggingName, 'info', 'Extracting bam reads from %s to %s' % (self.params.opts['%s_bam_file' % bamType], self.files['sv_fq']))

        regionStartCoord = self.start - self.regionBuffer
        regionEndCoord = self.end + self.regionBuffer
        bamFile = self.params.opts['%s_bam_file' % bamType]
        self.variation.var_reads[type] = bam_handler.get_variant_reads(bamFile, self.chrom, regionStartCoord, regionEndCoord)
        self.variation.var_reads[type].check_clippings(self.params.get_kmer_size(), self.start, self.end)

        svBam = None
        if sampleType == 'sv':
            svBam = pysam.Samfile(self.files['sv_bam'], 'wb', template=self.variation.var_reads[type].bam)

        readsFq = open(self.files['%s_fq' % type], 'w')
        scFa = open(self.files['%s_sc_unmapped_fa' % type], 'w')

        self.variation.var_reads[type].write_seqs(scFa, readsFq, svBam, self.params.get_kmer_size())

        readsFq.close()
        scFa.close()

        if sampleType == 'sv':
            svBam.close()
            utils.log(self.loggingName, 'info', 'Sorting bam file %s to %s' % (self.files['sv_bam'], self.files['sv_bam_sorted']))
            pysam.sort(self.files['sv_bam'], self.files['sv_bam_sorted'].replace('.bam', ''))
            utils.log(self.loggingName, 'info', 'Indexing sorted bam file %s' % self.files['sv_bam_sorted'])
            pysam.index(self.files['sv_bam_sorted'])

    def setup_read_extraction_files(self, sampleType):
        """Create file names to store the extracted reads.
        This creates four files (for tumor samples):
        1. fastq with extracted reads
        2. fasta file with softclipped sequences
        3. bam file with extracted reads
        4. sorted bam file with extracted reads.
        Args:
            sampleType (str): The type of input data - tumor or normal
        Returns:
            None
        """

        # Store extracted reads in <data_path>/<target_name>_<type>_reads.fastq
        self.files['%s_fq' % sampleType] = os.path.join(self.paths['data'], self.name + '_%s_reads.fastq' % sampleType)
        # Store softclipped sequences in a fasta file <data_path>/<target_name>_<type>_sc_seqs.fa
        self.files['%s_sc_unmapped_fa' % type] = os.path.join(self.paths['data'], self.name + '_%s_sc_seqs.fa' % sampleType)

        if sampleType == 'sv':
            # Store variant reads in bam formatted file <data_path>/<target_name>_sv_reads.bam
            self.files['sv_bam'] = os.path.join(self.paths['data'], self.name + '_sv_reads.bam')
            # Store variant reads in sorted bam file
            self.files['sv_bam_sorted'] = os.path.join(self.paths['data'], self.name + '_sv_reads.sorted.bam')

    def compare_kmers(self):
        """Move this to Variation class
        """

        kmer_dict = self.variation.kmers
        jellyfish = self.params.get_param('jellyfish')
        kmer_size = self.params.get_kmer_size()

        # Set the reference sequence kmers.
        kmer_dict['ref'] = {}
        for i in range(len(self.files['target_ref_fn'])):
            utils.log(self.loggingName, 'info', 'Indexing kmers for reference sequence %s' % self.files['target_ref_fn'][i])
            kmer_dict['ref'] = load_kmers(utils.run_jellyfish(self.files['target_ref_fn'][i], jellyfish, kmer_size), kmer_dict['ref'])

        if 'target_altref_fn' in self.files:
            for i in range(len(self.files['target_altref_fn'])):
                for j in range(len(self.files['target_altref_fn'][i])):
                    utils.log(self.loggingName, 'info', 'Indexing kmers for reference sequence %s' % self.files['target_altref_fn'][i])
                    kmer_dict['ref'] = load_kmers(utils.run_jellyfish(self.files['target_altref_fn'][i][j], jellyfish, kmer_size), kmer_dict['ref'])

        # Set sample kmers.
        utils.log(self.loggingName, 'info', 'Indexing kmers for sample sequence %s' % self.files['sv_cleaned_fq'])
        kmer_dict['case'] = {}
        kmer_dict['case'] = load_kmers(utils.run_jellyfish(self.files['sv_cleaned_fq'], jellyfish, kmer_size), kmer_dict['case'])
        kmer_dict['case_sc'] = {}
        kmer_dict['case_sc'] = load_kmers(utils.run_jellyfish(self.files['sv_sc_unmapped_fa'], jellyfish, kmer_size), kmer_dict['case_sc'])
        sc_mers = set(kmer_dict['case'].keys()) & set(kmer_dict['case_sc'].keys())
        sample_only_mers = list(sc_mers.difference(set(kmer_dict['ref'].keys())))
        # Add normal sample kmers if available.
        if self.params.get_param('normal_bam_file'):
            norm_kmers = {}
            norm_kmers = load_kmers(utils.run_jellyfish(self.files['norm_cleaned_fq'], jellyfish, kmer_size), norm_kmers)
            sample_only_mers = set(sample_only_mers).difference(set(norm_kmers.keys()))

        sample_only_mers = list(sample_only_mers)

        # Write case only kmers out to file.
        self.files['sample_kmers'] = os.path.join(self.paths['kmers'], self.name + "_sample_kmers.out")
        sample_kmer_fout = open(self.files['sample_kmers'], 'w')
        kmer_counter = 1
        kmer_dict['case_only'] = {}
        for mer in sample_only_mers:
            sample_kmer_fout.write("\t".join([str(x) for x in [mer, str(kmer_dict['case'][mer])]])+"\n")
            kmer_dict['case_only'][mer] = kmer_dict['case'][mer]
        sample_kmer_fout.close()

        # Clean out data structures.
        kmer_dict['ref'] = {}
        kmer_dict['case'] = {}
        kmer_dict['case_sc'] = {}

        utils.log(self.loggingName, 'info', 'Writing %d sample-only kmers to file %s' % (len(kmer_dict['case_only']), self.files['sample_kmers']))
        self.files['kmer_clusters'] = os.path.join(self.paths['kmers'], self.name + "_sample_kmers_merged.out")
        utils.log(self.loggingName, 'info', 'Writing kmer clusters to file %s' % self.files['kmer_clusters'])

        kmer_dict['clusters'] = assembly.init_assembly(kmer_dict['case_only'], self.variation.cleaned_read_recs['sv'], kmer_size, self.params.get_sr_thresh('min'), self.read_len)
        self.clear_cleaned_reads()
        kmer_dict['case_only'] = {}

    def resolve_sv(self):
        """
        """

        iter = 1
        contigs = self.variation.kmers['clusters']
        utils.log(self.loggingName, 'info', 'Resolving structural variants from %d kmer clusters' % len(contigs))

        for contig in contigs:
            utils.log(self.loggingName, 'info', 'Assessing contig %s' % contig.seq())
            contig_id = 'contig' + str(iter)
            contig.set_meta_information(contig_id, self.params, self.get_values(), self.paths['contigs'], self.files['kmer_clusters'])

            contig.query_ref()
            contig.make_calls()

            if contig.has_result():
                contig.write_result()
                contig.write_bam()
                self.add_result(result)
            else:
                utils.log(self.loggingName, 'info', '%s has no structural variant result.' % contig.id)
            iter += 1

    def complete_analysis(self):
        """ """
        if len(self.results) > 0:
            self.write_results()
        else:
            self.rm_output_dir()

    def get_target_intervals(self):
        """Return the list of tuples defining intervals for this target"""

        return self.params.targets[self.name]

    def get_values(self):
        """Return the defined features of this target"""

        return(self.chrom, self.start, self.end, self.name, self.get_target_intervals())

    def get_sv_reads(self, type):
        """ """
        return self.variation.get_sv_reads(type)

    def clear_sv_reads(self, type):
        """ """
        self.variation.clear_sv_reads(type)

    def clear_cleaned_reads(self):
        """ """
        self.variation.clear_cleaned_reads()

    def rm_output_dir(self):
        shutil.rmtree(self.paths['output'])

    def add_result(self, result):
        self.variation.add_result(result)