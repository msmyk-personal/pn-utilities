"""
Praneeth's tools for making life easy while coding in python.
"""

import errno
import functools
import importlib
import inspect
import os
import re
import sys
import subprocess
import weakref
import socket
import fnmatch
import time
from copy import deepcopy
from timeit import default_timer as timer

if os.name == 'nt':
    import multiprocess
import numpy as np
import blinker

try: # this is for the benefit of bpn and other blender-based modules that use pntools
    import bpy
    BLENDER_MODE = True
except ImportError:
    BLENDER_MODE = False


## Inheritance
class AddMethods:
    """
    Add methods to a class. Decorator.
    
    Usage:
        @AddMethods([pntools.properties])
        class myClass:
            def foo(self):
                print("bar")
        
        a = myClass()
        a.foo() # prints bar
        a.properties() # lists foo and properties
    """
    def __init__(self, methodList):
        self.methodList = methodList
    def __call__(self, func):
        functools.update_wrapper(self, func)
        @functools.wraps(func)
        def wrapperFunc(*args, **kwargs):
            for method in self.methodList:
                setattr(func, method.__name__, method)
            funcOut = func(*args, **kwargs)
            return funcOut
        return wrapperFunc

class MixIn:
    """
    Decorator to grab properties from src class and put them in target class.
    No overwrites.
    ALMOST the same as inheritance, BUT, if the source class has a
    'list' or 'dict' class attribute, then it makes a deepcopy
    Example:
        @MixIn(src_class)
        class trg_class:
            pass
    """
    def __init__(self, src_class):
        self.src_class = src_class
    def __call__(self, trg_class):
        src_attrs = {attr_name:attr for attr_name, attr in self.src_class.__dict__.items() if attr_name[0] != '_'}
        for src_attr_name, src_attr in src_attrs.items():
            if not hasattr(trg_class, src_attr_name): # no overwrites
                if isinstance(src_attr, (list, dict)):
                    src_attr = deepcopy(src_attr)
                setattr(trg_class, src_attr_name, src_attr)
        return trg_class

def port_properties(src_class, trg_class, trg_attr_name='data'):
    """
    Port properties and methods (not hidden) from source class src_class
    to target class trg_class.

    Differs from Mixin and inheriance. Used to design 'containers' with automatic routing.

    :param src_class: (class)
    :param trg_class: (class)
    :param trg_attr_name: (str)

    Basically, trg_class objects have an attribute with name
    trg_attr_name, which is an instance of trg_class
    Example:
        MeshObject class has an attribute (or property) 'data' that is an instance of trg class
        s = MeshObject() # MeshObject is the trg_class
        s.data = Mesh()  # Mesh is the src_class
    Now,
        s.data.prop : to execute this, I want to say s.prop
        s.data.func() : to execute this, I want to say s.func()
        
    Within MeshObject, defining the __init__ function as follows achieves this!
    class MeshObject(Object):
        def __init__(self, name, *args, **kwargs):
            super().__init__(name, *args, **kwargs) # make an instance of Object
            self.data = Mesh(...) # make an instance of mesh
            pn.port_properties(Mesh, self.__class__, 'data') 
            # grants direct access to Mesh's stuff with appropritate routing

    Now a MeshObject instance inherits all methods and properties from
    Object class, AND from Mesh class. Methods from Mesh class are
    automagically called with the correct Mesh object as the first input.

    Note that trg_class itself is being modified
    (i.e., return statement is just to enable the decorator)

    Note that attributes of the Mesh class will NOT be copied
    """
    # properties
    def swap_input_fget(this_prop):
        return lambda x: this_prop.fget(getattr(x, trg_attr_name))
    
    def swap_input_fset(this_prop):
        return lambda x, s: this_prop.fset(getattr(x, trg_attr_name), s)

    src_properties = {p_name : p for p_name, p in src_class.__dict__.items() if isinstance(p, property)}
    for p_name, p in src_properties.items():
        if not hasattr(trg_class, p_name): # no overwrites - this implmentation is more readable
            if p.fset is None:
                setattr(trg_class, p_name, property(swap_input_fget(p)))
            else:
                setattr(trg_class, p_name, property(swap_input_fget(p), swap_input_fset(p)))

    # methods
    def swap_first_input(func): # when we don't know how many inputs func has
        return lambda x: functools.partial(func, getattr(x, trg_attr_name))

    src_methods = {func_name:func for func_name, func in src_class.__dict__.items() if type(func).__name__ == 'function' and func_name[0] != '_'}
    for src_func_name, src_func in src_methods.items():
        if not hasattr(trg_class, src_func_name): # no overwrites
            setattr(trg_class, src_func_name, property(swap_first_input(src_func)))

    return trg_class

