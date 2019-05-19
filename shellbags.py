#!/usr/bin/python



import re
import sys
import csv
import logging
import datetime
import argparse
import calendar

from Registry import Registry

from BinaryParser import OverrunBufferException
from ShellItems import SHITEMLIST

g_logger = logging.getLogger("shellbags")


def date_safe(d):
    """
    From a Python datetime object, return a corresponding Unix timestamp
    or the epoch timestamp if the datetime object doesn't make sense
    Arguments:
    - `d`: A Python datetime object
    Throws:
    """
    try:
        return int(calendar.timegm(d.timetuple()))
    except (ValueError, OverflowError):
        return int(calendar.timegm(datetime.datetime(1970, 1, 1, 0, 0, 0).timetuple()))


def date_safe_str(d):
    try:
        return d.strftime("%m/%d/%Y %H:%M:%S")
    except:
        return "01/01/1970 00:00:00"


################ CLASS DEFINITIONS #############v

class ShellbagException(Exception):
    """
    Base Exception class for shellbag parsing.
    """
    def __init__(self, value):
        """
        Constructor.
        Arguments:
        - `value`: A string description.
        """
        super(ShellbagException, self).__init__()
        self._value = value

    def __str__(self):
        return str(unicode(self))

    def __unicode__(self):
        return u"Shellbag Exception: %s" % (self._value)


################ PROGRAM FUNCTIONS #############

def get_shellbags(shell_key):
    """
    Given a python-registry RegistryKey object, look for and return a
    list of shellbag items. A shellbag item is a dict with the keys
    (mtime, atime, crtime, path).
    Arguments:
    - `shell_key`: A python-registry Registry object.
    Throws:
    """
    shellbags = []
    bagmru_key = shell_key.subkey("BagMRU")
    bags_key = shell_key.subkey("Bags")

    def shellbag_rec(key, bag_prefix, path_prefix):
        """
        Function to recursively parse the BagMRU Registry key structure.
        Arguments:
        `key`: The current 'BagsMRU' key to recurse into.
        `bag_prefix`: A string containing the current subkey path of
            the relevant 'Bags' key. It will look something like '1\\2\\3\\4'.
        `path_prefix` A string containing the current human-readable,
            file system path so far constructed.
        Throws:
        """
        try:
            # First, consider the current key, and extract shellbag items
            slot = key.value("NodeSlot").value()
            for bag in bags_key.subkey(str(slot)).subkeys():
                for value in [value for value in bag.values() if
                              "ItemPos" in value.name()]:
                    buf = value.value()

                    block = SHITEMLIST(buf, 0x0, False)
                    offset = 0x10

                    while True:
                        offset += 0x8
                        size = block.unpack_word(offset)
                        if size == 0:
                            break
                        elif size < 0x15:
                            pass
                        else:
                            item = block.get_item(offset)
                            shellbags.append({
                                "path": path_prefix + "\\" + item.name(),
                                "mtime": item.m_date(),
                                "atime": item.a_date(),
                                "crtime": item.cr_date(),
                                "source":  bag.path() + " @ " + hex(item.offset()),
                                "regsource": bag.path() + "\\" + value.name(),
                                "klwt": key.timestamp()
                            })
                        offset += size
        except Registry.RegistryValueNotFoundException:
            g_logger.warning("Registry.RegistryValueNotFoundException")
            pass
        except Registry.RegistryKeyNotFoundException:
            g_logger.warning("Registry.RegistryKeyNotFoundException")
            pass
        except:
            g_logger.warning("Unexpected error %s" % sys.exc_info()[0])

        # Next, recurse into each BagMRU key
        for value in [value for value in key.values()
                      if re.match("\d+", value.name())]:
            path = ""
            try:  # TODO(wb): removeme
                l = SHITEMLIST(value.value(), 0, False)
                for item in l.items():
                    # assume there is only one entry in the value, or take the last
                    # as the path component
                    path = path_prefix + "\\" + item.name()
                    shellbags.append({
                        "path":  path,
                        "mtime": item.m_date(),
                        "atime": item.a_date(),
                        "crtime": item.cr_date(),
                        "source": key.path() + " @ " + hex(item.offset()),
                        "regsource": key.path() + "\\" + value.name(),
                        "klwt":  key.timestamp()
                    })
            except OverrunBufferException:
                print key.path()
                print value.name()
                raise

            shellbag_rec(key.subkey(value.name()),
                         bag_prefix + "\\" + value.name(),
                         path)

    shellbag_rec(bagmru_key, "", "")
    return shellbags


