import AppKit
let wave = NSImage(contentsOfFile: "/tmp/mb_alpha.png")!
func rep(_ n:Int)->NSBitmapImageRep{ NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:n,pixelsHigh:n,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:n*4,bitsPerPixel:32)! }
func ctx(_ r:NSBitmapImageRep,_ b:()->Void){ NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current=NSGraphicsContext(bitmapImageRep:r); b(); NSGraphicsContext.restoreGraphicsState() }
// render directly at native pixel size so the border stays crisp
func make(_ n:Int, lw:CGFloat, _ path:String){
    let N=CGFloat(n)
    let m=N*0.02, side=N-2*m
    // inset the stroke rect by half the line width so the border isn't clipped at the edge
    let sq=NSRect(x:m+lw/2, y:m+lw/2, width:side-lw, height:side-lw)
    let p=NSBezierPath(roundedRect:sq,xRadius:(side-lw)*0.2237,yRadius:(side-lw)*0.2237)
    let ww=side*1.10, wh=ww*(455.0/1024.0)
    let wr=NSRect(x:m+(side-ww)/2,y:m+(side-wh)/2,width:ww,height:wh)
    let r=rep(n)
    ctx(r){
        NSColor.black.setStroke(); p.lineWidth=lw; p.stroke()
        NSGraphicsContext.saveGraphicsState()
        let clip=NSBezierPath(roundedRect:NSRect(x:m,y:m,width:side,height:side),xRadius:side*0.2237,yRadius:side*0.2237)
        clip.addClip()
        wave.draw(in:wr,from:.zero,operation:.sourceOver,fraction:1)
        NSGraphicsContext.restoreGraphicsState()
        NSColor.black.set(); NSRect(x:0,y:0,width:N,height:N).fill(using:.sourceAtop)
    }
    try! r.representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:path))
}
make(36, lw:1.0, "/tmp/thin@2x.png")   // 1px border on Retina — thinnest crisp
make(18, lw:1.0, "/tmp/thin@1x.png")
print("ok")