class PortProperties:
    """
    Providing port_properties functionality as a decorator.
    
    This is for implementing the idea of a 'container' in blender, that
    I could not solve using multiple inheritance. A container is an
    instance of a specific class, but also contains instances of other
    classes. You can act on any 'contained' object directly (you just
    have to use the method name)
    cont = primary_class()
    cont.two = secondary_class()
    If dummy is a method of cont.two, then I want to say:
    cont.dummy() instead of cont.two.dummy(), 
    BUT cont.dummy() should execute cont.two.dummy() if cont doesn't have a method called dummy()

    A container class is created by:
    Inheriting from a primary class.
    Modifiying the container class with port_properties (or using the PortProperties decorator)

    Example:
    @PortProperties(Mesh, 'data') # instance of MeshObject MUST have 'data' attribute/property
    class MeshObject(Object):   
        def __init__(self):
            super().__init__()
            self.data = Mesh()

    m = MeshObject()

    In this example, MeshObject is the container class.
    It inherits from Object class.
    Mesh is the secondary class
    """
    def __init__(self, src_class, trg_attr_name):
        self.src_class = src_class
        self.trg_attr_name = trg_attr_name
    def __call__(self, trg_class):
        return port_properties(self.src_class, trg_class, self.trg_attr_name)


## Event handlers and broadcasting using blinker's signal
class Handler:
    """
    Event handlers based on blinker's signal.
    Currently, handlers can be defined on:
    1) Class functions - act on all members of a class
    2) Bound methods - act on a specific class member
    3) Class property - act on all members of a class when a property is set
    4) Object property - act on a specific class member when its property is set
    A receiver function can be attached either before, or after for each of these categories.
    Therefore, there are 8 types of handlers in total.
    thing = (class, object)
    attr = (function, property)
    mode = (pre, post)
    """
    def __init__(self, thing, attr, mode='post', sig=None):
        assert isinstance(attr, str)
        assert hasattr(thing, attr)
        assert mode in ('pre', 'post')
        self._thing = weakref.ref(thing)
        self.attr = attr
        self.mode = mode
        if sig is None:
            self.signal = blinker.base.signal
        else: # providing signal from a specific namespace will leave blinker's default namespace free for other apps
            assert isinstance(sig.__self__, blinker.base.Namespace)
            self.signal = sig
        assert self.attr_cat in ('property', 'function')
        if self.attr_cat == 'property':
            assert getattr(self.thing_class, self.attr).fset is not None
    
    thing = property(lambda s: s._thing())
    thing_is_class = property(lambda s: inspect.isclass(s.thing))
    thing_class = property(lambda s: s.thing if s.thing_is_class else type(s.thing))
    attr_cat = property(lambda s: type(getattr(s.thing_class, s.attr)).__name__)
    mod_name = property(lambda s: s.thing.__module__ if s.thing_is_class else type(s.thing).__module__)
    cls_id = property(lambda s: s.mode + '-' + s.mod_name + '-' + s.thing_class.__name__ + '-' + s.attr_name)
    @property
    def attr_name(self):
        """Name of the attribute. If it is a property, it must have a setter to support a handler."""
        if self.attr_cat == 'function':
            return self.attr
        return self.attr + '.fset'
    @property
    def instance_name(self):
        """Name of the instance"""
        if self.thing_is_class:
            return ''
        return self.thing.name if hasattr(self.thing, 'name') else hex(id(self.thing))
    @property
    def id(self):
        """This is the broadcasted signal."""
        if self.thing_is_class:
            return self.cls_id
        return self.cls_id + '(' + self.instance_name + ')'

    def id2dict(self):
        """Handler ID as a dictionary"""
        return handler_id2dict(self.id)

    def broadcast(self):
        """Tweak thing's attr to broadcast a signal either before or after execution."""
        if self.attr_cat == 'function':
            setattr(self.thing, self.attr, self._broadcast_function())
        if self.attr_cat == 'property': # only the class property can broadcast!            
            # Remember that either all instances broadcast a property, or none of them do.
            # The strategy for object specific handlers is to filter at the receiver.
            setattr(self.thing_class, self.attr, self._broadcast_property())

    def add_receiver(self, receiver_func):
        """
        Add a receiver function to the handler.
        A receiver function should have the same signature as defining a function in a class:
        def receiver_fun(self):
            pass
        """
        assert type(receiver_func).__name__ in ('function', 'method')
        r_desc = self.receiver_descriptor(receiver_func)
        if r_desc not in [r for r in self.receivers if r[1] != '<lambda>']:
            self.signal(self.id).connect(receiver_func)
        else:
            print('Receiver with description '+str(r_desc)+' already connected. No action taken.')
    
    def get_receivers(self):
        """Return the receivers (weakref list)"""
        return self.signal(self.id).receivers

    def delete_receivers(self):
        """Delete all receivers for a signal."""
        self.signal(self.id).receivers = {}

    @property #**
    def channels(self):
        """Broadcasting channels (if any)"""
        if self.attr_cat == 'function':
            func = getattr(self.thing, self.attr)
            if hasattr(func, '__broadcast__'):
                return func.__broadcast__
            return None
        if self.attr_cat == 'property':
            p = getattr(self.thing_class, self.attr)
            if hasattr(p.fset, '__broadcast__'):
                return p.fset.__broadcast__
            return None

    @property #**
    def receivers(self):
        """
        Descriptions for current receiver functions.
        ('function'/'method(obj_id)', __qualname__, __module__)
        """
        return [self.receiver_descriptor(r()) for r in list(self.get_receivers().values())]
    
    def __eq__(self, other):
        return self.channels == other.channels and self.receivers == other.receivers
    
    def __str__(self):
        return object.__repr__(self)

    def __repr__(self):
        return self.__module__ + "." + self.__class__.__name__ + ': ' + self.id + '\nChannels: ' + str(self.channels) + '\nReceivers: ' + str(self.receivers)

    @staticmethod
    def receiver_descriptor(r):
        """Tuple description of a signal's receiver function"""
        f_type = type(r).__name__
        if f_type == 'method':
            bound_obj = r.__self__
            bound_obj_id = bound_obj.name if hasattr(bound_obj, 'name') else hex(id(bound_obj))
            return (f_type+'('+ bound_obj_id +')', r.__qualname__, r.__module__)
        return (f_type, r.__qualname__, r.__module__)

    def _broadcast_function(self):
        """
        modifies self.thing's attribute to broadcast
        """
        func = getattr(self.thing, self.attr)
        func_type = type(func).__name__
        signal_name = self.id

        if hasattr(func, '__broadcast__'): # already broadcasting
            assert func.__broadcast__ == signal_name
            return func

        if func_type == 'method': # 'unbounded'
            meth = func
            func = getattr(meth.__self__.__class__, meth.__name__)

        def _new_func_pre(s, *args, **kwargs):
            if bool(self.signal(signal_name).receivers):
                self.signal(signal_name).send(s) # signal is sent BEFORE the object is modified
            f_out = func(s, *args, **kwargs)
            return f_out
        def _new_func_post(s, *args, **kwargs):
            f_out = func(s, *args, **kwargs)
            if bool(self.signal(signal_name).receivers):
                self.signal(signal_name).send(s) # signal is sent AFTER the object is modified
            return f_out

        _new_func = _new_func_pre if self.mode == 'pre' else _new_func_post
        _new_func.__name__ = func.__name__
        _new_func.__qualname__ = func.__qualname__
        _new_func.__module__ = func.__module__
        _new_func.__broadcast__ = signal_name

        if func_type == 'method': # bind the function to the object
            return _new_func.__get__(meth.__self__)
        
        return _new_func

    def _broadcast_property(self):
        """
        Creates a new property with a modified setter.
        Adds a broadcasting signal to the setter of property p.
        """
        p = getattr(self.thing_class, self.attr)
        signal_name = self.cls_id
        assert isinstance(p, property)

        if hasattr(p.fset, '__broadcast__'):
            if signal_name in p.fset.__broadcast__:
                return p # no need to modify the property

        def _new_fset_pre(x, s): # x is the object whose property is being modified (self)
            # broadcast signal for all members
            if bool(self.signal(signal_name).receivers):
                self.signal(signal_name).send(x)
            # member-specific broadcast
            instance_name = x.name if hasattr(x, 'name') else hex(id(x))
            new_signal_name = signal_name+'('+instance_name+')'
            if bool(self.signal(new_signal_name).receivers):
                self.signal(new_signal_name).send(x) # signal is sent AFTER the object is modified
            f_out = p.fset(x, s)
            return f_out
        def _new_fset_post(x, s): # x is the object whose property is being modified (self)
            f_out = p.fset(x, s)
            # broadcast signal for all members
            if bool(self.signal(signal_name).receivers):
                self.signal(signal_name).send(x)
            # member-specific broadcast
            instance_name = x.name if hasattr(x, 'name') else hex(id(x))
            new_signal_name = signal_name+'('+instance_name+')'
            if bool(self.signal(new_signal_name).receivers):
                self.signal(new_signal_name).send(x) # signal is sent AFTER the object is modified
            return f_out

        _new_fset = _new_fset_pre if self.mode == 'pre' else _new_fset_post
        _new_fset.__name__ = p.fset.__name__
        _new_fset.__qualname__ = p.fset.__qualname__
        _new_fset.__module__ = p.fset.__module__
        if hasattr(p.fset, '__broadcast__'):
            _new_fset.__broadcast__ = p.fset.__broadcast__
        else:
            _new_fset.__broadcast__ = []
        _new_fset.__broadcast__ += [signal_name] # this is the signal name for the class
        return property(p.fget, _new_fset)

