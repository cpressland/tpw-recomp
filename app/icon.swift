// The app's icon, a stand-in: a ferris wheel against an evening sky, drawn
// here so nothing of the game's own art is used.
//
//   swift app/icon.swift OUT.iconset && iconutil -c icns OUT.iconset
import AppKit

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AppIcon.iconset"

func draw(_ ctx: CGContext, _ s: CGFloat) {
    // The rounded square of a macOS icon: 824 of 1024, corner 185.
    let inset = s * 100 / 1024, side = s - 2 * inset
    let tile = CGPath(roundedRect: CGRect(x: inset, y: inset, width: side, height: side),
                      cornerWidth: side * 0.225, cornerHeight: side * 0.225, transform: nil)
    ctx.saveGState()
    ctx.addPath(tile)
    ctx.clip()
    let sky = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                         colors: [CGColor(red: 0.98, green: 0.62, blue: 0.36, alpha: 1),
                                  CGColor(red: 0.85, green: 0.38, blue: 0.55, alpha: 1),
                                  CGColor(red: 0.25, green: 0.22, blue: 0.55, alpha: 1)] as CFArray,
                         locations: [0, 0.45, 1])!
    ctx.drawLinearGradient(sky, start: CGPoint(x: 0, y: inset), end: CGPoint(x: 0, y: s - inset), options: [])
    // Grass along the bottom.
    ctx.setFillColor(CGColor(red: 0.23, green: 0.55, blue: 0.28, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: s, height: inset + side * 0.16))

    let c = CGPoint(x: s / 2, y: inset + side * 0.56), r = side * 0.31
    let frame = CGColor(red: 1, green: 1, blue: 1, alpha: 0.95)
    ctx.setStrokeColor(frame)
    ctx.setLineCap(.round)
    // The A-frame from the hub down to the grass.
    ctx.setLineWidth(side * 0.03)
    for dx in [-1.0, 1.0] {
        ctx.move(to: c)
        ctx.addLine(to: CGPoint(x: c.x + CGFloat(dx) * r * 0.62, y: inset + side * 0.13))
    }
    ctx.strokePath()
    // Rim and spokes.
    ctx.setLineWidth(side * 0.022)
    ctx.strokeEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
    ctx.setLineWidth(side * 0.011)
    let cars = 8
    for i in 0..<cars {
        let a = CGFloat(i) * 2 * .pi / CGFloat(cars)
        ctx.move(to: c)
        ctx.addLine(to: CGPoint(x: c.x + r * cos(a), y: c.y + r * sin(a)))
    }
    ctx.strokePath()
    // The cars, hanging below the rim, in fairground colours.
    let colours: [CGColor] = [
        CGColor(red: 0.95, green: 0.26, blue: 0.24, alpha: 1), CGColor(red: 1.0, green: 0.80, blue: 0.20, alpha: 1),
        CGColor(red: 0.20, green: 0.70, blue: 0.95, alpha: 1), CGColor(red: 0.45, green: 0.85, blue: 0.35, alpha: 1)]
    let car = side * 0.085
    for i in 0..<cars {
        let a = CGFloat(i) * 2 * .pi / CGFloat(cars)
        let p = CGPoint(x: c.x + r * cos(a), y: c.y + r * sin(a))
        let box = CGRect(x: p.x - car / 2, y: p.y - car * 1.05, width: car, height: car * 0.85)
        ctx.setFillColor(colours[i % colours.count])
        ctx.addPath(CGPath(roundedRect: box, cornerWidth: car * 0.25, cornerHeight: car * 0.25, transform: nil))
        ctx.fillPath()
    }
    // The hub.
    ctx.setFillColor(frame)
    ctx.fillEllipse(in: CGRect(x: c.x - side * 0.04, y: c.y - side * 0.04, width: side * 0.08, height: side * 0.08))
    ctx.restoreGState()
}

func png(_ px: Int, _ name: String) throws {
    let cs = CGColorSpace(name: CGColorSpace.sRGB)!
    let ctx = CGContext(data: nil, width: px, height: px, bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    draw(ctx, CGFloat(px))
    let rep = NSBitmapImageRep(cgImage: ctx.makeImage()!)
    try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(out)/\(name)"))
}

try FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
for pt in [16, 32, 128, 256, 512] {
    try png(pt, "icon_\(pt)x\(pt).png")
    try png(pt * 2, "icon_\(pt)x\(pt)@2x.png")
}
