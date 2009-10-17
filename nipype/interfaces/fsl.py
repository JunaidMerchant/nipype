"""The fsl module provides classes for interfacing with the `FSL
<http://www.fmrib.ox.ac.uk/fsl/index.html>`_ command line tools.  This
was written to work with FSL version 4.1.4.

Currently these tools are supported:

* BET v2.1: brain extraction
* FAST v4.1: segmentation and bias correction
* FLIRT v5.5: linear registration
* MCFLIRT: motion correction
* FNIRT v1.0: non-linear warp

Examples
--------
See the docstrings of the individual classes for examples.

"""

import os
import subprocess
from copy import deepcopy
from glob import glob
from nipype.utils.filemanip import fname_presuffix
from nipype.interfaces.base import (Bunch, CommandLine, 
                                    load_template, InterfaceResult)
from nipype.utils import setattr_on_read
from nipype.utils.docparse import get_doc
from nipype.utils.misc import container_to_string, is_container
from nipype.utils.filemanip import fname_presuffix
import warnings
warn = warnings.warn

warnings.filterwarnings('always', category=UserWarning)

class FSLInfo():
    '''A class to encapsulate stuff we'll need throughout the 
    
    This should probably be a singleton class? or do we want to make it
    possible to wrap a few versions of FSL? In any case, currently we
    instantiate an instance here called fsl_info
    
    I'm also not sure this is the best ordering for the various attributes and
    methods. Please feel free to reorder.'''
    @property
    def version(self):
        """Check for fsl version on system

        Parameters
        ----------
        None

        Returns
        -------
        version : string
           Version number as string or None if FSL not found

        """
        # find which fsl being used....and get version from
        # /path/to/fsl/etc/fslversion
        clout = CommandLine('which fsl').run()

        if clout.runtime.returncode is not 0:
            return None

        out = clout.runtime.stdout
        basedir = os.path.split(os.path.split(out)[0])[0]
        clout = CommandLine('cat %s/etc/fslversion'%(basedir)).run()
        out = clout.runtime.stdout
        return out.strip('\n')

    ftypes = {'NIFTI':'nii',
              'ANALYZE':'hdr',
              'NIFTI_PAIR':'hdr',
              'ANALYZE_GZ':'hdr.gz',
              'NIFTI_GZ':'nii.gz',
              'NIFTI_PAIR_GZ':'hdr.gz',
              None: 'env variable FSLOUTPUTTYPE not set'}

    def outputtype(self, ftype=None):
        """Check and or set the global FSL output file type FSLOUTPUTTYPE
        
        Parameters
        ----------
        ftype :  string
            Represents the file type to set based on string of valid FSL
            file types ftype == None to get current setting/ options

        Returns
        -------
        fsl_ftype : string
            Represents the current environment setting of FSLOUTPUTTYPE
        ext : string
            The extension associated with the FSLOUTPUTTYPE

        """
        if ftype is None:
            # get environment setting
            fsl_ftype = os.getenv('FSLOUTPUTTYPE')

        else:
            # set environment setting - updating environ automatically calls
            # putenv. Note, docs claim putenv may cause memory leaks on OSX and
            # FreeBSD :\ I see no workarounds -DJC
            # os.putenv('FSLOUTPUTTYPE',fsl_ftype)
            if ftype in self.ftypes.keys():
                os.environ['FSLOUTPUTTYPE'] = ftype 
            else:
                pass
                # raise an exception? warning?
            fsl_ftype = ftype

        # This is inappropriate in a utility function
        # print 'FSLOUTPUTTYPE = %s (\"%s\")' % (fsl_ftype, 
        #                                        self.ftypes[fsl_ftype] )
        return fsl_ftype, self.ftypes[fsl_ftype]

    def standard_image(self, img_name):
        '''Grab an image from the standard location. 
        
        Could be made more fancy to allow for more relocatability'''
        fsldir = os.environ['FSLDIR']
        return os.path.join(fsldir, 'data/standard', img_name)

    def glob(self, fname):
        '''Check if, given a filename, FSL actually produced it. 
        
        The maing thing we're doing here is adding an appropriate extension
        while globbing. Note that it returns a single string, not a list
        (different from glob.glob)'''
        # Could be made more faster / less clunky, but don't care until the API
        # is sorted out
        _, ext = self.outputtype()
        # While this function is a little globby, it may not be the best name.
        # Certainly, glob here is more expensive than necessary (could just use
        # os.exists
        files = glob(fname) or glob(fname + '.' + ext)

        try:
            return files[0]
        except IndexError:
            return None

    def gen_fname(self, basename, fname=None, cwd=None, suffix='_fsl', 
                  check=False):
        '''Define a generic mapping for a single outfile
        
        The filename is potentially autogenerated by suffixing inputs.infile
        
        Parameters
        ----------
        basename : string (required)
            filename to base the new filename on
        fname : string
            if not None, just use this fname
        cwd : string
            prefix paths with cwd, otherwise os.getcwd()
        suffix : string
            default suffix
        check : bool
            check if file exists, adding appropriate extension, raise exception
            if it doesn't
        '''
        if cwd is None:
            cwd = os.getcwd()

        if fname is None:
            fname = fname_presuffix(basename, suffix=suffix, newpath=cwd)

        if check:
            fname = fsl_info.glob(fname)
            if fname is None:
                raise IOError('file %s not generated by %s' % (file, self.cmd))

        return fname
    
fsl_info = FSLInfo()

# Legacy to support old code. Should be deleted soon. before 0.2?
def fslversion():
    warn(DeprecationWarning('fslversion should be accessed via fsl_info'))
    return(fsl_info.version)

def fsloutputtype(ftype=None):
    warn(DeprecationWarning('fsloutputtype should be accessed via fsl_info'))
    return fsl_info.outputtype(ftype)

