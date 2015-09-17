# -*- coding: utf-8 -*-

import datetime
import logging
import os
import shutil
import urllib2

from twisted.internet import reactor
from twisted.internet.defer import gatherResults
from twisted.internet.task import LoopingCall
from twisted.internet.threads import deferToThread, blockingCallFromThread
from watchdog.events import PatternMatchingEventHandler
from watchdog.observers import Observer


class SyncFolder(PatternMatchingEventHandler):
    def __init__(self, local_dir, remote_dircap, tahoe=None):
        super(SyncFolder, self).__init__(
                ignore_patterns=["*.gridsync-versions*", "*.part"])
        if not tahoe:
            from gridsync.tahoe import Tahoe
            tahoe = Tahoe()
        self.tahoe = tahoe
        self.local_dir = os.path.expanduser(local_dir)
        self.remote_dircap = remote_dircap
        self.versions_dir = os.path.join(self.local_dir, '.gridsync-versions')
        self.local_snapshot = 0
        self.filesystem_modified = False
        self.do_backup = False
        self.do_sync = False
        self.sync_state = 0
        self.sync_log = []
        self.local_checker = LoopingCall(self.check_for_changes)
        self.remote_checker = LoopingCall(reactor.callInThread, self.sync)
        logging.debug("{} initialized; "
                "{} <-> {}".format(self, self.local_dir, self.remote_dircap))

    def on_modified(self, event):
        self.filesystem_modified = True
        try:
            self.local_checker.start(1)
        except AssertionError:
            return

    def check_for_changes(self):
        if self.filesystem_modified:
            self.filesystem_modified = False
        else:
            self.local_checker.stop()
            reactor.callInThread(self.sync, force_backup=True)

    def start(self):
        try:
            self.remote_dircap = self.tahoe.aliasify(self.remote_dircap)
        except ValueError:
            # TODO: Alert user alias is garbled?
            pass
        except LookupError:
            # TODO: Alert user alias is missing?
            pass
        logging.info("Starting Observer in {}...".format(self.local_dir))
        try:
            self.observer = Observer()
            self.observer.schedule(self, self.local_dir, recursive=True)
            self.observer.start()
        except Exception, error:
            logging.error(error)
        self.remote_checker.start(30)

    def _create_conflicted_copy(self, filename, mtime):
        base, extension = os.path.splitext(filename)
        t = datetime.datetime.fromtimestamp(mtime)
        tag = t.strftime('.(conflicted copy %Y-%m-%d %H-%M-%S)')
        newname = base + tag + extension
        shutil.copy2(filename, newname)

    def _create_versioned_copy(self, filename, mtime):
        local_filepath = os.path.join(self.local_dir, filename)
        base, extension = os.path.splitext(filename)
        t = datetime.datetime.fromtimestamp(mtime)
        tag = t.strftime('.(%Y-%m-%d %H-%M-%S)')
        newname = base + tag + extension
        versioned_filepath = os.path.join(self.versions_dir, newname)
        if not os.path.isdir(os.path.dirname(versioned_filepath)):
            os.makedirs(os.path.dirname(versioned_filepath))
        logging.info("Creating {}".format(versioned_filepath))
        shutil.copy2(local_filepath, versioned_filepath)

    def get_local_metadata(self, basedir=None):
        metadata = {}
        if not basedir:
            basedir = self.local_dir
        for root, dirs, files in os.walk(basedir, followlinks=True):
            for name in dirs:
                path = os.path.join(root, name)
                if not path.startswith(self.versions_dir):
                    metadata[path] = {}
            for name in files:
                path = os.path.join(root, name)
                if not path.startswith(self.versions_dir):
                    metadata[path] = {
                        'mtime': int(os.path.getmtime(path)),
                        'size': os.path.getsize(path)
                    }
        return metadata

    def sync(self, snapshot=None, force_backup=False):
        if self.sync_state:
            logging.debug("Sync already in progress; queueing to end...")
            self.do_sync = True
            return
        if not snapshot:
            # XXX: It might be preferable to just check the dircap of /Latest/
            pre_sync_archives = self.tahoe.command(['ls', 
                self.remote_dircap + "Archives"], quiet=True).split('\n')
            available_snapshot = pre_sync_archives[-1]
            if self.local_snapshot == available_snapshot:
                if force_backup:
                    self.sync_state += 1
                    self.backup(self.local_dir, self.remote_dircap)
                    self.sync_complete(pre_sync_archives)
                return
            else:
                snapshot = available_snapshot
        remote_dircap = self.tahoe.get_dircap_from_alias(self.remote_dircap)
        remote_path = remote_dircap + '/Archives/' + snapshot
        logging.info("Syncing {} with {}...".format(self.local_dir, snapshot))
        self.sync_state += 1
        self.local_metadata = self.get_local_metadata(self.local_dir)
        self.remote_metadata = self.tahoe.get_metadata(remote_path)
        # TODO: If tahoe.get_metadata() fails or doesn't contain a
        # valid snapshot, jump to backup?
        jobs = []
        for file, metadata in self.remote_metadata.iteritems():
            if metadata['uri'].startswith('URI:DIR'):
                dirpath = os.path.join(self.local_dir, file)
                if not os.path.isdir(dirpath):
                    logging.info("Creating directory: {}...".format(dirpath))
                    os.makedirs(dirpath)
        for file, metadata in self.remote_metadata.iteritems():
            if not metadata['uri'].startswith('URI:DIR'):
                filepath = os.path.join(self.local_dir, file)
                remote_mtime = metadata['mtime']
                if filepath in self.local_metadata:
                    local_filesize = self.local_metadata[filepath]['size']
                    local_mtime = self.local_metadata[filepath]['mtime']
                    if local_mtime < remote_mtime:
                        logging.debug("[<] {} is older than remote version; "
                                "downloading {}...".format(file, file))
                        self._create_versioned_copy(filepath, local_mtime)
                        jobs.append(deferToThread(self.download,
                            remote_path + '/' + file, filepath, remote_mtime))
                    elif local_mtime > remote_mtime:
                        logging.debug("[>] {} is newer than remote version; "
                                "backup scheduled".format(file)) 
                        self.do_backup = True
                    else:
                        logging.debug("[.] {} is up to date.".format(file))
                else:
                    logging.debug("[?] {} is missing; "
                            "downloading {}...".format(file, file))
                    jobs.append(deferToThread(self.download,
                        remote_path + '/' + file, filepath, remote_mtime))
        for file, metadata in self.local_metadata.iteritems():
            fn = file.split(self.local_dir + os.path.sep)[1]
            if fn not in self.remote_metadata:
                if metadata:
                    recovery_uri = self.tahoe.stored(file, metadata['size'],
                            metadata['mtime'])
                    if recovery_uri:
                        logging.debug("[x] {} removed from latest snapshot; "
                                "deleting local file...".format(file))
                        try:
                            os.remove(file)
                        except Exception, error:
                            logging.error(error)
                    else:
                        logging.debug("[!] {} isn't stored; "
                            "backup scheduled".format(file))
                        self.do_backup = True
        blockingCallFromThread(reactor, gatherResults, jobs)
        if self.do_backup:
            self.backup(self.local_dir, self.remote_dircap)
            self.do_backup = False
        if self.do_sync:
            self.sync()
        else:
            self.sync_complete(pre_sync_archives)

    def sync_complete(self, pre_sync_archives):
        post_sync_archives = self.tahoe.command(['ls', 
            self.remote_dircap + "Archives"], quiet=True).split('\n')
        if len(post_sync_archives) - len(pre_sync_archives) <= 1:
            self.local_snapshot = post_sync_archives[-1]
            logging.info("Synchronized {} with {}".format(
                self.local_dir, self.local_snapshot))
        else:
            logging.warn("Remote state changed during sync")
            # TODO: Re-sync/merge overlooked snapshot
        self.sync_state -= 1

    def download(self, remote_uri, local_filepath, mtime=None):
        url = self.tahoe.node_url + 'uri/' + urllib2.quote(remote_uri)
        request = urllib2.Request(url)
        download_path = local_filepath + '.part'
        if os.path.exists(download_path):
            size = os.path.getsize(download_path)
            logging.debug("Partial download of {} found; resuming byte {}..."\
                    .format(local_filepath, size))
            request.headers['Range'] = 'bytes={}-'.format(size)
        # TODO: Handle exceptions..
        remote_file = urllib2.urlopen(request)
        with open(download_path, "ab") as local_file:
            while True:
                data = remote_file.read(4096)
                if not data:
                    break
                local_file.write(data)
        # XXX: Check if dest exists; create conflicted/versioned copy?
        os.rename(download_path, local_filepath)
        if mtime:
            os.utime(local_filepath, (-1, mtime))
        print 'download complete'
        self.sync_log.append("Downloaded {}".format(
            local_filepath.lstrip(self.local_dir)))

    def backup(self, local_dir, remote_dircap):
        output = self.tahoe.command(['backup', '-v', '--exclude=*.part',
            '--exclude=*.gridsync-versions*', local_dir, remote_dircap])
        for line in output.split('\n'):
            if line.startswith('uploading'):
                filename = line.split()[1][:-3][1:].lstrip(self.local_dir) # :/
                self.sync_log.append("Uploaded {}".format(filename))

    def stop(self):
        logging.info("Stopping Observer in {}...".format(self.local_dir))
        try:
            self.observer.stop()
            self.observer.join()
        except Exception, error:
            logging.error(error)
        self.remote_checker.stop()

