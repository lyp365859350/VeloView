import os
import datetime
import paraview.simple as smp
from paraview import vtk

import PythonQt
from PythonQt import QtCore, QtGui

from vtkIOXMLPython import vtkXMLPolyDataWriter
import kiwiviewerExporter
import gridAdjustmentDialog

class AppLogic(object):

    def __init__(self):
        self.playing = False
        self.playDirection = 1
        self.seekPlayDirection = 1
        self.seekPlay = False
        self.targetFps = 30
        self.renderIsPending = False
        self.createStatusBarWidgets()
        self.setupTimers()

    def setupTimers(self):
        self.playTimer = QtCore.QTimer()
        self.playTimer.setSingleShot(True)
        self.playTimer.connect('timeout()', onPlayTimer)

        self.seekTimer = QtCore.QTimer()
        self.seekTimer.setSingleShot(True)
        self.seekTimer.connect('timeout()', seekPressTimeout)

        self.renderTimer = QtCore.QTimer()
        self.renderTimer.setSingleShot(True)
        self.renderTimer.connect('timeout()', forceRender)

    def createStatusBarWidgets(self):

        self.logoLabel = QtGui.QLabel()
        self.logoLabel.setPixmap(QtGui.QPixmap(":/VelodyneHDLPlugin/velodyne_logo.png"))
        self.logoLabel.setScaledContents(True)

        self.filenameLabel = QtGui.QLabel()
        self.statusLabel = QtGui.QLabel()
        self.timeLabel = QtGui.QLabel()


class IconPaths(object):

    play = ':/VelodyneHDLPlugin/media-playback-start.png'
    pause =':/VelodyneHDLPlugin/media-playback-pause.png'
    seekForward = ':/VelodyneHDLPlugin/media-seek-forward.png'
    seekForward2x = ':/VelodyneHDLPlugin/media-seek-forward-2x.png'
    seekForwardHalfx = ':/VelodyneHDLPlugin/media-seek-forward-0.5x.png'
    seekForwardQuarterx = ':/VelodyneHDLPlugin/media-seek-forward-0.25x.png'
    seekForward3x = ':/VelodyneHDLPlugin/media-seek-forward-3x.png'
    seekBackward = ':/VelodyneHDLPlugin/media-seek-backward.png'
    seekBackward2x = ':/VelodyneHDLPlugin/media-seek-backward-2x.png'
    seekBackward3x = ':/VelodyneHDLPlugin/media-seek-backward-3x.png'
    seekBackwardHalfx = ':/VelodyneHDLPlugin/media-seek-backward-0.5x.png'
    seekBackwardQuarterx = ':/VelodyneHDLPlugin/media-seek-backward-0.25x.png'


def hasArrayName(sourceProxy, arrayName):
    '''
    Returns True if the data has non-zero points and has a point data
    attribute with the given arrayName.
    '''
    info = sourceProxy.GetDataInformation().DataInformation

    if info.GetNumberOfPoints() == 0:
        return False

    info = info.GetAttributeInformation(0)
    for i in xrange(info.GetNumberOfArrays()):
        if info.GetArrayInformation(i).GetName() == arrayName:
            return True
    return False


def openData(filename):

    smp.OpenDataFile(filename)
    smp.Show()
    smp.ResetCamera()
    smp.Render()
    smp.SetActiveSource(None)


def colorByIntensity(sourceProxy):

    if not hasArrayName(sourceProxy, 'intensity'):
        return False

    rep = smp.GetDisplayProperties(sourceProxy)
    rep.ColorArrayName = 'intensity'
    rgbPoints = [0.0, 0.0, 0.0, 1.0,
                 100.0, 1.0, 1.0, 0.0,
                 256.0, 1.0, 0.0, 0.0]
    rep.LookupTable = smp.GetLookupTableForArray('intensity', 1, RGBPoints=rgbPoints, ColorSpace="HSV", ScalarRangeInitialized=1.0)
    return True


def getTimeStamp():
    format = '%Y-%m-%d-%H-%M-%S'
    return datetime.datetime.now().strftime(format)