class FSLCommand(CommandLine):
    '''General support for FSL commands'''
    opt_map = {}
    
    @property
    def cmdline(self):
        """validates fsl options and generates command line argument"""
        allargs = self._parse_inputs()
        allargs.insert(0, self.cmd)
        return ' '.join(allargs)

    def run(self, cwd=None):
        """Execute the command.
        
        Returns
        -------
        results : Bunch
            A `Bunch` object with a copy of self in `interface`

        """
        results = self._runner(cwd=cwd)
        if results.runtime.returncode == 0:
            results.outputs = self.aggregate_outputs()

        return results        

    def _parse_inputs(self, skip=()):
        """Parse all inputs and format options using the opt_map format string.

        Any inputs that are assigned (that are not None) are formatted
        to be added to the command line.

        Parameters
        ----------
        skip : tuple or list
            Inputs to skip in the parsing.  This is for inputs that
            require special handling, for example input files that
            often must be at the end of the command line.  Inputs that
            require special handling like this should be handled in a
            _parse_inputs method in the subclass.
        
        Returns
        -------
        allargs : list
            A list of all inputs formatted for the command line.

        """
        allargs = []
        inputs = sorted((k, v) for k, v in self.inputs.iteritems() 
                            if v is not None and k not in skip)
        for opt, value in inputs:
            if opt == 'args':
                # XXX Where is this used?  Is self.inputs.args still
                # used?  Or is it leftover from the original design of
                # base.CommandLine?
                allargs.extend(value)
                continue
            try:
                argstr = self.opt_map[opt]
                if argstr.find('%') == -1:
                    # Boolean options have no format string.  Just
                    # append options if True.
                    if value is True:
                        allargs.append(argstr)
                    elif value is not False:
                        raise TypeError('Boolean option %s set to %s' % 
                                         (opt, str(value)) )
                # XXX This is where we could change the logic to like Fnirt
                elif type(value) == list:
                    allargs.append(argstr % tuple(value))
                else:
                    # Append options using format string.
                    allargs.append(argstr % value)
            except TypeError, err:
                msg = 'Error when parsing option %s in class %s.\n%s' % \
                    (opt, self.__class__.__name__, err.message)
                warn(msg)
            except KeyError:                   
                warn("Option '%s' is not supported!" % (opt))
        
        return allargs

    def _populate_inputs(self):
        self.inputs = Bunch((k,None) for k in self.opt_map.keys())

    def aggregate_outputs(self):
        raise NotImplementedError(
                'Subclasses of FSLCommand must implement aggregate_outputs')


class Bet(FSLCommand):
    """Use FSL BET command for skull stripping.

    For complete details, see the `BET Documentation. 
    <http://www.fmrib.ox.ac.uk/fsl/bet2/index.html>`_

    To print out the command line help, use:
        fsl.Bet().inputs_help()

    Examples
    --------
    Initialize Bet with no options, assigning them when calling run:

    >>> from nipype.interfaces import fsl
    >>> btr = fsl.Bet()
    >>> res = btr.run('infile', 'outfile', frac=0.5)

    Assign options through the ``inputs`` attribute:

    >>> btr = fsl.Bet()
    >>> btr.inputs.infile = 'foo.nii'
    >>> btr.inputs.outfile = 'bar.nii'
    >>> btr.inputs.frac = 0.7
    >>> res = btr.run()

    Specify options when creating a Bet instance:

    >>> btr = fsl.Bet(infile='infile', outfile='outfile', frac=0.5)
    >>> res = btr.run()

    Loop over many inputs (Note: the snippet below would overwrite the
    outfile each time):

    >>> btr = fsl.Bet(infile='infile', outfile='outfile')
    >>> fracvals = [0.3, 0.4, 0.5]
    >>> for val in fracvals:
    ...     res = btr.run(frac=val)

    """

    @property
    def cmd(self):
        """sets base command, immutable"""
        return 'bet'

    def inputs_help(self):
        """Print command line documentation for BET."""
        print get_doc(self.cmd, self.opt_map)

    opt_map = {
        'outline':            '-o',
        'mask':               '-m',
        'skull':              '-s',
        'nooutput':           '-n',
        'frac':               '-f %.2f',
        'vertical_gradient':  '-g %.2f',
        'radius':             '-r %d', # in mm
        'center':             '-c %d %d %d', # in voxels
        'threshold':          '-t',
        'mesh':               '-e',
        'verbose':            '-v',
        'functional':         '-F',
        'flags':              '%s',
        'infile':             None,
        'outfile':            None,
        }
    # Currently we don't support -R, -S, -B, -Z,-A or -A2

    def _parse_inputs(self):
        """validate fsl bet options"""
        allargs = super(Bet, self)._parse_inputs(skip=('infile', 'outfile'))

        outfile = self.inputs.outfile

        if self.inputs.infile:
            allargs.insert(0, self.inputs.infile)

            # XXX This default stuff gets done in two places :\
            if outfile is None:
                outfile = fname_presuffix(self.inputs.infile, suffix='_bet', 
                                          newpath='.')
        if outfile is not None:
            allargs.insert(1, outfile)

        return allargs
        
    def run(self, cwd=None, infile=None, outfile=None, **inputs):
        """Execute the command.

        Parameters
        ----------
        infile : string
            Filename to be skull stripped.
        outfile : string, optional
            Filename to save output to. If not specified, the ``infile``
            filename will be used with a "_bet" suffix.
        inputs : dict
            Additional ``inputs`` assignments can be passed in.  See
            Examples section.

        Returns
        -------
        results : Bunch
            A `Bunch` object with a copy of self in `interface`
            runtime : Bunch containing stdout, stderr, returncode, commandline

        Examples
        --------
        To pass command line arguments to ``bet`` that are not part of
        the ``inputs`` attribute, pass them in with the ``flags``
        input.

        >>> from nipype.interfaces import fsl
        >>> btr = fsl.Bet(infile='foo.nii', outfile='bar.nii', flags='-v')
        >>> btr.cmdline
        'bet foo.nii bar.nii -v'

        """
        if infile:
            self.inputs.infile = infile
        if self.inputs.infile is None:
            raise ValueError('Bet requires an input file')
        if outfile:
            self.inputs.outfile = outfile
        if cwd is None:
            cwd = os.getcwd()

        self.inputs.update(**inputs)
        
        results = self._runner(cwd=cwd)
        if results.runtime.returncode == 0:
            results.outputs = self.aggregate_outputs(cwd)

        return results        


    def outputs_help(self):
        """
        Parameters
        ----------
        (all default to None and are unset)
        
        outfile : /path/to/outfile
            path/name of skullstripped file
        maskfile : Bool
            binary brain mask if generated
        """
        print self.outputs_help.__doc__

    def aggregate_outputs(self, cwd=None):
        """Create a Bunch which contains all possible files generated
        by running the interface.  Some files are always generated, others
        depending on which ``inputs`` options are set.

        Parameters
        ----------
        cwd : /path/to/outfiles
            Where do we look for the outputs? None means look in same location
            as infile
        Returns
        -------
        outputs : Bunch object
            Bunch object containing all possible files generated by
            interface object.

            If None, file was not generated
            Else, contains path, filename of generated outputfile

        """
        outputs = Bunch()
        
        outputs.outfile = fsl_info.gen_fname(self.inputs.infile,
                                self.inputs.outfile, cwd=cwd, suffix='_bet', 
                                check=True)

        if self.inputs.mask:
            outputs.maskfile = fsl_info.gen_fname(outputs.outfile, cwd=cwd, 
                                                  suffix='_mask', check=True)
        else:
            outputs.maskfile = None

        return outputs


