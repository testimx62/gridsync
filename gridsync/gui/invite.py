# -*- coding: utf-8 -*-

import json
import os

from PyQt5.QtCore import pyqtSignal, QStringListModel, Qt
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QAction, QCheckBox, QCompleter, QGridLayout, QLabel, QLineEdit,
    QMessageBox, QSizePolicy, QSpacerItem, QWidget)
from twisted.internet.defer import CancelledError
from wormhole.errors import (
    LonelyError, ServerConnectionError, WelcomeError, WrongPasswordError)
try:
    from wormhole.wordlist import raw_words
except ImportError:  # TODO: Switch to new magic-wormhole completion API
    from wormhole._wordlist import raw_words

from gridsync import pkgdir, resource, APP_NAME
from gridsync.desktop import get_clipboard_modes, get_clipboard_text
from gridsync.errors import UpgradeRequiredError


cheatcodes = []
try:
    for file in os.listdir(os.path.join(pkgdir, 'resources', 'providers')):
        cheatcodes.append(file.split('.')[0].lower())
except OSError:
    pass


wordlist = []
for word in raw_words.items():
    wordlist.extend(word[1])
for c in cheatcodes:
    wordlist.extend(c.split('-'))
wordlist = sorted([word.lower() for word in wordlist])


def get_settings_from_cheatcode(cheatcode):
    path = os.path.join(pkgdir, 'resources', 'providers', cheatcode + '.json')
    try:
        with open(path) as f:
            return json.loads(f.read())
    except (OSError, json.decoder.JSONDecodeError):
        return None


def is_valid(code):
    words = code.split('-')
    if len(words) != 3:
        return False
    elif not words[0].isdigit():
        return False
    elif not words[1] in wordlist:
        return False
    elif not words[2] in wordlist:
        return False
    return True


class InviteCodeCompleter(QCompleter):
    def __init__(self):
        super(InviteCodeCompleter, self).__init__()
        self.setCaseSensitivity(Qt.CaseInsensitive)
        self.setCompletionMode(QCompleter.InlineCompletion)

    def pathFromIndex(self, index):
        path = QCompleter.pathFromIndex(self, index)
        words = self.widget().text().split('-')
        if len(words) > 1:
            path = '{}-{}'.format('-'.join(words[:-1]), path)
        return path

    def splitPath(self, path):  # pylint: disable=no-self-use
        return [str(path.split('-')[-1])]


class InviteCodeLineEdit(QLineEdit):

    error = pyqtSignal(str)
    go = pyqtSignal(str)

    def __init__(self, parent=None):
        super(InviteCodeLineEdit, self).__init__()
        self.parent = parent
        model = QStringListModel()
        model.setStringList(wordlist)
        completer = InviteCodeCompleter()
        completer.setModel(model)
        font = QFont()
        font.setPointSize(16)
        self.setFont(font)
        self.setCompleter(completer)
        self.setAlignment(Qt.AlignCenter)
        #self.setPlaceholderText("Enter invite code")
        self.action_button = QAction(QIcon(), '', self)
        self.addAction(self.action_button, 1)
        self.addAction(QAction(QIcon(), '', self), 0)  # for symmetry

        completer.highlighted.connect(self.update_action_button)
        self.textChanged.connect(self.update_action_button)
        self.returnPressed.connect(self.return_pressed)
        self.action_button.triggered.connect(self.button_clicked)

        self.update_action_button()

    def update_action_button(self, text=None):
        text = (text if text else self.text())
        if not text:
            self.action_button.setIcon(QIcon())
            self.action_button.setToolTip('')
            for mode in get_clipboard_modes():
                if is_valid(get_clipboard_text(mode)):
                    self.action_button.setIcon(QIcon(resource('paste.png')))
                    self.action_button.setToolTip("Paste")
        elif is_valid(text):
            self.action_button.setIcon(QIcon(resource('arrow-right.png')))
            self.action_button.setToolTip("Go")
        else:
            self.action_button.setIcon(QIcon(resource('close.png')))
            self.action_button.setToolTip("Clear")

    def keyPressEvent(self, event):
        key = event.key()
        text = self.text()
        if key in (Qt.Key_Space, Qt.Key_Minus, Qt.Key_Tab):
            if text and len(text.split('-')) < 3 and not text.endswith('-'):
                self.setText(text + '-')
            else:
                self.setText(text)
        elif text and key == Qt.Key_Escape:
            self.setText('')
        else:
            return QLineEdit.keyPressEvent(self, event)
        return None

    def return_pressed(self):
        code = self.text().lower()
        if is_valid(code):
            self.go.emit(code)
        else:
            self.error.emit("Invalid code")

    def button_clicked(self):
        code = self.text().lower()
        if not code:
            for mode in get_clipboard_modes():
                text = get_clipboard_text(mode)
                if is_valid(text):
                    self.setText(text)
        elif is_valid(code):
            self.go.emit(code)
        else:
            self.setText('')