def handler_id2dict(k):
    """
    Turn a handler ID into meaningful parts
    A handler id is a string that has the following construction:
    mode-module-class-attribute(instance)
    """
    k_dict = {}
    stg1 = k.split('(')
    k_dict['instance'] = stg1[-1].rstrip(')') if len(stg1) == 2 else ''
    stg2 = stg1[0].split('-')
    assert len(stg2) == 4
    k_dict['mode'], k_dict['module'], k_dict['class'], stg3 = stg2
    k_dict['attr'] = stg3.replace('.fset', '')
    return k_dict

def add_handler(thing, attr, receiver_func, mode='post', sig=None):
    """
    One-liner access to setting up a broadcaster and receiver.

    Example:
        s1 = new.sphere('sph1')
        # s1.frame is a property, and fire fun whenever s1.frame is set
        add_handler(s1, 'frame', fun, mode='pre') 
        # Fire fun when the frame attribute of any instance of core.Object is set
        add_handler(core.Object, 'frame', fun, mode='post')
        # s1.translate is a method, and fire fun whenever s1.translate is invoked!
        add_handler(s1, 'translate', core.Object.show_frame, mode='post')
    """
    h = Handler(thing, attr, mode, sig)
    h.broadcast()
    h.add_receiver(receiver_func)
    return h