def getDefaultSaveFileName(extension, appendFrameNumber=True):

    sensor = getSensor()
    reader = getReader()

    if sensor:
        return '%s_Velodyne-HDL-Data.%s' % (getTimeStamp(), extension)
    if reader:
        basename =  os.path.splitext(os.path.basename(reader.FileName))[0]
        if appendFrameNumber:
            basename = '%s (Frame %04d)' % (basename, int(app.scene.AnimationTime))
        return '%s.%s' % (basename, extension)


def openSensor(calibrationFile):

    close()

    sensor = smp.VelodyneHDLSource(guiName='Data', CalibrationFile=calibrationFile, CacheSize=100)
    sensor.UpdatePipeline()
    sensor.Start()
    rep = smp.Show(sensor)
    rep.InterpolateScalarsBeforeMapping = 0

    smp.GetActiveView().ViewTime = 0.0

    app.sensor = sensor
    app.colorByInitialized = False
    app.filenameLabel.setText('Live sensor stream.')
    enablePlaybackActions()
    smp.Render()

    showSourceInSpreadSheet(sensor)

    play()


def openPCAP(filename, calibrationFile):

    close()

    progressDialog = QtGui.QProgressDialog('Reading packet file...', '', 0, 0, getMainWindow())
    progressDialog.setCancelButton(None)
    progressDialog.setModal(True)
    progressDialog.show()

    reader = smp.VelodyneHDLReader(guiName='Data', FileName=filename, CalibrationFile=calibrationFile)
    reader.UpdatePipeline()

    progressDialog.close()

    if not hasArrayName(reader, 'intensity'):
        smp.Delete(reader)
        resetCameraToForwardView()
        return

    rep = smp.Show(reader)
    rep.InterpolateScalarsBeforeMapping = 0
    colorByIntensity(reader)

    showSourceInSpreadSheet(reader)

    smp.GetActiveView().ViewTime = 0.0

    app.reader = reader
    app.filenameLabel.setText('File: %s' % os.path.basename(filename))

    updateSliderTimeRange()
    enablePlaybackActions()
    app.actions['actionRecord'].setEnabled(False)

    resetCamera()


def hideMeasurementGrid():
    rep = smp.GetDisplayProperties(app.grid)
    rep.Visibility = 0
    smp.Render()


def showMeasurementGrid():
    rep = smp.GetDisplayProperties(app.grid)
    rep.Visibility = 1
    smp.Render()


def saveCSVCurrentFrame(filename):
    w = smp.DataSetCSVWriter(FileName=filename)
    w.UpdatePipeline()
    smp.Delete(w)


def saveCSVAllFrames(filename):
    saveCSV(filename, getCurrentTimesteps())


def saveCSVFrameRange(filename, frameStart, frameStop):
    timesteps = range(frameStart, frameStop+1)
    saveCSV(filename, timesteps)


def saveCSV(filename, timesteps):

    tempDir = kiwiviewerExporter.tempfile.mkdtemp()
    basenameWithoutExtension = os.path.splitext(os.path.basename(filename))[0]
    outDir = os.path.join(tempDir, basenameWithoutExtension)
    filenameTemplate = os.path.join(outDir, basenameWithoutExtension + ' (Frame %04d).csv')
    os.makedirs(outDir)

    writer = smp.DataSetCSVWriter()
    view = smp.GetActiveView()

    for t in timesteps:
        app.scene.AnimationTime = t
        writer.FileName = filenameTemplate % t
        writer.UpdatePipeline()

    smp.Delete(writer)

    kiwiviewerExporter.zipDir(outDir, filename)
    kiwiviewerExporter.shutil.rmtree(tempDir)


def getTimeStamp():
    format = '%Y-%m-%d-%H-%M-%S'
    return datetime.datetime.now().strftime(format)


def getSaveFileName(title, extension, defaultFileName=None):

    settings = getPVSettings()
    defaultDir = settings.value('VelodyneHDLPlugin/OpenData/DefaultDir', QtCore.QDir.homePath())
    defaultFileName = defaultDir if not defaultFileName else os.path.join(defaultDir, defaultFileName)

    filters = '%s (*.%s)' % (extension, extension)
    selectedFilter = '*.%s' % extension
    fileName = QtGui.QFileDialog.getSaveFileName(getMainWindow(), title,
                        defaultFileName, filters, selectedFilter)

    if fileName:
        settings.setValue('VelodyneHDLPlugin/OpenData/DefaultDir', QtCore.QFileInfo(fileName).absoluteDir().absolutePath())
        return fileName



