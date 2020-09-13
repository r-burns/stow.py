#!/usr/bin/env python
"""
Helper used by test code. Creates simple directory trees from json files.
This script is also set up for use as a CLI script to simplify creating
new test case json files; see the argparse usage help for more info.
"""

from __future__ import print_function
from stow import cd
import json, os

# designate that a file is a link
# when its contents start with this string
linkmark = "-> "


def fstree(root):
    with cd(root):
        results = {}
        for dir, _, files in os.walk("."):
            # recursively add this directory to the dict
            curr = results
            for p in dir.split(os.sep):
                if p != ".":
                    curr = curr.setdefault(p, {})
            # add entries for each file
            for f in files:
                with cd(dir):
                    if os.path.islink(f):
                        curr[f] = linkmark + os.path.relpath(
                            os.path.realpath(f), "."
                        )
                    else:
                        with open(f, "r") as txt:
                            curr[f] = txt.read()
        return results


def mktree_here(tree):
    for path in tree:
        contents = tree[path]
        if type(contents) is dict:
            os.mkdir(path)
            with cd(path):
                mktree_here(contents)
        else:
            if contents.startswith("->"):
                os.symlink(contents[len(linkmark) :], path)
            else:
                with open(path, "w") as f:
                    f.write(contents)


def mktree(dict, dir="."):
    os.makedirs(dir)
    with cd(dir):
        mktree_here(dict)


def load(file, dir="."):
    with open(file) as f:
        dict = json.load(f)
    mktree(dict, dir)


if __name__ == "__main__":
    import argparse, sys

    example = """
    Example usage: {} my/test/basedir/ > my_test_case.json
    """.format(
        sys.argv[0]
    )
    parser = argparse.ArgumentParser(epilog=example)
    parser.add_argument("dir", help="Directory tree to convert to json")
    args = parser.parse_args()
    print(json.dumps(fstree(args.dir), indent=4))