# BroadcastProperties is useful for modifying classes when defining them
class BroadcastProperties:
    """
    Enables properties in a class to have event handlers. This
    manipulation 'replaces' a property in a class with a new property
    object.

    Takes a class, and makes chosen properties setter emit a signal on
    every change. Use it as a decorator on classes to broadcast some/all
    property changes. Receiver receives the object after it is changed.

    Example: see tests.test_broadcasting2()

    Usage: (Don't chain with the same property. Chaining below is OK)
        @pn.BroadcastProperties('loc', mode='pre')
        @pn.BroadcastProperties('frame', mode='post')
        class Object(Thing):
            frame = property(...)
            loc = property(...)
    """
    def __init__(self, p_names='ALL', mode='post'):
        assert isinstance(p_names, (str, list, tuple))
        assert mode in ('pre', 'post')
        self.p_names = p_names
        self.mode = mode
    def __call__(self, src_class):
        if isinstance(self.p_names, str) and self.p_names == 'ALL':
            src_properties = {p_name : p for p_name, p in src_class.__dict__.items() if isinstance(p, property)}
        else:
            src_properties = {p_name : p for p_name, p in src_class.__dict__.items() if isinstance(p, property) and p_name in self.p_names}
        for p_name, p in src_properties.items():
            if p.fset is not None:
                h = Handler(src_class, p_name, self.mode)
                h.broadcast()
        return src_class


## File system
def locate_command(thingToFind, requireStr=None, verbose=True):
    """
    Locate an executable on your computer.

    :param thingToFind: string name of the executable (e.g. python)
    :param requireStr: require path to thingToFind to have a certain string
    :returns: Full path (like realpath) to thingToFind if it exists
              Empty string if thing does not exist
    """
    if sys.platform == 'linux' or sys.platform == 'darwin':
        queryCmd = 'which'
    elif sys.platform == 'win32':
        queryCmd = 'where'
    proc = subprocess.Popen(queryCmd+' '+thingToFind, stdout=subprocess.PIPE, shell=True)
    thingPath = proc.communicate()[0].decode('utf-8').rstrip('\n').rstrip('\r')
    if not thingPath:
        print('Terminal cannot find ', thingToFind)
        return ''

    if verbose:
        print('Terminal found: ', thingPath)
    if requireStr is not None:
        if requireStr not in thingPath:
            print('Path to ' + thingToFind + ' does not have ' + requireStr + ' in it!')
            return ''
    return thingPath

class OnDisk:
    """
    Raise error if function output not on disk. Decorator.

    :param func: function that outputs a path/directory
    :returns: decorated function with output handling
    :raises keyError: FileNotFoundError if func's output is not on disk

    Example:
        @OnDisk
        def getFileName_full(fPath, fName):
            fullName = os.path.join(os.path.normpath(fPath), fName)
            return fullName
    """
    def __init__(self, func):
        self.func = func
        functools.update_wrapper(self, func)
    def __call__(self, *args, **kwargs):
        thisDirFiles = self.func(*args, **kwargs)
        self.checkFiles(thisDirFiles)
        return thisDirFiles
    @staticmethod
    def checkFiles(thisFileList):
        """Raise error if any file in thisFileList not on disk."""
        if isinstance(thisFileList, str):
            thisFileList = [thisFileList]
        for dirFile in thisFileList:
            if not os.path.exists(dirFile):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), dirFile)

def ospath(thingToFind, errContent=None):
    """
    Find file or directory.
    
    :param thingToFind: string input to os path
    :param errContent=None: what to show when not found
    :returns: Full path to thingToFind if it exists.
              Empty string if thingToFind does not exist.
    """
    if errContent is None:
        errContent = thingToFind
    if os.path.exists(thingToFind):
        print('Found: ', os.path.realpath(thingToFind))
        return os.path.realpath(thingToFind)
    print('Did not find ', errContent)
    return ''