def getFrameSelectionFromUser():

    dialog = PythonQt.paraview.vvSelectFramesDialog(getMainWindow())
    dialog.setFrameMinimum(app.scene.StartTime)
    dialog.setFrameMaximum(app.scene.EndTime)
    dialog.restoreState()
    accepted = dialog.exec_()
    frameMode = dialog.frameMode()
    frameStart = dialog.frameStart()
    frameStop = dialog.frameStop()
    dialog.setParent(None)

    return accepted, frameMode, frameStart, frameStop


def onSaveCSV():

    if not getNumberOfTimesteps():
        return

    accepted, frameMode, frameStart, frameStop = getFrameSelectionFromUser()
    if not accepted:
        return


    if frameMode == 0:
        fileName = getSaveFileName('Save CSV', 'csv', getDefaultSaveFileName('csv'))
        if fileName:
            saveCSVCurrentFrame(fileName)

    else:
        fileName = getSaveFileName('Save CSV (to zip file)', 'zip', getDefaultSaveFileName('zip', appendFrameNumber=False))
        if fileName:
            if frameMode == 1:
                saveCSVAllFrames(fileName)
            else:
                saveCSVFrameRange(fileName, frameStart, frameStop)


def onSaveScreenshot():

    fileName = getSaveFileName('Save Screenshot', 'png', getDefaultSaveFileName('png'))
    if fileName:
        saveScreenshot(fileName)


def onKiwiViewerExport():

    if not getNumberOfTimesteps():
        return

    accepted, frameMode, frameStart, frameStop = getFrameSelectionFromUser()
    if not accepted:
        return

    defaultFileName = getDefaultSaveFileName('zip', appendFrameNumber=False)
    defaultFileName = defaultFileName.replace('.zip', ' (KiwiViewer).zip')
    fileName = getSaveFileName('Export To KiwiViewer', 'zip', defaultFileName)
    if not fileName:
        return

    stride = 3

    if frameMode == 0:
        saveToKiwiViewer(fileName, [app.scene.AnimationTime])
    elif frameMode == 1:
        saveToKiwiViewer(fileName, range(int(app.scene.StartTime), int(app.scene.EndTime), stride))
    else:
        saveToKiwiViewer(fileName, range(frameStart, frameStop, stride))



def saveToKiwiViewer(filename, timesteps):

    tempDir = kiwiviewerExporter.tempfile.mkdtemp()
    outDir = os.path.join(tempDir, os.path.splitext(os.path.basename(filename))[0])

    os.makedirs(outDir)

    filenames = exportToDirectory(outDir, timesteps)

    kiwiviewerExporter.writeJsonData(outDir, smp.GetActiveView(), smp.GetDisplayProperties(), filenames)

    kiwiviewerExporter.zipDir(outDir, filename)
    kiwiviewerExporter.shutil.rmtree(tempDir)


def exportToDirectory(outDir, timesteps):

    filenames = []

    alg = smp.GetActiveSource().GetClientSideObject()

    writer = vtkXMLPolyDataWriter()
    writer.SetDataModeToAppended()
    writer.EncodeAppendedDataOff()
    writer.SetCompressorTypeToZLib()

    for t in timesteps:

        filename = 'frame_%04d.vtp' % t
        filenames.append(filename)

        app.scene.AnimationTime = t
        polyData = vtk.vtkPolyData()
        polyData.ShallowCopy(alg.GetOutput())

        writer.SetInputData(polyData)
        writer.SetFileName(os.path.join(outDir, filename))
        writer.Update()

    return filenames


def close():

    stop()
    unloadData()
    resetCameraToForwardView()
    app.filenameLabel.setText('')
    app.statusLabel.setText('')
    app.timeLabel.setText('')
    updateSliderTimeRange()
    disablePlaybackActions()
    app.actions['actionRecord'].setChecked(False)


