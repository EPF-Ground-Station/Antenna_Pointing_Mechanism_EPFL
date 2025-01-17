"""
Module defining the server to be run on the VEGA radio-telescope computer for monitoring remote users connexion to
the antenna backend. If executed as main, simply instanciates and runs the server.

Notice that in addition to enabling remote connexions and ergonomic operation of VEGA, the proposed server-client
architecture adds a layer of security by only allowing indirect user access to the APM features.

Format of exchanged messages :
    Client -> Server : &{cmd} {\*args, separated by spaces}

    Server -> Client : &{Status}|{feedback}

    with Status in (PRINT, OK, WARNING, ERROR)

    Notice hence forbidden characters & and | in the body of exchanged messages
"""


from lib_SRT.Srt import *
import sys
from os.path import expanduser
from time import time, localtime, strftime
import io
from GUI.ui_form_server import Ui_MainWindow
import socket
from PySide6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress, QNetworkInterface, QAbstractSocket
from PySide6.QtCore import Slot, QFileInfo, QTimer, Signal, QThread, QObject
from PySide6.QtWidgets import QApplication, QWidget, QFileDialog, QMainWindow
import os
import time

sys.path.append("../")


POS_LOGGING_RATE = 3
WATER_RATE = 3600


class sigEmettor(QObject):
    """QObject that handles sending a signal from a non-Q thread.
    Used by StdoutRedirector to pass the Server a print statement
    to send to the client
    """

    printMsg = Signal(str, bool)  # Signal emitted when sth is printed

    def __init__(self, parent=None):
        super().__init__(parent)


class StdoutRedirector(io.StringIO):
    """Handles the redirection of print statements to both the server-side
    console and to the client via messages with PRINT status"""

    def __init__(self, target, parent=None):
        super().__init__()
        self.target = target
        self.emettor = sigEmettor()

    def write(self, message):
        self.target.write(message)
        self.target.flush()
        if message == '\n':
            return

        message = "PRINT|" + str(message)
        self.emettor.printMsg.emit(message, False)  # Set verbose to False