def find(pattern, path=None, exclude_hidden=True):
    "Example: find('*.txt', '/path/to/dir')"
    if path is None:
        path = os.getcwd()
        
    result = []
    for root, dirs, files in os.walk(path):
        for name in files:
            if fnmatch.fnmatch(name, pattern):
                result.append(os.path.join(root, name))

    if exclude_hidden:
        return [r for r in result if not (r.split(os.sep)[-1].startswith('~$') or r.split(os.sep)[-1].startswith('.'))]
    return result

def run(filename, start_line=1, end_line=None):
    """
    Most commonly used to run code that I'm working on inside the console.
    NOTE MATLAB-like indexing run(x.py, 1, 2) runs lines 1 and 2
    That the line numbers are 1-indexed to match the line numbers in the code editor (VSCode)
    Runs the last line number indicated as well!
    """
    if not os.path.isfile(filename):
        filename = find(filename)[0]
    assert os.path.isfile(filename)
    code = open(filename).readlines()
    if end_line is None:
        end_line = len(code)
    exec(''.join(code[(start_line-1):end_line]))

def file_size(file_list, units='MB'):
        """
        Returns files sizes in descending order (default: megabytes)
        """
        div = {'B':1, 'KB':1024, 'MB':1024**2, 'GB':1024**3, 'TB':1024**4}
        if isinstance(file_list, str):
            file_list = [file_list]
        assert isinstance(file_list, list)
        size_mb = {os.path.getsize(f)/div[units]:f for f in file_list} # {size: file_name}
        size_list = list(size_mb.keys())
        size_list.sort(reverse=True)
        return {size_mb[s]:s for s in size_list} # {file_name : size}

class FileManager:
    """
    Manage files in a project.
    Created with working on the operator project.
    Useful for creating datasets with data distributed across files and modalities.
    """
    def __init__(self, base_dir):
        assert isinstance(base_dir, str)
        self.base_dir = os.path.realpath(base_dir)
        self._files = {}
        self._filters = {}
        self._inclusions = {}
        self._exclusions = {}
    
    def add(self, type_name, pattern_list, include=None, exclude=None):
        """
        Add a type of file with a given filter.
        e.g. fm.add('video', '*Camera*.avi')
        """
        if isinstance(pattern_list, str):
            pattern_list = [pattern_list]
        self._files[type_name] = []
        for pattern in pattern_list:
            self._files[type_name] += find(pattern, path=self.base_dir)
        self._filters[type_name] = pattern_list
        self._inclusions[type_name] = []
        self._exclusions[type_name] = []

        if include is not None:
            if isinstance(include, str):
                self._include(type_name, include)
            else:
                assert isinstance(include, (list, tuple))
                for inc_str in include:
                    assert isinstance(inc_str, str)
                    self._include(type_name, inc_str)
        
        if exclude is not None:
            if isinstance(exclude, str):
                self._exclude(type_name, exclude)
            else:
                assert isinstance(exclude, (list, tuple))
                for exc_str in exclude:
                    assert isinstance(exc_str, str)
                    self._exclude(type_name, exc_str)
        
        return self # for chaining commands

    def _include(self, type_name, inclusion_string):
        """
        Include a set of files from the list. Useful for choosing files in specific sub-folders.
        Use this functionality using the add method.
        """
        assert type_name in self._files
        self._files[type_name] = [fn for fn in self._files[type_name] if inclusion_string in fn]
        self._inclusions[type_name].append(inclusion_string)

    def _exclude(self, type_name, exclusion_string):
        """
        Exclude a set of files from the list. Useful for ignoring files in specific sub-folders.
        Use this functionality using the add method.
        """
        assert type_name in self._files
        self._files[type_name] = [fn for fn in self._files[type_name] if exclusion_string not in fn]
        self._exclusions[type_name].append(exclusion_string)

    def __getitem__(self, key):
        assert key in self._files
        return self._files[key]
    
    def types(self):
        return self._files.keys()

    @property
    def all_files(self):
        ret = []
        for ftype in self.types():
            ret += self[ftype]
        return ret

    def report(self, units='MB'):
        for file_type, file_list in self._files.items():
            fs = sum(list(file_size(file_list, units=units).values()))
            print(str(len(file_list)) + ' ' + file_type + ' files taking up {:4.3f} '.format(fs) + units)


## Package management
def pkg_list():
    """
    Return a list of installed packages.

    :returns: output of pip freeze
    :raises keyError: raises an exception
    """
    proc = subprocess.Popen('pip freeze', stdout=subprocess.PIPE, shell=True)
    out = proc.communicate()
    pkgs = out[0].decode('utf-8').rstrip('\n').split('\n')
    pkgs = [k.rstrip('\r') for k in pkgs]  # windows compatibility
    pkgNames = [m[0] for m in [k.split('==') for k in pkgs]]
    pkgVers = [m[0] for m in [k.split('==') for k in pkgs]]
    return pkgs, pkgNames, pkgVers