def seekForward():

  if app.playing:
    if app.playDirection < 0 or app.playDirection == 5:
        app.playDirection = 0
    app.playDirection += 1
    updateSeekButtons()

  else:
    gotoNext()


def seekBackward():

  if app.playing:
    if app.playDirection > 0 or app.playDirection == -5:
        app.playDirection = 0
    app.playDirection -= 1
    updateSeekButtons()

  else:
    gotoPrevious()


def seekPressTimeout():
    app.seekPlay = True
    onPlayTimer()


def seekForwardPressed():
    app.seekPlayDirection = 1
    if not app.playing:
        app.seekTimer.start(500)


def seekForwardReleased():
    app.seekTimer.stop()
    app.seekPlay = False


def seekBackwardPressed():
    app.seekPlayDirection = -1
    if not app.playing:
        app.seekTimer.start(500)


def seekBackwardReleased():
    seekForwardReleased()


def updateSeekButtons():

    icons = {
              -5 : IconPaths.seekBackwardQuarterx,
              -4 : IconPaths.seekBackwardHalfx,
              -3 : IconPaths.seekBackward3x,
              -2 : IconPaths.seekBackward2x,
              -1 : IconPaths.seekBackward,
               1 : IconPaths.seekForward,
               2 : IconPaths.seekForward2x,
               3 : IconPaths.seekForward3x,
               4 : IconPaths.seekForwardHalfx,
               5 : IconPaths.seekForwardQuarterx,
            }

    setActionIcon('actionSeek_Backward', icons[app.playDirection] if app.playDirection < 0 else IconPaths.seekBackward)
    setActionIcon('actionSeek_Forward', icons[app.playDirection] if app.playDirection > 0 else IconPaths.seekForward)

    fpsMap = {-5:5, 5:5, -4:11, 4:11}
    fpsDefault = 30
    app.targetFps = fpsMap.get(app.playDirection, fpsDefault)


def disablePlaybackActions():
    for action in ('Play', 'Record', 'Seek_Forward', 'Seek_Backward', 'Go_To_Start', 'Go_To_End'):
        app.actions['action'+action].setEnabled(False)


def enablePlaybackActions():
    for action in ('Play', 'Record', 'Seek_Forward', 'Seek_Backward', 'Go_To_Start', 'Go_To_End'):
        app.actions['action'+action].setEnabled(True)


def recordFile(filename):

    sensor = getSensor()
    if sensor:
        stopStream()
        sensor.OutputFile = filename
        app.statusLabel.setText('  Recording file: %s.' % os.path.basename(filename))
        if app.playing:
            startStream()


def onRecord():

    recordAction = app.actions['actionRecord']

    if not recordAction.isChecked():
        stopRecording()

    else:

        fileName = getSaveFileName('Choose Output File', 'pcap', getDefaultSaveFileName('pcap'))
        if not fileName:
            recordAction.setChecked(False)
            return

        recordFile(fileName)


def stopRecording():

    app.statusLabel.setText('')
    sensor = getSensor()
    if sensor:
        stopStream()
        sensor.OutputFile = ''
        if app.playing:
            startStream()


def startStream():

    sensor = getSensor()
    if sensor:
        sensor.Start()


def stopStream():
    sensor = getSensor()
    if sensor:
        sensor.Stop()


def pollSource():

    source = getSensor()
    reader = getReader()
    if source is not None:
        source.Poll()
        source.UpdatePipelineInformation()
    return source or reader


def getCurrentTimesteps():
    source = pollSource()
    return list(source.TimestepValues) if source is not None else []


def getNumberOfTimesteps():
    return getTimeKeeper().getNumberOfTimeStepValues()


def togglePlay():
    setPlayMode(not app.playing)

def play():
    setPlayMode(True)


def stop():
    setPlayMode(False)


def onPlayTimer():

    if app.playing or app.seekPlay:

        startTime = vtk.vtkTimerLog.GetUniversalTime()

        playbackTick()

        fpsDelayMilliseconds = int(1000 / app.targetFps)
        elapsedMilliseconds = int((vtk.vtkTimerLog.GetUniversalTime() - startTime)*1000)
        waitMilliseconds = fpsDelayMilliseconds - elapsedMilliseconds
        app.playTimer.start(waitMilliseconds if waitMilliseconds > 0 else 1)