def get_all_shellbags(reg):
    """
    Given a python-registry Registry object, look for and return a
    list of shellbag items. A shellbag item is a dict with the keys
    (mtime, atime, crtime, path).
    Arguments:
    - `reg`: A python-registry Registry object.
    Throws:
    """
    shellbags = []
    paths = [
        # xp
        "Software\\Microsoft\\Windows\\Shell",
        "Software\\Microsoft\\Windows\\ShellNoRoam",
        # win7
        "Local Settings\\Software\\Microsoft\\Windows\\ShellNoRoam",
        "Local Settings\\Software\\Microsoft\\Windows\\Shell",
    ]

    for path in paths:
        try:
            shell_key = reg.open(path)
            new = get_shellbags(shell_key)
            shellbags.extend(new)
        except Registry.RegistryKeyNotFoundException:
            pass
        except Exception:
            g_logger.exception("Unhandled exception while parsing %s" % path)

    return shellbags


def print_shellbag_csv(shellbags, regfile):
    stdoutWriter = csv.writer(sys.stdout)
    stdoutWriter.writerow(["Key Last Write Time", "Hive",
                           "Modification Date", "Accessed Date",
                           "Creation Date", "Path", "Key"])
    for shellbag in shellbags:
        modified = date_safe_str(shellbag["mtime"])
        accessed = date_safe_str(shellbag["atime"])
        created = date_safe_str(shellbag["crtime"])
        keymod = date_safe_str(shellbag["klwt"])
        try:
            stdoutWriter.writerow([keymod, regfile, modified,
                                   accessed, created,
                                   shellbag["path"], shellbag["regsource"]])
        except:
            stdoutWriter.writerow([keymod, regfile, modified,
                                   accessed, created, "Unprintable Shellbag",
                                   shellbag["regsource"]])


def print_shellbag_bodyfile(m, a, cr, path, fail_note=None):
    """
    Given the MAC timestamps and a path, print a Bodyfile v3 string entry
    formatted with the data. We print instead of returning so we can handle
    cases where the implicit string encoding conversion takes place as
    things are written to STDOUT.
    Arguments:
    - `m`: A Python datetime object representing the modified date.
    - `a`: A Python datetime object representing the accessed date.
    - `cr`: A Python datetime object representing the created date.
    - `path`: A string with the entry path.
    - `fail_note`: An alternate path to print if an encoding error
         is encountered.
    Throws:
    """
    modified = date_safe(m)
    accessed = date_safe(a)
    created = date_safe(cr)
    changed = int(calendar.timegm(datetime.datetime.min.timetuple()))
    try:
        print u"0|%s (Shellbag)|0|0|0|0|0|%s|%s|%s|%s" % \
            (path, modified, accessed, changed, created)
    except UnicodeDecodeError:
        print u"0|%s (Shellbag)|0|0|0|0|0|%s|%s|%s|%s" % \
            (fail_note, modified, accessed, changed, created)
    except UnicodeEncodeError:
        print u"0|%s (Shellbag)|0|0|0|0|0|%s|%s|%s|%s" % \
            (fail_note, modified, accessed, changed, created)


################ MAIN  #############

def main(argv=None):
    if argv is None:
        argv = sys.argv
        
    parser = argparse.ArgumentParser(description="Parse Shellbag entries from a Windows Registry.")
    parser.add_argument("-v", action="store_true", dest="vverbose",
                        help="Print debugging information while parsing")
    parser.add_argument("file", nargs="+",
                        help="Windows Registry hive file(s)")
    parser.add_argument("-o", choices=["csv", "bodyfile"],
                        dest="fmt", default="bodyfile",
                        help="Output format: csv or bodyfile; default is bodyfile")
    args = parser.parse_args(argv[1:])

    for f in args.file:
        registry = Registry.Registry(f)

        parsed_shellbags = get_all_shellbags(registry)

        if args.fmt == "csv":
            print_shellbag_csv(parsed_shellbags, f)
        elif args.fmt == "bodyfile":
            for shellbag in parsed_shellbags:
                print_shellbag_bodyfile(shellbag["mtime"],
                                        shellbag["atime"],
                                        shellbag["crtime"],
                                        shellbag["path"],
                                        fail_note="Failed to parse entry name from: " + shellbag["source"])
        else:
            print "Error: Unsupported output format"
            
if __name__ == "__main__":
    main(argv=sys.argv)