class InviteCodeWidget(QWidget):
    def __init__(self, parent=None):
        super(InviteCodeWidget, self).__init__()
        self.parent = parent

        self.label = QLabel("Enter invite code:")
        font = QFont()
        font.setPointSize(14)
        self.label.setFont(font)
        self.label.setStyleSheet("color: grey")
        self.label.setAlignment(Qt.AlignCenter)

        self.lineedit = InviteCodeLineEdit(self)

        self.checkbox = QCheckBox("Connect over the Tor network")
        self.checkbox.setEnabled(True)
        self.checkbox.setCheckable(False)
        self.checkbox.setStyleSheet("color: grey")
        self.checkbox.setFocusPolicy(Qt.NoFocus)

        layout = QGridLayout(self)
        layout.addItem(QSpacerItem(0, 0, 0, QSizePolicy.Expanding), 1, 1)
        layout.addWidget(self.label, 2, 1)
        layout.addWidget(self.lineedit, 3, 1)
        #layout.addWidget(self.checkbox, 4, 1, Qt.AlignCenter)
        layout.addItem(QSpacerItem(0, 0, 0, QSizePolicy.Expanding), 5, 1)


def show_failure(failure, parent=None):
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Warning)
    msg.setStandardButtons(QMessageBox.Retry)
    msg.setEscapeButton(QMessageBox.Retry)
    msg.setDetailedText(str(failure))
    if failure.type == ServerConnectionError:
        msg.setWindowTitle("Server Connection Error")
        msg.setText(
            "An error occured while connecting to the invite server. This "
            "could mean that it is currently offline or that there is some "
            "other problem with your connection. Please try again later.")
    elif failure.type == WelcomeError:
        msg.setWindowTitle("Invite refused")
        msg.setText(
            "The server negotiating your invitation is online but is "
            "currently refusing to process any invitations. This may indicate "
            "that your version of {} is out-of-date, in which case you should "
            "upgrade to the latest version and try again.".format(APP_NAME))
        msg.setIcon(QMessageBox.Critical)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.setEscapeButton(QMessageBox.Ok)
    elif failure.type == WrongPasswordError:
        msg.setWindowTitle("Invite confirmation failed")
        msg.setText(
            "Either your recipient mistyped the invite code or a potential "
            "attacker tried to guess the code and failed.\n\nTo try again, "
            "you will need a new invite code.")
    elif failure.type == LonelyError:  # Raises only when closing(?)
        return
    elif failure.type == UpgradeRequiredError:
        msg.setWindowTitle("Upgrade required")
        msg.setText(
            "Your version of {} is out-of-date. Please upgrade to the latest "
            "version and try again with a new invite code.".format(APP_NAME))
        msg.setIcon(QMessageBox.Critical)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.setEscapeButton(QMessageBox.Ok)
    elif failure.type == CancelledError:
        msg.setWindowTitle("Invite timed out")
        msg.setText(
            "The invitation process has timed out. Your invite code may have "
            "have expired. Please request a new invite code from the other "
            "party and try again.")
    else:
        msg.setWindowTitle(str(failure.type.__name__))
        msg.setText(str(failure.value))
    msg.exec_()
