import QtQuick
import QtQuick.Controls
import Quickshell.Io
import qs.Commons
import qs.Ui

// Beeper: unread conversations only. Click a row to open that chat in Beeper
// Desktop and mark it read.
//
// Data comes from `bin/omarchy-beeper`, which is the only thing that talks to
// the local Beeper API. This file never sees the token and never opens a
// socket; it runs the CLI and parses one JSON object from stdout.
//
// Every string below the header is text someone else wrote, so each Text
// carries `textFormat: Text.PlainText`.
Panel {
  id: root

  moduleName: "prdlk.beeper"
  ipcTarget: "prdlk.beeper"

  readonly property string script:
    Qt.resolvedUrl("bin/omarchy-beeper").toString().replace(/^file:\/\//, "")

  readonly property string iconOpenApp: "\uF08E"
  readonly property string iconMarkAll: "\uF2B6"
  readonly property string iconConfirm: "\uF00C"
  readonly property string iconPrev: "\uF053"
  readonly property string iconNext: "\uF054"

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color accent: Color.accent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  property var messages: []
  property int unread: 0
  property var inboxes: []
  property bool reachable: true
  property string errorText: ""
  property string warningText: ""
  property string pendingId: ""
  property var readQueue: []
  property var dismissedIds: ({})
  property bool markAllArmed: false
  property bool markAllBusy: false
  property string actionWarning: ""
  property int cursor: -1

  property string pageToken: ""
  property var pageStack: []
  property string nextPage: ""
  property int accountCount: 0
  readonly property bool hasPrev: pageStack.length > 0
  readonly property bool hasNext: nextPage !== ""

  property double now: 0

  readonly property int badgeCount: unread
  readonly property bool hasUnread: unread > 0

  readonly property int badgeWidth: badgeCount > 0
    ? Math.max(Style.space(12), String(badgeCount).length * Style.space(6) + Style.space(8))
    : 0
  readonly property int barContentWidth: Style.bar.iconFont + badgeWidth + Style.space(5)
  readonly property int barSlot: barContentWidth + Style.space(10)

  implicitWidth: bar && bar.vertical ? (bar ? bar.barSize : Style.bar.sizeHorizontal) : barSlot
  implicitHeight: bar && bar.vertical ? barSlot : (bar ? bar.barSize : Style.bar.sizeHorizontal)

  // Page tokens are plain offsets produced by the CLI.
  function validToken(t) {
    return /^[0-9]{1,9}$/.test(String(t))
  }

  // beeper:<base64url of the chat id>, exactly what `list` emits.
  function validId(id) {
    return /^beeper:[A-Za-z0-9_-]{1,1024}$/.test(String(id))
  }

  readonly property int pageSize: {
    var n = parseInt(setting("max", 25), 10)
    if (!(n > 0)) n = 25
    return Math.max(1, Math.min(50, n))
  }
  readonly property int refreshMs: {
    var n = parseInt(setting("refreshIntervalSec", 60), 10)
    if (!(n > 0)) n = 60
    return Math.max(15, Math.min(3600, n)) * 1000
  }

  function refresh() {
    if (listProc.running || root.markAllBusy) return
    var argv = [root.script, "list", "--limit", String(root.pageSize)]
    if (pageToken !== "" && validToken(pageToken)) argv.push("--page", pageToken)
    listProc.command = argv
    listProc.running = true
  }

  function goNextPage() {
    if (!hasNext || listProc.running || root.markAllBusy) return
    var stack = pageStack.slice()
    stack.push(pageToken)
    pageStack = stack
    pageToken = nextPage
    cursor = -1
    refresh()
  }

  function goPrevPage() {
    if (!hasPrev || listProc.running || root.markAllBusy) return
    var stack = pageStack.slice()
    pageToken = stack.pop()
    pageStack = stack
    cursor = -1
    refresh()
  }

  function firstPage() {
    pageToken = ""
    pageStack = []
    cursor = -1
  }

  function titleText() {
    if (root.unread === 1) return "1 unread chat"
    return root.unread + " unread chats"
  }

  function tooltipFor() {
    if (!root.reachable)
      return root.errorText !== "" ? root.errorText : "Beeper unreachable"
    if (!root.hasUnread) return "No unread chats"
    return root.titleText()
  }

  // "WhatsApp 3 · Slack 1", newest networks first, so the header says where
  // the pile is without opening every row.
  function inboxSummary() {
    var parts = []
    var list = root.inboxes || []
    for (var i = 0; i < list.length && parts.length < 3; i++) {
      var box = list[i] || {}
      var n = parseInt(box.unread, 10)
      var name = String(box.account || "")
      if (!(n > 0) || name === "") continue
      parts.push(name + " " + n)
    }
    return parts.join("  ·  ")
  }

  function rememberDismissed(id) {
    var next = Object.assign({}, root.dismissedIds)
    next[id] = true
    root.dismissedIds = next
  }

  function dismissLocal(id) {
    rememberDismissed(id)
    var next = []
    for (var i = 0; i < messages.length; i++) {
      if (messages[i].id !== id) next.push(messages[i])
    }
    messages = next
    if (unread > 0) unread -= 1
    if (cursor > messages.length - 1) cursor = messages.length - 1
  }

  function enqueueRead(id) {
    var q = root.readQueue.slice()
    q.push(id)
    root.readQueue = q
    root.pumpRead()
  }

  function pumpRead() {
    if (readProc.running || root.readQueue.length === 0) return
    var q = root.readQueue.slice()
    var id = q.shift()
    root.readQueue = q
    root.pendingId = id
    readProc.command = [root.script, "read", id]
    readProc.running = true
  }

  // Beeper has no web URL for a chat, so opening one means asking Beeper
  // Desktop to focus it through the same local API.
  function openChat(message) {
    if (root.markAllBusy) return
    if (!message || !validId(message.id)) return
    openProc.command = [root.script, "open", message.id]
    openProc.running = true
    dismissLocal(message.id)
    enqueueRead(message.id)
    close()
  }

  function openBeeper() {
    if (root.markAllBusy) return
    openProc.command = [root.script, "open"]
    openProc.running = true
    close()
  }

  function markCursorRead() {
    if (root.markAllBusy) return
    if (cursor < 0 || cursor >= messages.length) return
    var message = messages[cursor]
    if (!message || !validId(message.id)) return
    cancelMarkAllConfirm()
    dismissLocal(message.id)
    enqueueRead(message.id)
  }

  function cancelMarkAllConfirm() {
    markAllArmed = false
    if (markAllArmTimer.running) markAllArmTimer.stop()
  }

  function requestMarkAll() {
    if (!root.hasUnread || !root.reachable || root.markAllBusy) return
    if (!root.markAllArmed) {
      root.markAllArmed = true
      markAllArmTimer.restart()
      return
    }
    root.cancelMarkAllConfirm()
    root.markAllBusy = true
    readAllProc.command = [root.script, "read-all"]
    readAllProc.running = true
  }

  function applyReadAllPayload(text) {
    root.markAllBusy = false
    root.cancelMarkAllConfirm()
    root.dismissedIds = ({})
    try {
      var data = JSON.parse(text)
      var marked = parseInt(data.marked, 10)
      if (!(marked > 0)) marked = 0
      if (data.ok === true) {
        root.actionWarning = data.warning || ""
        firstPage()
        refresh()
        return
      }
      root.actionWarning = data.error || "could not mark all as read"
      if (marked > 0) {
        firstPage()
        refresh()
      }
    } catch (e) {
      root.actionWarning = "unexpected output from omarchy-beeper"
    }
  }

  function moveCursor(delta) {
    if (messages.length === 0) return
    var next = cursor + delta
    if (next < 0) next = 0
    if (next > messages.length - 1) next = messages.length - 1
    cursor = next
    list.positionViewAtIndex(next, ListView.Contain)
  }

  function activateCursor() {
    if (cursor < 0 || cursor >= messages.length) return
    openChat(messages[cursor])
  }

  function ageLabel(ts) {
    if (!ts || ts <= 0) return ""
    var seconds = Math.max(0, root.now - ts)
    if (seconds < 60) return "now"
    if (seconds < 3600) return Math.floor(seconds / 60) + "m"
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h"
    if (seconds < 604800) return Math.floor(seconds / 86400) + "d"
    if (seconds < 2592000) return Math.floor(seconds / 604800) + "w"
    return Qt.formatDate(new Date(ts * 1000), "d MMM")
  }

  function oneLine(value) {
    return String(value || "").replace(/\s+/g, " ").trim()
  }

  function applyPayload(text) {
    try {
      var data = JSON.parse(text)
      reachable = data.ok === true
      errorText = data.error || ""
      warningText = reachable ? (data.warning || "") : ""
      // Keep actionWarning across this refresh: a write can fail while list works.
      if (!reachable) return
      var incoming = data.messages || []
      var kept = []
      var dropped = 0
      for (var i = 0; i < incoming.length; i++) {
        var row = incoming[i]
        if (row && root.dismissedIds[row.id]) dropped += 1
        else kept.push(row)
      }
      messages = kept
      unread = Math.max(0, (data.unread || 0) - dropped)
      inboxes = data.inboxes || []
      accountCount = data.accountCount || 0
      nextPage = validToken(data.nextPage) ? data.nextPage : ""
      if (cursor > messages.length - 1) cursor = messages.length - 1
    } catch (e) {
      reachable = false
      errorText = "unexpected output from omarchy-beeper"
    }
  }

  onOpenedChanged: {
    if (opened) {
      now = Date.now() / 1000
      refresh()
    } else {
      cursor = -1
      firstPage()
      cancelMarkAllConfirm()
      actionWarning = ""
      dismissedIds = ({})
    }
  }

  Component.onCompleted: now = Date.now() / 1000

  Process {
    id: listProc
    stdout: StdioCollector {
      onStreamFinished: root.applyPayload(text)
    }
  }

  Process {
    id: readProc
    onExited: function(exitCode) {
      root.pendingId = ""
      if (root.readQueue.length > 0) {
        root.pumpRead()
        return
      }
      root.refresh()
    }
  }

  Process {
    id: openProc
  }

  Process {
    id: readAllProc
    stdout: StdioCollector {
      onStreamFinished: root.applyReadAllPayload(text)
    }
  }

  Timer {
    interval: root.refreshMs
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: {
      root.now = Date.now() / 1000
      root.refresh()
    }
  }

  Timer {
    id: markAllArmTimer
    interval: 4000
    repeat: false
    onTriggered: root.markAllArmed = false
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    opacity: root.reachable ? 1 : 0.5
    slotSize: root.barSlot
    opticalSize: root.barContentWidth
    tooltipText: root.tooltipFor()

    iconComponent: Component {
      Item {
        Row {
          anchors.centerIn: parent
          spacing: Style.space(5)

          BeeperIcon {
            anchors.verticalCenter: parent.verticalCenter
            color: button.foreground
            dotColor: button.foreground
            hasMail: root.hasUnread && root.reachable
          }

          Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.reachable && root.badgeCount > 0
            height: Style.space(12)
            width: root.badgeWidth
            radius: height / 2
            color: Qt.rgba(button.foreground.r, button.foreground.g,
                           button.foreground.b, 0.14)

            Text {
              anchors.centerIn: parent
              text: root.badgeCount
              textFormat: Text.PlainText
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              renderType: Text.NativeRendering
              color: button.foreground
            }
          }
        }
      }
    }

    onPressed: function(b) {
      if (b === Qt.RightButton) {
        root.openBeeper()
      } else if (b === Qt.MiddleButton) {
        root.refresh()
      } else {
        root.toggle()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: {
        if (root.markAllArmed) {
          root.cancelMarkAllConfirm()
          return
        }
        root.close()
      }
      onMoveRequested: function(dx, dy) { if (dy !== 0) root.moveCursor(dy) }
      onActivateRequested: root.activateCursor()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var onCursor = root.cursor >= 0 && root.cursor < root.messages.length
        if (t === "o" && onCursor)
          root.openChat(root.messages[root.cursor])
        else if (t === "i")
          root.openBeeper()
        else if (t === "a")
          root.markCursorRead()
        else if (t === "A")
          root.requestMarkAll()
        else if (t === "n")
          root.goNextPage()
        else if (t === "p")
          root.goPrevPage()
      }

      Column {
        id: content
        anchors.fill: parent
        spacing: Style.space(6)

        Item {
          width: parent.width
          height: Math.max(heading.implicitHeight, openAppButton.height)

          Column {
            id: heading
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: headerActions.left
            anchors.rightMargin: Style.space(8)
            spacing: Style.space(1)

            PanelSectionHeader {
              width: parent.width
              text: root.titleText()
              textFormat: Text.PlainText
              elide: Text.ElideRight
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              visible: root.accountCount > 1 && text !== ""
              text: root.inboxSummary()
              textFormat: Text.PlainText
              elide: Text.ElideRight
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              color: Qt.darker(root.foreground, 1.6)
            }
          }

          Row {
            id: headerActions
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            PanelActionButton {
              id: markAllButton
              visible: root.hasUnread && root.reachable
              enabled: root.hasUnread && root.reachable && !root.markAllBusy
              iconText: root.markAllArmed || root.markAllBusy
                ? root.iconConfirm : root.iconMarkAll
              tooltipText: root.markAllBusy
                ? "Marking unread chats as read…"
                : (root.markAllArmed
                  ? "Click again to confirm"
                  : "Mark all unread as read (A)")
              foreground: root.foreground
              hoverColor: root.accent
              fontFamily: root.fontFamily
              fontSize: Style.font.iconSmall
              onClicked: root.requestMarkAll()
            }

            PanelActionButton {
              id: openAppButton
              enabled: !root.markAllBusy
              iconText: root.iconOpenApp
              tooltipText: "Open Beeper Desktop (i)"
              foreground: root.foreground
              hoverColor: root.accent
              fontFamily: root.fontFamily
              fontSize: Style.font.iconSmall
              onClicked: root.openBeeper()
            }
          }
        }

        PanelSeparator { width: parent.width }

        Item {
          width: parent.width
          height: root.reachable ? 0 : staleWarning.implicitHeight + Style.space(6)
          visible: !root.reachable

          Text {
            id: staleWarning
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            text: root.errorText !== ""
              ? root.errorText
              : "Could not reach Beeper. Showing the last list."
            textFormat: Text.PlainText
            elide: Text.ElideRight
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: bar ? bar.urgent : Color.urgent
          }
        }

        Item {
          width: parent.width
          height: (root.reachable && root.warningText !== "")
            ? partialWarning.implicitHeight + Style.space(6) : 0
          visible: root.reachable && root.warningText !== ""

          Text {
            id: partialWarning
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            text: root.warningText
            textFormat: Text.PlainText
            elide: Text.ElideRight
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: bar ? bar.urgent : Color.urgent
          }
        }

        Item {
          width: parent.width
          height: (root.actionWarning !== "" && !root.markAllBusy)
            ? actionWarningLabel.implicitHeight + Style.space(6) : 0
          visible: root.actionWarning !== "" && !root.markAllBusy

          Text {
            id: actionWarningLabel
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            text: root.actionWarning
            textFormat: Text.PlainText
            elide: Text.ElideRight
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: bar ? bar.urgent : Color.urgent
          }
        }

        Item {
          width: parent.width
          height: root.markAllBusy ? markAllBusyLabel.implicitHeight + Style.space(6) : 0
          visible: root.markAllBusy

          Text {
            id: markAllBusyLabel
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            text: "Marking unread chats as read…"
            textFormat: Text.PlainText
            elide: Text.ElideRight
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: Qt.darker(root.foreground, 1.6)
          }
        }

        ListView {
          id: list
          width: parent.width
          visible: root.messages.length > 0
          clip: true
          opacity: root.markAllBusy ? 0.4 : 1
          enabled: !root.markAllBusy
          model: root.messages
          spacing: Style.space(1)
          boundsBehavior: Flickable.StopAtBounds
          flickableDirection: Flickable.VerticalFlick
          interactive: contentHeight > height && !root.markAllBusy
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          readonly property int cap: {
            var chrome = Style.space(70)
            if (root.hasPrev || root.hasNext) chrome += Style.space(38)
            if (!root.reachable) chrome += Style.space(24)
            if (root.reachable && root.warningText !== "") chrome += Style.space(24)
            if (root.actionWarning !== "" && !root.markAllBusy) chrome += Style.space(24)
            if (root.markAllBusy) chrome += Style.space(24)
            return Math.max(Style.space(200),
                            panel.availableCardHeight - panel.verticalContentInset - chrome)
          }
          height: Math.min(contentHeight, cap)

          delegate: Rectangle {
            id: row
            required property var modelData
            required property int index

            readonly property bool active: root.cursor === row.index || rowMouse.containsMouse

            width: list.width - (list.interactive ? Style.space(10) : 0)
            height: rowContent.implicitHeight + Style.space(10)
            radius: Style.cornerRadius
            opacity: root.pendingId === modelData.id ? 0.4 : 1
            color: active
              ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.08)
              : "transparent"

            Behavior on color { ColorAnimation { duration: 80 } }

            MouseArea {
              id: rowMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onContainsMouseChanged: if (containsMouse) root.cursor = row.index
              onClicked: if (!root.markAllBusy) root.openChat(row.modelData)
            }

            Column {
              id: rowContent
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(6)
              anchors.rightMargin: Style.space(6)
              spacing: Style.space(2)

              Item {
                width: parent.width
                height: subject.implicitHeight

                Row {
                  id: line
                  anchors.left: parent.left
                  anchors.right: age.left
                  anchors.rightMargin: Style.space(6)
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.space(5)

                  Row {
                    id: chips
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(3)
                    visible: root.accountCount > 1
                             && (row.modelData.labels || []).length > 0

                    Repeater {
                      model: (row.modelData.labels || []).slice(0, 1)

                      Rectangle {
                        required property string modelData
                        anchors.verticalCenter: parent.verticalCenter
                        height: chipText.implicitHeight + Style.space(3)
                        width: chipText.width + Style.space(8)
                        radius: Style.space(3)
                        color: Qt.rgba(root.foreground.r, root.foreground.g,
                                       root.foreground.b, 0.14)

                        Text {
                          id: chipText
                          anchors.centerIn: parent
                          text: parent.modelData
                          textFormat: Text.PlainText
                          elide: Text.ElideRight
                          wrapMode: Text.NoWrap
                          maximumLineCount: 1
                          width: Math.min(implicitWidth, Style.space(64))
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                          color: Qt.darker(root.foreground, 1.35)
                        }
                      }
                    }
                  }

                  Text {
                    id: subject
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(Style.space(40),
                                    line.width - (chips.visible ? chips.width + line.spacing : 0))
                    text: root.oneLine(row.modelData.subject)
                    textFormat: Text.PlainText
                    wrapMode: Text.NoWrap
                    maximumLineCount: 1
                    elide: Text.ElideRight
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                    color: root.foreground
                  }
                }

                Text {
                  id: age
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.ageLabel(row.modelData.ts)
                  textFormat: Text.PlainText
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  color: Qt.darker(root.foreground, 1.7)
                }
              }

              Row {
                width: parent.width
                spacing: 0

                Text {
                  id: fromLabel
                  text: row.modelData.from || ""
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  width: Math.min(implicitWidth, parent.width * 0.5)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  color: Qt.darker(root.foreground, 1.15)
                }

                Text {
                  text: {
                    var body = root.oneLine(row.modelData.snippet)
                    if (body === "") return ""
                    return (fromLabel.text !== "" ? "  -  " : "") + body
                  }
                  textFormat: Text.PlainText
                  wrapMode: Text.NoWrap
                  maximumLineCount: 1
                  elide: Text.ElideRight
                  width: parent.width - fromLabel.width
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  color: Qt.darker(root.foreground, 1.7)
                }
              }
            }
          }
        }

        Item {
          width: parent.width
          height: (root.hasPrev || root.hasNext) ? pagerRow.implicitHeight + Style.space(8) : 0
          visible: root.hasPrev || root.hasNext

          Row {
            id: pagerRow
            anchors.centerIn: parent
            spacing: Style.space(10)

            PanelActionButton {
              iconText: root.iconPrev
              tooltipText: "Previous page"
              enabled: root.hasPrev && !root.markAllBusy
              opacity: enabled ? 1 : 0.3
              foreground: root.foreground
              hoverColor: root.accent
              fontFamily: root.fontFamily
              fontSize: Style.font.iconSmall
              onClicked: root.goPrevPage()
            }

            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "page " + (root.pageStack.length + 1)
              textFormat: Text.PlainText
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              color: Qt.darker(root.foreground, 1.7)
            }

            PanelActionButton {
              iconText: root.iconNext
              tooltipText: "Next page"
              enabled: root.hasNext && !root.markAllBusy
              opacity: enabled ? 1 : 0.3
              foreground: root.foreground
              hoverColor: root.accent
              fontFamily: root.fontFamily
              fontSize: Style.font.iconSmall
              onClicked: root.goNextPage()
            }
          }
        }

        Item {
          width: parent.width
          height: root.messages.length === 0 ? Style.space(60) : 0
          visible: root.messages.length === 0

          Text {
            anchors.centerIn: parent
            width: parent.width - Style.space(20)
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: root.reachable
              ? "You're all caught up."
              : (root.errorText !== "" ? root.errorText : "Beeper unreachable")
            textFormat: Text.PlainText
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            color: root.foreground
            opacity: 0.6
          }
        }
      }
    }
  }
}