class Fast(FSLCommand):
    """Use FSL FAST for segmenting and bias correction.

    For complete details, see the `FAST Documentation. 
    <http://www.fmrib.ox.ac.uk/fsl/fast4/index.html>`_

    To print out the command line help, use:
        fsl.Fast().inputs_help()

    Examples
    --------
    >>> from nipype.interfaces import fsl
    >>> faster = fsl.Fast(out_basename='myfasted')
    >>> fasted = faster.run(['file1','file2'])

    >>> faster = fsl.Fast(infiles=['filea','fileb'], out_basename='myfasted')
    >>> fasted = faster.run()

    """

    @property
    def cmd(self):
        """sets base command, not editable"""
        return 'fast'

    opt_map = {'number_classes':       '-n %d',
            'bias_iters':           '-I %d',
            'bias_lowpass':         '-l %d', # in mm
            'img_type':             '-t %d',
            'init_seg_smooth':      '-f %.3f',
            'segments':             '-g',
            'init_transform':       '-a %s',
            # This option is not really documented on the Fast web page:
            # http://www.fmrib.ox.ac.uk/fsl/fast4/index.html#fastcomm
            # I'm not sure if there are supposed to be exactly 3 args or what
            'other_priors':         '-A %s %s %s',
            'nopve':                '--nopve',
            'output_biasfield':     '-b',
            'output_biascorrected': '-B',
            'nobias':               '-N',
            'n_inputimages':        '-S %d',
            'out_basename':         '-o %s',
            'use_priors':           '-P', # must also set -a!
            'segment_iters':        '-W %d',
            'mixel_smooth':         '-R %.2f',
            'iters_afterbias':      '-O %d',
            'hyper':                '-H %.2f',
            'verbose':              '-v',
            'manualseg':            '-s %s',
            'probability_maps':     '-p',
            'infiles':               None,
            }

    def inputs_help(self):
        """Print command line documentation for FAST."""
        print get_doc(self.cmd, self.opt_map)


    def run(self, infiles=None, **inputs):
        """Execute the FSL fast command.

        Parameters
        ----------
        infiles : string or list of strings
            File(s) to be segmented or bias corrected
        inputs : dict
            Additional ``inputs`` assignments can be passed in.

        Returns
        -------
        results : Bunch
            A `Bunch` object with a copy of self in `interface`
            runtime : Bunch containing stdout, stderr, returncode, commandline

        """

        if infiles:
            self.inputs.infiles = infiles
        if not self.inputs.infiles:
            raise AttributeError('Fast requires input file(s)')
        self.inputs.update(**inputs)

        results = self._runner()
        if results.runtime.returncode == 0:
            results.outputs = self.aggregate_outputs()
            # NOT checking if files exist
            # Once implemented: results.outputs = self.aggregate_outputs()

        return results        

    def _parse_inputs(self):
        '''Call our super-method, then add our input files'''
        # Could do other checking above and beyond regular _parse_inputs here
        allargs = super(Fast, self)._parse_inputs(skip=('infiles'))
        if self.inputs.infiles:
            allargs.append(container_to_string(self.inputs.infiles))
        return allargs

    def aggregate_outputs(self):
        """Create a Bunch which contains all possible files generated
        by running the interface.  Some files are always generated, others
        depending on which ``inputs`` options are set.

        Returns
        -------
        outputs : Bunch object
            Each attribute in ``outputs`` is a list.  There will be
            one set of ``outputs`` for each file specified in
            ``infiles``.  ``outputs`` will contain the following
            files:

            mixeltype : list
                filename(s)
            partial_volume_map : list
                filenames, one for each input
            partial_volume_files : list
                filenames, one for each class, for each input
            tissue_class_map : list
                filename(s), each tissue has unique int value
            tissue_class_files : list
                filenames, one for each class, for each input
            restored_image : list 
                filename(s) bias corrected image(s)
            bias_field : list
                filename(s) 
            probability_maps : list
                filenames, one for each class, for each input

        Notes
        -----
        For each item in Bunch:
        If [] empty list, optional file was not generated
        Else, list contains path,filename of generated outputfile(s)

        Raises
        ------
        IOError
            If any expected output file is not found.

        """
        _, ext = fsl_info.outputtype()
        outputs = Bunch(mixeltype = [],
                seg = [],
                partial_volume_map=[],
                partial_volume_files=[],
                tissue_class_map=[],
                tissue_class_files=[],
                bias_corrected=[],
                bias_field=[],
                prob_maps=[])

        if not is_container(self.inputs.infiles):
            infiles = [self.inputs.infiles]
        else:
            infiles = self.inputs.infiles
        for item in infiles:
            # get basename (correct fsloutpputytpe extension)
            if self.inputs.out_basename:
                pth, nme = os.path.split(item)
                jnk, ext = os.path.splitext(nme)
                item = pth + self.inputs.out_basename + '.%s' % (envext)
            else:
                nme, ext = os.path.splitext(item)
                item = nme + '.%s' % (envext)
            # get number of tissue classes
            if not self.inputs.number_classes:
                nclasses = 3
            else:
                nclasses = self.inputs.number_classes

            # always seg, (plus mutiple?)
            outputs.seg.append(fname_presuffix(item, suffix='_seg'))
            if self.inputs.segments:
                for i in range(nclasses):
                    outputs.seg.append(fname_presuffix(item, 
                        suffix='_seg_%d'%(i)))
                    # always pve,mixeltype unless nopve = True
            if not self.inputs.nopve:
                fname = fname_presuffix(item, suffix='_pveseg')
                outputs.partial_volume_map.append(fname)
                fname = fname_presuffix(item, suffix='_mixeltype')
                outputs.mixeltype.append(fname)

                for i in range(nclasses):
                    fname = fname_presuffix(item, suffix='_pve_%d'%(i))
                    outputs.partial_volume_files.append(fname)

            # biasfield ? 
            if self.inputs.output_biasfield:
                outputs.bias_field.append(fname_presuffix(item, suffix='_bias'))

            # restored image (bias corrected)?
            if self.inputs.output_biascorrected:
                fname = fname_presuffix(item, suffix='_restore')
                outputs.biascorrected.append(fname)

            # probability maps ?
            if self.inputs.probability_maps:
                for i in range(nclasses):
                    fname = fname_presuffix(item, suffix='_prob_%d'%(i))
                    outputs.prob_maps.append(fname)

        # For each output file-type (key), check that any expected
        # files in the output list exist.
        for outtype, outlist in outputs.iteritems():
            if len(outlist) > 0:
                for outfile in outlist:
                    if not len(glob(outfile))==1:
                        msg = "Output file '%s' of type '%s' was not generated"\
                                % (outfile, outtype)
                        raise IOError(msg)

        return outputs