def setPlayMode(mode):

    if not getReader() and not getSensor():
        return

    app.playing = mode

    if mode:
        startStream()
        setActionIcon('actionPlay', IconPaths.pause)
        app.playTimer.start(33)
        if app.scene.AnimationTime == app.scene.EndTime:
            app.scene.AnimationTime = app.scene.StartTime

    else:
      stopStream()
      setActionIcon('actionPlay', IconPaths.play)
      app.playDirection = 1
      updateSeekButtons()


def gotoStart():
    pollSource()
    app.scene.GoToFirst()


def gotoEnd():
    pollSource()
    app.scene.GoToLast()


def gotoNext():
    pollSource()
    app.scene.GoToNext()


def gotoPrevious():
    pollSource()
    app.scene.GoToPrevious()


def playbackTick():

  sensor = getSensor()
  reader = getReader()
  view = smp.GetActiveView()

  if sensor is not None:
      timesteps = getCurrentTimesteps()
      if not timesteps:
          return

      if view.ViewTime == timesteps[-1]:
          return

      if not app.colorByInitialized:
          sensor.UpdatePipeline()
          if colorByIntensity(sensor):
              app.colorByInitialized = True
              resetCamera()

      app.scene.GoToLast()

  elif reader is not None:

      numberOfTimesteps = getNumberOfTimesteps()
      if not numberOfTimesteps:
          return

      step = app.seekPlayDirection if app.seekPlay else app.playDirection
      stepMap = {4:1, 5:1, -4:-1, -5:-1}
      step = stepMap.get(step, step)
      newTime = app.scene.AnimationTime + step

      if app.actions['actionLoop'].isChecked():
          newTime = newTime % numberOfTimesteps
      else:
          newTime = max(app.scene.StartTime, min(newTime, app.scene.EndTime))

          # stop playback when it reaches the first or last timestep
          if newTime in (app.scene.StartTime, app.scene.EndTime):
              stop()

      app.scene.AnimationTime = newTime


def unloadData():

    reader = getReader()
    sensor = getSensor()

    if reader is not None:
        smp.Delete(reader)
        app.reader = None

    if sensor is not None:
        sensor.Stop()
        smp.Delete(sensor)
        app.sensor = None

    clearSpreadSheetView()


def getReader():
    return getattr(app, 'reader', None)


def getSensor():
    return getattr(app, 'sensor', None)


def setCalibrationFile(calibrationFile):

    reader = getReader()
    sensor = getSensor()

    if reader is not None:
        reader.CalibrationFile = calibrationFile
        smp.Render()

    elif sensor is not None:
        sensor.CalibrationFile = calibrationFile
        # no need to render now, calibration file will be used on the next frame


def resetCamera():


    def subtract(a, b):
        result = range(3)
        vtk.vtkMath.Subtract(a, b, result)
        return result

    def cross(a, b):
        result = range(3)
        vtk.vtkMath.Cross(a, b, result)
        return result

    view = smp.GetActiveView()

    foc = list(view.CenterOfRotation)
    pos = list(view.CameraPosition)

    viewDirection = subtract(foc, pos)

    view.CameraPosition = subtract([0, 0, 0], viewDirection)
    view.CameraFocalPoint = [0, 0, 0]
    view.CenterOfRotation = [0, 0, 0]

    vtk.vtkMath.Normalize(viewDirection)

    perp = cross(viewDirection, [0, 0, 1])
    viewUp = cross(perp, viewDirection)
    view.CameraViewUp = viewUp

    view.StillRender()


def resetCameraToBirdsEyeView(view=None):

    view = view or smp.GetActiveView()
    view.CameraFocalPoint = [0, 0, 0]
    view.CameraViewUp = [0, 1, 0]
    view.CameraPosition = [0, 0, 40]
    view.CenterOfRotation = [0, 0, 0]
    smp.Render(view)


