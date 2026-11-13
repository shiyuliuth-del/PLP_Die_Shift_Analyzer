"""Shared on-canvas selected-object callouts for width and reconstruction."""

def put(window,labels,index,text,tip,offset,fontsize,edge,ink,arrow,alpha=.94,zorder=15):
    if index==len(labels) or labels[index].axes is not window.ax:
        a=window.ax.annotate('',xy=(0,0),textcoords='offset points',xytext=(0,0),
            ha='center',va='center',multialignment='left',bbox=dict(boxstyle='square,pad=0.15',facecolor='white'),
            arrowprops=dict(arrowstyle='->',lw=.9,shrinkB=0),annotation_clip=False)
        if index==len(labels):labels.append(a)
        else:labels[index]=a
    a=labels[index];a.xy=tip;a.set_position(offset);a.set_text(text);a.set_fontsize(fontsize)
    a.set_color(ink);a.arrow_patch.set_color(arrow)
    a.get_bbox_patch().set_edgecolor(edge);a.get_bbox_patch().set_alpha(alpha)
    a.set_zorder(zorder);a.set_visible(True)
    return a