def pkg_path(pkgNames=None):
    """Return path to installed packages."""
    if not pkgNames:
        _, pkgNames, _ = pkg_list()
    elif isinstance(pkgNames, str):
        pkgNames = [pkgNames]
    
    currPkgDir = []
    failedPackages = []
    for pkgName in pkgNames:
        print(pkgName)
        if pkgName == 'ipython':
            pkgName = 'IPython'
        elif pkgName == 'ipython-genutils':
            pkgName = str(pkgName).lower().replace('-', '_')
        elif pkgName in ['pywinpty', 'pyzmq', 'terminado']:
            continue
        else:
            pkgName = str(pkgName).lower().replace('-', '_').replace('python_', '').replace('_websupport', '')
        try:
            currPkgDir.append(importlib.import_module(pkgName).__file__)
        except UserWarning:
            failedPackages.append(pkgName)

    print('Failed for: ', failedPackages)    
    return currPkgDir


## introspection
def inputs(func):
    """Get the input variable names and default values to a function."""
    inpdict = {}
    if callable(func):
        inpdict = {str(k):inspect.signature(func).parameters[str(k)].default for k in inspect.signature(func).parameters.keys()}
    return inpdict

def module_members(mod, includeSubModules=True):
    """Return members of a module."""
    members = {}
    for name, data in inspect.getmembers(mod):
        if name.startswith('__') or (inspect.ismodule(data) and not includeSubModules):
            continue
        members[name] = str(type(inspect.unwrap(data))).split("'")[1]
    return members

def properties(obj):
    """
    For an instance obj of any class, use pn.properties(obj) for a summary of properties.
    Especially useful in the blender console.
    """
    #pylint:disable=expression-not-assigned
    [print((k, type(getattr(obj, k)), np.shape(getattr(obj, k)))) for k in dir(obj) if '_' not in k and 'method' not in k]


## input management
def clean_kwargs(kwargs, kwargs_def, kwargs_alias=None):
    """
    Clean keyword arguments based on default values and aliasing.

    :param kwargs: (dict) input kwargs that require cleaning.
    :param kwargs_def: (dict) should have all the possible keyword arguments.
    :param kwargs_alias: (dict) lists all possible aliases for each keyword.
        {kw1: [kw1, kw1_alias1, ..., kw1_aliasn], ...}
        kw1 is used inside the function, but kw1=val, kw1_alias1=val, ..., kw1_aliasn are all valid

    Returns: 
        (dict) keyword arguments after cleaning. Ensures all keywords in kwargs_def are present, and have the names used in the function.
        (dict) remaining keyword arguments
    """
    if not kwargs_alias:
        kwargs_alias = {key : [key] for key in kwargs_def.keys()}
    kwargs_fun = deepcopy(kwargs_def)
    kwargs_out = deepcopy(kwargs)
    for k in kwargs_fun:
        for ka in kwargs_alias[k]:
            if ka in kwargs:
                kwargs_fun[k] = kwargs_out.pop(ka)

    return kwargs_fun, kwargs_out


## Code development
def reload(constraint='Workspace'):
    """
    Reloads all modules in sys with a specified constraint.
    :param constraint: (str) name to be present within the module's path for reload
    Returns:
        names of all the modules that were identified for reload.
    """
    all_mod = [mod for key, mod in sys.modules.items() if constraint in str(mod)]
    reloaded_mod = []
    for mod in all_mod:
        try:
            importlib.reload(mod)
            reloaded_mod.append(mod.__name__)
        except: # pylint: disable=bare-except 
            #Using a specific exception creates a problem when developing with runpy (Blender development plugin workflow)
            if '<run_path>' not in  mod.__name__:
                print('Could not reload ' + mod.__name__)
    return reloaded_mod

class TimeIt:
    """
    Prints execution time. Decorator.
    Note that this only works on functions.
    Consider a function call:
    out1 = m.inflate(0.15, 0.1, 0.02, 100)
    Using the following will give the output in addition to printing the
    execution time.
    out1 = pn.TimeIt(m.inflate)(0.15, 0.1, 0.02, 100)
    """
    def __init__(self, func):
        self.func = func
        functools.update_wrapper(self, func)
    def __call__(self, *args, **kwargs):
        start = timer()
        funcOut = self.func(*args, **kwargs)
        end = timer()
        print(end-start)
        return funcOut