def resetCameraToForwardView(view=None):

    view = view or smp.GetActiveView()
    view.CameraFocalPoint = [0,0,0]
    view.CameraViewUp = [0, 0.27, 0.96]
    view.CameraPosition = [0, -72, 18.0]
    view.CenterOfRotation = [0, 0, 0]
    smp.Render(view)


def saveScreenshot(filename):
    smp.WriteImage(filename)

    # reload the saved screenshot as a pixmap
    screenshot = QtGui.QPixmap()
    screenshot.load(filename)

    # create a new pixmap with the status bar widget painted at the bottom
    statusBar = QtGui.QPixmap.grabWidget(getMainWindow().statusBar())
    composite = QtGui.QPixmap(screenshot.width(), screenshot.height() + statusBar.height())
    painter = QtGui.QPainter()
    painter.begin(composite)
    painter.drawPixmap(screenshot.rect(), screenshot, screenshot.rect())
    painter.drawPixmap(statusBar.rect().translated(0, screenshot.height()), statusBar, statusBar.rect())
    painter.end()

    # save final screenshot
    composite.save(filename)


def getRenderViewWidget():
    return getPQView(smp.GetRenderView()).getWidget()


def getPQView(view):
    app = PythonQt.paraview.pqApplicationCore.instance()
    model = app.getServerManagerModel()
    return PythonQt.paraview.pqPythonQtMethodHelpers.findProxyItem(model, view.SMProxy)


def getSpreadSheetViewProxy():
    for p in smp.servermanager.ProxyManager():
        if p.GetXMLName() == 'SpreadSheetView':
            return p


def clearSpreadSheetView():
    view = getSpreadSheetViewProxy()
    view.Representations = []


def showSourceInSpreadSheet(source):

    spreadSheetView = getSpreadSheetViewProxy()
    smp.Show(source, spreadSheetView)

    # Work around a bug where the 'Showing' combobox doesn't update.
    # Calling hide and show again will trigger the refresh.
    smp.Hide(source, spreadSheetView)
    smp.Show(source, spreadSheetView)


def createGrid(view=None):

    view = view or smp.GetActiveView()
    grid = smp.VelodyneHDLGridSource(guiName='Measurement Grid')
    rep = smp.Show(grid, view)
    rep.DiffuseColor = [0.2, 0.2, 0.2]
    rep.Pickable = 0
    rep.Visibility = 0
    smp.SetActiveSource(None)
    return grid


def hideGrid():
    smp.GetDisplayProperties(app.grid).Hide()


def showGrid():
    smp.GetDisplayProperties(app.grid).Show()


def start():

    global app
    app = AppLogic()
    app.scene = smp.GetAnimationScene()

    view = smp.GetActiveView()
    view.Background = [0.0, 0.0, 0.0]
    view.Background2 = [0.0, 0.0, 0.2]
    view.UseGradientBackground = True
    smp._DisableFirstRenderCameraReset()
    smp.GetActiveView().LODThreshold = 1e100
    app.grid = createGrid()

    resetCameraToForwardView()

    setupActions()
    disablePlaybackActions()
    setupStatusBar()
    setupTimeSliderWidget()
    hideColorByComponent()
    getTimeKeeper().connect('timeChanged()', onTimeChanged)


def findQObjectByName(widgets, name):
    for w in widgets:
        if w.objectName == name:
            return w


def getMainWindow():
    return findQObjectByName(QtGui.QApplication.topLevelWidgets(), 'vvMainWindow')


def getPVApplicationCore():
    return PythonQt.paraview.pqPVApplicationCore.instance()


def getPVSettings():
    return getPVApplicationCore().settings()


def getTimeKeeper():
    return getPVApplicationCore().getActiveServer().getTimeKeeper()


def getPlaybackToolBar():
    return findQObjectByName(getMainWindow().children(), 'playbackToolbar')


def quit():
    PythonQt.QtGui.QApplication.instance().quit()
exit = quit


def addShortcuts(keySequenceStr, function):
    shortcut = PythonQt.QtGui.QShortcut(PythonQt.QtGui.QKeySequence(keySequenceStr), getMainWindow())
    shortcut.connect("activated()", function)