class SRTThread(QThread):
    """Thread that handles communication with SRT object, including tracking. Internal class instantiated by ServerGUI.
    The self.msg is updated when a command is received from the client."""

    endMotion = Signal(str, str)
    send2log = Signal(str)

    send2socket = Signal(str)

    def __init__(self, msg: str = '', parent=None):
        """
        Constructor of the thread.

        :param msg: Motion command sent from client, to be processed and executed. Continuously updated
        :type msg: str
        """
        super().__init__(parent)

        self.measuring = False
        self.on = True
        self.posLoggingOn = True
        self.pending = False
        self.connected = 0
        self.trackingBool = False
        self.timeLastPosCheck = time.time()
        self.timeLastWater = time.time()

        self.SRT = Srt("/dev/ttyUSB0", 115200, 1)
        self.msg = msg

    def tracking(self):
        return self.SRT.tracking

    def sendClient(self, msgSend):
        """
        Method that emits a signal with a non-formatted message to send to the server as argument. The signal triggers
        the ServerGUI.sendClient slot which in turns formats and sends the message to the client

        :param msgSend: non-formatted message to send to the client
        :type msgSend: str
        """

        self.send2socket.emit(msgSend)

    def sendOK(self, msg):
        """Adds prefix OK to message passed to sendClient. See self.sendClient for more"""

        msg = "OK|" + msg
        self.sendClient(msg)

    def sendWarning(self, msg):
        """Adds prefix WARNING to message passed to sendClient. See self.sendClient for more"""

        msg = "WARNING|" + msg
        self.sendClient(msg)

    def sendError(self, msg):
        """Adds prefix ERROR to message passed to sendClient. See self.sendClient for more"""

        msg = "ERROR|" + msg
        self.sendClient(msg)

    def receiveCommand(self, str):
        """Slot triggered when ServerGui receives a command to be executed by the SRT class"""
        self.msg = str

    def pausePositionLogging(self):
        self.posLoggingOn = False

    def unpausePositionLogging(self):
        self.posLoggingOn = True

    def measurementDone(self):
        self.measuring = False
        self.sendOK('measurement_completed')

    def sendPos(self):
        """
        Method that sends to the client the current coordinates of the mount in all coordinate systems.
        """
        if self.connected == 0:
            return

        az, alt, ra, dec, long, lat = self.SRT.returnStoredCoords()

        if (az == -1) or (alt == -1):
            self.sendError(
                "Error while trying to get current coordinates. Hardware may be damaged. Please report this"
                " event to the person in charge ASAP")

        msgReturn = f"COORDS {az} {alt} {ra} {dec} {long} {lat}"
        self.sendOK(msgReturn)

    def run(self):
        """Loop of the thread. Processes the command contained in self.msg, then resets it waiting for the next"""

        while self.on:
            feedback = ''
            if time.time() > self.timeLastPosCheck + POS_LOGGING_RATE:
                self.timeLastPosCheck = time.time()
                self.sendPos()
            #print("DEBUG Value of self.connected : ", self.connected)

            if self.measuring:
                if not self.SRT.observing:
                    self.measurementDone()

            if self.msg != '':

                self.pending = True
                msg = self.msg
                print("Handling "+msg+" at the moment.")

                args = msg.split(" ")
                cmd = args[0]
                print("SRT Thread handling command: " + cmd +
                      ", with "+str(len(args))+" arguments")
                # Processing of command
                if cmd in ("pointRA", "pointGal", "pointAzAlt", "trackRA", "trackGal"):

                    if len(args) == 3:  # Parses arguments (point/track) : need for 2 coords
                        a, b = float(args[1]), float(args[2])

                        try:

                            if cmd == "pointRA":
                                a, b = RaDec2AzAlt(a, b)
                            if cmd == "pointGal":
                                a, b = Gal2AzAlt(a, b)
                            if "point" in msg:
                                feedback = self.SRT.pointAzAlt(a, b)
                            elif cmd == "trackRA":
                                self.trackingBool = True
                                feedback = self.SRT.trackRaDec(a, b)
                            elif cmd == "trackGal":
                                feedback = self.SRT.trackGal(a, b)

                            feedback = "finishedPointing"

                        # If wrong coords values for conversion to AzAlt
                        except ValueError:
                            feedback = "Invalid coordinates values. Latitudes should be within [-90°, 90°]. Pointing aborted."

                    # If invalid number of args
                    else:
                        raise ValueError(
                            "ERROR : invalid command passed to server")

                if cmd in ("connect", "goHome", "untangle",
                           "standby", "disconnect", "stopTracking"):

                    if len(args) == 1:
                        if cmd == "goHome":
                            feedback = self.SRT.go_home()
                        elif cmd == "connect":
                            # TODO: remove False for debug
                            feedback = self.SRT.connectAPM(False)
                            if feedback == 'IDLE' or feedback == 'Untangled':
                                print("SRT Thread connected")
                                self.connected = 1
                                feedback = 'APMConnected'
                        elif cmd == "disconnect":
                            feedback = self.SRT.disconnectAPM()
                            self.timeLastWater = time.time()  # Reset timer for water evacuation after activity
                            self.connected = 0
                            feedback = 'APMDisconnected'
                        elif cmd == "untangle":
                            feedback = self.SRT.untangle()
                        elif cmd == "standby":
                            feedback = self.SRT.standby()
                        elif cmd == "wait":
                            feedback = ""
                        elif cmd == "stopTracking":
                            feedback = self.SRT.stopTracking()
                            self.trackingBool = False

                    else:
                        raise ValueError(
                            "ERROR : invalid command passed to server")

                if cmd == "measure":
                    if len(args) == 13+1:  # Parses arguments (measurement)
                        (repo, prefix, rf_gain, if_gain, bb_gain, centerFreq, bandwidth, channels, sampleTime, duration,
                         obs_mode, raw_mode, studentflag) = (
                            str(args[1]), str(args[2]), float(
                                args[3]), float(args[4]), float(args[5]),
                            float(args[6]), float(args[7]), float(
                                args[8]), float(args[9]), float(args[10]),
                            bool(int(args[11])), bool(int(args[12])), bool(int(args[13])))

                        if self.SRT.observing == 0:
                            self.measuring = True
                            self.SRT.observe(repo=repo, prefix=prefix, rf_gain=rf_gain, if_gain=if_gain, bb_gain=bb_gain,
                                             fc=centerFreq, bw=bandwidth, channels=int(channels), t_sample=sampleTime,
                                             duration=duration, obs_mode=obs_mode, raw_mode=raw_mode,
                                             studentflag=studentflag)
                        else:
                            self.sendError("Already measuring!")
                    else:
                        raise ValueError(
                            "ERROR : invalid command passed to server")

                    feedback = 'measurementReceived'
                # TODO: stop measurement
                feedback = str(feedback)
                feedback = str(feedback)

                print("SRT Thread handled: " + msg +
                      " with feedback: " + feedback)
                self.pending = False
                self.endMotion.emit(msg, feedback)
                self.msg = ''

            # WATER CLOCK : temporary I hope
            if (not self.connected) and (time.time() - self.timeLastWater > WATER_RATE):
                self.timeLastWater = time.time()
                self.send2log.emit("Water evacuation process launched")
                self.pending = True
                self.SRT.connectAPM(water=True)
                feedback = str(self.SRT.disconnectAPM())
                self.pending = False
                self.send2log.emit("Water evacuation process over")


