"""Native current-pose GDS/OAS export, separate from nominal ABC DXF."""
from dataclasses import dataclass,asdict,replace
from pathlib import Path
import math,os,tempfile
import numpy as np
import shapely
from .resources import cancel_check

@dataclass(frozen=True)
class OutputOptions:
    directory:str=''
    layer_scope:str='all'
    merge_layers:bool=True
    dbu_um:float=0.
    max_points:int=8190
    @classmethod
    def from_dict(cls,value=None):
        value=value or {};q=cls(**{k:v for k,v in value.items() if k in cls.__dataclass_fields__})
        if not isinstance(q.directory,str) or q.layer_scope not in ('all','visible','participating'):raise ValueError('GDS/OAS输出范围无效')
        if type(q.merge_layers) is not bool or not math.isfinite(q.dbu_um) or not 0<=q.dbu_um<=1000:raise ValueError('输出格点须为非负μm，0沿用输入格点')
        if type(q.max_points) is not int or not 4<=q.max_points<=8190:raise ValueError('GDS多边形最大节点须在4～8190之间')
        return q

def build_controls(w,layout):
    from .qt_compat import QtWidgets as W
    w.layout_output_button=W.QPushButton('输出GDS/OAS');layout.addWidget(w.layout_output_button)
    _,_,form=w.collapsible(layout,'GDS/OAS输出参数',w.setting_sections)
    fields={};w.layout_output_fields=fields;w.layout_output_directory=''
    form.addRow(W.QLabel('导出当前完成姿态，包含重构铜；Flatten顶层。'))
    box=W.QComboBox()
    for title,value in [('全部已导入图层','all'),('当前显示图层','visible'),('参与分析图层','participating')]:box.addItem(title,value)
    fields['layer_scope']=box;form.addRow('导出图层',box)
    box=W.QCheckBox('Merge 导出层图案');fields['merge_layers']=box;form.addRow(box)
    box=W.QDoubleSpinBox();box.setDecimals(6);box.setRange(0,1000);box.setSpecialValueText('沿用输入格点');box.setKeyboardTracking(False);fields['dbu_um']=box;form.addRow('输出格点 / μm',box)
    box=W.QSpinBox();box.setRange(4,8190);box.setKeyboardTracking(False);fields['max_points']=box;form.addRow('GDS最大节点',box)
    form.addRow(W.QLabel('开启“显示DRC问题”时附带ERROR；不修改源文件。'))
    restore_controls(w);w.layout_output_button.clicked.connect(lambda:export_layout(w))

def configuration(w):
    from .qt_compat import QtWidgets as W
    return asdict(OutputOptions.from_dict(dict(directory=w.layout_output_directory,**{k:v.currentData() if isinstance(v,W.QComboBox) else v.isChecked() if isinstance(v,W.QCheckBox) else v.value() for k,v in w.layout_output_fields.items()})))

def restore_controls(w,value=None):
    from .qt_compat import QtWidgets as W
    q=OutputOptions.from_dict(value);w.layout_output_directory=q.directory
    for key,widget in w.layout_output_fields.items():
        value=getattr(q,key)
        if isinstance(widget,W.QComboBox):widget.setCurrentIndex(widget.findData(value))
        elif isinstance(widget,W.QCheckBox):widget.setChecked(value)
        else:widget.setValue(value)

def write(snapshot,destination,dbu,options=None,issues=(),die_count=0,progress=lambda *_:None):
    """Atomic output. Quantization drops only zero-area, unrepresentable pieces."""
    import gdstk
    from .gds_merge import winding_outline
    from .oas_errors import choose_error_layer,rectangles
    options=options or OutputOptions();dbu=options.dbu_um or dbu;path=Path(destination).resolve()
    if path.suffix.lower() not in ('.gds','.gdsii','.oas','.oasis'):raise ValueError('请选择GDS或OAS文件后缀')
    layers={};total=max(1,len(snapshot.layers));dropped=0
    for n,(key,blobs) in enumerate(snapshot.layers.items()):
        cancel_check();geos=[shapely.from_wkb(b) for b in blobs]
        if options.merge_layers:geos=list(shapely.get_parts(shapely.union_all(geos)))
        from .short_edges import contract
        if snapshot.minimum_edge_um:
            snapped=[shapely.transform(g,lambda xy:np.rint(xy/dbu)*dbu) for g in geos]
            geos=[contract(poly,snapshot.minimum_edge_um) for g in snapped
                  for poly in shapely.get_parts(shapely.make_valid(g)) if poly.geom_type=='Polygon']
        layers[key]=tuple(g.wkb for g in geos if not g.is_empty)
        if progress:progress(round(30*(n+1)/total),'整理输出层 '+key)
    error_layer=choose_error_layer(layers) if issues else None
    if path.suffix.lower() in ('.oas','.oasis'):
        from .oas_stream import Store
        store=Store(path.parent,dbu,error_layer=error_layer)
        try:
            store.add('CurrentPreview',layers,(0.,0.),(0.,0.),errors=list(issues) if issues else None,die_count=die_count)
            if progress:progress(70,'写入当前姿态OAS')
            result=store.write(path,progress)
        finally:store.close()
        return dict(result,path=str(path),format='OAS',error_layer=error_layer,dbu_um=dbu)
    lib=gdstk.Library(unit=1e-6,precision=dbu*1e-6);top=lib.new_cell('CurrentPreview')
    def add(cell,key,blobs):
        nonlocal dropped
        layer,datatype=map(int,key.split('/'))
        if not 0<=layer<=65535 or not 0<=datatype<=65535:raise ValueError('GDS图层编号超过16位范围')
        for blob in blobs:
            for g in shapely.get_parts(shapely.from_wkb(blob)):
                cancel_check()
                if g.is_empty:continue
                if g.geom_type!='Polygon' or not g.is_valid:raise ValueError('输出包含无效多边形')
                xy=np.rint(winding_outline(g)/dbu)
                if np.max(abs(xy),initial=0)>2**31-1:raise ValueError('GDS坐标超过32位格点范围，请用OAS')
                xy=xy.astype(np.int64);xy=xy[np.any(xy!=np.roll(xy,1,axis=0),axis=1)]
                area=sum(int(a[0])*int(b[1])-int(a[1])*int(b[0]) for a,b in zip(xy,np.roll(xy,-1,axis=0)))
                if len(xy)<3 or not area:dropped+=1;continue
                cell.add(gdstk.Polygon(xy*dbu,layer=layer,datatype=datatype))
    for key,blobs in layers.items():add(top,key,blobs)
    if issues:
        cell=lib.new_cell('ERROR');top.add(gdstk.Reference(cell))
        for blobs in rectangles(list(issues),die_count,dbu).values():add(cell,f'{error_layer[0]}/{error_layer[1]}',blobs)
    fd,temp=tempfile.mkstemp(prefix='.dsa-export-',suffix='.gds',dir=path.parent);os.close(fd)
    try:
        if progress:progress(70,'写入当前姿态GDS')
        lib.write_gds(temp,max_points=options.max_points)
        # Format integrity is part of atomic writing, not an extra release round.
        check=gdstk.read_gds(temp)
        if not check.top_level():raise ValueError('导出文件无顶层Cell')
        cancel_check();os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return dict(path=str(path),format='GDS',error_layer=error_layer,dbu_um=dbu,discarded_subgrid_fragments=dropped)

