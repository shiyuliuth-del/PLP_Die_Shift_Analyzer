"""Motion-type palette: dark solid vias and pastel solid RDL."""
def layer_style(role,motion=None):
    palette={'运动过孔':('#24647a',1,True,0,4),'固定过孔':('#715287',1,True,0,4),
             '运动RDL':('#b4d4bf',.70,True,0,2),'固定RDL':('#c3d3e9',.70,True,0,2),
             '封装边框':('#929cab',.9,False,1,1),'芯片边框':('#547a9c',1,False,1.2,5),
             '禁布区':('#d6aaa5',.3,True,0,.5)}
    color,alpha,fill,width,z=palette.get(role,('#b9c1c9',.6,False,.7,.5))
    return dict(color=color,alpha=alpha,fill=fill,label=role,width=width,z=z)