class ServerGUI(QMainWindow):
    """Class that operates the server by handshaking clients, receiving and
    sending messages and modifying the GUI display in consequence.

    Owner of the SRTthread that operates the mount"""
    sendToSRTSignal = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.addToLog("Launched server.")

        # Updates the port on which the sever listens
        self.ui.spinBox_port.valueChanged.connect(self.portChanged)

        # Obtains the IP address of host on the network
        ipaddress = get_ipv4_address()
        self.setIPAddress(ipaddress)
        self.IPAddress = QHostAddress(ipaddress)
        self.port = self.ui.spinBox_port.value()

        # Initializes server
        self.server = QTcpServer(self)
        self.server.listen(self.IPAddress, self.port)
        self.client_socket = None
        self.original_stdout = sys.stdout  # Backup of stdout

        self.server.newConnection.connect(self.handleConnection)

        self.SRTThread = SRTThread()
        self.SRTThread.send2socket.connect(self.sendClient)
        self.SRTThread.endMotion.connect(self.sendEndMotion)
        self.SRTThread.send2log.connect(self.receiveLog)
        self.sendToSRTSignal.connect(self.SRTThread.receiveCommand)
        self.SRTThread.start()
        # When in motion, stop asking for position. Tracking not affected

    def handleConnection(self):
        """Method triggered when a new client connects the server
        If a client is already connected, rejects the connection
        Otherwise, executes the handshake (sends CONNECTED to client)"""
        if self.client_socket is None:
            self.client_socket = self.server.nextPendingConnection()
            self.client_socket.readyRead.connect(self.receiveMessage)
            self.client_socket.disconnected.connect(self.disconnectClient)
            self.addToLog("Client connected.")
            self.sendClient("CONNECTED", True)
            # Redirect sys.stdout to send print statements to the client
            self.redirect_stdout()
        else:
            other_client = self.server.nextPendingConnection()
            other_client.write("BUSY".encode())
            self.addToLog(
                "New connection rejected for a client is already connected")
            other_client.disconnectFromHost()
            other_client.deleteLater()

    def disconnectClient(self):
        """Method triggered when the client disconnects from the server
        A safety SRT disconnect is called in order to set the antenna to
        standby mode"""
        if self.client_socket:
            self.addToLog("Client disconnected.")
            self.client_socket = None

            self.restore_stdout()  # Restore sys.stdout to its original state

            self.sendToSRTSignal.emit("disconnect")  # Disconnect SRT

    def redirect_stdout(self):
        """Operates the redirection of stdout to both the server console and
        to the client via a msg with PRINT status"""
        redirector = StdoutRedirector(sys.stdout)
        redirector.emettor.printMsg.connect(self.sendClient)
        sys.stdout = redirector

    def restore_stdout(self):
        """Method triggered when the client disconnects. Print statements are 
        no longer redirected to a client"""

        sys.stdout = self.original_stdout

    # ======= Send / Receive methods =======

    def sendClient(self, msg, verbose=True):
        """Send message to client via TCP socket"""

        if self.client_socket:
            unpausePosLoggingLater = False
            # Pauses the thread spamming the position getter
            if self.SRTThread.posLoggingOn:
                unpausePosLoggingLater = True
                self.SRTThread.pausePositionLogging()

            # Waits for the previous request to have returned to avoid multiple
            # messages sent to client
            time1 = time.time_ns()

            # if self.SRTThread.pending:
            #print("DEBUG : waiting for SRTthread to stop pending...")
            while self.SRTThread.pending:
                pass
            #print(f"DEBUG : SRTthread stopped pending... Sending msg {msg}")
            time2 = time.time_ns()
            # self.addToLog(f"DEBUG: waited {round((time2 - time1) / 1e6)} ms for SRTThread to stop pending (in fn sendClient, "
            #      f"sending message "+msg+")")

            msg = '&' + msg  # Adds a "begin" character
            # Sends the message
            self.client_socket.write(msg.encode())
            if verbose:
                self.addToLog(f"Message sent : {msg}")

            # If tracking, the position spamming thread should be turned back on
            if unpausePosLoggingLater:
                self.SRTThread.unpausePositionLogging()

        else:
            self.addToLog(f"No connected client to send msg : {msg}")

    def sendOK(self, msg):
        """Adds prefix OK to message passed to sendClient. See self.sendClient for more"""
        msg = "OK|" + msg
        self.sendClient(msg)

    def sendWarning(self, msg):
        """Adds prefix WARNING to message passed to sendClient. See self.sendClient for more"""
        msg = "WARNING|" + msg
        self.sendClient(msg)

    def sendError(self, msg):
        """Adds prefix ERROR to message passed to sendClient. See self.sendClient for more"""
        msg = "ERROR|" + msg
        self.sendClient(msg)

    def receiveMessage(self, verbose=True):
        """Method that handles receiving a message from the client via TCP socket"""
        if self.client_socket:
            msg = self.client_socket.readAll().data().decode()

            self.processMsg(msg, verbose)

    def processMsg(self, msg, verbose):
        """Method that processes the command sent from client.
        Note that several messages might be received simultaneously, hence
        the recursive approach taking advantage of the format of messages :
        &{command} {\*args}
        """

        # Sort messages, sometimes several
        if '&' in msg:
            messages = msg.split('&')[1:]

            # If several messages
            if len(messages) > 1:
                print(f"Received concatenated messages : {messages}")
                for message in messages:
                    print(f"processing msg {message}")

                    # Re-add the start character
                    self.processMsg('&' + message, verbose)

                return

            # If only one message
            else:
                msg = messages[0]

        else:
            self.addToLog(
                f"Warning : incorrectly formatted message received : {msg}")
            return  # Ignores incorrectly formatted messages

        self.addToLog("Received: " + msg)

        if not self.SRTThread.pending:
            self.sendToSRTSignal.emit(msg)
        else:
            self.sendWarning("MOVING")

    def sendEndMotion(self, cmd, feedback):
        """Sends message to client when motion is ended. See mainclient script for more detail

        This is a slot connected to signal self.motionThread.endMotion"""
        self.sendClient("PRINT|" + feedback)

        if cmd == "connect":
            self.sendOK("connected")
        elif cmd == "disconnect":
            self.sendOK("disconnected")
        elif cmd.split(" ")[0] in ["trackRA", "trackGal"]:
            self.sendOK("tracking")
        elif feedback == 'measurementReceived':
            self.sendOK("measurementReceived")
        elif feedback == 'finishedPointing':
            self.sendOK("IDLE")
        elif "Pointing aborted" in feedback:
            self.sendWarning(feedback)
            self.sendOK("IDLE")
        elif cmd == 'stopTracking' and str(feedback) == "None":
            self.sendOK("IDLE")
        else:
            print("cmd: "+cmd+", "+"feedback was "+feedback+", printing other")
            self.sendOK("other")

    def receiveLog(self, log):
        """ Slot activated when a thread adds a message to log window"""

        self.addToLog(log)

    # ======== GUI methods ========

    def closeEvent(self, event):
        """Triggered when the server GUI is closed manually"""
        if self.client_socket:
            self.client_socket.disconnectFromHost()
        self.server.close()
        event.accept()

    def addToLog(self, strInput):
        """Adds statement to the GUI log console

        :param strInput: Message to be logged
        :type strInput: str
        """
        self.ui.textBrowser_log.append(
            f"{strftime('%Y-%m-%d %H:%M:%S', localtime())}: " + strInput)

    def setIPAddress(self, stringIn):
        """Sets the IP address displayed in the GUI

        :param stringIn: IP address to update
        :type stringIn: str
        """
        self.ui.lineEdit_IP.setText(stringIn)

    def portChanged(self):
        """Handles manual port changing"""

        print("Port changed.")
        self.port = self.ui.spinBox_port.value()
        self.server.close()
        self.server.listen(self.IPAddress, self.port)


def get_ipv4_address():
    """Method that gets the ipv4 address of host to initialize the server """

    try:
        # Get a list of all network interfaces
        interfaces = QNetworkInterface.allInterfaces()
        for interface in interfaces:
            # Check if the interface is not loopback and is running
            if not interface.flags() & QNetworkInterface.InterfaceFlag.IsLoopBack and \
                    interface.flags() & QNetworkInterface.InterfaceFlag.IsRunning:
                addresses = interface.addressEntries()
                for address in addresses:
                    if address.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                        return address.ip().toString()
        return "Not Found"
    except Exception as e:
        return str(e)


if __name__ == "__main__":
    sys.argv[0] = 'Astro Antenna'
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("Astro Antenna")

    widgetServer = ServerGUI()
    widgetServer.show()
    sys.exit(app.exec())
