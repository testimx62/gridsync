#!/usr/bin/env python2
# vim:fileencoding=utf-8:ft=python

from __future__ import unicode_literals

import os
import sys
import time
import argparse
import threading

from config import Config
from tahoe import Tahoe
from watcher import Watcher
from gui import main_window

def main():
    #signal.signal(signal.SIGINT, signal.SIG_DFL)
    parser = argparse.ArgumentParser(
            description='Synchronize local directories with a Tahoe-LAFS storage grid.',
            epilog='Example: ')
    parser.add_argument('-c', metavar='<config file>', help='load settings from config file')
    args = parser.parse_args()
    #parser.print_help()
    config = Config()
    settings = config.load()
    tahoe_objects = []
    watcher_objects = []
    for node_name, node_settings in settings['tahoe_nodes'].items():
        t = Tahoe(os.path.join(config.config_dir, node_name), node_settings)
        tahoe_objects.append(t)
        for sync_name, sync_settings in settings['sync_targets'].items():
            if sync_settings[0] == node_name:
                w = Watcher(t, os.path.expanduser(sync_settings[1]), sync_settings[2])
                watcher_objects.append(w)
    g = threading.Thread(target=main_window.main)
    g.setDaemon(True)
    #g.start()
    
    threads = [threading.Thread(target=o.start) for o in tahoe_objects]
    [t.start() for t in threads]
    [t.join() for t in threads]

    time.sleep(1)

    threads = [threading.Thread(target=o.start) for o in watcher_objects]
    [t.start() for t in threads]
    [t.join() for t in threads]
    


    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n*** Shutting down!')

        threads = [threading.Thread(target=o.stop) for o in watcher_objects]
        [t.start() for t in threads]
        [t.join() for t in threads]
        
        threads = [threading.Thread(target=o.stop) for o in tahoe_objects]
        [t.start() for t in threads]
        [t.join() for t in threads]
        
        config.save(settings)
        sys.exit()


if __name__ == "__main__":
    main()