class Flirt(FSLCommand):
    """Use FSL FLIRT for coregistration.

    For complete details, see the `FLIRT Documentation. 
    <http://www.fmrib.ox.ac.uk/fsl/flirt/index.html>`_

    To print out the command line help, use:
        fsl.Flirt().inputs_help()

    Examples
    --------
    >>> from nipype.interfaces import fsl
    >>> flt = fsl.Flirt(bins=640, searchcost='mutualinfo')
    >>> flt.inputs.infile = 'subject.nii'
    >>> flt.inputs.reference = 'template.nii'
    >>> flt.inputs.outfile = 'moved_subject.nii'
    >>> flt.inputs.outmatrix = 'subject_to_template.mat'
    >>> res = flt.run()


    """

    @property
    def cmd(self):
        """sets base command, not editable"""
        return "flirt"

    opt_map = {'datatype':           '-datatype %d ',
            'cost':               '-cost %s',
            'searchcost':         '-searchcost %s',
            'usesqform':          '-usesqform',
            'displayinit':        '-displayinit',
            'anglerep':           '-anglerep %s',
            'interp':             '-interp',
            'sincwidth':          '-sincwidth %d',
            'sincwindow':         '-sincwindow %s',
            'bins':               '-bins %d',
            'dof':                '-dof %d',
            'noresample':         '-noresample',
            'forcescaling':       '-forcescaling',
            'minsampling':        '-minsamplig %f',
            'paddingsize':        '-paddingsize %d',
            'searchrx':           '-searchrx %d %d',
            'searchry':           '-searchry %d %d',
            'searchrz':           '-searchrz %d %d',
            'nosearch':           '-nosearch',
            'coarsesearch':       '-coarsesearch %d',
            'finesearch':         '-finesearch %d',
            'refweight':          '-refweight %s',
            'inweight':           '-inweight %s',
            'noclamp':            '-noclamp',
            'noresampblur':       '-noresampblur',
            'rigid2D':            '-2D',
            'verbose':            '-v %d',
            'flags':              '%s',
            'infile':             None,
            'outfile':            None,
            'reference':          None,
            'outmatrix':          None,
            'inmatrix':           None,
            }

    def inputs_help(self):
        """Print command line documentation for FLIRT."""
        print get_doc(self.cmd, self.opt_map)


    def _parse_inputs(self):
        '''Call our super-method, then add our input files'''
        # Could do other checking above and beyond regular _parse_inputs here
        allargs = super(Flirt, self)._parse_inputs(skip=('infile', 
            'outfile',
            'reference', 
            'outmatrix',
            'inmatrix'))
        possibleinputs = [(self.inputs.outfile,'-out'),
                (self.inputs.inmatrix, '-init'),
                (self.inputs.outmatrix, '-omat'),
                (self.inputs.reference, '-ref'),
                (self.inputs.infile, '-in')]

        for val, flag in possibleinputs:
            if val:
                allargs.insert(0, '%s %s' % (flag, val))
        return allargs

    def run(self, cwd=None, infile=None, reference=None, outfile=None, 
            outmatrix=None, **inputs):
        """Run the flirt command

        Parameters
        ----------
        infile : string
            Filename of volume to be moved.
        reference : string
            Filename of volume used as target for registration.
        outfile : string, optional
            Filename of the output, registered volume.  If not specified, only
            the transformation matrix will be calculated.
        outmatrix : string, optional
            Filename to output transformation matrix in asci format.
            If not specified, the output matrix will not be saved to a file.
        inputs : dict
            Additional ``inputs`` assignments.

        Returns
        -------
        results : Bunch
            A `Bunch` object with a copy of self in `interface`
            runtime : Bunch containing stdout, stderr, returncode, commandline

        """

        if infile:
            self.inputs.infile = infile
        if not self.inputs.infile:
            raise AttributeError('Flirt requires an infile.')
        if reference:
            self.inputs.reference = reference
        if not self.inputs.reference:
            raise AttributeError('Flirt requires a reference file.')

        if outfile:
            self.inputs.outfile = outfile
        if outmatrix:
            self.inputs.outmatrix = outmatrix
        self.inputs.update(**inputs)

        results = self._runner()
        if results.runtime.returncode == 0:
            results.outputs = self.aggregate_outputs(cwd)
        return results 

    def aggregate_outputs(self, cwd=None):
        """Create a Bunch which contains all possible files generated
        by running the interface.  Some files are always generated, others
        depending on which ``inputs`` options are set.

        Returns
        -------
        outputs : Bunch object
            outfile
            outmatrix

        Raises
        ------
        IOError
            If expected output file(s) outfile or outmatrix are not found.

        """
        outputs = Bunch(outfile=None, outmatrix=None)

        def raise_error(filename):
            raise IOError('File %s was not generated by Flirt' % filename)

        if cwd is None:
            cwd = os.getcwd()

        if self.inputs.outfile:
            outputs.outfile = os.path.join(cwd, self.inputs.outfile)
            if not glob(outputs.outfile):
                raise_error(outputs.outfile)
        if self.inputs.outmatrix:
            outputs.outmatrix = os.path.join(cwd, self.inputs.outmatrix)
            if not glob(outputs.outmatrix):
                raise_error(outputs.outmatrix)

        return outputs

