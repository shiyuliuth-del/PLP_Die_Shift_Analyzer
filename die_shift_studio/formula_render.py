"""Offline publication-style math, embedded as high-DPI QTextDocument resources."""
from functools import lru_cache
from io import BytesIO
import hashlib
from matplotlib.mathtext import math_to_image
from matplotlib.font_manager import FontProperties
from PIL import Image

@lru_cache(maxsize=256)
def render_formula(tex):
    buffer=BytesIO()
    math_to_image('$'+tex+'$',buffer,prop=FontProperties(family='STIXGeneral',size=13),dpi=192,format='png',color='#203e56')
    data=buffer.getvalue()
    with Image.open(BytesIO(data)) as img:width,height=img.size
    return data,width/2,height/2

class MathDocument:
    def __init__(self):self.resources={}
    def formula(self,tex):
        name='math:///'+hashlib.sha256(tex.encode()).hexdigest()+'.png'
        data,width,height=render_formula(tex);self.resources[name]=data
        return f'<img src="{name}" width="{width:g}" height="{height:g}" alt="数学公式">'
    def block(self,*lines):return ''.join('<p>'+self.formula(line)+'</p>' for line in lines)

def set_document(browser,html,resources):
    from .qt_compat import QtCore,QtGui
    doc=browser.document();kind=getattr(QtGui.QTextDocument,'ResourceType',QtGui.QTextDocument).ImageResource
    for name,data in resources.items():
        if not QtCore.QUrl(name).isValid():raise ValueError('数学资源URL无效')
        image=QtGui.QImage.fromData(data,'PNG')
        if image.isNull():raise ValueError('数学公式渲染失败')
        doc.addResource(kind,QtCore.QUrl(name),image)
    browser.setHtml(html)
