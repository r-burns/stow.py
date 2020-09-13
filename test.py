#!/usr/bin/env python
import json, os, shutil, subprocess, sys, unittest
import jsondirs
import stow

base = os.path.dirname(os.path.realpath(__file__))  # my real location
tmpdir = os.path.join(base, "test-tmp")

verbosity = "1"
vflag = "--verbose=" + verbosity

# N.B. I'm doing this rather than fixture tearDown so that I can inspect
# the generated dir structures used by the tests. Is there a way to
# manually disable the tearDown methods?
def cleanup():
    if os.path.isdir(tmpdir):
        shutil.rmtree(tmpdir)


cleanup()

plstow_exe = os.environ.get("GNU_STOW", "stow")
plstow = lambda args: subprocess.check_call([plstow_exe, vflag] + args.split())
pystow = lambda args: stow.run_with_args([vflag] + args.split())


def compareTest(name, file, args, stowdir="stow"):
    """
    A test class which will:
      * Set up a directory structure from a chosen file
      * Run GNU stow and python stow on copies of this dir structure
      * Verify that both programs produced the same result
    """

    if type(args) != list:
        args = [args]

    dstdir = os.path.join(tmpdir, name)
    file = os.path.join("tests", file)

    # form testing paths
    a = os.path.join(dstdir, "pl")
    b = os.path.join(dstdir, "py")
    asub = os.path.join(a, stowdir)
    bsub = os.path.join(b, stowdir)

    class CompareTest(unittest.TestCase):
        def setUp(self):
            for dir in (a, b):
                jsondirs.load(file, dir)

        def test(self):
            for argset in args:
                # Run each program in its own subdir
                for prog, subdir in ((plstow, asub), (pystow, bsub)):
                    with stow.cd(subdir):
                        prog(argset)
                # Compare results using recursive diff
                subprocess.check_call(["diff", "-r", a, b])

    return CompareTest


adopt = compareTest("adopt", "conflict.json", "--adopt pkg")
simple = compareTest("simple", "simple.json", "pkg")
twice = compareTest("twice", "simple.json", "pkg pkg")
ignore = compareTest("ignore", "simple.json", "pkg --ignore file")
restow = compareTest("restow", "simple.json", "-R pkg")
unstow = compareTest("unstow", "simple.json", ["pkg", "-D pkg"])
unfold = compareTest("unfold", "unfold.json", ["pkg1 pkg2", "-D pkg1 pkg2"])
abslink = compareTest("abslink", "abslink.json", ["-D pkg"])

if __name__ == "__main__":
    unittest.main()