class ApplyXFM(Flirt):
    '''Use FSL FLIRT to apply a linear transform matrix.

    For complete details, see the `FLIRT Documentation. 
    <http://www.fmrib.ox.ac.uk/fsl/flirt/index.html>`_

    To print out the command line help, use:
        fsl.ApplyXFM().inputs_help()

    Note: This class is currently untested. Use at your own risk!

    Examples
    --------
    >>> from nipype.interfaces import fsl
    >>> xfm = ApplyXFM(infile='subject.nii', reference='mni152.nii', bins=640)
    >>> xfm_applied = xfm.run(inmatrix='xform.mat')
    '''
    def run(self, cwd=None, infile=None, reference=None, inmatrix=None, 
            outfile=None, **inputs):
        """Run flirt and apply the transformation to the image.

        eg.
        flirt [options] -in <inputvol> -ref <refvol> -applyxfm -init
        <matrix> -out <outputvol>

        Parameters
        ----------
        infile : string
            Filename of volume to be moved.
        reference : string
            Filename of volume used as target for registration.
        inmatrix : string
            Filename for input transformation matrix, in ascii format.
        outfile : string, optional
            Filename of the output, registered volume.  If not
            specified, only the transformation matrix will be
            calculated.
        inputs : dict
            Additional ``inputs`` assignments.

        Returns
        -------
        results : Bunch
            A `Bunch` object with a copy of self in `interface`
            runtime : Bunch containing stdout, stderr, returncode, commandline

        Examples
        --------
        >>> from nipype.interfaces import fsl
        >>> flt = fsl.Flirt(infile='subject.nii', reference='template.nii')
        >>> xformed = flt.run(inmatrix='xform.mat', outfile='xfm_subject.nii')

        """

        if infile:
            self.inputs.infile = infile
        if not self.inputs.infile:
            raise AttributeError('ApplyXFM requires an infile.')
        if reference:
            self.inputs.reference = reference
        if not self.inputs.reference:
            raise AttributeError('ApplyXFM requires a reference file.')
        if inmatrix:
            self.inputs.inmatrix = inmatrix
        if not self.inputs.inmatrix:
            raise AttributeError('ApplyXFM requires an inmatrix')
        if outfile:
            self.inputs.outfile = outfile
        # If the inputs dict already has a set of flags, we want to
        # update it, not overwrite it.
        flags = inputs.get('flags', None)
        if flags is None:
            inputs['flags'] = '-applyxfm'
        else:
            inputs['flags'] = ' '.join([flags, '-applyxfm'])
        self.inputs.update(**inputs)

        results = self._runner(cwd=cwd)
        if results.runtime.returncode == 0:
            # applyxfm does not output the outmatrix
            results.outputs = self.aggregate_outputs(verify_outmatrix=False)
        return results 

    def aggregate_outputs(self):
        """Create a Bunch which contains all possible files generated
        by running the interface.  Some files are always generated, others
        depending on which ``inputs`` options are set.

        Returns
        -------
        outputs : Bunch object
            outfile

        Raises
        ------
        IOError
            If expected output file(s) outfile or outmatrix are not found.

        """
        outputs = Bunch(outfile=None)
        if self.inputs.outfile:
            outputs.outfile = self.inputs.outfile
        if self.inputs.outmatrix:
            outputs.outmatrix = self.inputs.outmatrix

        def raise_error(filename):
            raise IOError('File %s was not generated by Flirt' % filename)

        # Verify output files exist
        if not glob(outputs.outfile):
            raise_error(outputs.outfile)
        if verify_outmatrix:
            if not glob(outputs.outmatrix):
                raise_error(outputs.outmatrix)
        return outputs