def tracker(trg_class):
    """
    Use this as a decorator to track class instances (and keep tracked classes as classes).
    include self.track(self) in the decorated class' __init__
    If there is a tracker in the parent class, don't add self.track(self) to the child class.
    BUT, decorate the child class!!
    
    Example:
        @tracker
        class Thing:
            def __init__(self):
                self.track(self)

        @tracker
        class Mesh:
            def __init__(self):
                pass # Don't track again!
    """
    class TrackMethods:
        """
        This is just a method container.
        see tracker function
        """
        all = []
        cache = []

        @classmethod
        def track(cls, obj):
            """Just used by the initalization function to track object."""
            cls.all.append(obj)
        
        @classmethod
        def track_clear(cls):
            """Forget the objects tracked so far."""
            cls.all = []
        
        @classmethod
        def track_clear_cache(cls):
            """Clear cache used by temporary tracking sessions."""
            cls.cache = []

        @classmethod
        def track_start(cls):
            """
            Start a tracking session. Move current objects to cache, and clean.
            Note that objects are tracked even without this method if a class is being tracked.
            Use this to create temporary tracking sessions.
            """
            cls.cache = cls.all
            cls.all = []

        @classmethod
        def track_end(cls):
            """End tracking session."""
            cls.all = cls.cache
            cls.cache = []

        @classmethod
        def dict_access(cls, key='id', val=None):
            """
            Give access to the object based on key. 
            
            Note:
            If keys (id) of different objects are the same, then only the
            last reference will be preserved.

            :param key: Property of the object being tracked (to be used as the key).
            :param val: Property of the object being tracked (to be used as the value).
                        When set to None, val is set to the object itself.
            :returns: A dictionary of property pairs for all objects in key(property1):val(property2)
            """
            if not val:
                return {getattr(k, key):k for k in cls.all}
            
            return {getattr(k, key):getattr(k, val) for k in cls.all}

    src_class = TrackMethods
    src_attrs = {attr_name:attr for attr_name, attr in src_class.__dict__.items() if attr_name[0] != '_'}
    # deliberately overwrite all and cache
    trg_class.all = deepcopy(src_class.all)
    trg_class.cache = deepcopy(src_class.cache)
    for src_attr_name, src_attr in src_attrs.items():
        if not hasattr(trg_class, src_attr_name): # no overwrites
            setattr(trg_class, src_attr_name, src_attr)
    return trg_class

class Tracker:
    """
    Keep track of all instances of objects created by a class.
    This converts a class into a Tracker object. 
    To keep a class as a class, decorate with the tracker function.
    clsToTrack. Decorator. 

    Meant to convert clsToTrack into a Tracker object with properties
    all, n, and methods dictAccess

    TODO:list 
    1. keeping track of all tracked classes is controversial because all
       the tracked objects (formerly classes) know what other classes
       are being tracked. (see cls._tracked)

    :param clsToTrack: keep track of objects created by this class
    :returns: a tracker object

    For operations that span across all objects created by a clsToTrack,
    you can simply create a groupOperations class without __new__,
    __init__ or __call__ functions, and decorate clsToDecorate with that
    class. See tests for an example.

    Example of extending the Tracker class:
    class ImgGroupOps(my.Tracker):
        def __init__(self, clsToTrack):
            super().__init__(clsToTrack)
            self.load()
        
        def load(self):
    """
    _tracked = [] # all the classes being tracked
    def __new__(cls, clsToTrack):
        cls._tracked.append(clsToTrack)
        return object.__new__(cls)
    def __init__(self, clsToTrack):
        self.clsToTrack = clsToTrack
        functools.update_wrapper(self, clsToTrack)
        self.all = []
        self.cache = []
    def __call__(self, *args, **kwargs):
        funcOut = self.clsToTrack(*args, **kwargs)
        self.all.append(funcOut)
        return funcOut
    def __delitem__(self, item):
        """
        Stop tracking item.
        item is an instance of clsToTrack
        """
        self.all.remove(item)

    def dictAccess(self, key='id', val=None):
        """
        Give access to the object based on key. 
        
        Note:
        If keys (id) of different objects are the same, then only the
        last reference will be preserved.

        :param key: Property of the object being tracked (to be used as the key).
        :param val: Property of the object being tracked (to be used as the value).
                    When set to None, val is set to the object itself.
        :returns: A dictionary of property pairs for all objects in key(property1):val(property2)
        """
        if not val:
            return {getattr(k, key):k for k in self.all}
        
        return {getattr(k, key):getattr(k, val) for k in self.all}

    @property
    def n(self):
        """Return the number of instances being tracked."""
        return len(self.all)

    def query(self, queryStr="agent == 'sausage' and accuracy > 0.7", keys=None):
        """
        Filter all tracked objects based on object fields (keys).
        
        :param queryStr: list-comprehension style filter string
        :param keys: list of object keys used in the query
        :returns: a subset of tracked objects that satisfy query criteria
        :raises Warning: prints processed query string if the query fails

        Refer to tests for examples and notes on how to use.
        """
        if not queryStr:
            return self.all

        def parseQuery(queryStr):
            queryUnits = re.split(r'and|or', queryStr)
            queryUnits = [queryUnit.lstrip(' ').lstrip('(').lstrip(' ') for queryUnit in queryUnits]
            for queryUnit in queryUnits:
                if ' in ' not in queryUnit:
                    queryStr = queryStr.replace(queryUnit, 'k.'+queryUnit)

            queryStr = queryStr.replace(' in ', ' in k.')
            return queryStr

        if keys is None:
            queryStr = parseQuery(queryStr)
        else:
            for key in keys:
                queryStr = queryStr.replace(key, 'k.'+key)

        try:
            objList = eval("[k for k in self.all if " + queryStr + "]") #pylint:disable=eval-used
        except Warning:
            print('Query failed.')
            print(queryStr)
        return objList

    def clean(self):
        """Forget the objects tracked so far."""
        self.all = []
    
    def clear_cache(self):
        """Clear cache used by temporary tracking sessions."""
        self.cache = []

    def track_start(self):
        """
        Start a tracking session. Move current objects to cache, and clean.
        Note that objects are tracked even without this method if a class is being tracked.
        Use this to create temporary tracking sessions.
        """
        self.cache = self.all
        self.clean()

    def track_end(self):
        """End tracking session."""
        self.all = self.cache
        self.clear_cache()


