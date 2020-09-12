#!/usr/bin/env python
"""
Copyright (C) 1993, 1994, 1995, 1996 by Bob Glickstein
Copyright (C) 2000, 2001 Guillaume Morin
Copyright (C) 2007 Kahlil Hodgson
Copyright (C) 2011 Adam Spiers
Copyright (C) 2020 Ryan Burns

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""


from __future__ import print_function
from collections import namedtuple
from contextlib import contextmanager
from warnings import warn
import inspect, os, re, sys

version = "0.0.1"

debug_level = 0
test_mode = True

def join_paths(*args):
    return os.path.normpath(os.path.join(*args))

class Task:
    class Link:
        def __init__(self, action, type, source, path):
            self.action = action
            self.type = type
            self.source = source
            self.path = path

    class Dir:
        def __init__(self, action, type, path):
            self.action = action
            self.type = type
            self.path = path

    class Mv:
        action = "move"
        type = "file"
        def __init__(self, path, dest):
            self.path = path
            self.dest = dest

def debug(level, msg):
    if debug_level < level:
        return

    if test_mode:
        print(msg)
    else:
        warn(msg)

def debug_fn(level, msg = "", indent = 0):
    if debug_level < level:
        return

    caller = inspect.currentframe().f_back
    prefix = "  " * indent
    if caller:
        prefix += caller.f_code.co_name
        prefix += inspect.formatargvalues(*inspect.getargvalues(caller))
        prefix += " "
        del caller

    debug(level, prefix + msg)

@contextmanager
def cd(path):
    old_dir = os.getcwd()
    os.chdir(path)
    debug(3, "cwd now " + os.getcwd())
    yield
    os.chdir(old_dir)
    debug(3, "cwd restored to " + old_dir)

class Stow:

    dotfiles = False
    no_folding = False
    ignores = []
    defers = []
    overrides = []

    action_count = 0
    conflict_count = 0

    def __repr__(self):
        return "Stow"

    def __init__(self, target, dir=".", verbose=0, ignore=[], adopt=False):
        self.adopt=adopt
        self.ignores = ignore
        self.target = target
        self.set_stow_dir(dir)
        self.link_task_for = {}
        self.dir_task_for = {}
        self.tasks = []
        self.conflicts = {
            "stow": {},
            "unstow": {},
        }
        global debug_level
        debug_level = verbose

    def set_stow_dir(self, dir):
        self.dir = dir

        stow_dir = os.path.realpath(dir)
        target = os.path.realpath(self.target)
        self.stow_path = os.path.relpath(stow_dir, target)

        debug(2, "stow dir is " + stow_dir)
        debug(2, "stow dir path relative to target {} is {}".format(
            target, self.stow_path))

    def package_path(self, package):
        path = join_paths(self.stow_path, package)
        if not os.path.isdir(path):
            raise RuntimeError("The stow directory " + self.stow_path +
                    " does not contain package " + package)
        return path

    def plan_unstow(self, packages):
        with cd(self.target):
            for package in packages:
                debug(2, "Planning unstow of package " + package + "...")
                self.unstow_contents(self.stow_path, package, ".")
                debug(2, "Planning unstow of package " + package + "... done")
                self.action_count += 1

    def plan_stow(self, packages):
        with cd(self.target):
            for package in packages:
                debug(2, "Planning stow of package " + package + "...")
                path = self.package_path(package)
                self.stow_contents(self.stow_path, package, ".", path)
                debug(2, "Planning stow of package " + package + "... done")
                self.action_count += 1

    def process_tasks(self):
        debug(2, "Processing tasks...")

        # Strip out all tasks with a skip action
        self.tasks = [x for x in self.tasks if x.action != "skip"]

        if self.tasks:
            with cd(self.target):
                for task in self.tasks:
                    self.process_task(task)

        debug(2, "Processing tasks... done")

    def process_task(self, task):
        if task.action == "create":
            if task.type == "dir":
                os.mkdir(task.path)
                return
            elif task.type == "link":
                os.symlink(task.source, task.path)
                return
        elif task.action == "remove":
            if task.type == "dir":
                os.rmdir(task.path)
                return
            elif task.type == "link":
                os.unlink(task.path)
                return
        elif task.action == "move":
            if task.type == "file":
                os.rename(task.path, task.dest)
                return

        raise RuntimeError("bad task: " + task)

    def defer(self, path):
        """
        Determine if the given path matches a regex in our defer list
        """
        return any(exp.search(path) for exp in self.defers)

    def override(self, path):
        """
        Determine if the given path matches a regex in our override list
        """
        return any(exp.search(path) for exp in self.overrides)

    def parent_link_scheduled_for_removal(self, path):
        prefix = ""
        for part in path.split(os.sep):
            prefix = os.path.join(prefix, part)
            debug_fn(4, "prefix " + prefix, indent=2)
            if self.link_task_for.get(prefix) == "remove":
                debug_fn(4, "link scheduled for removal", indent=2)
                return True

        debug_fn(4, "returning False", indent=2)
        return False

    def is_a_link(self, path):
        debug_fn(4, indent=1)

        try:
            action = self.link_task_for[path].action
            if action == "remove":
                debug_fn(4, "returning False (remove action found)")
                return False
            elif action == "create":
                debug_fn(4, "returning True (create action found)")
                return True
        except KeyError:
            pass

        if os.path.islink(path):
            # Check if any of its parents are links scheduled for removal
            # (need this for edge case during unfolding)
            debug_fn(4, "is a real link")
            return not self.parent_link_scheduled_for_removal(path)

        debug_fn(4, "returning False")
        return False

    def read_a_link(self, path):
        try:
            action = self.link_task_for[path].action
            debug_fn(4, "task exists with action " + action, indent=1)
            if action == "create":
                return self.link_task_for[path].source
            elif action == "remove":
                raise RuntimeError("read_a_link() passed a path scheduled for removal: " + path)
        except KeyError:
            pass

        if os.path.islink(path):
            debug_fn(4, "real link", indent=1)
            return os.readlink(path)

        raise RuntimeError("read_a_link() passed a non link path: " + path)

    def find_stowed_path(self, target, source):
        # Evaluate softlink relative to its target
        path = join_paths(target, os.pardir, source)
        debug(4, "  is path " + path + " owned by stow?")

        # Search for .stow files - this allows us to detect links
        # owned by stow directories other than the current one
        dir = ""
        pathparts = path.split(os.sep)
        for i in range(len(pathparts)):
            part = pathparts[i]
            dir = os.path.join(dir, part)
            if self.marked_stow_dir(dir):
                # FIXME - not sure if this can ever happen
                if i + 1 == len(path):
                    internal_error(
                            "find_stowed_path() called directly on stow dir")

                debug(4, "    yes - " + dir + " was marked as a stow dir")
                package = pathparts[i + 1]
                return path, dir, package

        # If no .stow file was found, we need to find out whether it's
        # owned by the current stow directory, in which case the path will be
        # a prefix of self.stow_path.
        xor = lambda a, b: bool(a) != bool(b)
        if xor(os.path.isabs(path), os.path.isabs(self.stow_path)):
            warn("BUG in find_stowed_path? Absolute/relative mismatch between "
                    "Stow dir " + self.stow_path + " and path " + path)

        stow_path = self.stow_path.split(os.sep)

        # Strip off common prefixes until one is empty
        while path and stow_path:
            if pathparts.pop(0) != stow_path.pop(0):
                debug(4, "    no - either " + path + " not under " +
                        self.stow_path + " or vice-versa")
                return "", "", ""

        if stow_path: # path list must be empty
            debug(4, "    no - " + path + " is not under " + self.stow_path)
            return "", "", ""

        package = pathparts.pop(0)
        debug(4, "    yes - by " + package + " in " + os.path.join(*pathparts))
        return path, self.stow_path, package

    def conflict(self, action, package, message):
        debug(2, "CONFLICT when {}ing {}: {}".format(action, package, message))
        if not self.conflicts[action][package]:
            self.conflicts[action][package] = []
        self.conflicts[action][package].append(message)
        self.conflict_count += 1
        raise RuntimeError(message)

    def is_a_dir(self, path):
        debug_fn(4)

        try:
            action = self.link_task_for[path].action
            if action == "remove":
                return False
            elif action == "create":
                return True
        except KeyError:
            pass

        if self.parent_link_scheduled_for_removal(path):
            return False

        if os.path.isdir(path):
            debug_fn(4, "real dir")
            return True

        debug_fn(4, "returning False")
        return False

    def stow_node(self, stow_path, package, target, source):

        path = join_paths(stow_path, package, target)

        debug(3, "Stowing {} / {} / {}".format(stow_path, package, target))
        debug(4, "  => " + source)

        # Don't try to stow absolute symlinks (they can't be unstowed)
        if os.path.islink(source) and os.path.isabs(self.read_a_link(source)):
            self.conflict("stow", package,
                    "source is an absolute symlink {} => {}".\
                            format(source, second_source))
            debug(3, "Absolute symlinks cannot be unstowed")
            return

        # Does the target already exist?
        if self.is_a_link(target):
            existing_source = self.read_a_link(target)
            if not existing_source:
                error("Could not read link: " + target)
            debug(4, "Evaluate existing link: {} => {}".format(
                        target, existing_source))

            # Does it point to a node under any stow directory?
            existing_path, existing_stow_path, existing_package = \
                self.find_stowed_path(target, existing_source)

            if not existing_path:
                self.conflict("stow", package,
                        "existing target is not owned by stow: " + target)
                return

            # Does the existing target actually point to anything?
            if self.is_a_node(existing_path):
                if existing_source == source:
                    debug(2, "--- Skipping {} as it already points to {}".
                            format(target, source))
                elif self.defer(target):
                    debug(2, "--- Deferring installation of " + target)
                elif self.override(target):
                    debug(2, "--- Overriding installation of " + target)
                    self.do_unlink(target)
                    self.do_link(source, target)
                elif self.is_a_dir(join_paths(target, os.pardir, source)) and \
                     self.is_a_dir(join_paths(target, os.pardir, existing_source)):

                    # If the existing link points to a directory,
                    # and the proposed new link points to a directory,
                    # then we can unfold (split open) the tree at that point

                    debug(2, "--- Unfolding {} which was already owned by {}".
                        format(target, existing_package))
                    self.do_unlink(target)
                    self.do_mkdir(target)
                    self.stow_contents(
                        existing_stow_path,
                        existing_package,
                        target,
                        os.path.join(os.pardir, existing_source)
                    )
                    self.stow_contents(
                        self.stow_path,
                        package,
                        target,
                        os.path.join(os.pardir, source)
                    )
                else:
                    self.conflict("stow", package,
                        "existing target is stowed to a different package: "
                        "{} => {}".format(target, existing_source))
            else:
                # The existing link is invalid, so replace it with a good link
                debug(2, "--- replacing invalid link: " + path)
                self.do_unlink(target)
                self.do_link(source, target)
        elif self.is_a_node(target):
            debug(4, "Evaluate existing node: " + target)
            if self.is_a_dir(target):
                self.stow_contents(self.stow_path, package, target,
                    os.path.join(os.pardir, source))
            else:
                if self.adopt:
                    self.do_mv(target, path)
                    self.do_link(source, target)
                else:
                    self.conflict("stow", package,
                        "existing target is neither a link nor a dir: " +
                        target)
        elif self.no_folding and os.path.isdir(path) and \
             not os.path.islink(path):
            self.do_mkdir(target)
            self.stow_contents(self.stow_path, package, target,
                os.path.join(os.pardir, source))
        else:
            self.do_link(source, target)

    def unstow_node(self, stow_path, package, target):
        path = join_paths(stow_path, package, target)

        debug(3, "Unstowing " + path)
        debug(4, "  target is " + target)

        # Does the target exist?
        if self.is_a_link(target):
            debug(4, "  Evaluating existing link: " + target)

            # Where is the link pointing?
            existing_source = self.read_a_link(target)
            if not existing_source:
                error("Could not read link: " + target)

            if os.path.isabs(existing_source):
                warn("Ignoring an absolute symlink: " + target +
                        " => " + existing_source)
                return

            # Does it point to a node under any stow directory?
            existing_path, existing_stow_path, existing_package = \
                    self.find_stowed_path(target, existing_source)
            if not existing_path:
                self.conflict("unstow", package,
                        "existing target is not owned by stow: " + target +
                        " => " + existing_source)
                return

            # Does the existing target actually point to anything?
            if os.path.exists(existing_path):
                # Does the link point to the right place?

                # Adjust for dotfile if necessary.
                if self.dotfiles:
                    existing_path = adjust_dotfile(existing_path)

                if existing_path == path:
                    self.do_unlink(target)

                # XXX we quietly ignore links that are stowed to a different
                # package.

                #elsif (defer($target)) {
                #    debug(2, "--- deferring to installation of: $target");
                #}
                #elsif ($self->override($target)) {
                #    debug(2, "--- overriding installation of: $target");
                #    $self->do_unlink($target);
                #}
                #else {
                #    $self->conflict(
                #        'unstow',
                #        $package,
                #        "existing target is stowed to a different package: "
                #        . "$target => $existing_source"
                #    );
                #}

            else:
                debug(2, "--- removing invalid link into a stow dir: " + path)
                self.do_unlink(target)
        elif os.path.exists(target):
            debug(4, "  Evaluate existing node: " + target)
            if os.path.isdir(target):
                self.unstow_contents(stow_path, package, target)

                # This action may have made the parent directory foldable
                parent = self.foldable(target)
                if parent:
                    self.fold_tree(target, parent)
            else:
                self.conflict("unstow", package, "existing target is neither "
                        "a link nor a dir: " + target)
        else:
            debug(2, target + " did not exist to be unstowed")


    def should_skip_target_which_is_stow_dir(self, target):
        if target == self.stow_path:
            warn("skipping target which was current stow directory " + target)
            return True
        if self.marked_stow_dir(target):
            warn("skipping protected directory " + target)
            return True

        debug(4, target + " not protected")
        return False

    def marked_stow_dir(self, target):
        for f in (".stow", ".nonstow"):
            if os.path.exists(os.path.join(target, f)):
                debug(4, target + " contained " + f)
                return True
        return False

    def stow_contents(self, stow_path, package, target, source):
        """
        stow the contents of the given directory
        stow_path => relative path from current (i.e. target) directory
                     to the stow dir containing the package to be stowed
        package => the package whose contents are being stowed
        target => subpath relative to package directory which needs
                  stowing as a symlink at subpath relative to target
                  directory.
        source => relative path from the (sub)dir of target
                  to symlink source

        Throws a fatal error if directory cannot be read
        stow_node() and stow_contents() are mutually recursive.
        $source and $target are used for creating the symlink
        $path is used for folding/unfolding trees as necessary
        """

        path = os.path.normpath(os.path.join(stow_path, package, target))

        if self.should_skip_target_which_is_stow_dir(target):
            return

        msg = "Stowing contents of {} (cwd={})".format(path, os.getcwd())
        msg = msg.replace(os.environ["HOME"], "~")
        debug(3, msg)
        debug(4, "  => " + source)

        if not os.path.isdir(path):
            raise RuntimeError("called with non-directory path: " + path)
        if not self.is_a_node(target):
            raise RuntimeError("called with non-directory target: " + target)

        for node in os.listdir(path):
            node_target = join_paths(target, node)
            if self.ignore(stow_path, package, node_target):
                continue

            if self.dotfiles:
                adj_node_target = adjust_dotfile(node_target)
                debug(4, "  Adjusting: " + node_target + " => " + adj_node_target)
                node_target = adj_node_target

            self.stow_node(stow_path, package, node_target,
                    join_paths(source, node))

    def unstow_contents(self, stow_path, package, target):
        path = join_paths(stow_path, package, target)

        if self.should_skip_target_which_is_stow_dir(target):
            return

        msg = "Unstowing from target (cwd=" + os.getcwd() + ", stow_dir=" + \
            self.stow_path
        msg = msg.replace(os.environ["HOME"], "~")
        debug(3, msg)
        debug(4, "  source path is " + path)
        # We traverse the source tree, not the target tree, so path must exist
        if not os.path.isdir(path):
            error("unstow_contents() called with non-directory path:" + path)
        # When called at the top level, target should exist. And unstow_node()
        # should only call this via mutual recursiion if target exists.
        if not self.is_a_node(target):
            error("unstow_contents() called with invalid target:" + target)

        for node in os.listdir(path):
            node_target = join_paths(target, node)
            if self.ignore(stow_path, package, node_target):
                continue

            if self.dotfiles:
                adj_node_target = adjust_dotfile(node_target)
                debug(4, "  Adjusting: " + node_target + " => " + adj_node_target)
                node_target = adj_node_target

            self.unstow_node(stow_path, package, node_target)

    def ignore(self, stow_path, package, target):
        if len(target) == 0:
            raise RuntimeError("ignore() called with empty target")

        for suffix in self.ignores:
            if suffix.match(target):
                debug(4, "  Ignoring path " + target + " due to --ignore=" +
                        str(suffix))
                return True

        # TODO match ignore regexps

        debug(5, "  Not ignoring " + target)
        return False

    def is_a_node(self, path):
        """
        Determine whether the given path is a current or planned node
        Returns false if an existing node is scheduled for removal,
        true if a non-existent node is scheduled for creation
        we also need to be sure we are not just following a link
        """
        debug_fn(4, indent=1)

        try:
            laction = self.link_task_for[path].action
        except KeyError:
            laction = ""
        try:
            daction = self.dir_task_for[path].action
        except KeyError:
            daction = ""

        if laction == "remove":
            if daction == "remove":
                raise RuntimeError("removing link and dir: " + path)
                return False
            elif daction == "create":
                # Assume we're unfolding the path, and that the link
                # removal action is earlier than the dir creation action
                # in the task queue.  FIXME: is this a safe assumption?
                return True
            else: # no dir action
                return False
        elif laction == "create":
            if daction == "remove":
                # Assume we're unfolding the path, and that the link
                # removal action is earlier than the dir creation action
                # in the task queue.  FIXME: is this a safe assumption?
                return True
            elif daction == "create":
                raise RuntimeError("creating link and dir: " + path)
                return True
            else: # no dir action
                return True
        else:
            # No link action
            if daction == "remove":
                return False
            elif daction == "create":
                return True
            else: # no dir action
                pass # fall through to below

        if self.parent_link_scheduled_for_removal(path):
            return False

        if os.path.exists(path):
            debug_fn(4, "really exists", indent=1)
            return True

        debug_fn(4, "returning False")
        return False

    def do_link(self, oldfile, newfile):

        if newfile in self.dir_task_for:
            task_ref = self.dir_task_for[newfile]
            if task_ref.action == "create":
                if task_ref.type == "dir":
                    internal_error("new link ({} => {}) clashes with planned "
                            "new directory".format(newfile, oldfile))
            elif task_ref.action == "remove":
                # We may need to remove a dir before creating a link to continue
                pass
            else:
                internal_error("bad task action: " + task_ref.action)

        if newfile in self.link_task_for:
            task_ref = self.link_task_for[newfile]
            if task_ref.action == "create":
                if task_ref.source != oldfile:
                    internal_error("new link clashes with planned new link: "
                            "{} => {}".format(task_ref.path, task_ref.source))
                else:
                    debug(1, "LINK: {} => {} (duplicates previous action)"\
                            .format(newfile, oldfile))
                    return
            elif task_ref.action == "remove":
                if task_ref.source == oldfile:
                    debug(1, "LINK: " + newfile + " => " + oldfile +
                            " (reverts previous action)")
                    self.link_task_for[newfile].action = "skip"
                    del self.link_task_for[newfile]
                    return
            else:
                internal_error("bad task action: " + task_ref.action)

        debug(1, "LINK: " + newfile + " => " + oldfile)
        task = Task.Link(
            action = "create",
            type = "link",
            path = newfile,
            source = oldfile,
        )
        self.tasks.append(task)
        self.link_task_for[newfile] = task

    def do_unlink(self, file):
        if file in self.link_task_for:
            task_ref = self.link_task_for[file]
            if task_ref.action == "remove":
                debug(1, "UNLINK: " + file + " (duplicates previous action)")
                return
            elif task_ref.action == "create":
                self.link_task_for[file].action = "skip"
                del self.link_task_for[file]
                return
            else:
                internal_error("bad task action: " + task_ref.action)

        if file in self.dir_task_for and \
                self.dir_task_for[file].action == "create":
            internal_error("new unlink operation clashes with planned "
                    "operation: " + self.dir_task_for[file].action +
                    " dir " + file)

        debug(1, "UNLINK: " + file)
        source = os.readlink(file)
        task = Task.Link(
            action = "remove",
            type = "link",
            path = file,
            source = source,
        )
        self.tasks.append(task)
        self.link_task_for[file] = task

    def do_mv(self, src, dst):
        if src in self.link_task_for:
            task_ref = self.link_task_for[src]
            internal_error("do_mv: pre-existing link task for {}: action: {}, "
                    "source: {}".format(src, task_ref.action, task_ref.source))
        elif src in self.dir_task_for:
            task_ref = self.link_task_for[src]
            internal_error("do_mv: pre-existing dir task for {}?! action: {}" \
                    .format(src, task_ref.action))

        # Remove the link
        debug(1, "MV: " + src + " => " + dst)

        task = Task.Mv(
            path = src,
            dest = dst,
        )
        self.tasks.append(task)

        # FIXME: do we need this for anything?
        # self.mv_task_for[file] = task

    def do_mkdir(self, dir):
        if dir in self.link_task_for:
            task_ref = self.link_task_for[dir]
            if task_ref.action == "create":
                internal_error(
                    "new dir clashes with planned new link " +
                    task_ref.path + " => " + task_ref.source
                )
            elif task_ref.action == 'remove':
                # May need to remove a link before creating a directory so continue
                pass
            else:
                internal_error("bad task action: " + task_ref.action)

        if dir in self.dir_task_for:
            task_ref = self.dir_task_for[dir]
            if task_ref.action == "create":
                debug(1, "MKDIR: " + dir + " (duplicates previous action)")
                return
            elif task_ref.action == "remove":
                debug(1, "MKDIR: " + dir + " (reverts previous action)")
                self.dir_task_for[dir].action == "skip"
                del self.dir_task_for[dir]
                return
            else:
                internal_error("bad task action: " + task_ref.action)

        debug(1, "MKDIR: " + dir)
        task = Task.Dir(
                action = "create",
                type = "dir",
                path = dir,
        )
        self.tasks.append(task)
        self.dir_task_for[dir] = task

    def foldable(self, target):
        debug(3, "--- Is " + target + " foldable?")
        if self.no_folding:
            debug(3, "--- no because --no-folding enabled")
            return ""

        parent = ""
        for node in os.listdir(target):
            path = join_paths(target, node)

            # Skip nodes scheduled for removal
            if not self.is_a_node(path):
                continue

            # If it's not a link then we can't fold its parent
            if not self.is_a_link(path):
                return ""

            # Where is the link pointing?
            source = self.read_a_link(path)
            if not source:
                error("Could not read link " + path)
            if parent == "":
                parent = join_paths(source, os.pardir)
            elif parent != join_paths(source, os.pardir):
                return ""

        if not parent:
            return ""

        # If we get here then all nodes in target are links, and those links
        # point to nodes inside the same directory.

        # chop the leading ".." to get the path to the common parent directory
        # relative to the parent of our target
        assert(parent.startswith(os.pardir + os.sep))
        parent = parent[len(os.pardir + os.sep):]

        # If the resulting path is owned by stow, we can fold it
        if self.path_owned_by_package(target, parent):
            debug(3, "--- target is foldable")
            return parent
        else:
            return ""

    def do_rmdir(self, dir):
        if dir in self.link_task_for:
            task_ref = self.link_task_for[dir]
            internal_error("rmdir clashes with planned operation: " +
                    task_ref.action + " link " + task_ref.path + " => "
                    + task_ref.source)

        if dir in self.dir_task_for:
            task_ref = self.dir_task_for[dir]

            if task_ref.action == "remove":
                debug(1, "RMDIR " + dir + " (duplicates previous action)")
                return
            elif task_ref.action == "create":
                debug(1, "MKDIR " + dir + " (reverts previous action)")
                self.link_task_for[dir].action = "skip"
                del self.link_task_for[dir]
                return
            else:
                internal_error("bad task action: " + task_ref.action)

        debug(1, "RMDIR " + dir)
        task = Task.Dir(
                action = "remove",
                type = "dir",
                path = dir,
        )
        self.tasks.append(task)
        self.dir_task_for[dir] = task

    def fold_tree(self, target, source):
        debug(3, "--- Folding tree: " + target + " => " + source)
        for node in os.listdir(target):
            if not self.is_a_node(join_paths(target, node)):
                continue
            self.do_unlink(join_paths(target, node))
        self.do_rmdir(target)
        self.do_link(source, target)

    def path_owned_by_package(self, target, source):
        _, _, package = self.find_stowed_path(target, source)
        return package

def run_with_args(argv = []):

    import argparse
    parser = argparse.ArgumentParser("stow")
    parser.add_argument("-d", "--dir",
            help="Set stow dir to DIR (default is current dir)")
    parser.add_argument("-t", "--target", metavar="DIR",
            help="Set target to DIR (default is parent of stow dir)")
    parser.add_argument("--ignore", metavar="REGEX", action="append",
            default=[], type=re.compile,
            help="Ignore files ending in this Perl regex")
    parser.add_argument("--adopt", action="store_true",
            help="(Use with care!) Import existing files into stow package "
                 "from target. Please read docs before using.")
    parser.add_argument("-v", action="count", default=0,
            help="Increase verbosity by one (levels are from 0 to 5)")
    parser.add_argument("--verbose", nargs="?", type=int, const=1,
            metavar="N",
            help="Set verbosity level")
    parser.add_argument("-V", "--version", action="store_true",
            help="Show stow version number")

    args, rest = parser.parse_known_args(argv)

    if args.version:
        print("stow.py version " + version)
        return
    del args.version

    if not args.dir:
        try:
            args.dir = os.environ["STOW_DIR"]
        except KeyError:
            args.dir = os.getcwd()
    if not args.target:
        args.target = join_paths(args.dir, os.pardir)

    if not args.verbose:
        args.verbose = args.v
    del args.v

    pkgs_to_stow = []
    pkgs_to_unstow = []
    mode_stow, mode_unstow = (True, False)
    for arg in rest:
        if arg in ("-S", "--stow"):
            mode_stow, mode_unstow = (True, False)
        elif arg in ("-D", "--unstow"):
            mode_stow, mode_unstow = (False, True)
        elif arg in ("-R", "--restow"):
            mode_stow, mode_unstow = (True, True)
        else:
            if mode_stow:
                pkgs_to_stow.append(arg)
            if mode_unstow:
                pkgs_to_unstow.append(arg)

    def usage(msg = None):
        if msg:
            print("stow: " + msg)
        parser.print_help()
        sys.exit(1 if msg else 0)

    if not pkgs_to_stow and not pkgs_to_unstow:
        usage("No packages to stow or unstow")

    stow = Stow(**vars(args))
    stow.plan_unstow(pkgs_to_unstow)
    stow.plan_stow(pkgs_to_stow)
    stow.process_tasks()

if __name__ == "__main__":
    import sys
    run_with_args(sys.argv[1:])
