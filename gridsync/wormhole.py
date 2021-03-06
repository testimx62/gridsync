# -*- coding: utf-8 -*-

import json
import logging

from PyQt5.QtCore import pyqtSignal, QObject
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from wormhole import wormhole
from wormhole.errors import WormholeError

from gridsync import settings
from gridsync.errors import UpgradeRequiredError


APPID = settings['wormhole']['appid']
RELAY = settings['wormhole']['relay']


class Wormhole(QObject):

    got_welcome = pyqtSignal(dict)
    got_code = pyqtSignal(str)
    got_introduction = pyqtSignal()
    got_message = pyqtSignal(dict)
    closed = pyqtSignal()
    send_completed = pyqtSignal()

    def __init__(self):
        super(Wormhole, self).__init__()
        self._wormhole = wormhole.create(APPID, RELAY, reactor)

    @inlineCallbacks
    def connect(self):
        logging.debug("Connecting to %s...", RELAY)
        welcome = yield self._wormhole.get_welcome()
        logging.debug("Connected to wormhole server; got welcome: %s", welcome)
        self.got_welcome.emit(welcome)

    @inlineCallbacks
    def close(self):
        logging.debug("Closing wormhole...")
        try:
            yield self._wormhole.close()
        except WormholeError:
            pass
        logging.debug("Wormhole closed.")
        self.closed.emit()

    @inlineCallbacks
    def receive(self, code):
        yield self.connect()
        self._wormhole.set_code(code)
        logging.debug("Using code: %s (APPID is '%s')", code, APPID)

        client_intro = {"abilities": {"client-v1": {}}}
        self._wormhole.send_message(json.dumps(client_intro).encode('utf-8'))

        data = yield self._wormhole.get_message()
        data = json.loads(data.decode('utf-8'))
        offer = data.get('offer', None)
        if offer:
            logging.warning(
                "The message-sender appears to be using the older, "
                "'xfer_util'-based version of the invite protocol.")
            msg = None
            if 'message' in offer:
                msg = json.loads(offer['message'])
                ack = {'answer': {'message_ack': 'ok'}}
                self._wormhole.send_message(json.dumps(ack).encode('utf-8'))
            else:
                raise Exception("Unknown offer type: {}".format(offer.keys()))
        else:
            logging.debug("Received server introduction: %s", data)
            if 'abilities' not in data:
                raise UpgradeRequiredError
            if 'server-v1' not in data['abilities']:
                raise UpgradeRequiredError
            self.got_introduction.emit()

            msg = yield self._wormhole.get_message()
            msg = json.loads(msg.decode("utf-8"))

        logging.debug("Received message: %s", msg)
        self.got_message.emit(msg)
        yield self.close()
        returnValue(msg)

    @inlineCallbacks
    def send(self, msg, code=None):
        yield self.connect()
        if code is None:
            self._wormhole.allocate_code()
            logging.debug("Generating code...")
            code = yield self._wormhole.get_code()
            self.got_code.emit(code)
        else:
            self._wormhole.set_code(code)
        logging.debug("Using code: %s (APPID is '%s')", code, APPID)

        server_intro = {"abilities": {"server-v1": {}}}
        self._wormhole.send_message(json.dumps(server_intro).encode('utf-8'))

        data = yield self._wormhole.get_message()
        data = json.loads(data.decode('utf-8'))
        logging.debug("Received client introduction: %s", data)
        if 'abilities' not in data:
            raise UpgradeRequiredError
        if 'client-v1' not in data['abilities']:
            raise UpgradeRequiredError
        self.got_introduction.emit()

        logging.debug("Sending message: %s", msg)
        self._wormhole.send_message(json.dumps(msg).encode('utf-8'))
        yield self.close()
        self.send_completed.emit()


@inlineCallbacks
def wormhole_receive(code):
    w = Wormhole()
    msg = yield w.receive(code)
    returnValue(msg)


@inlineCallbacks
def wormhole_send(msg, code=None):
    w = Wormhole()
    yield w.send(msg, code)
