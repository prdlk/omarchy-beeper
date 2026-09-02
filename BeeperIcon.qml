import QtQuick
import QtQuick.Window
import qs.Commons

// Chat bubble: rounded outline with a tail, and three dots that fade in when
// something is unread. Stroke, not a filled blob, so the silhouette keeps
// contrast on a transparent bar over light, dark, or mixed wallpapers.
// Every painted extent stays inside `iconSize` at every dot pose.
//
// `iconSize` is the *ink* box, not the bar's icon slot. Neighbouring bar
// icons are Nerd Font glyphs drawn at Style.bar.iconFont inside the wider
// Style.bar.iconCanvas slot, so their ink is a fraction of the slot, never
// the whole of it. Measured off the running bar (VictorMono Nerd Font,
// iconFont 13, scale 2): those glyphs ink 9.0-11.5 logical px, median 10,
// with a 1 logical px stroke. 0.85 em lands the bubble on that median;
// painting edge to edge on the 16 px canvas made it 15 px with a 2 px
// stroke, half again as large as everything beside it.
Item {
  id: root

  property real iconSize: Style.bar.iconFont * 0.85
  property color color: Color.foreground
  property color dotColor: color
  property bool hasMail: false

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  readonly property real dpr: {
    var win = Window.window
    return (win && win.devicePixelRatio > 0) ? win.devicePixelRatio : 1
  }

  function snap(v) {
    return Math.round(v * root.dpr) / root.dpr
  }

  function snapStroke(v) {
    return Math.max(1, Math.round(v * root.dpr)) / root.dpr
  }

  // snapStroke already floors the stroke at one device pixel, which is what
  // the glyphs beside it use; a logical-pixel floor would double it on HiDPI.
  readonly property real stroke: snapStroke(iconSize * 0.11)
  readonly property real pad: snap(Math.max(stroke / 2, 0.5))

  readonly property real tailH: snap(Math.max(stroke * 1.2, iconSize * 0.16))
  readonly property real bodyX: snap(pad)
  readonly property real bodyY: snap(pad)
  readonly property real bodyW: snap(iconSize - pad * 2)
  readonly property real bodyH: snap(Math.max(stroke * 3, iconSize - pad * 2 - tailH))
  readonly property real radius: snap(Math.min(bodyW, bodyH) * 0.3)

  // The tail hangs off the bottom edge, left of centre, and never reaches
  // the corner arc: keep both feet between the arcs.
  readonly property real tailLeft: snap(bodyX + Math.max(radius, bodyW * 0.2))
  readonly property real tailRight: snap(tailLeft + Math.max(stroke, bodyW * 0.2))
  readonly property real tailTipX: snap(tailLeft + Math.max(stroke / 2, bodyW * 0.04))

  readonly property real dotR: snap(Math.max(1, Math.min(iconSize * 0.075, bodyH * 0.16)))
  readonly property real dotGap: snap(Math.max(dotR * 2.2, bodyW * 0.24))
  readonly property real dotY: snap(bodyY + bodyH / 2)

  property real dotAmount: hasMail ? 1 : 0
  Behavior on dotAmount {
    NumberAnimation { duration: 180; easing.type: Easing.InOutQuad }
  }

  Canvas {
    id: canvas
    anchors.fill: parent
    antialiasing: true
    renderTarget: Canvas.FramebufferObject
    renderStrategy: Canvas.Cooperative

    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      ctx.clearRect(0, 0, width, height)
      ctx.lineCap = "round"
      ctx.lineJoin = "round"
      ctx.strokeStyle = root.color
      ctx.lineWidth = root.stroke

      var x = root.bodyX
      var y = root.bodyY
      var w = root.bodyW
      var h = root.bodyH
      var r = Math.min(root.radius, w / 2, h / 2)

      // One closed path: bubble outline with the tail cut into the bottom
      // edge, so there is no seam where a second stroke would overlap.
      ctx.beginPath()
      ctx.moveTo(x + r, y)
      ctx.lineTo(x + w - r, y)
      ctx.arcTo(x + w, y, x + w, y + r, r)
      ctx.lineTo(x + w, y + h - r)
      ctx.arcTo(x + w, y + h, x + w - r, y + h, r)
      ctx.lineTo(root.tailRight, y + h)
      ctx.lineTo(root.tailTipX, y + h + root.tailH)
      ctx.lineTo(root.tailLeft, y + h)
      ctx.lineTo(x + r, y + h)
      ctx.arcTo(x, y + h, x, y + h - r, r)
      ctx.lineTo(x, y + r)
      ctx.arcTo(x, y, x + r, y, r)
      ctx.closePath()
      ctx.stroke()

      if (root.dotAmount <= 0.01)
        return

      ctx.fillStyle = root.dotColor
      ctx.globalAlpha = root.dotAmount
      var cx = x + w / 2
      var scaled = root.dotR * (0.6 + 0.4 * root.dotAmount)
      for (var i = -1; i <= 1; i++) {
        ctx.beginPath()
        ctx.arc(cx + i * root.dotGap, root.dotY, scaled, 0, Math.PI * 2, false)
        ctx.fill()
      }
      ctx.globalAlpha = 1
    }
  }

  onColorChanged: canvas.requestPaint()
  onDotColorChanged: canvas.requestPaint()
  onDotAmountChanged: canvas.requestPaint()
  onIconSizeChanged: canvas.requestPaint()
  onBodyWChanged: canvas.requestPaint()
  onDotRChanged: canvas.requestPaint()
  onDprChanged: canvas.requestPaint()
  Component.onCompleted: canvas.requestPaint()
}