class McFlirt(FSLCommand):
    """Use FSL MCFLIRT to do within-modality motion correction.

    For complete details, see the `MCFLIRT Documentation. 
    <http://www.fmrib.ox.ac.uk/fsl/mcflirt/index.html>`_

    To print out the command line help, use:
        McFlirt().inputs_help()

    Examples
    --------
    >>> from nipype.interfaces import fsl
    >>> mcflt = fsl.McFlirt(infile='timeseries.nii', cost='mututalinfo')
    >>> res = mcflt.run()

    """
    @property
    def cmd(self):
        """sets base command, not editable"""
        return 'mcflirt'

    def inputs_help(self):
        """Print command line documentation for MCFLIRT."""
        print get_doc(self.cmd, self.opt_map)

    opt_map = {
            'outfile':     '-out %s',
            'cost':        '-cost %s',
            'bins':        '-bins %d',
            'dof':         '-dof %d',
            'refvol':      '-refvol %d',
            'scaling':     '-scaling %.2f',
            'smooth':      '-smooth %.2f',
            'rotation':    '-rotation %d',
            'verbose':     '-verbose',
            'stages':      '-stages %d',
            'init':        '-init %s',
            'usegradient': '-gdt',
            'usecontour':  '-edge',
            'meanvol':     '-meanvol',
            'statsimgs':   '-stats',
            'savemats':    '-mats',
            'saveplots':   '-plots',
            'report':      '-report',
            'reffile':     '-reffile %s',
            'infile':      None,
            }

    def _parse_inputs(self):
        """Call our super-method, then add our input files"""
        allargs = super(McFlirt, self)._parse_inputs(skip=('infile'))
        # XXX This would be handled properly by the standard mechanisms,
        # Why is it being done here?
        if self.inputs.infile is not None:
            allargs.insert(0,'-in %s'%(self.inputs.infile))

            # This IS contingent on self.inputs.infile being defined... don't
            # de-dent!
            # XXX This currently happens twice, slightly differently
            if self.inputs.outfile is None:
                # XXX newpath could be cwd, but then we have to put it in inputs or
                # pass it to _parse_inputs (or similar).
                outfile = fname_presuffix(self.inputs.infile,
                                            suffix='_mcf', newpath='.')
                allargs.append(self.opt_map['outfile'] % outfile)

        return allargs

    def run(self, cwd=None, infile=None, **inputs):
        """Runs mcflirt

        Parameters
        ----------
        cwd : string
            currently ignored
        infile : string
            Filename of volume to be aligned
        inputs : dict
            Additional ``inputs`` assignments.

        Returns
        -------
        results : Bunch
            A `Bunch` object with a copy of self in `interface`
            runtime : Bunch containing stdout, stderr, returncode, commandline

        Examples
        --------
        >>> from nipype.interfaces import fsl
        >>> mcflrt = fsl.McFlirt(cost='mutualinfo')
        >>> mcflrtd = mcflrt.run(infile='timeseries.nii')

        """
        if infile:
            self.inputs.infile = infile
        if not self.inputs.infile:
            raise AttributeError('McFlirt requires an infile.')

        self.inputs.update(**inputs)


        results = self._runner()
        if results.runtime.returncode == 0:
            results.outputs = self.aggregate_outputs()

        
        return results 

    def aggregate_outputs(self, cwd=None):
        if cwd is None:
            cwd = os.getcwd()

        outputs = Bunch(outfile=None,
                varianceimg=None,
                stdimg=None,
                meanimg=None,
                parfile=None,
                outmatfile=None)
        # get basename (correct fsloutpputytpe extension)
        # We are generating outfile if it's not there already
        # if self.inputs.outfile:

        outputs.outfile = fsl_info.gen_fname(self.inputs.infile,
                self.inputs.outfile, cwd=cwd, suffix='_mcf', check=True)

        # XXX Need to change 'item' below to something that exists
        # outfile? infile?
        # These could be handled similarly to default values for inputs
        if self.inputs.statsimgs:
            outputs.varianceimg = fname_presuffix(item, suffix='_variance')
            outputs.stdimg = fname_presuffix(item, suffix='_sigma')
            outputs.meanimg = fname_presuffix(item, suffix='_meanvol')
        if self.inputs.savemats:
            matnme, ext = os.path.splitext(item)
            matnme = matnme + '.mat'
            outputs.outmatfile = matnme
        if self.inputs.saveplots:
            # Note - if e.g. outfile has .nii.gz, you get .nii.gz.par, which is
            # what mcflirt does!
            outputs.parfile = outputs.outfile + '.par'
            if not os.path.exists(outputs.parfile):
                msg = "Output file '%s' for '%s' was not generated" \
                        % (outname, outtype)
                raise IOError(msg)

        return outputs



