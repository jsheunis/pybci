from .Utils.LSLScanner import LSLScanner
from .ThreadClasses.ThreadControlFunctions import DataReceiverThread, MarkerReceiverThread, FeatureProcessorThread, ClassifierThread
from .Configuration.EpochSettings import GlobalEpochSettings, IndividualEpochSetting
from .Configuration.FeatureSettings import GeneralFeatureChoices
import queue, threading, copy
import tensorflow as tf

class PyBCI:
    """The PyBCI object stores data from available lsl time series data streams (EEG, pupilometry, EMG, etc.)
     and holds a configurable number of samples based on lsl marker strings.
     If no marker strings are available on the LSL the class will close and return an error.
     Optional Inputs:
        dataStreams = List of strings, allows user to set custom acceptable EEG stream definitions, if None defaults to streamTypes scan
        markerStream = List of strings, allows user to set custom acceptable Marker stream definitions, if None defaults to markerTypes scan
        streamTypes = List of strings, allows user to set custom acceptable EEG type definitions, ignored if dataStreams not None
        markerTypes = List of strings, allows user to set custom acceptable Marker type definitions, ignored if markerStream not None
        printDebug = boolean, if true prints LSLScanner debug information
    """
    printDebug = True   # boolean, used to toggle print statements from LSLScanner class
    globalEpochSettings = GlobalEpochSettings()
    customEpochSettings = {}
    minimumEpochsRequired = 10
    markerThread = []
    dataThreads = []
    streamChsDropDict= {}
    dataStreams = []
    markerStream = None
    connected = False
    epochCounts = {} # holds markers received, their target ids and number received of each
    classifierInformation = []

    def __init__(self, dataStreams = None, markerStream= None, streamTypes = None, markerTypes = None, printDebug = True,
                 globalEpochSettings = GlobalEpochSettings(), customEpochSettings = {}, streamChsDropDict = {},
                 freqbands = [[1.0, 4.0], [4.0, 8.0], [8.0, 12.0], [12.0, 20.0]], featureChoices = GeneralFeatureChoices(),
                 minimumEpochsRequired = 10, clf= None, model = None):
        self.freqbands = freqbands
        self.featureChoices = featureChoices
        self.globalEpochSettings = globalEpochSettings
        self.customEpochSettings = customEpochSettings
        self.streamChsDropDict = streamChsDropDict
        self.lslScanner = LSLScanner(self, dataStreams, markerStream,streamTypes, markerTypes)
        self.printDebug = printDebug
        self.ConfigureMachineLearning(minimumEpochsRequired,  clf, model) # configure first, connect second
        self.Connect()
       
    def __enter__(self, dataStreams = None, markerStream= None, streamTypes = None, markerTypes = None, printDebug = True,
                 globalEpochSettings = GlobalEpochSettings(), customEpochSettings = {}, streamChsDropDict = {},
                 freqbands = [[1.0, 4.0], [4.0, 8.0], [8.0, 12.0], [12.0, 20.0]], featureChoices = GeneralFeatureChoices()): # with bci
        self.freqbands = freqbands
        self.featureChoices = featureChoices
        self.globalEpochSettings = globalEpochSettings
        self.customEpochSettings = customEpochSettings
        self.streamChsDropDict = streamChsDropDict
        self.lslScanner = LSLScanner(self, dataStreams, markerStream,streamTypes, markerTypes)
        self.printDebug = printDebug
        self.Connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.StopThreads()

    def Connect(self): # Checks valid data and markers streams are present, controls dependant functions by setting self.connected
        if self.lslScanner.CheckAvailableLSL():
            self.__StartThreads()
            self.connected = True
            return True # uses return statements so user can check if connected with bool returned
        else:
            self.connected = False
            return False

    # set test and train boolean for changing  thread operation 
    def TrainMode(self):
        if self.connected:
            if self.printDebug:
                print("PyBCI: Started training...")
            self.trainTestEvent.set()
        else:
            self.Connect()

    def TestMode(self):
        if self.connected:
            if self.printDebug:
                print("PyBCI: Started testing...")
            self.trainTestEvent.clear()
        else:
            self.Connect()

    # Get data from threads
    def CurrentClassifierInfo(self):
        if self.connected:
            self.classifierInfoRetrieveEvent.set()
            classInfo = self.classifierInfoQueue.get()
            self.classifierInfoRetrieveEvent.clear()
            return classInfo
        else:
            self.Connect()

    def CurrentClassifierMarkerGuess(self):
        if self.connected:
            # probably needs check that we're in test mode, maybe debu print if not?
            self.classifierGuessMarkerEvent.set()
            classGuess = self.classifierGuessMarkerQueue.get()
            self.classifierGuessMarkerEvent.clear()
            return classGuess
        else:
            self.Connect()

    def ReceivedMarkerCount(self):
        if self.connected:
            self.markerCountRetrieveEvent.set()
            markers = self.markerCountQueue.get()
            self.markerCountRetrieveEvent.clear()
            return markers
        else:
            self.Connect()

    def __StartThreads(self):
        self.featureQueueTrain = queue.Queue()
        self.featureQueueTest = queue.Queue()
        self.dataQueueTrain = queue.Queue()
        self.dataQueueTest = queue.Queue()
        self.classifierInfoQueue = queue.Queue()
        self.markerCountQueue = queue.Queue()
        self.classifierGuessMarkerQueue = queue.Queue()
        self.classifierGuessMarkerEvent = threading.Event()
        self.closeEvent = threading.Event() # used for closing threads
        self.trainTestEvent = threading.Event()
        self.markerCountRetrieveEvent = threading.Event()
        self.classifierInfoRetrieveEvent = threading.Event()
        self.featureRetrieveEvent = threading.Event() # still needs coding

        self.trainTestEvent.set() # if set we're in train mode, if not we're in test mode, always start in train...
        if self.printDebug:
            print("PyBCI: Starting threads initialisation...")
        # setup data thread
        self.dataThreads = []
        for stream in self.dataStreams:
            if stream.info().name() in self.streamChsDropDict.keys():
                dt = DataReceiverThread(self.closeEvent, self.trainTestEvent, self.dataQueueTrain,self.dataQueueTest, stream,  self.customEpochSettings, self.globalEpochSettings, len(self.dataThreads), self.streamChsDropDict[stream.info().name()])
            else:
                dt = DataReceiverThread(self.closeEvent, self.trainTestEvent, self.dataQueueTrain,self.dataQueueTest, stream,  self.customEpochSettings, self.globalEpochSettings, len(self.dataThreads))
            dt.start()
            self.dataThreads.append(dt)
        self.featureThread = FeatureProcessorThread(self.closeEvent,self.trainTestEvent, self.dataQueueTrain, self.dataQueueTest,
                                                    self.featureQueueTest,self.featureQueueTrain, len(self.dataStreams),
                                                    self.markerCountRetrieveEvent, self.markerCountQueue,
                                                    globalEpochSettings = self.globalEpochSettings, customEpochSettings = self.customEpochSettings)
        self.featureThread.start()
        # marker thread requires data and feature threads to push new markers too
        self.markerThread = MarkerReceiverThread(self.closeEvent,self.trainTestEvent, self.markerStream,self.dataThreads, self.featureThread)
        self.markerThread.start()
        self.classifierThread = ClassifierThread(self.closeEvent,self.trainTestEvent, self.featureQueueTest,self.featureQueueTrain,
                                                 self.classifierInfoQueue, self.classifierInfoRetrieveEvent,
                                                 self.classifierGuessMarkerQueue, self.classifierGuessMarkerEvent,
                                                self.minimumEpochsRequired, clf = self.clf, model = self.model)
        self.classifierThread.start()

    def StopThreads(self):
        self.closeEvent.set()
        self.markerThread.join()
        # wait for all threads to finish processing, probably worth pulling out finalised classifier information stored for later use.
        for dt in self.dataThreads:
            dt.join()
        self.featureThread.join()
        self.classifierThread.join()
        self.connected = False
        if self.printDebug:
            print("PyBCI: Threads stopped.")

    def ConfigureMachineLearning(self, minimumEpochsRequired = 10, clf = None, model = None):
        from sklearn.base import ClassifierMixin
        self.minimumEpochsRequired = minimumEpochsRequired
        if isinstance(clf, ClassifierMixin):
            self.clf = clf
        else:
            self.clf = None
            if self.printDebug:
                print("PyBCI: Error - Invalid sklearn classifier passed to clf, setting to SVM if no tensorflow model passed either.")
        if isinstance(model, tf.keras.Model):
            self.model = model
        else:
            self.model = None
            if self.printDebug:
                print("PyBCI: Error - Invalid tensorflow model passed to model, setting to None.")

    # Could move all configures to a configuration class, might make options into more descriptive classes?
    def ConfigureEpochWindowSettings(self, globalEpochSettings = GlobalEpochSettings(), customEpochSettings = {}):
        """allows globalWindowSettings to be modified, customWindowSettings is a dict with value names for marker strings which will appear on avalable markerStreams """
        valid = False
        for key in customEpochSettings.keys():
            if isinstance(customEpochSettings[key], IndividualEpochSetting):
                valid = True
            else:
                valid = False
                if self.printDebug:
                    print("PyBCI: Error - Invalid datatype passed for customWindowSettings, create dict of wanted markers \
                          using class bci.IndividualEpochSetting() as value to configure individual epoch window settings.")
                    break
        #if isinstance(customWindowSettings[key], GlobalEpochSettings()):
        if valid:   
            self.customEpochSettings = customEpochSettings
            if globalEpochSettings.windowLength > globalEpochSettings.tmax + globalEpochSettings.tmin:
                if self.printDebug:
                    print("PyBCI: Error - windowLength < (tmin+tmax), pass vaid settings to ConfigureEpochWindowSettings")
            else:
                self.globalWindowglobalEpochSettingsSettings = globalEpochSettings
                self.ResetThreadsAfterConfigs()

    def ConfigureFeatures(self, freqbands = [[1.0, 4.0], [4.0, 8.0], [8.0, 12.0], [12.0, 20.0]], featureChoices = GeneralFeatureChoices()):
        # potentially should move configuration to generic class which can be used for both test and train
        if freqbands != None:
            self.freqbands = freqbands
        if featureChoices != None:    
            self.featureChoices = featureChoices
        self.ResetThreadsAfterConfigs()

    def ConfigureDataStreamChannels(self,streamChsDropDict = {}):
        # potentially should move configuration to generic class which can be used for both test and train
        self.streamChsDropDict = streamChsDropDict 
        self.ResetThreadsAfterConfigs()

    def ResetThreadsAfterConfigs(self):
        if self.connected:
            if self.printDebug:
                print("PyBCI: Resetting threads after BCI reconfiguration...")
            self.StopThreads()
            self.Connect()