def export_layout(w):
    from .qt_compat import QtWidgets as W,run_dialog,ACCEPTED
    if w.data is None:w.error('请先打开GDS/OAS');return
    if w.worker or w.preview_workers or w.preview_pending or w.cut_drag.state or getattr(w,'reconstruction_pending',False):w.error('请先完成当前预演及待应用的重构修改');return
    options=OutputOptions.from_dict(configuration(w));root=Path(options.directory or w.data.path).parent if not options.directory else Path(options.directory)
    dialog=W.QFileDialog(w,'输出GDS/OAS',str(root/(Path(w.data.path).stem+'_preview.oas')),'OASIS (*.oas);;GDSII (*.gds)')
    dialog.setAcceptMode(getattr(W.QFileDialog,'AcceptMode',W.QFileDialog).AcceptSave);dialog.setDefaultSuffix('oas')
    dialog.filterSelected.connect(lambda name:dialog.setDefaultSuffix('gds' if name.startswith('GDSII') else 'oas'))
    ok=run_dialog(dialog)==ACCEPTED;paths=dialog.selectedFiles() if ok else [];dialog.deleteLater()
    if not paths:return
    path=Path(paths[0]).resolve()
    if path==Path(w.data.path).resolve():w.error('不能覆盖当前源图纸，请使用新文件名');return
    w.layout_output_directory=str(path.parent);w.stop_animation()
    data=w.data;cache=w.geometry_cache;frame=w.current_frame;roles=w.roles();settings=w.view_settings;p=w.params();case=w.case
    selected=set(data.layers) if options.layer_scope=='all' else {k for k,v in w.visible_boxes.items() if v.isChecked()} if options.layer_scope=='visible' else {k for k,v in roles.items() if v!='不参与'}
    show_drc=w.show_issues.isChecked();reconstruction=w.reconstruction_toggle.isChecked();motions=w.motions()
    def work(progress):
        from .fast_preview import GeometryCache
        from .resources import progress_slice
        from .dxf_export import snapshot
        from .measured_issues import catalogue
        gc=cache or GeometryCache(data);current=frame;issues=[]
        if show_drc:
            if case is None:raise ValueError('输出DRC问题需要有效的芯片/封装用途及方案评估')
            from .contact import ContactModel
            from .drc import GlobalDRC
            model=ContactModel(data,case,roles,case['connections'],gc,motions,p)
            current=dict(frame or dict(states=np.zeros((len(case['dies']),3))))
            replacements=current.get('reconstruction',{}).get('replacements',{}) if reconstruction else {}
            current['drc']=GlobalDRC(model,p).frame(current['states'],replacements,settings,progress_slice(progress,0,.35))
            from .drc_policy import visible
            current['drc']=[q for q in current['drc'] if visible(settings,q)]
            issues=catalogue(current,case,model)
        frozen=snapshot(data,gc,current,reconstruction=reconstruction,progress=progress_slice(progress,35,.2))
        frozen=replace(frozen,layers={k:v for k,v in frozen.layers.items() if k in selected})
        if not frozen.layers:raise ValueError('导出范围没有图形层')
        return write(frozen,path,data.dbu_um,options,issues,len(case['dies']) if case else 0,progress_slice(progress,55,.45))
    def done(result):
        message=f"当前姿态{result['format']}已输出：{result['path']}";w.log_bus.emit(message);w.statusBar().showMessage(message)
    w.start_worker(work,done)