class ExComm:
    """
    For communicating with other programs via TCPIP.
    For MATLAB communication, use MATLAB engine!
    """
    def __init__(self, host='localhost', port=50000):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((host, port))
        s.listen(1)
        print("waiting for response from client at port ", port)
        self.host = host
        self.port = port
        self.conn, self.addr = s.accept()
        print('Connected by', self.addr)

    def __pos__(self):
        # listen
        data = self.conn.recv(1024)
        print(data)

    def __call__(self, message=b"hello"):
        # send data
        self.conn.sendall(message)
    
    def __neg__(self):
        # close connection
        self.conn.close()

if os.name == 'nt':
    class Spawn:
        def __init__(self, func):
            self.func = func
        def __call__(self, *args, **kwargs):
            self._q = multiprocess.Queue()
            self._proc = multiprocess.Process(target=self.func, args=(self._q, *args), kwargs=kwargs)
            self._proc.start()
            return self
        def __neg__(self):
            self._q.put('done')
            self._proc.terminate()
        def send(self, msg):
            self._q.put(msg)


def spawn_commands(cmds, nproc=3, verbose=False, retry=False, sleep_time=0.5, wait=True):
    """
    Spawn multiple detached processes. Originally designed for converting videos using ffmpeg.
    cmds is a list of commands, and each command is a list that can be supplied to subprocess.Popen
    """
    n_running = lambda: sum([int(p.poll() is None) for p in all_proc])
    all_proc = []
    cmd_count = 0
    if nproc > len(cmds):
        nproc = len(cmds)

    while True:
        if n_running() < nproc and cmd_count < len(cmds):
            if os.name == 'nt':
                all_proc.append(subprocess.Popen(cmds[cmd_count], shell=True, creationflags=0x00000008))
            else:
                all_proc.append(subprocess.Popen(cmds[cmd_count], stderr=subprocess.STDOUT, stdout=subprocess.PIPE))
            time.sleep(sleep_time)
            if all_proc[-1].poll() == 1 and retry:
                # process exited - probably graphics card out of memory
                all_proc.pop()
                if nproc > 1:
                    nproc -= 1
            else:
                cmd_count += 1
            if verbose:
                print({'Poll': [p.poll() for p in all_proc], 'Running': n_running()})
            if cmd_count == len(cmds):
                break
    
    if wait:
        while n_running() > 0:
            time.sleep(sleep_time)

    return all_proc


## extensions to basic classes
class dotdict(dict):
    """dot.notation access to dictionary attributes"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

class namelist:
    """List of elements where each element has the 'name' field"""
    def __init__(self, data):
        self.data = list(data)
    
    @property
    def names(self):
        return [x.name for x in self.data]
    
    def __getitem__(self, key):
        if key not in self.names:
            if isinstance(key, int):
                return self.data[key]
            else:
                print(self.names)
                raise KeyError
        return {d.name : d for d in self.data}[key]

class nameidlist(namelist):
    """List of elements where each element has 'name' AND 'id' fields."""
    @property
    def ids(self):
        return [x.id for x in self.data]

    def __call__(self, key=None):
        if key is None:
            print(self.ids)
            return
        return {x.id:x for x in self.data}[key]


def find_nearest(x, y):
    """
    Find the nearest x-values for every value in y.
    x and y are expected to be lists of floats.
    Returns:
        List with the same number of values as y, but each value is the closest value in x.
    """
    x = np.asarray(x)
    y = np.asarray(y)
    return [x[np.argmin(np.abs(x - yi))] for yi in y]


## matplotlib-specific stuff
def ticks_from_times(times, tick_lim):
    """Generate x, y arrays to supply to plt.plot function to plot a set of x-values (times) as ticks."""
    def nan_pad_x(inp):
            return [item for x in inp for item in (x, x, np.nan)]
    def nan_pad_y(ylim, n):
        return [item for y1, y2 in [ylim]*n for item in (y1, y2, np.nan)]
    return nan_pad_x(times), nan_pad_y(tick_lim, len(times))

if not BLENDER_MODE:
    import matplotlib as mpl
    mpl.rcParams['lines.linewidth'] = 0.75
    def format_legend(ax):
        """Set the legend labels using 'label' field in plot."""
        box = ax.get_position()
        ax.set_position([box.x0, box.y0, box.width*0.8, box.height])
        ax.legend(loc='center left', bbox_to_anchor=(1, 0.5))
