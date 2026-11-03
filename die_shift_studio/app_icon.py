"""Original application mark: a via moves between two offset die outlines."""
from functools import lru_cache
from io import BytesIO
from PIL import Image,ImageDraw

@lru_cache(maxsize=1)
def icon_image():
    scale=3
    image=Image.new('RGBA',(256*scale,256*scale))
    pen=ImageDraw.Draw(image)
    def coords(values):
        return tuple(round(value*scale) for value in values)
    def line(points,ink,width):
        pen.line([coords(p) for p in points],fill=ink,width=width*scale,joint='curve')
    def circle(x,y,r,ink):
        pen.ellipse(coords((x-r,y-r,x+r,y+r)),fill=ink)
    pen.rounded_rectangle(coords((8,8,248,248)),radius=48*scale,fill='#213951')
    # Lower, fixed die and upper, shifted die. Keep the via visible at 16 px.
    for x in (74,109,144,179):
        line([(x,185),(x,210)],'#7d9faa',7)
    pen.rounded_rectangle(coords((39,70,186,192)),radius=18*scale,outline='#94b5c1',width=8*scale)
    pen.rounded_rectangle(coords((65,48,211,170)),radius=17*scale,fill='#245865',outline='#b1e6dc',width=9*scale)
    line([(82,143),(116,143),(147,112),(167,112)],'#93dbd0',9)
    circle(167,112,12,'#95d4c9')
    line([(164,110),(190,83)],'#f3b879',7)
    circle(190,83,16,'#ffc987');circle(190,83,6,'#284c5e')
    return image.resize((256,256),Image.Resampling.LANCZOS if hasattr(Image,'Resampling') else Image.LANCZOS)

def window_icon():
    from .qt_compat import QtGui
    buf=BytesIO();icon_image().save(buf,format='PNG')
    pixmap=QtGui.QPixmap();pixmap.loadFromData(buf.getvalue(),'PNG')
    return QtGui.QIcon(pixmap)

def write_ico(path):
    icon_image().save(path,format='ICO',sizes=[(s,s) for s in (16,24,32,48,64,128,256)])