class Fnirt(FSLCommand):
    """Use FSL FNIRT for non-linear registration.

    For complete details, see the `FNIRT Documentation.
    <http://www.fmrib.ox.ac.uk/fsl/fnirt/index.html>`_

    To print out the command line help, use:
        fsl.Fnirt().inputs_help()

    Examples
    --------
    >>> from nipype.interfaces import fsl
    >>> fnt = fsl.Fnirt(affine='affine.mat')
    >>> res = fnt.run(reference='ref.nii', infile='anat.nii')

    """
    @property
    def cmd(self):
        """sets base command, not editable"""
        return 'fnirt'

    # Leaving this in place 'til we get round to a thread-safe version
    @property
    def cmdline(self):
        """validates fsl options and generates command line argument"""
        self.update_optmap()
        allargs = self._parse_inputs()
        allargs.insert(0, self.cmd)
        return ' '.join(allargs)

    def inputs_help(self):
        """Print command line documentation for FNIRT."""
        print get_doc(self.cmd, self.opt_map)

    # XXX It's not clear if the '=' syntax (which is necessary for some
    # arguments) supports ' ' separated lists. We might need ',' separated lists
    opt_map = {
            'affine':           '--aff=%s',
            'initwarp':         '--inwarp=%s',
            'initintensity':    '--intin=%s',
            'configfile':       '--config=%s',
            'referencemask':    '--refmask=%s',
            'imagemask':        '--inmask=%s',
            'fieldcoeff_file':  '--cout=%s',
            'outimage':         '--iout=%s',
            'fieldfile':        '--fout=%s',
            'jacobianfile':     '--jout=%s',
            # XXX I think reffile is misleading / confusing
            'reffile':          '--refout=%s',
            'intensityfile':    '--intout=%s',
            'logfile':          '--logout=%s',
            'verbose':          '--verbose',
            'sub_sampling':     '--subsamp=%d',
            'max_iter':         '--miter=%d',
            'referencefwhm':    '--reffwhm=%f',
            'imgfwhm':          '--infwhm=%f',
            'lambdas':          '--lambda=%f',
            'estintensity':     '--estint=%f',
            'applyrefmask':     '--applyrefmask=%f',
            # XXX The closeness of this alternative name might cause serious
            # confusion
            'applyimgmask':      '--applyinmask=%f',
            'flags':            '%s',
            'infile':           '--in=%s',
            'reference':        '--ref=%s',
            }

    def run(self, cwd=None, infile=None, reference=None, **inputs):
        """Run the fnirt command

        Note: technically, only one of infile OR reference need be specified.

        You almost certainly want to start with a config file, such as
        T1_2_MNI152_2mm

        Parameters
        ----------
        infile : string
            Filename of the volume to be warped/moved.
        reference : string
            Filename of volume used as target for warp registration.
        inputs : dict
            Additional ``inputs`` assignments.

        Returns
        --------
        results : Bunch
            A `Bunch` object with a copy of self in `interface`
            runtime : Bunch containing stdout, stderr, returncode, commandline

        Examples
        --------
        T1 -> Mni153

        >>> from nipype.interfaces import fsl
        >>> fnirt_mprage = fsl.Fnirt()
        >>> fnirt_mprage.inputs.imgfwhm = [8, 4, 2]
        >>> fnirt_mprage.inputs.sub_sampling = [4, 2, 1]

        Specify the resolution of the warps, currently not part of the
        ``fnirt_mprage.inputs``:

        >>> fnirt_mprage.inputs.flags = '--warpres 6, 6, 6'
        >>> res = fnirt_mprage.run(infile='subj.nii', reference='mni.nii')

        We can check the command line and confirm that it's what we expect.

        >>> fnirt_mprage.cmdline  #doctest: +NORMALIZE_WHITESPACE
        'fnirt --in=subj.nii --ref=mni.nii --warpres 6, 6, 6
            --infwhm 8.000000 4.000000 2.000000 --subsamp 4 2 1'

        """
        if infile:
            self.inputs.infile = infile
        if reference:
            self.inputs.reference = reference
        if self.inputs.reference is None and self.inputs.infile is None:
            raise AttributeError('Fnirt requires at least a reference' \
                                 'or input file.')
        self.inputs.update(**inputs)

        results = self._runner(cwd=cwd)
        if results.runtime.returncode == 0:
            results.outputs = self.aggregate_outputs(cwd)

        return results 

    # This disturbs me a little, see my post on nipy-devel about the intent of
    # opt_map. I'm leaving this here as a counterexample, and a fine example of
    # me over-reacting -DJC
    def update_optmap(self):
        """Render the Fnirt class non-thread safe
        
        Updates opt_map AT THE CLASS LEVEL for inout items with variable values
        """
        warn(DeprecationWarning('This is wrong. Do not do anything like this.'
                                '(Please.)'))
        itemstoupdate = ['sub_sampling',
                'max_iter',
                'referencefwhm',
                'imgfwhm',
                'lambdas',
                'estintensity',
                'applyrefmask',
                'applyimgmask']
        # XXX Idea for implementing comparable logic in _parse_inputs:
        # if type(item) is list: to the opt split, then do:
        # ','.join(opt[1] % v for f in item), or maybe ' '.join()
        # _parse_inputs should probably split on ' ' or '='
        for item in itemstoupdate:
            values = self.inputs.get(item)
            if values:
                opt = self.opt_map[item].split('=')
                opt[1] = opt[1].split()[0]
                try:
                    valstr = opt[0] + '=' + ' '.join(opt[1:2] * len(values))
                except TypeError:
                    # TypeError is raised if values is not a SEQUENCE
                    # i.e., we are missing strings
                    valstr = opt[0] + '=' + opt[1]
                self.opt_map[item] = valstr

    def write_config(self,configfile):
        """Writes out currently set options to specified config file
        
        Parameters
        ----------
        configfile : /path/to/configfile
        """
        self.update_optmap()
        valid_inputs = self._parse_inputs() 
        try:
            fid = open(configfile, 'w+')
        except IOError:
            print ('unable to create config_file %s'%(configfile))
            
        for item in valid_inputs:
            fid.write('%s\n'%(item))
        fid.close()

    def aggregate_outputs(self, cwd=None):
        """Create a Bunch which contains all possible files generated
        by running the interface.  Some files are always generated, others
        depending on which ``inputs`` options are set.
        
        Returns
        -------
        outputs : Bunch object
             coefficientsfile
             warpedimage
             warpfield
             jacobianfield
             modulatedreference
             intensitymodulation
             logfile

        Notes
        -----
        For each item in Bunch:
        If None, optional file was not generated
        Else, contains path,filename of generated outputfile(s)
             Raises Exception if file is not found        
        """
        if cwd is None:
            cwd = os.getcwd()

        outputs = Bunch(fieldcoeff_file=None,
                        warpedimage=None,
                        fieldfile=None,
                        jacobianfield=None,
                        modulatedreference=None,
                        intensitymodulation=None,
                        logfile=None)

        # Note this is the only one that'll work with the pipeline code
        # currently
        if self.inputs.fieldcoeff_file:
            outputs.fieldcoeff_file = \
                    os.path.realpath(self.inputs.fieldcoeff_file)
        # the rest won't
        if self.inputs.outimage:
            # This should end with _warp
            outputs.warpedimage = self.inputs.outimage
        if self.inputs.fieldfile:
            outputs.fieldfile = self.inputs.fieldfile
        if self.inputs.jacobianfile:
            outputs.jacobianfield = self.inputs.jacobianfile
        if self.inputs.reffile:
            outputs.modulatedreference = self.inputs.reffile
        if self.inputs.intensityfile:
            outputs.intensitymodulation = self.inputs.intensityfile
        if self.inputs.logfile:
            outputs.logfile = self.inputs.logfile

        for item, file in outputs.iteritems():
            if file is not None:
                file = os.path.join(cwd, file)
                file = fsl_info.glob(file)
                if file is None:
                    raise IOError('file %s of type %s not generated'%(file,item))
                setattr(outputs, item, file)
        return outputs