def setupTimeSliderWidget():

    frame = QtGui.QWidget()
    layout = QtGui.QHBoxLayout(frame)
    spinBox = QtGui.QSpinBox()
    spinBox.setMinimum(0)
    spinBox.setMaximum(100)
    spinBox.setValue(0)
    slider = QtGui.QSlider(QtCore.Qt.Horizontal)
    slider.setMaximumWidth(160)
    slider.connect('valueChanged(int)', onTimeSliderChanged)
    spinBox.connect('valueChanged(int)', onTimeSliderChanged)
    slider.setEnabled(False)
    spinBox.setEnabled(False)
    layout.addWidget(slider)
    layout.addWidget(spinBox)
    layout.addStretch()

    toolbar = getPlaybackToolBar()
    toolbar.addWidget(frame)
    app.timeSlider = slider
    app.timeSpinBox = spinBox



def updateSliderTimeRange():

    timeKeeper = getTimeKeeper()

    frame = int(app.scene.AnimationTime)
    lastFrame = int(app.scene.EndTime)

    for widget in (app.timeSlider, app.timeSpinBox):
        widget.setMinimum(0)
        widget.setMaximum(lastFrame)
        widget.setSingleStep(1)
        if hasattr(widget, 'setPageStep'):
            widget.setPageStep(10)
        widget.setValue(frame)
        widget.setEnabled(getNumberOfTimesteps())


def scheduleRender():
    if not app.renderIsPending:
        app.renderIsPending = True
        app.renderTimer.start(33)


def forceRender():
    smp.Render()
    app.renderIsPending = False


def onTimeSliderChanged(frame):
    app.scene.AnimationTime = frame


def setupStatusBar():

    statusBar = getMainWindow().statusBar()
    statusBar.addPermanentWidget(app.logoLabel)
    statusBar.addWidget(app.filenameLabel)
    statusBar.addWidget(app.statusLabel)
    statusBar.addWidget(app.timeLabel)


def setActionIcon(actionName, iconPath):
    app.actions[actionName].setIcon(QtGui.QIcon(QtGui.QPixmap(iconPath)))


def onTimeChanged():

    frame = int(getTimeKeeper().getTime())
    app.timeLabel.setText('  Frame: %s' % frame)

    for widget in (app.timeSlider, app.timeSpinBox):
        widget.blockSignals(True)
        widget.setValue(frame)
        widget.blockSignals(False)


def onGridProperties():
    if gridAdjustmentDialog.showDialog(getMainWindow(), app.grid):
        smp.Render()


def hideColorByComponent():
    getMainWindow().findChild('pqColorToolbar').findChild('pqDisplayColorWidget').findChildren('QComboBox')[1].hide()


def setupActions():

    actions = getMainWindow().findChildren('QAction')
    app.actions = {}
    for a in actions:
        app.actions[a.objectName] = a

    app.actions['actionClose'].connect('triggered()', close)
    app.actions['actionPlay'].connect('triggered()', togglePlay)
    app.actions['actionRecord'].connect('triggered()', onRecord)
    app.actions['actionSave_CSV'].connect('triggered()', onSaveCSV)
    app.actions['actionSave_Screenshot'].connect('triggered()', onSaveScreenshot)
    app.actions['actionExport_To_KiwiViewer'].connect('triggered()', onKiwiViewerExport)
    app.actions['actionReset_Camera'].connect('triggered()', resetCamera)
    app.actions['actionGrid_Properties'].connect('triggered()', onGridProperties)
    app.actions['actionSeek_Forward'].connect('triggered()', seekForward)
    app.actions['actionSeek_Backward'].connect('triggered()', seekBackward)
    app.actions['actionGo_To_End'].connect('triggered()', gotoEnd)
    app.actions['actionGo_To_Start'].connect('triggered()', gotoStart)



    buttons = {}
    for button in getPlaybackToolBar().findChildren('QToolButton'):
        buttons[button.text] = button


    buttons['Seek Forward'].connect('pressed()', seekForwardPressed)
    buttons['Seek Forward'].connect('released()', seekForwardReleased)

    buttons['Seek Backward'].connect('pressed()', seekBackwardPressed)
    buttons['Seek Backward'].connect('released()', seekBackwardReleased)

