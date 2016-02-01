#!/usr/bin/env python
# Copyright 2002 Google Inc. All Rights Reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#     * Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following disclaimer
# in the documentation and/or other materials provided with the
# distribution.
#     * Neither the name of Google Inc. nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Flagvalues module - Registry of 'Flag' objects."""

import logging
import os
import sys
import traceback
import warnings


from gflags import _helpers
from gflags import exceptions
from gflags import flag as _flag
from gflags import validators as gflags_validators

# Add flagvalues module to disclaimed module ids.
_helpers.disclaim_module_ids.add(id(sys.modules[__name__]))

# The MOE directives in this file cause the docstring indentation
# linter to go nuts.
# pylint: disable=g-doc-bad-indent


class FlagValues(object):
  """Registry of 'Flag' objects.

  A 'FlagValues' can then scan command line arguments, passing flag
  arguments through to the 'Flag' objects that it owns.  It also
  provides easy access to the flag values.  Typically only one
  'FlagValues' object is needed by an application: gflags.FLAGS

  This class is heavily overloaded:

  'Flag' objects are registered via __setitem__:
       FLAGS['longname'] = x   # register a new flag

  The .value attribute of the registered 'Flag' objects can be accessed
  as attributes of this 'FlagValues' object, through __getattr__.  Both
  the long and short name of the original 'Flag' objects can be used to
  access its value:
       FLAGS.longname          # parsed flag value
       FLAGS.x                 # parsed flag value (short name)

  Command line arguments are scanned and passed to the registered 'Flag'
  objects through the __call__ method.  Unparsed arguments, including
  argv[0] (e.g. the program name) are returned.
       argv = FLAGS(sys.argv)  # scan command line arguments

  The original registered Flag objects can be retrieved through the use
  of the dictionary-like operator, __getitem__:
       x = FLAGS['longname']   # access the registered Flag object

  The str() operator of a 'FlagValues' object provides help for all of
  the registered 'Flag' objects.
  """

  def __init__(self):
    # Since everything in this class is so heavily overloaded, the only
    # way of defining and using fields is to access __dict__ directly.

    # Dictionary: flag name (string) -> Flag object.
    self.__dict__['__flags'] = {}

    # Set: name of hidden flag (string).
    # Holds flags that should not be directly accessible from Python.
    self.__dict__['__hiddenflags'] = set()

    # Dictionary: module name (string) -> list of Flag objects that are defined
    # by that module.
    self.__dict__['__flags_by_module'] = {}
    # Dictionary: module id (int) -> list of Flag objects that are defined by
    # that module.
    self.__dict__['__flags_by_module_id'] = {}
    # Dictionary: module name (string) -> list of Flag objects that are
    # key for that module.
    self.__dict__['__key_flags_by_module'] = {}

    # Bool: True if flags were parsed.
    self.__dict__['__flags_parsed'] = False

    # Set if we should use new style gnu_getopt rather than getopt when parsing
    # the args.  Only possible with Python 2.3+
    self.UseGnuGetOpt(False)

  def UseGnuGetOpt(self, use_gnu_getopt=True):
    """Use GNU-style scanning. Allows mixing of flag and non-flag arguments.

    See http://docs.python.org/library/getopt.html#getopt.gnu_getopt

    Args:
      use_gnu_getopt: wether or not to use GNU style scanning.
    """
    self.__dict__['__use_gnu_getopt'] = use_gnu_getopt

  def IsGnuGetOpt(self):
    return self.__dict__['__use_gnu_getopt']

  def FlagDict(self):
    return self.__dict__['__flags']

  def FlagsByModuleDict(self):
    """Returns the dictionary of module_name -> list of defined flags.

    Returns:
      A dictionary.  Its keys are module names (strings).  Its values
      are lists of Flag objects.
    """
    return self.__dict__['__flags_by_module']

  def FlagsByModuleIdDict(self):
    """Returns the dictionary of module_id -> list of defined flags.

    Returns:
      A dictionary.  Its keys are module IDs (ints).  Its values
      are lists of Flag objects.
    """
    return self.__dict__['__flags_by_module_id']

  def KeyFlagsByModuleDict(self):
    """Returns the dictionary of module_name -> list of key flags.

    Returns:
      A dictionary.  Its keys are module names (strings).  Its values
      are lists of Flag objects.
    """
    return self.__dict__['__key_flags_by_module']

  def _RegisterFlagByModule(self, module_name, flag):
    """Records the module that defines a specific flag.

    We keep track of which flag is defined by which module so that we
    can later sort the flags by module.

    Args:
      module_name: A string, the name of a Python module.
      flag: A Flag object, a flag that is key to the module.
    """
    flags_by_module = self.FlagsByModuleDict()
    flags_by_module.setdefault(module_name, []).append(flag)

  def _RegisterFlagByModuleId(self, module_id, flag):
    """Records the module that defines a specific flag.

    Args:
      module_id: An int, the ID of the Python module.
      flag: A Flag object, a flag that is key to the module.
    """
    flags_by_module_id = self.FlagsByModuleIdDict()
    flags_by_module_id.setdefault(module_id, []).append(flag)

  def _RegisterKeyFlagForModule(self, module_name, flag):
    """Specifies that a flag is a key flag for a module.

    Args:
      module_name: A string, the name of a Python module.
      flag: A Flag object, a flag that is key to the module.
    """
    key_flags_by_module = self.KeyFlagsByModuleDict()
    # The list of key flags for the module named module_name.
    key_flags = key_flags_by_module.setdefault(module_name, [])
    # Add flag, but avoid duplicates.
    if flag not in key_flags:
      key_flags.append(flag)

  def _GetFlagsDefinedByModule(self, module):
    """Returns the list of flags defined by a module.

    Args:
      module: A module object or a module name (a string).

    Returns:
      A new list of Flag objects.  Caller may update this list as he
      wishes: none of those changes will affect the internals of this
      FlagValue object.
    """
    if not isinstance(module, str):
      module = module.__name__

    return list(self.FlagsByModuleDict().get(module, []))

  def _GetKeyFlagsForModule(self, module):
    """Returns the list of key flags for a module.

    Args:
      module: A module object or a module name (a string)

    Returns:
      A new list of Flag objects.  Caller may update this list as he
      wishes: none of those changes will affect the internals of this
      FlagValue object.
    """
    if not isinstance(module, str):
      module = module.__name__

    # Any flag is a key flag for the module that defined it.  NOTE:
    # key_flags is a fresh list: we can update it without affecting the
    # internals of this FlagValues object.
    key_flags = self._GetFlagsDefinedByModule(module)

    # Take into account flags explicitly declared as key for a module.
    for flag in self.KeyFlagsByModuleDict().get(module, []):
      if flag not in key_flags:
        key_flags.append(flag)
    return key_flags

  def FindModuleDefiningFlag(self, flagname, default=None):
    """Return the name of the module defining this flag, or default.

    Args:
      flagname: Name of the flag to lookup.
      default: Value to return if flagname is not defined. Defaults
          to None.

    Returns:
      The name of the module which registered the flag with this name.
      If no such module exists (i.e. no flag with this name exists),
      we return default.
    """
    for module, flags in self.FlagsByModuleDict().iteritems():
      for flag in flags:
        if flag.name == flagname or flag.short_name == flagname:
          return module
    return default

  def FindModuleIdDefiningFlag(self, flagname, default=None):
    """Return the ID of the module defining this flag, or default.

    Args:
      flagname: Name of the flag to lookup.
      default: Value to return if flagname is not defined. Defaults
          to None.

    Returns:
      The ID of the module which registered the flag with this name.
      If no such module exists (i.e. no flag with this name exists),
      we return default.
    """
    for module_id, flags in self.FlagsByModuleIdDict().iteritems():
      for flag in flags:
        if flag.name == flagname or flag.short_name == flagname:
          return module_id
    return default

  def AppendFlagValues(self, flag_values):
    """Appends flags registered in another FlagValues instance.

    Args:
      flag_values: registry to copy from
    """
    for flag_name, flag in flag_values.FlagDict().iteritems():
      # Each flags with shortname appears here twice (once under its
      # normal name, and again with its short name).  To prevent
      # problems (DuplicateFlagError) with double flag registration, we
      # perform a check to make sure that the entry we're looking at is
      # for its normal name.
      if flag_name == flag.name:
        try:
          self[flag_name] = flag
        except exceptions.DuplicateFlagError:
          raise exceptions.DuplicateFlagError(
              flag_name, self, other_flag_values=flag_values)

  def RemoveFlagValues(self, flag_values):
    """Remove flags that were previously appended from another FlagValues.

    Args:
      flag_values: registry containing flags to remove.
    """
    for flag_name in flag_values.FlagDict():
      self.__delattr__(flag_name)

  def __setitem__(self, name, flag):
    """Registers a new flag variable."""
    fl = self.FlagDict()
    if not isinstance(flag, _flag.Flag):
      raise exceptions.IllegalFlagValue(flag)
    if str is bytes and isinstance(name, unicode):
      # When using Python 2 with unicode_literals, allow it but encode it
      # into the bytes type we require.
      name = name.encode('utf-8')
    if not isinstance(name, type('')):
      raise exceptions.FlagsError('Flag name must be a string')
    if not name:
      raise exceptions.FlagsError('Flag name cannot be empty')
    if name in fl and not flag.allow_override and not fl[name].allow_override:
      module, module_name = _helpers.GetCallingModuleObjectAndName()
      if (self.FindModuleDefiningFlag(name) == module_name and
          id(module) != self.FindModuleIdDefiningFlag(name)):
        # If the flag has already been defined by a module with the same name,
        # but a different ID, we can stop here because it indicates that the
        # module is simply being imported a subsequent time.
        return
      raise exceptions.DuplicateFlagError(name, self)
    short_name = flag.short_name
    if short_name is not None:
      if (short_name in fl and not flag.allow_override and
          not fl[short_name].allow_override):
        raise exceptions.DuplicateFlagError(short_name, self)
      fl[short_name] = flag
    if (name not in fl  # new flag
        or fl[name].using_default_value
        or not flag.using_default_value):
      fl[name] = flag

  def __dir__(self):
    """Returns list of names of all defined flags.

    Useful for TAB-completion in ipython.

    Returns:
      list(str)
    """
    return sorted(self.__dict__['__flags'])

  # TODO(olexiy): Call GetFlag() to raise UnrecognizedFlagError if name is
  # unknown.
  def __getitem__(self, name):
    """Retrieves the Flag object for the flag --name."""
    return self.FlagDict()[name]

  def GetFlag(self, name):
    """Same as __getitem__, but raises a specific error."""
    res = self.FlagDict().get(name)
    if res is None:
      raise exceptions.UnrecognizedFlagError(name)
    return res

  def HideFlag(self, name):
    """Mark the flag --name as hidden."""
    self.__dict__['__hiddenflags'].add(name)

  def __getattr__(self, name):
    """Retrieves the 'value' attribute of the flag --name."""
    fl = self.FlagDict()
    if name not in fl:
      raise AttributeError(name)
    if name in self.__dict__['__hiddenflags']:
      raise AttributeError(name)

    if self.__dict__['__flags_parsed'] or fl[name].present:
      return fl[name].value
    else:
      # Trying to use the flag before FlagValues object got a chance to parse
      # arguments.
      # Doing that results in __getattr__ returning default value of the flag,
      # no matter what was given in the arguments.

      # Note: if you are using hasattr() and seeing this error, please use
      # 'flag_name not in FLAGS.FlagDict()' instead of 'hasattr(flag_name)'.
      # Unfortunately hasattr() implemented via calling getattr and there's no
      # reliable way to determine hasattr() vs getattr().
      try:
        error_message = (
            'Trying to access flag %s before flags were parsed.' % name)
        warnings.warn(
            error_message + ' This will raise an exception in the future.',
            RuntimeWarning,
            stacklevel=2)
        traceback.print_stack()
        raise exceptions.UnparsedFlagAccessError(error_message)
      except exceptions.UnparsedFlagAccessError:
        if os.getenv('GFLAGS_ALLOW_UNPARSED_FLAG_ACCESS', '1') == '1':
          logging.exception(error_message)
          return fl[name].value
        else:
          raise

  def __setattr__(self, name, value):
    """Sets the 'value' attribute of the flag --name."""
    fl = self.FlagDict()
    if name in self.__dict__['__hiddenflags']:
      raise AttributeError(name)
    fl[name].value = value
    fl[name].using_default_value = False
    self._AssertValidators(fl[name].validators)
    return value

  def _AssertAllValidators(self):
    all_validators = set()
    for flag in self.FlagDict().itervalues():
      for validator in flag.validators:
        all_validators.add(validator)
    self._AssertValidators(all_validators)

  def _AssertValidators(self, validators):
    """Assert if all validators in the list are satisfied.

    Asserts validators in the order they were created.
    Args:
      validators: Iterable(gflags_validators.Validator), validators to be
        verified
    Raises:
      AttributeError: if validators work with a non-existing flag.
      IllegalFlagValue: if validation fails for at least one validator
    """
    for validator in sorted(
        validators, key=lambda validator: validator.insertion_index):
      try:
        validator.Verify(self)
      except gflags_validators.Error as e:
        message = validator.PrintFlagsWithValues(self)
        raise exceptions.IllegalFlagValue('%s: %s' % (message, str(e)))

  def _FlagIsRegistered(self, flag_obj):
    """Checks whether a Flag object is registered under some name.

    Note: this is non trivial: in addition to its normal name, a flag
    may have a short name too.  In self.FlagDict(), both the normal and
    the short name are mapped to the same flag object.  E.g., calling
    only "del FLAGS.short_name" is not unregistering the corresponding
    Flag object (it is still registered under the longer name).

    Args:
      flag_obj: A Flag object.

    Returns:
      A boolean: True iff flag_obj is registered under some name.
    """
    flag_dict = self.FlagDict()
    # Check whether flag_obj is registered under its long name.
    name = flag_obj.name
    if flag_dict.get(name, None) == flag_obj:
      return True
    # Check whether flag_obj is registered under its short name.
    short_name = flag_obj.short_name
    if (short_name is not None and
        flag_dict.get(short_name, None) == flag_obj):
      return True
    # The flag cannot be registered under any other name, so we do not
    # need to do a full search through the values of self.FlagDict().
    return False

  def __delattr__(self, flag_name):
    """Deletes a previously-defined flag from a flag object.

    This method makes sure we can delete a flag by using

      del flag_values_object.<flag_name>

    E.g.,

      gflags.DEFINE_integer('foo', 1, 'Integer flag.')
      del gflags.FLAGS.foo

    Args:
      flag_name: A string, the name of the flag to be deleted.

    Raises:
      AttributeError: When there is no registered flag named flag_name.
    """
    fl = self.FlagDict()
    if flag_name not in fl:
      raise AttributeError(flag_name)

    flag_obj = fl[flag_name]
    del fl[flag_name]

    if not self._FlagIsRegistered(flag_obj):
      # If the Flag object indicated by flag_name is no longer
      # registered (please see the docstring of _FlagIsRegistered), then
      # we delete the occurrences of the flag object in all our internal
      # dictionaries.
      self.__RemoveFlagFromDictByModule(self.FlagsByModuleDict(), flag_obj)
      self.__RemoveFlagFromDictByModule(self.FlagsByModuleIdDict(), flag_obj)
      self.__RemoveFlagFromDictByModule(self.KeyFlagsByModuleDict(), flag_obj)

  def __RemoveFlagFromDictByModule(self, flags_by_module_dict, flag_obj):
    """Removes a flag object from a module -> list of flags dictionary.

    Args:
      flags_by_module_dict: A dictionary that maps module names to lists of
        flags.
      flag_obj: A flag object.
    """
    for unused_module, flags_in_module in flags_by_module_dict.iteritems():
      # while (as opposed to if) takes care of multiple occurrences of a
      # flag in the list for the same module.
      while flag_obj in flags_in_module:
        flags_in_module.remove(flag_obj)

  def SetDefault(self, name, value):
    """Changes the default value of the named flag object."""
    fl = self.FlagDict()
    if name not in fl:
      raise AttributeError(name)
    fl[name].SetDefault(value)
    self._AssertValidators(fl[name].validators)

  def __contains__(self, name):
    """Returns True if name is a value (flag) in the dict."""
    return name in self.FlagDict()

  has_key = __contains__  # a synonym for __contains__()

  def __iter__(self):
    return iter(self.FlagDict())

  def __call__(self, argv):
    """Parses flags from argv; stores parsed flags into this FlagValues object.

    All unparsed arguments are returned.

    Args:
       argv: argument list. Can be of any type that may be converted to a list.

    Returns:
       The list of arguments not parsed as options, including argv[0].

    Raises:
       FlagsError: on any parsing error.
    """
    if not argv:
      # Unfortunately, the old parser used to accept an empty argv, and some
      # users rely on that behaviour. Allow it as a special case for now.
      self.MarkAsParsed()
      self._AssertAllValidators()
      return []

    # This pre parses the argv list for --flagfile=<> options.
    program_name = argv[0]
    args = self.ReadFlagsFromFiles(argv[1:], force_gnu=False)

    # Parse the arguments.
    unknown_flags, unparsed_args, undefok = self._ParseArgs(args)

    # Handle unknown flags by raising UnrecognizedFlagError.
    # Note some users depend on us raising this particular error.
    for name, value in unknown_flags:
      if name in undefok:
        continue

      suggestions = _helpers.GetFlagSuggestions(
          name, self.RegisteredFlags())
      raise exceptions.UnrecognizedFlagError(
          name, value, suggestions=suggestions)

    self.MarkAsParsed()
    self._AssertAllValidators()
    return [program_name] + unparsed_args

  def _ParseArgs(self, args):
    """Helper function to do the main argument parsing.

    This function goes through args and does the bulk of the flag parsing.
    It will find the corresponding flag in our flag dictionary, and call its
    .Parse() method on the flag value.

    Args:
      args: List of strings with the arguments to parse.

    Returns:
      A tuple with the following:
        unknown_flags: List of (flag name, arg) for flags we don't know about.
        unparsed_args: List of arguments we did not parse.
        undefok: Set of flags that were given via --undefok.
    """
    unknown_flags, unparsed_args, undefok = [], [], set()

    flag_dict = self.FlagDict()
    i = 0
    while i < len(args):
      arg = args[i]
      i += 1

      if not arg.startswith('-'):
        # A non-argument: default is break, GNU is skip.
        unparsed_args.append(arg)
        if self.IsGnuGetOpt():
          continue
        else:
          break

      if arg == '--':
        break

      if '=' in arg:
        name, value = arg.lstrip('-').split('=', 1)
      else:
        name, value = arg.lstrip('-'), None

      if not name:
        # The argument is all dashes (including one dash).
        unparsed_args.append(arg)
        if self.IsGnuGetOpt():
          continue
        else:
          break

      # --undefok is a special case.
      if name == 'undefok':
        if value is None:
          try:
            value = args[i]
            i += 1
          except IndexError:
            raise exceptions.FlagsError('Missing value for flag %s' % arg)

        undefok.update(v.strip() for v in value.split(','))
        undefok.update('no' + v.strip() for v in value.split(','))
        continue

      if name in flag_dict:
        flag = flag_dict[name]
        if flag.boolean and value is None:
          # Boolean flags can take the form of --flag, with no value.
          value = True
        else:
          if value is None:
            # The value is the next argument.
            try:
              value = args[i]
              i += 1
            except IndexError:
              raise exceptions.FlagsError('Missing value for flag %s' % arg)
      else:
        # Boolean flags can take the form of --noflag, with no value.
        noflag = None
        if name.startswith('no'):
          noflag = flag_dict.get(name[2:], None)

        if noflag and noflag.boolean:
          flag = noflag
          value = False
        else:
          unknown_flags.append((name, arg))
          continue

      flag.Parse(value)
      flag.using_default_value = False

    unparsed_args.extend(args[i:])
    return unknown_flags, unparsed_args, undefok

  def IsParsed(self):
    """Whether flags were parsed."""
    return self.__dict__['__flags_parsed']

  def MarkAsParsed(self):
    """Explicitly mark parsed.

    Use this when the caller knows that this FlagValues has been parsed as if
    a __call__() invocation has happened.  This is only a public method for
    use by things like appcommands which do additional command like parsing.
    """
    self.__dict__['__flags_parsed'] = True

  def Reset(self):
    """Resets the values to the point before FLAGS(argv) was called."""
    for f in self.FlagDict().values():
      f.Unparse()
    self.__dict__['__flags_parsed'] = False

  def RegisteredFlags(self):
    """Returns: a list of the names and short names of all registered flags."""
    return list(self.FlagDict())

  def FlagValuesDict(self):
    """Returns: a dictionary that maps flag names to flag values."""
    flag_values = {}

    for flag_name in self.RegisteredFlags():
      flag = self.FlagDict()[flag_name]
      flag_values[flag_name] = flag.value

    return flag_values

  def __str__(self):
    """Generates a help string for all known flags."""
    return self.GetHelp()

  def GetHelp(self, prefix='', include_special_flags=True):
    """Generates a help string for all known flags.

    Args:
      prefix: str, per-line output prefix.
      include_special_flags: bool, whether to include description of
        _SPECIAL_FLAGS, i.e. --flagfile and --undefok.

    Returns:
      str, formatted help message.
    """
    # TODO(vrusinov): this function needs a test.
    helplist = []

    flags_by_module = self.FlagsByModuleDict()
    if flags_by_module:
      modules = sorted(flags_by_module)

      # Print the help for the main module first, if possible.
      main_module = _helpers.GetMainModule()
      if main_module in modules:
        modules.remove(main_module)
        modules = [main_module] + modules

      for module in modules:
        self.__RenderOurModuleFlags(module, helplist)
      if include_special_flags:
        self.__RenderModuleFlags('gflags',
                                 _helpers.SPECIAL_FLAGS.FlagDict().values(),
                                 helplist)
    else:
      # Just print one long list of flags.
      values = self.FlagDict().values()
      if include_special_flags:
        values.append(_helpers.SPECIAL_FLAGS.FlagDict().values())
      self.__RenderFlagList(values, helplist, prefix)

    return '\n'.join(helplist)

  def __RenderModuleFlags(self, module, flags, output_lines, prefix=''):
    """Generates a help string for a given module."""
    if not isinstance(module, str):
      module = module.__name__
    output_lines.append('\n%s%s:' % (prefix, module))
    self.__RenderFlagList(flags, output_lines, prefix + '  ')

  def __RenderOurModuleFlags(self, module, output_lines, prefix=''):
    """Generates a help string for a given module."""
    flags = self._GetFlagsDefinedByModule(module)
    if flags:
      self.__RenderModuleFlags(module, flags, output_lines, prefix)

  def __RenderOurModuleKeyFlags(self, module, output_lines, prefix=''):
    """Generates a help string for the key flags of a given module.

    Args:
      module: A module object or a module name (a string).
      output_lines: A list of strings.  The generated help message
        lines will be appended to this list.
      prefix: A string that is prepended to each generated help line.
    """
    key_flags = self._GetKeyFlagsForModule(module)
    if key_flags:
      self.__RenderModuleFlags(module, key_flags, output_lines, prefix)

  def ModuleHelp(self, module):
    """Describe the key flags of a module.

    Args:
      module: A module object or a module name (a string).

    Returns:
      string describing the key flags of a module.
    """
    helplist = []
    self.__RenderOurModuleKeyFlags(module, helplist)
    return '\n'.join(helplist)

  def MainModuleHelp(self):
    """Describe the key flags of the main module.

    Returns:
      string describing the key flags of a module.
    """
    return self.ModuleHelp(_helpers.GetMainModule())

  def __RenderFlagList(self, flaglist, output_lines, prefix='  '):
    fl = self.FlagDict()
    special_fl = _helpers.SPECIAL_FLAGS.FlagDict()
    flaglist = [(flag.name, flag) for flag in flaglist]
    flaglist.sort()
    flagset = {}
    for (name, flag) in flaglist:
      # It's possible this flag got deleted or overridden since being
      # registered in the per-module flaglist.  Check now against the
      # canonical source of current flag information, the FlagDict.
      if fl.get(name, None) != flag and special_fl.get(name, None) != flag:
        # a different flag is using this name now
        continue
      # only print help once
      if flag in flagset: continue
      flagset[flag] = 1
      flaghelp = ''
      if flag.short_name: flaghelp += '-%s,' % flag.short_name
      if flag.boolean:
        flaghelp += '--[no]%s:' % flag.name
      else:
        flaghelp += '--%s:' % flag.name
      flaghelp += ' '
      if flag.help:
        flaghelp += flag.help
      flaghelp = _helpers.TextWrap(
          flaghelp, indent=prefix+'  ', firstline_indent=prefix)
      if flag.default_as_str:
        flaghelp += '\n'
        flaghelp += _helpers.TextWrap(
            '(default: %s)' % flag.default_as_str, indent=prefix+'  ')
      if flag.parser.syntactic_help:
        flaghelp += '\n'
        flaghelp += _helpers.TextWrap(
            '(%s)' % flag.parser.syntactic_help, indent=prefix+'  ')
      output_lines.append(flaghelp)

  def get(self, name, default):  # pylint: disable=invalid-name
    """Returns the value of a flag (if not None) or a default value.

    Args:
      name: A string, the name of a flag.
      default: Default value to use if the flag value is None.

    Returns:
      Requested flag value or default.
    """

    value = self.__getattr__(name)
    if value is not None:  # Can't do if not value, b/c value might be '0' or ""
      return value
    else:
      return default

  def ShortestUniquePrefixes(self, fl):
    """Returns: dictionary; maps flag names to their shortest unique prefix."""
    # Sort the list of flag names
    sorted_flags = []
    for name, flag in fl.items():
      sorted_flags.append(name)
      if flag.boolean:
        sorted_flags.append('no%s' % name)
    sorted_flags.sort()

    # For each name in the sorted list, determine the shortest unique
    # prefix by comparing itself to the next name and to the previous
    # name (the latter check uses cached info from the previous loop).
    shortest_matches = {}
    prev_idx = 0
    for flag_idx in xrange(len(sorted_flags)):
      curr = sorted_flags[flag_idx]
      if flag_idx == (len(sorted_flags) - 1):
        next_flag = None
      else:
        next_flag = sorted_flags[flag_idx+1]
        next_flag_len = len(next_flag)
      for curr_idx in xrange(len(curr)):
        if (next_flag is None
            or curr_idx >= next_flag_len
            or curr[curr_idx] != next_flag[curr_idx]):
          # curr longer than next or no more chars in common
          shortest_matches[curr] = curr[:max(prev_idx, curr_idx) + 1]
          prev_idx = curr_idx
          break
      else:
        # curr shorter than (or equal to) next
        shortest_matches[curr] = curr
        prev_idx = curr_idx + 1  # next will need at least one more char
    return shortest_matches

  def __IsFlagFileDirective(self, flag_string):
    """Checks whether flag_string contain a --flagfile=<foo> directive."""
    if isinstance(flag_string, type('')):
      if flag_string.startswith('--flagfile='):
        return 1
      elif flag_string == '--flagfile':
        return 1
      elif flag_string.startswith('-flagfile='):
        return 1
      elif flag_string == '-flagfile':
        return 1
      else:
        return 0
    return 0

  def ExtractFilename(self, flagfile_str):
    """Returns filename from a flagfile_str of form -[-]flagfile=filename.

    The cases of --flagfile foo and -flagfile foo shouldn't be hitting
    this function, as they are dealt with in the level above this
    function.

    Args:
      flagfile_str: flagfile string.

    Returns:
      str filename from a flagfile_str of form -[-]flagfile=filename.

    Raises:
      FlagsError: when illegal --flagfile provided.
    """
    if flagfile_str.startswith('--flagfile='):
      return os.path.expanduser((flagfile_str[(len('--flagfile=')):]).strip())
    elif flagfile_str.startswith('-flagfile='):
      return os.path.expanduser((flagfile_str[(len('-flagfile=')):]).strip())
    else:
      raise exceptions.FlagsError(
          'Hit illegal --flagfile type: %s' % flagfile_str)

  def __GetFlagFileLines(self, filename, parsed_file_stack=None):
    """Returns the useful (!=comments, etc) lines from a file with flags.

    Args:
      filename: A string, the name of the flag file.
      parsed_file_stack: A list of the names of the files that we have
        recursively encountered at the current depth. MUTATED BY THIS FUNCTION
        (but the original value is preserved upon successfully returning from
        function call).

    Returns:
      List of strings. See the note below.

    NOTE(springer): This function checks for a nested --flagfile=<foo>
    tag and handles the lower file recursively. It returns a list of
    all the lines that _could_ contain command flags. This is
    EVERYTHING except whitespace lines and comments (lines starting
    with '#' or '//').
    """
    if parsed_file_stack is None:
      parsed_file_stack = []
    # We do a little safety check for reparsing a file we've already encountered
    # at a previous depth.
    if filename in parsed_file_stack:
      sys.stderr.write('Warning: Hit circular flagfile dependency. Ignoring'
                       ' flagfile: %s\n' % (filename,))
      return []
    else:
      parsed_file_stack.append(filename)

    line_list = []  # All line from flagfile.
    flag_line_list = []  # Subset of lines w/o comments, blanks, flagfile= tags.
    try:
      file_obj = open(filename, 'r')
    except IOError as e_msg:
      raise exceptions.CantOpenFlagFileError(
          'ERROR:: Unable to open flagfile: %s' % e_msg)

    with file_obj:
      line_list = file_obj.readlines()

    # This is where we check each line in the file we just read.
    for line in line_list:
      if line.isspace():
        pass
      # Checks for comment (a line that starts with '#').
      elif line.startswith('#') or line.startswith('//'):
        pass
      # Checks for a nested "--flagfile=<bar>" flag in the current file.
      # If we find one, recursively parse down into that file.
      elif self.__IsFlagFileDirective(line):
        sub_filename = self.ExtractFilename(line)
        included_flags = self.__GetFlagFileLines(
            sub_filename, parsed_file_stack=parsed_file_stack)
        flag_line_list.extend(included_flags)
      else:
        # Any line that's not a comment or a nested flagfile should get
        # copied into 2nd position.  This leaves earlier arguments
        # further back in the list, thus giving them higher priority.
        flag_line_list.append(line.strip())

    parsed_file_stack.pop()
    return flag_line_list

  def ReadFlagsFromFiles(self, argv, force_gnu=True):
    """Processes command line args, but also allow args to be read from file.

    Args:
      argv: A list of strings, usually sys.argv[1:], which may contain one or
        more flagfile directives of the form --flagfile="./filename".
        Note that the name of the program (sys.argv[0]) should be omitted.
      force_gnu: If False, --flagfile parsing obeys normal flag semantics.
        If True, --flagfile parsing instead follows gnu_getopt semantics.
        *** WARNING *** force_gnu=False may become the future default!

    Returns:
      A new list which has the original list combined with what we read
      from any flagfile(s).

    Raises:
      IllegalFlagValue: when --flagfile provided with no argument.

    References: Global gflags.FLAG class instance.

    This function should be called before the normal FLAGS(argv) call.
    This function scans the input list for a flag that looks like:
    --flagfile=<somefile>. Then it opens <somefile>, reads all valid key
    and value pairs and inserts them into the input list in exactly the
    place where the --flagfile arg is found.

    Note that your application's flags are still defined the usual way
    using gflags DEFINE_flag() type functions.

    Notes (assuming we're getting a commandline of some sort as our input):
    --> For duplicate flags, the last one we hit should "win".
    --> Since flags that appear later win, a flagfile's settings can be "weak"
        if the --flagfile comes at the beginning of the argument sequence,
        and it can be "strong" if the --flagfile comes at the end.
    --> A further "--flagfile=<otherfile.cfg>" CAN be nested in a flagfile.
        It will be expanded in exactly the spot where it is found.
    --> In a flagfile, a line beginning with # or // is a comment.
    --> Entirely blank lines _should_ be ignored.
    """
    rest_of_args = argv
    new_argv = []
    while rest_of_args:
      current_arg = rest_of_args[0]
      rest_of_args = rest_of_args[1:]
      if self.__IsFlagFileDirective(current_arg):
        # This handles the case of -(-)flagfile foo.  In this case the
        # next arg really is part of this one.
        if current_arg == '--flagfile' or current_arg == '-flagfile':
          if not rest_of_args:
            raise exceptions.IllegalFlagValue('--flagfile with no argument')
          flag_filename = os.path.expanduser(rest_of_args[0])
          rest_of_args = rest_of_args[1:]
        else:
          # This handles the case of (-)-flagfile=foo.
          flag_filename = self.ExtractFilename(current_arg)
        new_argv.extend(self.__GetFlagFileLines(flag_filename))
      else:
        new_argv.append(current_arg)
        # Stop parsing after '--', like getopt and gnu_getopt.
        if current_arg == '--':
          break
        # Stop parsing after a non-flag, like getopt.
        if not current_arg.startswith('-'):
          if not force_gnu and not self.__dict__['__use_gnu_getopt']:
            break
        else:
          if ('=' not in current_arg and
              rest_of_args and not rest_of_args[0].startswith('-')):
            # If this is an occurence of a legitimate --x y, skip the value
            # so that it won't be mistaken for a standalone arg.
            fl = self.FlagDict()
            name = current_arg.lstrip('-')
            if name in fl and not fl[name].boolean:
              current_arg = rest_of_args[0]
              rest_of_args = rest_of_args[1:]
              new_argv.append(current_arg)

    if rest_of_args:
      new_argv.extend(rest_of_args)

    return new_argv

  def FlagsIntoString(self):
    """Returns a string with the flags assignments from this FlagValues object.

    This function ignores flags whose value is None.  Each flag
    assignment is separated by a newline.

    NOTE: MUST mirror the behavior of the C++ CommandlineFlagsIntoString
    from http://code.google.com/p/google-gflags

    Returns:
      string with the flags assignments from this FlagValues object.
    """
    s = ''
    for flag in self.FlagDict().values():
      if flag.value is not None:
        s += flag.Serialize() + '\n'
    return s

  def AppendFlagsIntoFile(self, filename):
    """Appends all flags assignments from this FlagInfo object to a file.

    Output will be in the format of a flagfile.

    NOTE: MUST mirror the behavior of the C++ AppendFlagsIntoFile
    from http://code.google.com/p/google-gflags

    Args:
      filename: string, name of the file.
    """
    with open(filename, 'a') as out_file:
      out_file.write(self.FlagsIntoString())

  def WriteHelpInXMLFormat(self, outfile=None):
    """Outputs flag documentation in XML format.

    NOTE: We use element names that are consistent with those used by
    the C++ command-line flag library, from
    http://code.google.com/p/google-gflags
    We also use a few new elements (e.g., <key>), but we do not
    interfere / overlap with existing XML elements used by the C++
    library.  Please maintain this consistency.

    Args:
      outfile: File object we write to.  Default None means sys.stdout.
    """
    outfile = outfile or sys.stdout

    outfile.write('<?xml version=\"1.0\"?>\n')
    outfile.write('<AllFlags>\n')
    indent = '  '
    _helpers.WriteSimpleXMLElement(outfile, 'program',
                                   os.path.basename(sys.argv[0]), indent)

    usage_doc = sys.modules['__main__'].__doc__
    if not usage_doc:
      usage_doc = '\nUSAGE: %s [flags]\n' % sys.argv[0]
    else:
      usage_doc = usage_doc.replace('%s', sys.argv[0])
    _helpers.WriteSimpleXMLElement(outfile, 'usage', usage_doc, indent)

    # Get list of key flags for the main module.
    key_flags = self._GetKeyFlagsForModule(_helpers.GetMainModule())

    # Sort flags by declaring module name and next by flag name.
    flags_by_module = self.FlagsByModuleDict()
    all_module_names = list(flags_by_module.keys())
    all_module_names.sort()
    for module_name in all_module_names:
      flag_list = [(f.name, f) for f in flags_by_module[module_name]]
      flag_list.sort()
      for unused_flag_name, flag in flag_list:
        is_key = flag in key_flags
        flag.WriteInfoInXMLFormat(outfile, module_name,
                                  is_key=is_key, indent=indent)

    outfile.write('</AllFlags>\n')
    outfile.flush()

  def AddValidator(self, validator):
    """Register new flags validator to be checked.

    Args:
      validator: gflags_validators.Validator
    Raises:
      AttributeError: if validators work with a non-existing flag.
    """
    for flag_name in validator.GetFlagsNames():
      self.GetFlag(flag_name).validators.append(validator)

_helpers.SPECIAL_FLAGS = FlagValues()