class ApplyWarp(FSLCommand):
    '''Use FSL's applywarp to apply the results of a Fnirt registration
    
    Note how little actually needs to be done if we have truly order-independent
    arguments!
    '''
    @property
    def cmd(self):
        return 'applywarp'

    opt_map = {'infile':            '--in=%s',
               'outfile':           '--out=%s',
               'reference':         '--ref=%s',
               'fieldfile':          '--warp=%s',
               'premat':            '--premat=%s',
               'postmat':           '--postmat=%s',
              }

    def run(self, cwd=None, infile=None, outfile=None, reference=None,
            fieldfile=None, **inputs):
        '''Interesting point - you can use coeff_files, or fieldfiles
        interchangeably here'''
        def set_attr(name, value, error=True):
            if value is not None:
                setattr(self.inputs, name, value)
            if self.inputs.get(name) is None and error:
                raise AttributeError('applywarp requires %s' % name)

        # XXX Even this seems overly verbose
        set_attr('infile', infile)
        set_attr('outfile', outfile, error=False)
        set_attr('reference', reference)
        set_attr('fieldfile', fieldfile)

        self.inputs.update(**inputs)

        results = self._runner(cwd=cwd)
        if results.runtime.returncode == 0:
            results.outputs = self.aggregate_outputs(cwd)

        return results 

    def _parse_inputs(self):
        """Call our super-method, then add our input files"""
        allargs = super(ApplyWarp, self)._parse_inputs()
        if self.inputs.infile is not None:
            # XXX This currently happens twice, slightly differently
            if self.inputs.outfile is None:
                # XXX newpath could be cwd, but then we have to put it in inputs
                # or pass it to _parse_inputs (or similar).
                outfile = fname_presuffix(self.inputs.infile,
                                            suffix='_warp', newpath='.')
                allargs.append(self.opt_map['outfile'] % outfile)

        return allargs

    def aggregate_outputs(self, cwd=None):
        if cwd is None:
            cwd = os.getcwd()

        outputs = Bunch()
        outputs.outfile = fsl_info.gen_fname(self.inputs.infile,
                self.inputs.outfile, cwd=cwd, suffix='_warp', check=True)

        return outputs

class FSLSmooth(FSLCommand):
    '''Use fslmaths to smooth the image

    This is dumb, of course - we should use nipy for such things! But it is a
    step along the way to get the "standard" FSL pipeline in place.
    
    This is meant to be a throwaway class, so it's not currently very robust.
    Effort would be better spent integrating basic numpy into nipype'''
    @property
    def cmd(self):
        return 'fslmaths'

    opt_map = {'infile':  None,
               'fwhm':    None,
               'outfile': None,
              }


    def _parse_inputs(self):
        outfile = self.inputs.outfile
        if outfile is None:
            outfile = fname_presuffix(self.inputs.infile, suffix='_smooth',
                    newpath='.')
        return ['%s -kernel gauss %d -fmean %s' % (self.inputs.infile, 
                                            self.inputs.fwhm,
                                            outfile)]

    def aggregate_outputs(self, cwd=None):
        if cwd is None:
            cwd = os.getcwd()
        
        outputs = Bunch()

        outputs.smoothedimage = fsl_info.gen_fname(self.inputs.infile,
                self.inputs.outfile, cwd=cwd, suffix='_smooth', check=True)

        return outputs

    

class L1FSFmaker:
    '''Use the template variables above to construct fsf files for feat.
    
    This doesn't actually run anything.
    
    Examples
    --------
    FSFmaker(5, ['left', 'right', 'both'])
        
    '''
    # These are still somewhat specific to my experiment, but should be so in an
    # obvious way.  Contrasts in particular need to be addressed more generally.
    # These should perhaps be redone with a setattr_on_read property, though we
    # don't want to reload for each instance separately.
    fsf_header = load_template('feat_header.tcl')
    fsf_ev = load_template('feat_ev.tcl')
    fsf_ev_ortho = load_template('feat_ev_ortho.tcl')
    fsf_contrasts = load_template('feat_contrasts.tcl')

    # This is a very different animal, and I won't worry with specifying inputs
    # properly yet. But here's a list:

    # num_scans, cond_names, func_file, struct_file, 

    def __init__(self, **inputs):
        self.inputs = Bunch(inputs)
        self.inputs.num_scans = num_scans
        self.inputs.cond_names = cond_names

        # This is more package general, and should happen at a higher level
        fsl_root = getenv('FSLDIR')
        for i in range(num_scans):
            fsf_txt = self.fsf_header.substitute(num_evs=len(cond_names), 
                         func_file=inputs.func_file, struct_file=struct_file, scan_num=i,
                         standard_image=fsl_info.standard_image('MNI152_T1_2mm_brain'))
            for j, cond in enumerate(cond_names):
                fsf_txt += self.gen_ev(i, j+1, cond, subj_dir, len(cond_names))
            fsf_txt += self.fsf_contrasts.substitute()

            f = open('scan%d.fsf' % i, 'w')
            f.write(fsf_txt)
            f.close()
                
                
    def gen_ev(self, scan, cond_num, cond_name, subj_dir, total_conds):
        ev_txt = self.fsf_ev.substitute(ev_num=cond_num, ev_name=cond_name,
                                        scan_num=scan, base_dir=subj_dir)

        for i in range(total_conds + 1):
            ev_txt += self.fsf_ev_ortho.substitute(c0=cond_num, c1=i) 

        return ev_txt
